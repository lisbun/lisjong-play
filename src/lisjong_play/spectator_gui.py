"""AI x4のlive対局を観戦するSpectator GUI entry point。

Human Play GUIとは別のentry pointで、Human action selection UIを持たない。
盤面は`GuiBoardRenderer`をそのまま共有し、spectator専用の河 / 副露 / 立直 /
点数 / 局情報 / ドラ描画を二重実装しない。

Tk widgetはmain threadだけが触り、engine / Policyはworker threadで動く。
Pause / Step / Speedはpresentationのpacing gateだけを操作し、engine stateも
Policy inputも変更しない。window closeはgateで待機中のworkerを必ず解放する。
"""

import argparse
import threading
from collections.abc import Callable, Sequence
from typing import Any, cast

from lisjong_play.gui import GuiUnavailableError, load_tk
from lisjong_play.gui_board import (
    CENTER_PLACE,
    GUI_RIVER_LEGEND,
    TABLE_PLACE,
    GuiBoardRenderer,
    build_board_tile_images,
    configure_board_styles,
)
from lisjong_play.session import (
    DEFAULT_OPPONENT,
    DEFAULT_SEED,
    OPPONENT_CHOICES,
    OpponentName,
)
from lisjong_play.spectator_source import (
    SpectatorControlError,
    SpectatorDecisionPresented,
    SpectatorEvent,
    SpectatorFailed,
    SpectatorFinished,
    SpectatorMatchResult,
    SpectatorProgress,
    SpectatorRoundResult,
    SpectatorSessionBridge,
    run_spectator_worker,
    spectator_speed_labels,
)

_IDLE_MESSAGE = "Policyとseedを選んで観戦を開始してください。"
_HAND_FRAME_TEXT = "現在の決定席の手牌（その席自身のplayer-safe手牌のみ）"
_INFO_VISIBLE_LINES = 8
# 4席のconcealed handはcurrent public engine contractでは安全に取得できないため、
# spectatorはpublic boardと現在の決定席の手牌だけを表示する。
CONCEALED_HAND_SCOPE_NOTE = (
    "表示範囲: public board + 現在の決定席の手牌のみ"
    "（4席同時のconcealed handは表示しません）"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lisjong-play-spectator",
        description="AI x4のlive対局を観戦するSpectator GUIを起動します。",
    )
    parser.add_argument(
        "--policy",
        choices=OPPONENT_CHOICES,
        default=DEFAULT_OPPONENT,
        help=f"initial Policy for all four seats (default: {DEFAULT_OPPONENT})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"initial deterministic match seed (default: {DEFAULT_SEED})",
    )
    return parser


class _TkSpectatorApplication:
    """Tk main threadだけでwidgetを操作するSpectator application。"""

    _POLL_INTERVAL_MS = 40

    def __init__(
        self,
        root: Any,
        *,
        tk: Any,
        ttk: Any,
        messagebox: Any,
        scrolledtext: Any,
        seed: int,
        policy: OpponentName,
    ) -> None:
        self._root = root
        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._scrolledtext = scrolledtext
        self._bridge: SpectatorSessionBridge | None = None
        self._worker: threading.Thread | None = None
        self._tile_images = build_board_tile_images(tk)
        # on_select_actionを渡さないため、牌はbuttonにならない。spectatorに
        # human action controlを与えないことをrenderer側の契約で保証する。
        self._board_renderer = GuiBoardRenderer(ttk, self._tile_images)

        root.title("lisjong-play Spectator (AI x4)")
        root.geometry("1180x900")
        root.minsize(920, 720)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._configure_style()
        self._build_layout(seed=seed, policy=policy)
        root.after(self._POLL_INTERVAL_MS, self._poll_events)

    def _configure_style(self) -> None:
        style = self._ttk.Style(self._root)
        configure_board_styles(style)
        style.configure("Primary.TButton", padding=(12, 8))

    def _build_layout(self, *, seed: int, policy: OpponentName) -> None:
        main = self._ttk.Frame(self._root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        setup = self._ttk.Frame(main)
        setup.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._ttk.Label(setup, text="Seed").pack(side="left")
        self._seed_var = self._tk.StringVar(value=str(seed))
        self._seed_entry = self._ttk.Entry(setup, textvariable=self._seed_var, width=12)
        self._seed_entry.pack(side="left", padx=(6, 16))
        self._ttk.Label(setup, text="Policy (4席共通)").pack(side="left")
        self._policy_var = self._tk.StringVar(value=policy)
        self._policy_box = self._ttk.Combobox(
            setup,
            textvariable=self._policy_var,
            values=OPPONENT_CHOICES,
            state="readonly",
            width=18,
        )
        self._policy_box.pack(side="left", padx=(6, 16))
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
        self._ttk.Label(self._hand, text=CONCEALED_HAND_SCOPE_NOTE).pack()

        controls = self._ttk.Frame(main)
        controls.grid(row=3, column=0, sticky="ew", pady=2)
        self._pause_button = self._ttk.Button(
            controls, text="⏸ 一時停止", command=self._toggle_pause
        )
        self._pause_button.pack(side="left", padx=3)
        self._step_button = self._ttk.Button(
            controls, text="⏭ ステップ", command=self._step
        )
        self._step_button.pack(side="left", padx=3)
        self._ttk.Label(controls, text="速度").pack(side="left", padx=(16, 4))
        self._speed_var = self._tk.StringVar(value="1.0")
        self._speed_box = self._ttk.Combobox(
            controls,
            textvariable=self._speed_var,
            values=list(spectator_speed_labels()),
            state="readonly",
            width=6,
        )
        self._speed_box.pack(side="left")
        self._speed_box.bind("<<ComboboxSelected>>", self._apply_speed)
        self._boundary_var = self._tk.StringVar(value="")
        self._ttk.Label(controls, textvariable=self._boundary_var).pack(
            side="left", padx=(16, 0)
        )

        info = self._ttk.LabelFrame(main, text="進行 / 局結果 / 半荘結果", padding=4)
        info.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        info.columnconfigure(0, weight=1)
        self._info = self._scrolledtext.ScrolledText(
            info, height=_INFO_VISIBLE_LINES, wrap="word", state="disabled"
        )
        self._info.grid(row=0, column=0, sticky="ew")
        self._set_controls_enabled(False)

    def _start_session(self) -> None:
        if self._bridge is not None:
            return
        try:
            seed = int(self._seed_var.get().strip())
        except ValueError:
            self._messagebox.showerror("入力エラー", "Seedは整数で入力してください。")
            return
        policy_value = self._policy_var.get()
        if policy_value not in OPPONENT_CHOICES:
            self._messagebox.showerror("入力エラー", "Policyを選択してください。")
            return
        policy = cast(OpponentName, policy_value)

        self._set_setup_enabled(False)
        self._clear_info()
        self._append_info(f"観戦開始: {policy} x4 / seed={seed}")
        self._append_info(CONCEALED_HAND_SCOPE_NOTE)
        self._status_var.set("engine実行中")
        self._bridge = SpectatorSessionBridge()
        self._speed_var.set(f"{self._bridge.control.speed}")
        self._set_controls_enabled(True)
        self._refresh_controls()
        self._worker = threading.Thread(
            target=run_spectator_worker,
            kwargs={"bridge": self._bridge, "seed": seed, "policy": policy},
            name="lisjong-play-spectator",
            daemon=True,
        )
        self._worker.start()

    def _poll_events(self) -> None:
        bridge = self._bridge
        if bridge is not None:
            self._handle_events(bridge.drain_events())
        self._root.after(self._POLL_INTERVAL_MS, self._poll_events)

    def _handle_events(self, events: Sequence[SpectatorEvent]) -> None:
        """1回のdrainでは、最新のdecision boardだけを描画する。

        高speed側ではboundary間隔が1回の盤面描画より短くなり得るため、
        中間boardまで描くと表示がworkerから際限なく遅れる。盤面はdeltaでは
        なくcumulative stateなので、最新のboardだけを描いても表示内容は
        正しい。進行 / 局結果 / 半荘結果のtextは1件も捨てず順序どおり残す。

        pause中は1 drainに新しいboardが高々1件しか入らないため、
        step semanticsはこのcoalesceで変わらない。
        """
        latest_board = -1
        for index, event in enumerate(events):
            if isinstance(event, SpectatorDecisionPresented):
                latest_board = index
        for index, event in enumerate(events):
            if isinstance(event, SpectatorDecisionPresented) and index != latest_board:
                continue
            self._handle_event(event)

    def _handle_event(self, event: SpectatorEvent) -> None:
        if isinstance(event, SpectatorDecisionPresented):
            self._board_renderer.render_board(
                event.board,
                (),
                seat_frames=self._seat_frames,
                center=self._center,
                hand=self._hand,
            )
            self._boundary_var.set(
                f"boundary {event.boundary_ordinal} / 決定席 {event.seat_label}"
            )
            self._status_var.set(f"進行中: {event.board.decision_label}")
            return
        if isinstance(event, SpectatorProgress):
            self._append_info(event.text)
            return
        if isinstance(event, SpectatorRoundResult):
            self._append_info(event.text, separator=True)
            self._status_var.set("局結果を表示しました。")
            return
        if isinstance(event, SpectatorMatchResult):
            self._append_info(event.text, separator=True)
            self._status_var.set("半荘結果を表示しました。")
            return
        if isinstance(event, SpectatorFinished):
            self._status_var.set("半荘が終了しました。")
            self._end_session()
            return
        if isinstance(event, SpectatorFailed):
            self._append_info(f"ERROR: {event.message}", separator=True)
            self._messagebox.showerror("観戦エラー", event.message)
            self._status_var.set("観戦はエラーで終了しました。")
            self._end_session()
            return
        raise AssertionError(f"unknown spectator event: {type(event).__name__}")

    def _toggle_pause(self) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        if bridge.control.paused:
            bridge.control.resume()
        else:
            bridge.control.pause()
        self._refresh_controls()

    def _step(self) -> None:
        """pause中に、次の1 selector decision boundaryだけを許可する。"""
        bridge = self._bridge
        if bridge is None:
            return
        try:
            bridge.control.request_step()
        except SpectatorControlError as error:
            self._messagebox.showerror("操作エラー", str(error))
        self._refresh_controls()

    def _apply_speed(self, _event: Any = None) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        try:
            bridge.control.set_speed(self._speed_var.get())
        except SpectatorControlError:
            self._speed_var.set(f"{bridge.control.speed}")

    def _refresh_controls(self) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        paused = bridge.control.paused
        self._pause_button.configure(text="▶ 再開" if paused else "⏸ 一時停止")
        self._step_button.configure(state="normal" if paused else "disabled")

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
        if self._bridge is not None:
            self._bridge.close()
        self._bridge = None
        self._worker = None
        self._set_controls_enabled(False)
        self._set_setup_enabled(True)
        self._boundary_var.set("")

    def _set_setup_enabled(self, enabled: bool) -> None:
        self._seed_entry.configure(state="normal" if enabled else "disabled")
        self._policy_box.configure(state="readonly" if enabled else "disabled")
        self._start_button.configure(state="normal" if enabled else "disabled")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._pause_button.configure(state=state)
        self._step_button.configure(state="disabled")
        self._speed_box.configure(state="readonly" if enabled else "disabled")

    def _close(self) -> None:
        """closeでgate待機中のworkerを必ず解放してからwindowを閉じる。"""
        if self._bridge is not None:
            self._bridge.close()
        self._root.destroy()


def launch_spectator_gui(*, seed: int, policy: OpponentName) -> None:
    """Tk rootを生成し、Spectator GUIを起動する。"""
    tk, ttk, messagebox, scrolledtext, _ = load_tk()
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise GuiUnavailableError(
            "GUI displayを初期化できません。desktop sessionで実行してください。"
        ) from error
    _TkSpectatorApplication(
        root,
        tk=tk,
        ttk=ttk,
        messagebox=messagebox,
        scrolledtext=scrolledtext,
        seed=seed,
        policy=policy,
    )
    root.mainloop()


def main(
    argv: Sequence[str] | None = None,
    *,
    error_writer: Callable[[str], None] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    writer = error_writer if error_writer is not None else print
    try:
        launch_spectator_gui(seed=args.seed, policy=args.policy)
    except GuiUnavailableError as error:
        writer(f"Spectator GUIを起動できません: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
