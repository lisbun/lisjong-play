"""RiichiLab ranked対局をliveで観戦する専用GUI entry point。

Human Play / Replay / Local Spectatorとは別のentry pointで、human action
selection UIを持たない。盤面は`GuiBoardRenderer`をそのまま共有し、河 / 副露 /
立直 / 点数 / 局情報 / ドラ描画を二重実装しない。

Tk widgetはmain threadだけが触り、Arena ranked実行とPolicy実行はworker
threadで動く。Arenaのpresentation bufferはTk main threadからだけdrainする。

window close semantics
----------------------
window closeはactive ranked gameをabortしない。closeでは presentation buffer
を`detach()`してwindowを閉じるだけで、ranked workerへcancel / disconnectを
送らない。workerはdaemon threadにせず、Tk mainloop終了後にcompletionまで
join するので、GUIを閉じてもprocessはranked完走まで生存する。
"""

import argparse
import threading
from collections.abc import Callable, Sequence
from typing import Any

from lisjong_arena.riichilab.live_presentation import BoundedRankedPresentationBuffer

from lisjong_play.gui import GuiUnavailableError, load_tk
from lisjong_play.gui_board import (
    CENTER_PLACE,
    GUI_RIVER_LEGEND,
    TABLE_PLACE,
    GuiBoardRenderer,
    build_board_tile_images,
    configure_board_styles,
)
from lisjong_play.riichilab_source import (
    PROFILE_NAMES,
    RiichiLabLiveController,
    RiichiLabWorkerStatus,
    resolve_record_path,
    run_riichilab_worker,
)

_IDLE_MESSAGE = "profileを確認して観戦を開始してください。"
_HAND_FRAME_TEXT = "lisjong自身の手牌（bound bot seatのplayer-visible手牌のみ）"
_INFO_VISIBLE_LINES = 8

# RiichiLab rankedはserver側のresponse time budgetを持つため、viewerは
# 「表示だけ」を止める。Local Spectatorのpauseはlocal engine実行のgateであり、
# semanticsが異なる。
LIVE_SCOPE_NOTE = (
    "表示範囲: lisjong自身のplayer-visible state のみ"
    "（他家のconcealed handは表示しません）"
)
PAUSE_SCOPE_NOTE = (
    "一時停止は表示だけを止めます。RiichiLab ranked対局とPolicy判断は進み続けます。"
)
CLOSE_SCOPE_NOTE = (
    "windowを閉じてもranked対局は中断されません。"
    "presentationのみdetachし、対局はArena lifecycleに従って完走します。"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lisjong-play-riichilab",
        description=(
            "RiichiLab ranked対局(1半荘)をlive観戦するGUIを起動します。"
            "profile / credential / ranked実行はlisjong-arenaのcontractを使います。"
        ),
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_NAMES,
        default=None,
        help="lisjong-arena RiichiLab execution profile (例: lisjong-dev)",
    )
    parser.add_argument(
        "--record-dir",
        default=None,
        help=(
            "指定した場合、Arenaのdurable ranked recordをこのdirectory配下へ"
            "取得します(live presentationとは独立のArena所有path)"
        ),
    )
    return parser


class _TkRiichiLabApplication:
    """Tk main threadだけでwidgetを操作するRiichiLab live viewer。"""

    _POLL_INTERVAL_MS = 40

    def __init__(
        self,
        root: Any,
        *,
        tk: Any,
        ttk: Any,
        messagebox: Any,
        scrolledtext: Any,
        profile_name: str | None,
        record_dir: str | None,
    ) -> None:
        self._root = root
        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._scrolledtext = scrolledtext
        self._profile_name = profile_name
        self._record_dir = record_dir
        self._buffer: BoundedRankedPresentationBuffer | None = None
        self._controller: RiichiLabLiveController | None = None
        self._status: RiichiLabWorkerStatus | None = None
        self._worker: threading.Thread | None = None
        self._reported_summary = False
        self._reported_error = False
        self._reported_runtime = False
        self._reported_run = False
        self._tile_images = build_board_tile_images(tk)
        # on_select_actionを渡さないため、牌はbuttonにならない。viewerへ
        # action control(human takeover)を与えないことをrendererの契約で保証する。
        self._board_renderer = GuiBoardRenderer(ttk, self._tile_images)

        root.title("lisjong-play RiichiLab live viewer")
        root.geometry("1180x900")
        root.minsize(920, 720)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._configure_style()
        self._build_layout()
        root.after(self._POLL_INTERVAL_MS, self._poll_presentation)

    @property
    def worker(self) -> threading.Thread | None:
        """Tk mainloop終了後にjoinするranked worker。"""
        return self._worker

    def _configure_style(self) -> None:
        style = self._ttk.Style(self._root)
        configure_board_styles(style)
        style.configure("Primary.TButton", padding=(12, 8))

    def _build_layout(self) -> None:
        main = self._ttk.Frame(self._root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        setup = self._ttk.Frame(main)
        setup.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._ttk.Label(setup, text="Profile").pack(side="left")
        self._profile_var = self._tk.StringVar(
            value=self._profile_name if self._profile_name is not None else ""
        )
        self._profile_box = self._ttk.Combobox(
            setup,
            textvariable=self._profile_var,
            values=list(PROFILE_NAMES),
            state="readonly",
            width=18,
        )
        self._profile_box.pack(side="left", padx=(6, 16))
        self._ttk.Label(
            setup,
            text=f"record: {'on' if self._record_dir else 'off'}",
        ).pack(side="left", padx=(0, 16))
        self._start_button = self._ttk.Button(
            setup,
            text="観戦開始",
            style="Primary.TButton",
            command=self._start_session,
        )
        self._start_button.pack(side="left")
        self._status_var = self._tk.StringVar(value=_IDLE_MESSAGE)
        self._ttk.Label(setup, textvariable=self._status_var).pack(
            side="right", padx=(12, 0)
        )

        self._table = self._ttk.Frame(main, style="Table.TFrame", padding=4)
        self._table.grid(row=1, column=0, sticky="nsew", pady=(0, 2))
        self._seat_frames: dict[str, Any] = {}
        for position, (relx, rely, anchor, x, y) in TABLE_PLACE.items():
            frame = self._ttk.Frame(self._table, style="Seat.TFrame", padding=2)
            frame.place(relx=relx, rely=rely, anchor=anchor, x=x, y=y)
            self._seat_frames[position] = frame
        self._center = self._ttk.Frame(self._table, style="Seat.TFrame", padding=4)
        relx, rely, anchor = CENTER_PLACE
        self._center.place(relx=relx, rely=rely, anchor=anchor)
        self._ttk.Label(
            self._center,
            text="卓情報は観戦開始後に更新されます",
            style="Center.TLabel",
            anchor="center",
            justify="center",
        ).pack()
        self._ttk.Label(
            self._table,
            text=GUI_RIVER_LEGEND,
            style="BoardText.TLabel",
            font=("TkDefaultFont", 8),
        ).place(relx=0.0, rely=1.0, anchor="sw")

        self._hand = self._ttk.LabelFrame(main, text=_HAND_FRAME_TEXT, padding=4)
        self._hand.grid(row=2, column=0, sticky="ew", pady=(4, 2))
        self._ttk.Label(self._hand, text=LIVE_SCOPE_NOTE).pack()

        controls = self._ttk.Frame(main)
        controls.grid(row=3, column=0, sticky="ew", pady=2)
        self._pause_button = self._ttk.Button(
            controls, text="⏸ 表示のみ一時停止", command=self._toggle_pause
        )
        self._pause_button.pack(side="left", padx=3)
        self._step_button = self._ttk.Button(
            controls, text="⏭ ステップ", command=self._step
        )
        self._step_button.pack(side="left", padx=3)
        self._follow_button = self._ttk.Button(
            controls, text="⏩ 最新へ追従", command=self._follow_live
        )
        self._follow_button.pack(side="left", padx=3)
        self._frame_var = self._tk.StringVar(value="")
        self._ttk.Label(controls, textvariable=self._frame_var).pack(
            side="left", padx=(16, 0)
        )

        info = self._ttk.LabelFrame(main, text="runtime / 結果", padding=4)
        info.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        info.columnconfigure(0, weight=1)
        self._info = self._scrolledtext.ScrolledText(
            info, height=_INFO_VISIBLE_LINES, wrap="word", state="disabled"
        )
        self._info.grid(row=0, column=0, sticky="ew")
        self._set_controls_enabled(False)

    def _start_session(self) -> None:
        if self._controller is not None:
            return
        profile_value = self._profile_var.get().strip()
        if profile_value not in PROFILE_NAMES:
            self._messagebox.showerror("入力エラー", "profileを選択してください。")
            return
        try:
            record_path = resolve_record_path(self._record_dir)
        except (OSError, ValueError) as error:
            self._messagebox.showerror(
                "record path エラー", f"{type(error).__name__}: {error}"
            )
            return

        self._clear_info()
        self._append_info(f"RiichiLab ranked live viewer: profile={profile_value}")
        self._append_info(LIVE_SCOPE_NOTE)
        self._append_info(PAUSE_SCOPE_NOTE)
        self._append_info(CLOSE_SCOPE_NOTE)
        if record_path is not None:
            self._append_info(f"durable record: {record_path}")

        self._buffer = BoundedRankedPresentationBuffer()
        self._controller = RiichiLabLiveController(self._buffer)
        self._status = RiichiLabWorkerStatus()
        self._reported_summary = False
        self._reported_error = False
        self._reported_runtime = False
        self._reported_run = False
        self._set_setup_enabled(False)
        self._set_controls_enabled(True)
        self._status_var.set("ranked接続中")
        # daemon threadにしない。GUIを閉じたあともranked完走まで生存させる。
        self._worker = threading.Thread(
            target=run_riichilab_worker,
            args=(self._buffer, self._status),
            kwargs={"profile_name": profile_value, "record_path": record_path},
            name="lisjong-play-riichilab",
            daemon=False,
        )
        self._worker.start()
        self._refresh_controls()

    def _poll_presentation(self) -> None:
        """Tk main threadからArena bufferをdrainし、表示stateを更新する。

        pause中もdrainを止めない(Arena側のterminal factとcoalesce countを
        受け取り続けるため)。
        """
        controller = self._controller
        if controller is not None:
            # drainは毎回行う。描画は新しいfactが届いたpassだけで足りる。
            if controller.ingest():
                self._refresh(controller.state())
        self._refresh_worker_status()
        self._root.after(self._POLL_INTERVAL_MS, self._poll_presentation)

    def _refresh(self, state: Any) -> None:
        if state.frame is not None:
            self._board_renderer.render_board(
                state.frame.board,
                (),
                seat_frames=self._seat_frames,
                center=self._center,
                hand=self._hand,
            )
        self._frame_var.set(self._frame_text(state))
        if state.failure is not None:
            self._status_var.set(f"failure: {state.failure.failure_type}")
        elif state.completion is not None:
            self._status_var.set("end_game: 半荘が終了しました。")
        elif state.frame is not None:
            self._status_var.set(
                "進行中 (表示停止中)" if not state.following else "進行中"
            )
        self._report_terminal(state)

    def _frame_text(self, state: Any) -> str:
        frame = state.frame
        parts = [
            "追従中" if state.following else "表示停止中",
            f"受信 {state.received_frames}",
            f"保留 {state.pending_frames}",
            f"未表示 {state.skipped_frames}",
        ]
        if frame is not None:
            parts.insert(1, f"request {frame.request_id} (#{frame.ordinal})")
            parts.insert(2, f"{frame.seat_label} {frame.action_label}")
        return " / ".join(parts)

    def _report_terminal(self, state: Any) -> None:
        if state.completion is not None and not self._reported_summary:
            self._reported_summary = True
            scores = state.completion.scores
            if scores is None:
                # Arenaが`scores`なしの`end_game`を受けた場合は推測しない。
                self._append_info("final scores: 未提供", separator=True)
            else:
                body = " / ".join(str(score) for score in scores)
                self._append_info(f"final scores: {body}", separator=True)
            self._append_info(f"bound seat: {state.completion.seat_label}")
        if state.failure is not None and not self._reported_error:
            self._reported_error = True
            self._append_info(
                f"ranked run failure type: {state.failure.failure_type}",
                separator=True,
            )

    def _refresh_worker_status(self) -> None:
        status = self._status
        if status is None:
            return
        snapshot = status.snapshot()
        if snapshot.runtime_summary is not None and not self._reported_runtime:
            self._reported_runtime = True
            self._append_info(snapshot.runtime_summary, separator=True)
        if snapshot.error_text is not None:
            # 「error文をすでに表示したか」と「session lifecycleを終了したか」は
            # 別の判断である。Arena側のfailure factを先に表示していた場合でも、
            # worker failureのsession終了処理(setup再有効化)は必ず行う。
            # 重複するtext / dialogだけを`_reported_error`で抑止する。
            if not self._reported_error:
                self._reported_error = True
                self._append_info(f"ERROR: {snapshot.error_text}", separator=True)
                self._messagebox.showerror("RiichiLab エラー", snapshot.error_text)
            self._status_var.set("ranked runはエラーで終了しました。")
            self._end_session()
            return
        if snapshot.summary is not None and not self._reported_run:
            self._reported_run = True
            summary = snapshot.summary
            self._append_info(
                f"完了: seat={summary.seat_label} / requests={summary.requests}"
                f" / responses={summary.responses}",
                separator=True,
            )
            if summary.record_identity is not None:
                self._append_info(f"record identity: {summary.record_identity}")
            self._end_session()

    def _toggle_pause(self) -> None:
        controller = self._controller
        if controller is None:
            return
        if controller.following:
            controller.pause()
        else:
            controller.follow_live()
        self._refresh_controls()

    def _step(self) -> None:
        """pause中に、保持済みframeをちょうど1件だけ進める。workerへは送らない。"""
        controller = self._controller
        if controller is None:
            return
        controller.step()
        self._refresh(controller.state())
        self._refresh_controls()

    def _follow_live(self) -> None:
        controller = self._controller
        if controller is None:
            return
        controller.follow_live()
        self._refresh(controller.state())
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        controller = self._controller
        if controller is None:
            return
        following = controller.following
        self._pause_button.configure(
            text="⏸ 表示のみ一時停止" if following else "▶ 表示を再開"
        )
        self._step_button.configure(state="disabled" if following else "normal")
        self._follow_button.configure(state="disabled" if following else "normal")

    def _append_info(self, text: str, *, separator: bool = False) -> None:
        self._info.configure(state="normal")
        if separator and self._info.index("end-1c") != "1.0":
            self._info.insert("end", "\n")
        self._info.insert("end", text.rstrip() + "\n")
        self._info.see("end")
        self._info.configure(state="disabled")

    def _clear_info(self) -> None:
        self._info.configure(state="normal")
        self._info.delete("1.0", "end")
        self._info.configure(state="disabled")

    def _end_session(self) -> None:
        """presentationを終了する。ranked worker自体はjoinで完走を待つ。"""
        self._controller = None
        self._buffer = None
        self._status = None
        self._set_controls_enabled(False)
        self._set_setup_enabled(True)

    def _set_setup_enabled(self, enabled: bool) -> None:
        self._profile_box.configure(state="readonly" if enabled else "disabled")
        self._start_button.configure(state="normal" if enabled else "disabled")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._pause_button.configure(state=state)
        self._step_button.configure(state="disabled")
        self._follow_button.configure(state="disabled")

    def _close(self) -> None:
        """presentationだけをdetachしてwindowを閉じる。

        ranked workerへcancel / disconnectを送らない。workerはnon-daemonで、
        `launch_riichilab_gui()`がmainloop終了後にjoinする。
        """
        controller = self._controller
        if controller is not None:
            controller.detach()
        elif self._buffer is not None:
            self._buffer.detach()
        self._root.destroy()


def launch_riichilab_gui(*, profile_name: str | None, record_dir: str | None) -> None:
    """Tk rootを生成し、RiichiLab live viewerを起動する。

    mainloop終了後、ranked workerが走っていればその完走まで待つ。window
    closeでprocessを終わらせてranked WebSocketを途中切断しないためである。
    """
    tk, ttk, messagebox, scrolledtext, _ = load_tk()
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise GuiUnavailableError(
            "GUI displayを初期化できません。desktop sessionで実行してください。"
        ) from error
    application = _TkRiichiLabApplication(
        root,
        tk=tk,
        ttk=ttk,
        messagebox=messagebox,
        scrolledtext=scrolledtext,
        profile_name=profile_name,
        record_dir=record_dir,
    )
    root.mainloop()
    worker = application.worker
    if worker is not None:
        worker.join()


def main(
    argv: Sequence[str] | None = None,
    *,
    error_writer: Callable[[str], None] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    writer = error_writer if error_writer is not None else print
    try:
        launch_riichilab_gui(profile_name=args.profile, record_dir=args.record_dir)
    except GuiUnavailableError as error:
        writer(f"RiichiLab live viewerを起動できません: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
