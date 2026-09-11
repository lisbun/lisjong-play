"""live Human PlayとpersistedReplayが共有するTk board / tile renderer。

`GuiBoardView`だけを入力とし、source(live engine / persisted record)を知らない。
牌画像は`lisjong_play.tile_images`のcanonical tile label解決をそのまま使う。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from lisjong_play.gui_model import (
    GuiActionView,
    GuiBoardView,
    GuiMeldView,
    GuiRiverTile,
    GuiSeatView,
)
from lisjong_play.tile_images import TileImageRegistry

RIVER_ROW_SIZE = 6
# 牌画像は用途ごとに3段階へ縮小する。600x800のvendored原寸に対し、
# 手牌 約33x44 / 河 約21x28 / 副露・ドラ表示牌 約20x26。
# 選択対象の手牌を一番大きく保ち、読み取る頻度の高い河をその次に、
# 補助情報である副露・ドラ表示牌を一番小さくする。
# default 1180x860でも minimum 920x700でも手牌領域が隠れない範囲に収める。
HAND_TILE_IMAGE_SUBSAMPLE = 18
RIVER_TILE_IMAGE_SUBSAMPLE = 28
BOARD_TILE_IMAGE_SUBSAMPLE = 30

# ツモ切り牌は彩度を落としたgrayscale画像で示す。text markerを読ませるより、
# 河を一目で見分けられるオンライン麻雀の表示に寄せる。
TSUMOGIRI_GRAY_MIX = 0.85

# 中央の卓情報から見た、各seatを置く隙間(px)。実卓と同じく、4人の河が
# 中央情報を囲む形にするため、seatは卓の端ではなく中央を基準に配置する。
CENTER_GAP_X = 130
CENTER_GAP_Y = 62

# Live / Replay双方で、卓を3x3の巨大セルへ分割せず、緑背景上に
# content-sized seatを浮かせるためのanchor。(relx, rely, anchor, x, y)で、
# 中央側の辺をanchorするため、河が伸びても外側へ伸びる。
TABLE_PLACE = {
    "top": (0.50, 0.50, "s", 0, -CENTER_GAP_Y),
    "left": (0.50, 0.50, "e", -CENTER_GAP_X, 0),
    "right": (0.50, 0.50, "w", CENTER_GAP_X, 0),
    "bottom": (0.50, 0.50, "n", 0, CENTER_GAP_Y),
}
CENTER_PLACE = (0.50, 0.50, "center")

# 各seatの構成要素を、卓の外周側から中央側へ並べる向き。
# text情報(名前 / 点数)と副露は外周側、河は中央側へ寄せる。
SEAT_PART_SIDE = {
    "top": "top",
    "bottom": "bottom",
    "left": "left",
    "right": "right",
}


def seat_status_side(position: str) -> str:
    """名前 / 点数textをpackする辺。

    左右の席で外周側へ置くとtextが卓の横幅を食い、河や中央情報と重なるため、
    左右だけは河の上へ積む。
    """
    side = seat_part_side(position)
    return "top" if side in ("left", "right") else side


def seat_part_side(position: str) -> str:
    """seatの構成要素をpackする辺。外周側から中央側へ積み上がる。

    未知のpositionはlayoutを黙って崩さないよう、fail closeする。
    """
    try:
        return SEAT_PART_SIDE[position]
    except KeyError:
        raise ValueError(f"unknown seat position: {position!r}") from None


def seat_melds_come_first(position: str) -> bool:
    """副露を河より先にpackするか。

    晒した牌は常に河の外周側へ出す。左の席だけ外周が左手側なので、
    副露を先にpackして河を中央側へ寄せる。
    """
    seat_part_side(position)
    return position == "left"


# 卓面の配色。Live / Replay双方で同じ見た目にするため、共有rendererが使う
# ttk styleもこのmoduleで定義する。
BOARD_FELT_COLOR = "#176b4d"
BOARD_TEXT_COLOR = "#f4f1e6"
SEAT_INFO_COLOR = "#0d4634"


def configure_board_styles(style: Any) -> None:
    """共有rendererが参照するttk styleをまとめて定義する。

    卓上のlabelは緑のfeltへ直接置くため、背景色を明示しないと既定のgray箱が
    牌やtextの周りへ出てしまう。Live GUIとReplay Viewerで同じ見た目にする。
    """
    style.configure("Table.TFrame", background=BOARD_FELT_COLOR)
    style.configure("Seat.TFrame", background=BOARD_FELT_COLOR)
    style.configure(
        "BoardTile.TLabel",
        background=BOARD_FELT_COLOR,
        padding=0,
        relief="flat",
    )
    style.configure(
        "BoardText.TLabel",
        background=BOARD_FELT_COLOR,
        foreground=BOARD_TEXT_COLOR,
    )
    style.configure(
        "SeatInfo.TLabel",
        background=SEAT_INFO_COLOR,
        foreground=BOARD_TEXT_COLOR,
        font=("TkDefaultFont", 9, "bold"),
        padding=(4, 1),
    )
    style.configure(
        "Center.TLabel",
        background=BOARD_FELT_COLOR,
        foreground=BOARD_TEXT_COLOR,
        font=("TkDefaultFont", 11, "bold"),
    )
    style.configure("TileImage.TLabel", padding=1, relief="flat")
    style.configure("TileImage.TButton", padding=1)


def load_tile_image(tk: Any, path: str) -> Any:
    """vendored牌画像を手牌向けsizeへ縮小する。"""
    return tk.PhotoImage(file=path).subsample(HAND_TILE_IMAGE_SUBSAMPLE)


def load_board_tile_image(tk: Any, path: str) -> Any:
    """vendored牌画像を卓上(副露 / ドラ表示牌)向けの小さいsizeへ縮小する。"""
    return tk.PhotoImage(file=path).subsample(BOARD_TILE_IMAGE_SUBSAMPLE)


def load_river_tile_image(tk: Any, path: str) -> Any:
    """vendored牌画像を河専用の小さい表示sizeへ縮小する。"""
    return tk.PhotoImage(file=path).subsample(RIVER_TILE_IMAGE_SUBSAMPLE)


def gray_pixel_rows(image: Any, mix: float = TSUMOGIRI_GRAY_MIX) -> list[list[str]]:
    """image各pixelを、輝度へ`mix`だけ寄せた`#rrggbb`文字列の2次元listにする。

    Tkの`PhotoImage`はfilterを持たないため、pixelを読み直して彩度を落とす。
    透過pixelの色は描画されないが、`put()`が不透明にしてしまうため、
    呼び出し側で透過情報を復元する前提で色だけを返す。
    """
    rows: list[list[str]] = []
    for y in range(image.height()):
        row: list[str] = []
        for x in range(image.width()):
            red, green, blue = _pixel_rgb(image, x, y)
            luma = 0.299 * red + 0.587 * green + 0.114 * blue
            row.append(
                "#%02x%02x%02x"
                % tuple(
                    min(255, max(0, round(channel + (luma - channel) * mix)))
                    for channel in (red, green, blue)
                )
            )
        rows.append(row)
    return rows


def _pixel_rgb(image: Any, x: int, y: int) -> tuple[int, int, int]:
    """`PhotoImage.get()`のtuple / 文字列どちらの戻り値もRGBへ正規化する。"""
    pixel = image.get(x, y)
    if isinstance(pixel, str):
        pixel = pixel.split()
    red, green, blue = (int(channel) for channel in tuple(pixel)[:3])
    return red, green, blue


def desaturate_tile_image(image: Any) -> Any:
    """牌画像をその場でgrayscale寄りへ変換し、透過pixelを復元して返す。"""
    transparent = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.transparency_get(x, y)
    ]
    image.put(gray_pixel_rows(image))
    for x, y in transparent:
        image.transparency_set(x, y, True)
    return image


def load_tsumogiri_river_tile_image(tk: Any, path: str) -> Any:
    """ツモ切り表示用に、河sizeの牌画像を彩度を落として生成する。"""
    return desaturate_tile_image(load_river_tile_image(tk, path))


@dataclass(frozen=True)
class BoardTileImages:
    """用途別の牌画像registry束。

    `TileImageRegistry`はfactory単位のcacheなので、表示の種類ごとに別registryを
    持てば、同じtile labelでも種類ごとに1つのcache済みimage objectへ解決される。
    この束をapplication lifetime中保持することが、Tkの`PhotoImage` reference
    を保つ責務も兼ねる。
    """

    hand: TileImageRegistry
    board: TileImageRegistry
    river: TileImageRegistry
    river_tsumogiri: TileImageRegistry


def build_board_tile_images(tk: Any) -> BoardTileImages:
    """手牌 / 卓上 / 河 / ツモ切り河の4registryをまとめて生成する。"""
    return BoardTileImages(
        hand=TileImageRegistry(lambda path: load_tile_image(tk, path)),
        board=TileImageRegistry(lambda path: load_board_tile_image(tk, path)),
        river=TileImageRegistry(lambda path: load_river_tile_image(tk, path)),
        river_tsumogiri=TileImageRegistry(
            lambda path: load_tsumogiri_river_tile_image(tk, path)
        ),
    )


# GUI専用の河凡例。CLI rendererのtext river(`*`)とは表記が異なるため、
# `renderer.RIVER_LEGEND`を共有しない。
GUI_RIVER_LEGEND = "河の表記: 灰色 = ツモ切り / [立] = 立直宣言牌 / →Pn = 鳴かれた牌"


def river_caption(cell: GuiRiverTile) -> str:
    """河牌画像へ添える、立直宣言 / 鳴かれた牌のtext marker。

    ツモ切りは彩度を落とした牌画像自体で示すため、captionへは出さない。
    """
    caption = "[立]" if cell.is_riichi_declaration else ""
    if cell.called_by is not None:
        caption += f"→{cell.called_by}"
    return caption


def hand_discard_actions(
    actions: Sequence[GuiActionView],
) -> dict[str, GuiActionView]:
    """表示中の concealed-hand tile label → 対応する打牌 GuiActionView。"""
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
    """`GuiBoardView`をTk widgetへ描画する共有renderer。"""

    def __init__(
        self,
        ttk: Any,
        tile_images: BoardTileImages,
        *,
        on_select_action: Callable[[int], None] | None = None,
    ) -> None:
        if on_select_action is not None and not callable(on_select_action):
            raise TypeError("on_select_action must be callable or None")
        self._ttk = ttk
        self._tile_images = tile_images
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
        self._ttk.Label(center, text=board.round_label, style="Center.TLabel").pack()
        self._ttk.Label(
            center, text=board.center_detail, style="BoardText.TLabel"
        ).pack()
        dora_line = self._ttk.Frame(center, style="Seat.TFrame")
        dora_line.pack(anchor="center", pady=1)
        self._ttk.Label(dora_line, text="ドラ", style="BoardText.TLabel").pack(
            side="left", padx=(0, 3)
        )
        if board.dora_indicators:
            for tile_label in board.dora_indicators:
                self.tile_image_label(dora_line, tile_label).pack(side="left")
        else:
            self._ttk.Label(dora_line, text="なし", style="BoardText.TLabel").pack(
                side="left"
            )
        self._ttk.Label(
            center, text=f"判断: {board.decision_label}", style="BoardText.TLabel"
        ).pack()

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
                side="left", fill="y", padx=3
            )
            tsumogiri_action = drawn_tile_tsumogiri_action(actions, board.drawn_tile)
            self.tile_control(tiles, board.drawn_tile, tsumogiri_action).pack(
                side="left", padx=1
            )

    def render_seat(self, frame: Any, seat: GuiSeatView) -> None:
        """seatを、卓の外周側から中央側へ text情報 → 副露 → 河 の順で描画する。

        オンライン麻雀卓と同じく、名前 / 点数のtextと晒した副露は自分の外周側
        に置き、河だけを中央へ向ける。`seat.position`ごとにpackする辺を変える
        ことで、4席の河が中央を囲む。
        """
        clear_frame(frame)

        status = f"{seat.label}  {seat.score}点"
        if seat.riichi:
            status += f" / {seat.riichi}"
        self._ttk.Label(frame, text=status, style="SeatInfo.TLabel").pack(
            side=seat_status_side(seat.position)
        )

        body = self._ttk.Frame(frame, style="Seat.TFrame")
        body.pack(side=seat_part_side(seat.position))

        melds_box = self._ttk.Frame(body, style="Seat.TFrame")
        river_box = self._ttk.Frame(body, style="Seat.TFrame")
        boxes = (
            (melds_box, river_box)
            if seat_melds_come_first(seat.position)
            else (river_box, melds_box)
        )
        for box in boxes:
            box.pack(side="left", anchor="n")

        if seat.melds:
            for meld in seat.melds:
                self.render_meld(melds_box, meld)

        if not seat.river:
            self._ttk.Label(river_box, text="河 -", style="BoardText.TLabel").pack()
        else:
            for start in range(0, len(seat.river), RIVER_ROW_SIZE):
                row = self._ttk.Frame(river_box, style="Seat.TFrame")
                row.pack(anchor="w")
                for cell in seat.river[start : start + RIVER_ROW_SIZE]:
                    self.render_river_tile(row, cell).pack(side="left")

    def render_meld(self, parent: Any, meld: GuiMeldView) -> None:
        box = self._ttk.Frame(parent, style="Seat.TFrame", padding=(0, 0, 3, 0))
        box.pack(side="left", anchor="n")
        meta = [meld.type_label]
        if meld.from_seat is not None:
            meta.append(f"from {meld.from_seat}")
        if meld.called_tile is not None:
            meta.append(f"called {meld.called_tile}")
        self._ttk.Label(
            box,
            text=" / ".join(meta),
            style="BoardText.TLabel",
            font=("TkDefaultFont", 7),
        ).pack()
        tiles_row = self._ttk.Frame(box, style="Seat.TFrame")
        tiles_row.pack()
        for tile_label in meld.tiles:
            self.tile_image_label(tiles_row, tile_label).pack(side="left")

    def render_river_tile(self, parent: Any, cell: GuiRiverTile) -> Any:
        box = self._ttk.Frame(parent, style="Seat.TFrame")
        self.river_tile_image_label(
            box, cell.tile, is_tsumogiri=cell.is_tsumogiri
        ).pack()
        caption = river_caption(cell)
        if caption:
            self._ttk.Label(
                box,
                text=caption,
                style="BoardText.TLabel",
                font=("TkDefaultFont", 6),
            ).pack()
        return box

    def tile_image(self, tile_label: str) -> Any:
        """手牌sizeの牌画像。打牌選択の対象になるため一番大きい。"""
        return self._tile_images.hand.get(tile_label)

    def board_tile_image(self, tile_label: str) -> Any:
        """副露 / ドラ表示牌sizeの牌画像。手牌より小さい。"""
        return self._tile_images.board.get(tile_label)

    def tile_image_label(self, parent: Any, tile_label: str) -> Any:
        """卓上(副露 / ドラ表示牌)の牌画像label。緑の卓面へ直接置く。"""
        return self._ttk.Label(
            parent, image=self.board_tile_image(tile_label), style="BoardTile.TLabel"
        )

    def river_tile_image(self, tile_label: str, *, is_tsumogiri: bool = False) -> Any:
        """河牌画像。ツモ切りだけ彩度を落としたregistryから解決する。"""
        registry = (
            self._tile_images.river_tsumogiri
            if is_tsumogiri
            else self._tile_images.river
        )
        return registry.get(tile_label)

    def river_tile_image_label(
        self, parent: Any, tile_label: str, *, is_tsumogiri: bool = False
    ) -> Any:
        return self._ttk.Label(
            parent,
            image=self.river_tile_image(tile_label, is_tsumogiri=is_tsumogiri),
            style="BoardTile.TLabel",
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
