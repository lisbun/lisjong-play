"""Arena durable local game recordを既存GUIで再生する牌譜Replay Viewer。

Replay中はlisjong-engineのgame executionもPolicy executionも起動しない。
盤面描画は live Human Playと同じ`GuiBoardRenderer`と同じ牌画像registryを使う。
auto-play timerはTk main threadの`after`だけで駆動し、pauseとwindow closeで
必ずcancelする。navigation stateはTk widgetではなく`ReplayController`が持つ。
"""

import argparse
from collections.abc import Callable, Sequence
from typing import Any

from lisjong_play.gui import GuiUnavailableError, load_tk
from lisjong_play.gui_board import (
    CENTER_PLACE,
    TABLE_PLACE,
    GuiBoardRenderer,
    build_board_tile_images,
    clear_frame,
    configure_board_styles,
)
from lisjong_play.replay_controller import (
    SPEED_CHOICES,
    ReplayControlError,
    ReplayController,
)
from lisjong_play.replay_source import (
    ReplayLoadError,
    ReplayTimeline,
    load_replay_timeline,
)

_NO_RECORD_MESSAGE = "牌譜を開いてください。"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lisjong-play-replay",
        description=("lisjong-arenaのdurable local game recordを既存GUIで再生します。"),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="durable record bundle directory (省略時はGUIから選択します)",
    )
    return parser


class _TkReplayApplication:
    """Tk main threadだけでwidgetを操作する牌譜Viewer。"""

    def __init__(
        self,
        root: Any,
        *,
        tk: Any,
        ttk: Any,
        messagebox: Any,
        scrolledtext: Any,
        filedialog: Any,
        initial_path: str | None = None,
        timeline_loader: Callable[[str], ReplayTimeline] = load_replay_timeline,
    ) -> None:
        self._root = root
        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._scrolledtext = scrolledtext
        self._filedialog = filedialog
        self._load_timeline = timeline_loader
        self._controller: ReplayController | None = None
        self._playback_job: Any = None
        self._tile_images = build_board_tile_images(tk)
        self._board_renderer = GuiBoardRenderer(ttk, self._tile_images)

        root.title("lisjong-play 牌譜Replay Viewer")
        root.geometry("1180x900")
        root.minsize(920, 720)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._configure_style()
        self._build_layout()
        if initial_path is not None:
            self._open_path(initial_path)

    def _configure_style(self) -> None:
        style = self._ttk.Style(self._root)
        configure_board_styles(style)
        style.configure("Primary.TButton", padding=(12, 8))

    def _build_layout(self) -> None:
        main = self._ttk.Frame(self._root, padding=12)
        main.pack(fill="both", expand=True)

        top = self._ttk.Frame(main)
        top.pack(fill="x", pady=(0, 8))
        self._ttk.Button(
            top, text="牌譜を開く", style="Primary.TButton", command=self._choose_record
        ).pack(side="left")
        self._status_var = self._tk.StringVar(value=_NO_RECORD_MESSAGE)
        self._ttk.Label(top, textvariable=self._status_var).pack(side="left", padx=12)

        # Live Human Playと同じく、緑の卓面へcontent-sizedのseatをanchorする。
        self._table = self._ttk.Frame(main, style="Table.TFrame", padding=4)
        self._table.pack(fill="both", expand=True)
        self._seat_frames: dict[str, Any] = {}
        for position, (relx, rely, anchor, x, y) in TABLE_PLACE.items():
            frame = self._ttk.Frame(self._table, style="Seat.TFrame", padding=2)
            frame.place(relx=relx, rely=rely, anchor=anchor, x=x, y=y)
            self._seat_frames[position] = frame
        self._center = self._ttk.Frame(self._table, style="Seat.TFrame", padding=4)
        relx, rely, anchor = CENTER_PLACE
        self._center.place(relx=relx, rely=rely, anchor=anchor)

        self._hand = self._ttk.LabelFrame(
            main, text="このdecision seatの手牌（recordが保持する範囲）", padding=8
        )
        self._hand.pack(fill="x", pady=(8, 4))

        controls = self._ttk.Frame(main)
        controls.pack(fill="x", pady=4)
        self._buttons: dict[str, Any] = {}
        for key, text, command in (
            ("first", "|< 先頭", self._to_first),
            ("previous_round", "<< 前局", self._to_previous_round),
            ("previous", "< 前へ", self._to_previous),
            ("next", "次へ >", self._to_next),
            ("next_round", "次局 >>", self._to_next_round),
        ):
            button = self._ttk.Button(controls, text=text, command=command)
            button.pack(side="left", padx=3)
            self._buttons[key] = button
        self._play_button = self._ttk.Button(
            controls, text="▶ 再生", command=self._toggle_playback
        )
        self._play_button.pack(side="left", padx=(16, 3))
        self._ttk.Label(controls, text="速度").pack(side="left", padx=(16, 4))
        self._speed_var = self._tk.StringVar(value="1.0")
        self._speed_box = self._ttk.Combobox(
            controls,
            textvariable=self._speed_var,
            values=[f"{speed}" for speed in SPEED_CHOICES],
            state="readonly",
            width=6,
        )
        self._speed_box.pack(side="left")
        self._speed_box.bind("<<ComboboxSelected>>", self._apply_speed)

        self._position_var = self._tk.StringVar(value="")
        self._ttk.Label(main, textvariable=self._position_var).pack(fill="x", pady=2)

        info = self._ttk.LabelFrame(
            main, text="局結果 / 半荘結果 / record情報", padding=6
        )
        info.pack(fill="both", pady=(4, 0))
        self._info = self._scrolledtext.ScrolledText(
            info, height=12, wrap="word", state="disabled"
        )
        self._info.pack(fill="both", expand=True)
        self._set_controls_enabled(False)

    def _choose_record(self) -> None:
        path = self._filedialog.askdirectory(
            parent=self._root, title="durable record bundle directoryを選択"
        )
        if not path:
            return
        self._open_path(path)

    def _open_path(self, path: str) -> None:
        """strict loaderがrejectしたrecordはpartial replayとして表示しない。"""
        self._cancel_playback()
        try:
            timeline = self._load_timeline(path)
        except ReplayLoadError as error:
            self._controller = None
            self._set_controls_enabled(False)
            self._status_var.set("牌譜を読み込めませんでした。")
            self._set_info(str(error))
            self._messagebox.showerror("牌譜エラー", str(error), parent=self._root)
            return
        self._controller = ReplayController(timeline)
        self._speed_var.set(f"{self._controller.speed}")
        self._set_controls_enabled(True)
        self._status_var.set(f"seed={timeline.seed} / {timeline.game_mode}")
        self._refresh()

    def _to_first(self) -> None:
        self._navigate(lambda controller: controller.to_first())

    def _to_previous(self) -> None:
        self._navigate(lambda controller: controller.to_previous())

    def _to_next(self) -> None:
        self._navigate(lambda controller: controller.to_next())

    def _to_previous_round(self) -> None:
        self._navigate(lambda controller: controller.to_previous_round())

    def _to_next_round(self) -> None:
        self._navigate(lambda controller: controller.to_next_round())

    def _navigate(self, move: Callable[[ReplayController], bool]) -> None:
        """手動navigationはauto-playを止めてからcursorを動かす。"""
        controller = self._controller
        if controller is None:
            return
        self._cancel_playback()
        controller.pause()
        move(controller)
        self._refresh()

    def _toggle_playback(self) -> None:
        controller = self._controller
        if controller is None:
            return
        if controller.playing:
            self._cancel_playback()
            controller.pause()
        else:
            controller.play()
            self._schedule_playback()
        self._refresh()

    def _schedule_playback(self) -> None:
        controller = self._controller
        if controller is None or not controller.playing:
            return
        self._cancel_playback()
        self._playback_job = self._root.after(
            controller.frame_interval_ms, self._playback_tick
        )

    def _playback_tick(self) -> None:
        self._playback_job = None
        controller = self._controller
        if controller is None:
            return
        controller.advance_for_playback()
        if controller.playing:
            self._schedule_playback()
        self._refresh()

    def _cancel_playback(self) -> None:
        job = self._playback_job
        if job is None:
            return
        self._playback_job = None
        self._root.after_cancel(job)

    def _apply_speed(self, _event: Any = None) -> None:
        controller = self._controller
        if controller is None:
            return
        try:
            controller.set_speed(self._speed_var.get())
        except ReplayControlError:
            self._speed_var.set(f"{controller.speed}")
            return
        if controller.playing:
            self._schedule_playback()

    def _refresh(self) -> None:
        controller = self._controller
        if controller is None:
            return
        position = controller.position()
        self._board_renderer.render_board(
            position.frame.board,
            (),
            seat_frames=self._seat_frames,
            center=self._center,
            hand=self._hand,
        )
        self._position_var.set(
            f"{position.round.label}"
            f"  /  局 {position.round_number}/{position.round_count}"
            f"  /  手順 {position.frame_number}/{position.frame_count}"
            f"  /  step {position.frame.step_ordinal}"
        )
        self._play_button.configure(
            text="⏸ 一時停止" if controller.playing else "▶ 再生"
        )
        self._buttons["first"].configure(
            state="disabled" if position.at_first else "normal"
        )
        self._buttons["previous"].configure(
            state="disabled" if position.at_first else "normal"
        )
        self._buttons["next"].configure(
            state="disabled" if position.at_last else "normal"
        )
        self._set_info(
            "\n\n".join(
                (
                    position.round.result_text,
                    controller.timeline.final_result_text,
                    controller.timeline.metadata_text,
                )
            )
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._buttons.values():
            button.configure(state=state)
        self._play_button.configure(state=state)
        self._speed_box.configure(state="readonly" if enabled else "disabled")
        if not enabled:
            # rejectされたrecordが直前のrecordの盤面を残したまま見えないよう、
            # 表示中の卓もすべて消す。
            self._position_var.set("")
            clear_frame(self._center)
            clear_frame(self._hand)
            for frame in self._seat_frames.values():
                clear_frame(frame)

    def _set_info(self, text: str) -> None:
        self._info.configure(state="normal")
        self._info.delete("1.0", "end")
        self._info.insert("end", text.rstrip() + "\n")
        self._info.see("1.0")
        self._info.configure(state="disabled")

    def _close(self) -> None:
        """closeでauto-play timerを必ず解放する。"""
        self._cancel_playback()
        if self._controller is not None:
            self._controller.pause()
        self._root.destroy()


def launch_replay_gui(*, path: str | None = None) -> None:
    """Tk rootを生成し、牌譜Replay Viewerを起動する。"""
    tk, ttk, messagebox, scrolledtext, filedialog = load_tk()
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise GuiUnavailableError(
            "GUI displayを初期化できません。desktop sessionで実行してください。"
        ) from error
    _TkReplayApplication(
        root,
        tk=tk,
        ttk=ttk,
        messagebox=messagebox,
        scrolledtext=scrolledtext,
        filedialog=filedialog,
        initial_path=path,
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
        launch_replay_gui(path=args.path)
    except GuiUnavailableError as error:
        writer(f"牌譜Viewerを起動できません: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
