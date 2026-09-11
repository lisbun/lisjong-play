"""live Human PlayとpersistedReplayが共有するTk board / tile renderer。

`GuiBoardView`だけを入力とし、source(live engine / persisted record)を知らない。
牌画像は`lisjong_play.tile_images`のcanonical tile label解決をそのまま使う。
"""

from collections.abc import Callable, Sequence
from typing import Any

from lisjong_play.gui_model import (
    GuiActionView,
    GuiBoardView,
    GuiMeldView,
    GuiRiverTile,
    GuiSeatView,
)

RIVER_ROW_SIZE = 6
# 600x800 assets -> about 33x44 for hand / meld / dora, 22x29 for river.
# Manual Windows validation showed the previous 50x66 / 33x44 pair still consumed
# too much vertical space and could push the hand / controls below the viewport.
TILE_IMAGE_SUBSAMPLE = 18
RIVER_TILE_IMAGE_SUBSAMPLE = 27

POSITION_GRID = {
    "top": (0, 1),
    "left": (1, 0),
    "right": (1, 2),
    "bottom": (2, 1),
}


def load_tile_image(tk: Any, path: str) -> Any:
    """vendored牌画像を原寸から手牌 / 副露 / ドラ表示牌向けの縮小sizeへ変換する。"""
    return tk.PhotoImage(file=path).subsample(TILE_IMAGE_SUBSAMPLE)


def load_river_tile_image(tk: Any, path: str) -> Any:
    """vendored牌画像を河専用のより小さい表示sizeへ変換する。

    最大4row x 6枚の河をseat frame内へ収めるため、手牌より小さいscaleを使う。
    """
    return tk.PhotoImage(file=path).subsample(RIVER_TILE_IMAGE_SUBSAMPLE)


def river_caption(cell: GuiRiverTile) -> str:
    """河牌画像へ添える、tsumogiri / 立直宣言 / 鳴かれた牌のtext marker。"""
    caption = "*" if cell.is_tsumogiri else ""
    if cell.is_riichi_declaration:
        caption = f"[{caption}]"
    if cell.called_by is not None:
        caption += f"→{cell.called_by}"
    return caption


def hand_discard_actions(
    actions: Sequence[GuiActionView],
) -> dict[str, GuiActionView]:
    """表示中の concealed-hand tile label → 対応する打牌 GuiActionView。

    同一tile_labelの打牌optionはengine projectionで常に1件へcollapseされる
    ため、同じ表示牌が複数あってもsame option_indexへ安全に対応付く。
    """
    return {
        action.tile_label: action
        for action in actions
        if action.style == "discard" and action.tile_label is not None
    }


def drawn_tile_tsumogiri_action(
    actions: Sequence[GuiActionView], drawn_tile: str | None
) -> GuiActionView | None:
    """表示中の drawn tileへ一致するツモ切り GuiActionViewだけを返す。"""
    if drawn_tile is None:
        return None
    for action in actions:
        if action.style == "tsumogiri" and action.tile_label == drawn_tile:
            return action
    return None


def clear_frame(frame: Any) -> None:
    for child in frame.winfo_children():
        child.destroy()


class GuiBoardRenderer:
    """`GuiBoardView`をTk widgetへ描画する共有renderer。

    `on_select_action`はlive Human decisionだけが渡す。Replayのように選択が
    存在しない表示では`None`のままにし、legal打牌buttonを生成しない。

    牌画像は表示sizeごとに別registryを受け取る。`tile_images`は手牌 / 副露 /
    ドラ表示牌、`river_tile_images`は河専用で、同じtile labelでもcache
    objectはsizeごとに分離される。
    """

    def __init__(
        self,
        ttk: Any,
        tile_images: Any,
        river_tile_images: Any,
        *,
        on_select_action: Callable[[int], None] | None = None,
    ) -> None:
        if on_select_action is not None and not callable(on_select_action):
            raise TypeError("on_select_action must be callable or None")
        self._ttk = ttk
        self._tile_images = tile_images
        self._river_tile_images = river_tile_images
        self._on_select_action = on_select_action

    def render_board(
        self,
        board: GuiBoardView,
        actions: Sequence[GuiActionView],
        *,
        seat_frames: dict[str, Any],
        center: Any,
        hand: Any,
    ) -> None:
        by_position = {seat.position: seat for seat in board.seats}
        for position, frame in seat_frames.items():
            self.render_seat(frame, by_position[position])

        clear_frame(center)
        self._ttk.Label(center, text=board.round_label, style="Center.TLabel").pack(
            pady=(2, 1)
        )
        self._ttk.Label(center, text=board.center_detail).pack(pady=1)
        self._ttk.Label(center, text="ドラ表示牌").pack(pady=(1, 0))
        dora_row = self._ttk.Frame(center)
        dora_row.pack(pady=(0, 1))
        if board.dora_indicators:
            for tile_label in board.dora_indicators:
                self.tile_image_label(dora_row, tile_label).pack(side="left")
        else:
            self._ttk.Label(dora_row, text="なし").pack()
        self._ttk.Label(
            center,
            text=f"判断\n{board.decision_label}",
            justify="center",
        ).pack(pady=1)

        clear_frame(hand)
        tiles = self._ttk.Frame(hand)
        tiles.pack(anchor="center")
        discard_actions = hand_discard_actions(actions)
        for value in board.hand_tiles:
            self.tile_control(tiles, value, discard_actions.get(value)).pack(
                side="left", padx=1
            )
        if board.drawn_tile is not None:
            self._ttk.Separator(tiles, orient="vertical").pack(
                side="left", fill="y", padx=4
            )
            tsumogiri_action = drawn_tile_tsumogiri_action(actions, board.drawn_tile)
            self.tile_control(tiles, board.drawn_tile, tsumogiri_action).pack(
                side="left", padx=1
            )

    def render_seat(self, frame: Any, seat: GuiSeatView) -> None:
        clear_frame(frame)
        frame.configure(text=seat.label)
        status = f"{seat.score}点"
        if seat.riichi:
            status += f"  /  {seat.riichi}"
        self._ttk.Label(frame, text=status).pack(anchor="w")

        self._ttk.Label(frame, text="副露:").pack(anchor="w", pady=(1, 0))
        melds_row = self._ttk.Frame(frame)
        melds_row.pack(anchor="w", pady=(0, 1))
        if not seat.melds:
            self._ttk.Label(melds_row, text="なし").pack(side="left")
        else:
            for meld in seat.melds:
                self.render_meld(melds_row, meld)

        self._ttk.Label(frame, text="河:").pack(anchor="w")
        river_box = self._ttk.Frame(frame)
        river_box.pack(anchor="w")
        if not seat.river:
            self._ttk.Label(river_box, text="-").pack(anchor="w")
        else:
            for start in range(0, len(seat.river), RIVER_ROW_SIZE):
                row = self._ttk.Frame(river_box)
                row.pack(anchor="w")
                for cell in seat.river[start : start + RIVER_ROW_SIZE]:
                    self.render_river_tile(row, cell).pack(side="left")

    def render_meld(self, parent: Any, meld: GuiMeldView) -> None:
        box = self._ttk.Frame(parent, padding=(0, 0, 3, 0))
        box.pack(side="left")
        self._ttk.Label(box, text=meld.type_label, font=("TkDefaultFont", 8)).pack()
        tiles_row = self._ttk.Frame(box)
        tiles_row.pack()
        for tile_label in meld.tiles:
            self.tile_image_label(tiles_row, tile_label).pack(side="left")
        if meld.from_seat is not None:
            self._ttk.Label(
                box, text=f"from {meld.from_seat}", font=("TkDefaultFont", 8)
            ).pack()
        if meld.called_tile is not None:
            self._ttk.Label(
                box, text=f"called {meld.called_tile}", font=("TkDefaultFont", 8)
            ).pack()

    def render_river_tile(self, parent: Any, cell: GuiRiverTile) -> Any:
        box = self._ttk.Frame(parent)
        self.river_tile_image_label(box, cell.tile).pack()
        caption = river_caption(cell)
        if caption:
            self._ttk.Label(box, text=caption, font=("TkDefaultFont", 7)).pack()
        return box

    def tile_image(self, tile_label: str) -> Any:
        return self._tile_images.get(tile_label)

    def tile_image_label(self, parent: Any, tile_label: str) -> Any:
        return self._ttk.Label(
            parent, image=self.tile_image(tile_label), style="TileImage.TLabel"
        )

    def river_tile_image(self, tile_label: str) -> Any:
        return self._river_tile_images.get(tile_label)

    def river_tile_image_label(self, parent: Any, tile_label: str) -> Any:
        return self._ttk.Label(
            parent,
            image=self.river_tile_image(tile_label),
            style="TileImage.TLabel",
        )

    def tile_control(
        self, parent: Any, value: str, action: GuiActionView | None
    ) -> Any:
        """legal打牌に対応する表示牌画像はbuttonへ、それ以外はlabelのままにする。"""
        photo = self.tile_image(value)
        select = self._on_select_action
        if action is None or select is None:
            return self._ttk.Label(parent, image=photo, style="TileImage.TLabel")
        return self._ttk.Button(
            parent,
            image=photo,
            style="TileImage.TButton",
            command=lambda index=action.option_index: select(index),
        )
