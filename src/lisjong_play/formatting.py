"""Player-safe engine valuesをCLI表示へ変換するpure formatter。"""

from lisjong_engine.action_descriptor import (
    ActionDescriptor,
    AnkanActionDescriptor,
    ChiActionDescriptor,
    DaiminkanActionDescriptor,
    DiscardActionDescriptor,
    KakanActionDescriptor,
    NineTerminalsActionDescriptor,
    PassActionDescriptor,
    PonActionDescriptor,
    RiichiActionDescriptor,
    RonActionDescriptor,
    TsumoActionDescriptor,
)
from lisjong_engine.public_state import PublicMeld, PublicMeldType, PublicTile
from lisjong_engine.seat import Seat
from lisjong_engine.tile import TileCategory
from lisjong_engine.wind import Wind

_SEAT_LABELS = {
    Seat.EAST: "東",
    Seat.SOUTH: "南",
    Seat.WEST: "西",
    Seat.NORTH: "北",
}
_WIND_LABELS = {
    Wind.EAST: "東",
    Wind.SOUTH: "南",
    Wind.WEST: "西",
    Wind.NORTH: "北",
}
_SUIT_SUFFIX = {
    TileCategory.MANZU: "m",
    TileCategory.PINZU: "p",
    TileCategory.SOUZU: "s",
}
_HONOR_LABELS = {1: "東", 2: "南", 3: "西", 4: "北", 5: "白", 6: "發", 7: "中"}
_MELD_LABELS = {
    PublicMeldType.CHI: "チー",
    PublicMeldType.PON: "ポン",
    PublicMeldType.DAIMINKAN: "大明槓",
    PublicMeldType.ANKAN: "暗槓",
    PublicMeldType.KAKAN: "加槓",
}


class UnsupportedActionDescriptorError(TypeError):
    """current Human Play CLIが知らないActionDescriptorを受け取った場合。"""


def format_seat(seat: Seat) -> str:
    if not isinstance(seat, Seat):
        raise TypeError("seat must be a Seat")
    return _SEAT_LABELS[seat]


def format_wind(wind: Wind) -> str:
    if not isinstance(wind, Wind):
        raise TypeError("wind must be a Wind")
    return _WIND_LABELS[wind]


def tile_sort_key(tile: PublicTile) -> tuple[int, bool]:
    if not isinstance(tile, PublicTile):
        raise TypeError("tile must be a PublicTile")
    return (tile.tile_type.id, tile.is_red)


def format_tile(tile: PublicTile) -> str:
    if not isinstance(tile, PublicTile):
        raise TypeError("tile must be a PublicTile")
    tile_type = tile.tile_type
    if tile_type.category is TileCategory.HONOR:
        return _HONOR_LABELS[tile_type.rank]
    suffix = _SUIT_SUFFIX[tile_type.category]
    red_suffix = "r" if tile.is_red else ""
    return f"{tile_type.rank}{suffix}{red_suffix}"


def format_tiles(tiles: tuple[PublicTile, ...]) -> str:
    return " ".join(format_tile(tile) for tile in sorted(tiles, key=tile_sort_key))


def format_meld(meld: PublicMeld) -> str:
    if not isinstance(meld, PublicMeld):
        raise TypeError("meld must be a PublicMeld")
    parts = [_MELD_LABELS[meld.meld_type], format_tiles(meld.tiles)]
    if meld.from_seat is not None:
        parts.append(f"from {format_seat(meld.from_seat)}")
    if meld.called_tile is not None:
        parts.append(f"called {format_tile(meld.called_tile)}")
    return " / ".join(parts)


def format_action_descriptor(action: ActionDescriptor) -> str:
    """current 11 ActionDescriptor variantを人間向けlabelへ変換する。"""
    if isinstance(action, DiscardActionDescriptor):
        suffix = "（ツモ切り）" if action.is_tsumogiri else ""
        return f"打牌 {format_tile(action.tile)}{suffix}"
    if isinstance(action, RiichiActionDescriptor):
        return "立直"
    if isinstance(action, ChiActionDescriptor):
        return (
            f"チー {format_tile(action.tile)} / 使用 {format_tiles(action.consumed_tiles)}"
            f" / from {format_seat(action.from_seat)}"
        )
    if isinstance(action, PonActionDescriptor):
        return (
            f"ポン {format_tile(action.tile)} / 使用 {format_tiles(action.consumed_tiles)}"
            f" / from {format_seat(action.from_seat)}"
        )
    if isinstance(action, DaiminkanActionDescriptor):
        return (
            f"大明槓 {format_tile(action.tile)} / 使用 {format_tiles(action.consumed_tiles)}"
            f" / from {format_seat(action.from_seat)}"
        )
    if isinstance(action, AnkanActionDescriptor):
        return f"暗槓 {format_tiles(action.tiles)}"
    if isinstance(action, KakanActionDescriptor):
        return f"加槓 {format_tile(action.tile)}"
    if isinstance(action, RonActionDescriptor):
        return f"ロン {format_tile(action.tile)} / from {format_seat(action.from_seat)}"
    if isinstance(action, TsumoActionDescriptor):
        return f"ツモ {format_tile(action.tile)}"
    if isinstance(action, PassActionDescriptor):
        return f"パス ({format_tile(action.tile)} from {format_seat(action.from_seat)})"
    if isinstance(action, NineTerminalsActionDescriptor):
        return "九種九牌"
    raise UnsupportedActionDescriptorError(
        f"unsupported ActionDescriptor type: {type(action).__name__}"
    )
