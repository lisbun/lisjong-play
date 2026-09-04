"""Tkinter desktop GUI prototype entry point。

TkinterはGUI起動時だけimportし、CLI runtimeをoptional GUI dependencyから分離する。
"""

import argparse
import threading
from collections.abc import Callable, Sequence
from typing import Any, cast

from lisjong_play.gui_bridge import (
    DecisionRequested,
    GuiBridgeError,
    GuiEvent,
    GuiSessionBridge,
    MatchCompleted,
    ProgressDelivered,
    RoundCompleted,
    SessionFailed,
    SessionFinished,
    run_gui_worker,
)
from lisjong_play.gui_model import GuiActionView, GuiBoardView, GuiSeatView
from lisjong_play.renderer import RIVER_LEGEND
from lisjong_play.session import (
    DEFAULT_OPPONENT,
    DEFAULT_SEED,
    OPPONENT_CHOICES,
    OpponentName,
)


class GuiUnavailableError(RuntimeError):
    """Tkinterまたはdesktop displayを利用できない場合。"""


_TILE_CONTROL_WIDTH = 4
_ACTION_CONTROL_WIDTH = 16
_ACTION_ROW_CAPACITY = 14
_WIDE_ACTION_UNITS = 4


def _only_pass_option_index(actions: Sequence[GuiActionView]) -> int | None:
    """選択の余地がないpass requestだけをGUI操作なしで確定する。"""
    if len(actions) == 1 and actions[0].style == "pass":
        return actions[0].option_index
    return None


def _action_units(action: GuiActionView) -> int:
    return 1 if action.style in {"discard", "tsumogiri"} else _WIDE_ACTION_UNITS


def _partition_action_rows(
    actions: Sequence[GuiActionView],
) -> tuple[tuple[GuiActionView, ...], ...]:
    """platform差があっても操作が横にはみ出さないbounded rowへ分割する。"""
    rows: list[tuple[GuiActionView, ...]] = []
    current: list[GuiActionView] = []
    used_units = 0
    for action in actions:
        units = _action_units(action)
        if current and used_units + units > _ACTION_ROW_CAPACITY:
            rows.append(tuple(current))
            current = []
            used_units = 0
        current.append(action)
        used_units += units
    if current:
        rows.append(tuple(current))
    return tuple(rows)


def _action_button_attributes(action: GuiActionView) -> tuple[str, str, int]:
    if action.style in {"discard", "tsumogiri"}:
        assert action.tile_label is not None
        suffix = "*" if action.style == "tsumogiri" else ""
        style = "RedTile.TButton" if action.tile_label.endswith("r") else "Tile.TButton"
        return f"{action.tile_label}{suffix}", style, _TILE_CONTROL_WIDTH
    return (
        action.label.replace(" / ", "\n"),
        "Primary.TButton",
        _ACTION_CONTROL_WIDTH,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lisjong-play-gui",
        description="Human EAST vs selected Policy x3 のGUI prototypeを起動します。",
    )
    parser.add_argument(
        "--opponent",
        choices=OPPONENT_CHOICES,
        default=DEFAULT_OPPONENT,
        help=f"initial AI opponent Policy (default: {DEFAULT_OPPONENT})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"initial deterministic match seed (default: {DEFAULT_SEED})",
    )
    return parser


def _load_tk() -> tuple[Any, Any, Any, Any]:
    try:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext, ttk
    except ImportError as error:
        raise GuiUnavailableError(
            "Tkinterを読み込めません。Tk対応のPython 3.14を使用してください。"
        ) from error
    return tk, ttk, messagebox, scrolledtext


class _TkGuiApplication:
    """Tk main threadだけでwidgetを操作するprototype application。"""

    _POLL_INTERVAL_MS = 40
    _POSITION_GRID = {
        "top": (0, 1),
        "left": (1, 0),
        "right": (1, 2),
        "bottom": (2, 1),
    }

    def __init__(
        self,
        root: Any,
        *,
        tk: Any,
        ttk: Any,
        messagebox: Any,
        scrolledtext: Any,
        seed: int,
        opponent: OpponentName,
    ) -> None:
        self._root = root
        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._scrolledtext = scrolledtext
        self._bridge: GuiSessionBridge | None = None
        self._worker: threading.Thread | None = None
        self._active_decision_id: int | None = None

        root.title("lisjong-play GUI prototype")
        root.geometry("1180x860")
        root.minsize(920, 700)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._configure_style()
        self._build_layout(seed=seed, opponent=opponent)
        root.after(self._POLL_INTERVAL_MS, self._poll_events)

    def _configure_style(self) -> None:
        style = self._ttk.Style(self._root)
        style.configure("Table.TFrame", background="#176b4d")
        style.configure(
            "Seat.TLabelframe",
            background="#f7f3e8",
            borderwidth=2,
            relief="solid",
        )
        style.configure("Seat.TLabelframe.Label", font=("TkDefaultFont", 11, "bold"))
        style.configure("Center.TLabel", font=("TkDefaultFont", 12, "bold"))
        style.configure(
            "Tile.TLabel",
            padding=(7, 9),
            relief="raised",
            anchor="center",
            font=("TkDefaultFont", 11, "bold"),
        )
        style.configure(
            "RedTile.TLabel",
            padding=(7, 9),
            relief="raised",
            anchor="center",
            font=("TkDefaultFont", 11, "bold"),
            foreground="#b42318",
        )
        style.configure(
            "Tile.TButton", padding=(7, 9), font=("TkDefaultFont", 11, "bold")
        )
        style.configure(
            "RedTile.TButton",
            padding=(7, 9),
            font=("TkDefaultFont", 11, "bold"),
            foreground="#b42318",
        )
        style.configure("Primary.TButton", padding=(12, 8))

    def _build_layout(self, *, seed: int, opponent: OpponentName) -> None:
        main = self._ttk.Frame(self._root, padding=12)
        main.pack(fill="both", expand=True)

        setup = self._ttk.Frame(main)
        setup.pack(fill="x", pady=(0, 8))
        self._ttk.Label(setup, text="Seed").pack(side="left")
        self._seed_var = self._tk.StringVar(value=str(seed))
        self._seed_entry = self._ttk.Entry(setup, textvariable=self._seed_var, width=12)
        self._seed_entry.pack(side="left", padx=(6, 16))
        self._ttk.Label(setup, text="Opponent").pack(side="left")
        self._opponent_var = self._tk.StringVar(value=opponent)
        self._opponent_box = self._ttk.Combobox(
            setup,
            textvariable=self._opponent_var,
            values=OPPONENT_CHOICES,
            state="readonly",
            width=18,
        )
        self._opponent_box.pack(side="left", padx=(6, 16))
        self._start_button = self._ttk.Button(
            setup,
            text="対局開始",
            style="Primary.TButton",
            command=self._start_session,
        )
        self._start_button.pack(side="left")
        self._status_var = self._tk.StringVar(
            value="設定を確認して対局を開始してください。"
        )
        self._ttk.Label(setup, textvariable=self._status_var).pack(
            side="right", padx=(12, 0)
        )

        self._table = self._ttk.Frame(main, style="Table.TFrame", padding=12)
        self._table.pack(fill="both", expand=True)
        for index in range(3):
            self._table.columnconfigure(index, weight=1)
            self._table.rowconfigure(index, weight=1)

        self._seat_frames: dict[str, Any] = {}
        for position, (row, column) in self._POSITION_GRID.items():
            frame = self._ttk.LabelFrame(
                self._table,
                text=position,
                style="Seat.TLabelframe",
                padding=8,
            )
            frame.grid(row=row, column=column, padx=8, pady=8, sticky="nsew")
            self._seat_frames[position] = frame
        self._center = self._ttk.Frame(self._table, padding=12)
        self._center.grid(row=1, column=1, padx=8, pady=8, sticky="nsew")
        self._ttk.Label(
            self._center,
            text="卓情報はHuman decision時に更新されます",
            style="Center.TLabel",
            anchor="center",
            justify="center",
        ).pack(fill="both", expand=True)

        self._ttk.Label(main, text=RIVER_LEGEND).pack(fill="x", pady=(4, 0))

        self._hand = self._ttk.LabelFrame(main, text="あなたの手牌", padding=8)
        self._hand.pack(fill="x", pady=(8, 4))
        self._ttk.Label(self._hand, text="対局開始後に表示されます").pack()

        self._actions = self._ttk.LabelFrame(main, text="操作", padding=8)
        self._actions.pack(fill="x", pady=4)
        self._ttk.Label(self._actions, text="操作待ちではありません").pack()

        log_frame = self._ttk.LabelFrame(main, text="進行・局結果", padding=6)
        log_frame.pack(fill="both", pady=(4, 0))
        self._log = self._scrolledtext.ScrolledText(
            log_frame,
            height=10,
            wrap="word",
            state="disabled",
        )
        self._log.pack(fill="both", expand=True)

    def _start_session(self) -> None:
        if self._bridge is not None:
            return
        try:
            seed = int(self._seed_var.get().strip())
        except ValueError:
            self._messagebox.showerror("入力エラー", "Seedは整数で入力してください。")
            return
        opponent_value = self._opponent_var.get()
        if opponent_value not in OPPONENT_CHOICES:
            self._messagebox.showerror("入力エラー", "Opponentを選択してください。")
            return
        opponent = cast(OpponentName, opponent_value)

        self._set_setup_enabled(False)
        self._clear_frame(self._actions)
        self._ttk.Label(self._actions, text="engineを開始しています…").pack()
        self._clear_log()
        self._append_log(f"対局開始: Human EAST vs {opponent} x3 / seed={seed}")
        self._status_var.set("engine実行中")
        self._bridge = GuiSessionBridge()
        self._worker = threading.Thread(
            target=run_gui_worker,
            kwargs={"bridge": self._bridge, "seed": seed, "opponent": opponent},
            name="lisjong-play-engine",
            daemon=True,
        )
        self._worker.start()

    def _poll_events(self) -> None:
        bridge = self._bridge
        if bridge is not None:
            for event in bridge.drain_events():
                self._handle_event(event)
        self._root.after(self._POLL_INTERVAL_MS, self._poll_events)

    def _handle_event(self, event: GuiEvent) -> None:
        if isinstance(event, DecisionRequested):
            automatic_index = _only_pass_option_index(event.actions)
            if automatic_index is not None:
                self._active_decision_id = event.request_id
                self._choose_action(automatic_index)
                return
            self._render_board(event.board)
            self._render_actions(event)
            self._status_var.set(f"あなたの操作: {event.board.decision_label}")
            return
        if isinstance(event, ProgressDelivered):
            self._append_log(event.text)
            return
        if isinstance(event, RoundCompleted):
            self._append_log(event.text, separator=True)
            self._render_round_confirmation(event.confirmation_id)
            return
        if isinstance(event, MatchCompleted):
            self._append_log(event.text, separator=True)
            return
        if isinstance(event, SessionFinished):
            self._status_var.set("半荘が終了しました。")
            self._end_session()
            return
        if isinstance(event, SessionFailed):
            self._append_log(f"ERROR: {event.message}", separator=True)
            self._messagebox.showerror("対局エラー", event.message)
            self._status_var.set("対局はエラーで終了しました。")
            self._end_session()
            return
        raise AssertionError(f"unknown GUI event: {type(event).__name__}")

    def _render_board(self, board: GuiBoardView) -> None:
        by_position = {seat.position: seat for seat in board.seats}
        for position, frame in self._seat_frames.items():
            self._render_seat(frame, by_position[position])

        self._clear_frame(self._center)
        self._ttk.Label(
            self._center, text=board.round_label, style="Center.TLabel"
        ).pack(pady=(8, 4))
        self._ttk.Label(self._center, text=board.center_detail).pack(pady=4)
        dora = "  ".join(board.dora_indicators) or "なし"
        self._ttk.Label(
            self._center, text=f"ドラ表示牌\n{dora}", justify="center"
        ).pack(pady=4)
        self._ttk.Label(
            self._center,
            text=f"判断\n{board.decision_label}",
            justify="center",
        ).pack(pady=4)

        self._clear_frame(self._hand)
        tiles = self._ttk.Frame(self._hand)
        tiles.pack(anchor="center")
        for value in board.hand_tiles:
            self._tile_label(tiles, value).pack(side="left", padx=2)
        if board.drawn_tile is not None:
            self._ttk.Separator(tiles, orient="vertical").pack(
                side="left", fill="y", padx=8
            )
            self._tile_label(tiles, board.drawn_tile).pack(side="left", padx=2)

    def _render_seat(self, frame: Any, seat: GuiSeatView) -> None:
        self._clear_frame(frame)
        frame.configure(text=seat.label)
        status = f"{seat.score}点"
        if seat.riichi:
            status += f"  /  {seat.riichi}"
        self._ttk.Label(frame, text=status).pack(anchor="w")
        melds = " | ".join(seat.melds) or "なし"
        self._ttk.Label(frame, text=f"副露: {melds}", wraplength=260).pack(
            anchor="w", pady=(4, 2)
        )
        river_rows = [
            "  ".join(seat.river[index : index + 6])
            for index in range(0, len(seat.river), 6)
        ]
        river = "\n".join(river_rows) or "-"
        self._ttk.Label(frame, text=f"河:\n{river}", justify="left").pack(
            anchor="w", pady=(2, 0)
        )

    def _tile_label(self, parent: Any, value: str) -> Any:
        return self._ttk.Label(
            parent,
            text=value,
            width=_TILE_CONTROL_WIDTH,
            anchor="center",
            style="RedTile.TLabel" if value.endswith("r") else "Tile.TLabel",
        )

    def _render_actions(self, event: DecisionRequested) -> None:
        self._active_decision_id = event.request_id
        self._clear_frame(self._actions)
        for actions in _partition_action_rows(event.actions):
            row = self._ttk.Frame(self._actions)
            row.pack(anchor="center")
            for action in actions:
                label, style, width = _action_button_attributes(action)
                button = self._ttk.Button(
                    row,
                    text=label,
                    width=width,
                    style=style,
                    command=lambda index=action.option_index: self._choose_action(
                        index
                    ),
                )
                button.pack(side="left", padx=3, pady=2)

    def _choose_action(self, option_index: int) -> None:
        bridge = self._bridge
        request_id = self._active_decision_id
        if bridge is None or request_id is None:
            return
        try:
            bridge.choose_action(request_id, option_index)
        except GuiBridgeError as error:
            self._messagebox.showerror("操作エラー", str(error))
            return
        self._active_decision_id = None
        self._clear_frame(self._actions)
        self._ttk.Label(self._actions, text="AI / engineの進行を待っています…").pack()
        self._status_var.set("engine実行中")

    def _render_round_confirmation(self, confirmation_id: int | None) -> None:
        self._active_decision_id = None
        self._clear_frame(self._actions)
        if confirmation_id is None:
            self._ttk.Label(self._actions, text="半荘結果を集計しています…").pack()
            return
        self._ttk.Button(
            self._actions,
            text="次局へ",
            style="Primary.TButton",
            command=lambda: self._confirm_round(confirmation_id),
        ).pack()
        self._status_var.set("局結果を確認してください。")

    def _confirm_round(self, confirmation_id: int) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        try:
            bridge.confirm_round(confirmation_id)
        except GuiBridgeError as error:
            self._messagebox.showerror("操作エラー", str(error))
            return
        self._clear_frame(self._actions)
        self._ttk.Label(self._actions, text="次局を開始しています…").pack()
        self._status_var.set("engine実行中")

    def _append_log(self, text: str, *, separator: bool = False) -> None:
        self._log.configure(state="normal")
        if separator and self._log.index("end-1c") != "1.0":
            self._log.insert("end", "\n")
        self._log.insert("end", text.rstrip() + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _end_session(self) -> None:
        if self._bridge is not None:
            self._bridge.close()
        self._bridge = None
        self._worker = None
        self._active_decision_id = None
        self._set_setup_enabled(True)
        self._clear_frame(self._actions)
        self._ttk.Label(self._actions, text="新しい半荘を開始できます").pack()

    def _set_setup_enabled(self, enabled: bool) -> None:
        self._seed_entry.configure(state="normal" if enabled else "disabled")
        self._opponent_box.configure(state="readonly" if enabled else "disabled")
        self._start_button.configure(state="normal" if enabled else "disabled")

    @staticmethod
    def _clear_frame(frame: Any) -> None:
        for child in frame.winfo_children():
            child.destroy()

    def _close(self) -> None:
        if self._bridge is not None:
            self._bridge.close()
        self._root.destroy()


def launch_gui(*, seed: int, opponent: OpponentName) -> None:
    """Tk rootを生成し、desktop GUI prototypeを起動する。"""
    tk, ttk, messagebox, scrolledtext = _load_tk()
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise GuiUnavailableError(
            "GUI displayを初期化できません。desktop sessionで実行してください。"
        ) from error
    _TkGuiApplication(
        root,
        tk=tk,
        ttk=ttk,
        messagebox=messagebox,
        scrolledtext=scrolledtext,
        seed=seed,
        opponent=opponent,
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
        launch_gui(seed=args.seed, opponent=args.opponent)
    except GuiUnavailableError as error:
        writer(f"GUIを起動できません: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
