"""Player-safe engine valuesからTk非依存のGUI表示modelを構築する。"""

from dataclasses import dataclass
from typing import Literal

from lisjong_engine.action_descriptor import (
    ActionDescriptor,
    DiscardActionDescriptor,
    PassActionDescriptor,
    is_action_descriptor,
)
from lisjong_engine.observation import ObservationDecisionKind, SeatObservation
from lisjong_engine.public_state import PublicDiscard, PublicRiichiStatus
from lisjong_engine.seat import Seat

from lisjong_play.formatting import (
    UnsupportedActionDescriptorError,
    format_action_descriptor,
    format_meld,
    format_seat,
    format_tile,
    format_wind,
    tile_sort_key,
)
from lisjong_play.seat_display import SEAT_ORDER, round_seat_label

TablePosition = Literal["bottom", "right", "top", "left"]
ActionStyle = Literal["discard", "tsumogiri", "pass", "action"]

_POSITIONS: tuple[TablePosition, ...] = ("bottom", "right", "top", "left")
_DECISION_LABELS = {
    ObservationDecisionKind.TURN: "自摸番",
    ObservationDecisionKind.RIICHI_DISCARD: "立直宣言牌選択",
    ObservationDecisionKind.DISCARD_REACTION: "打牌への反応",
    ObservationDecisionKind.KAKAN_REACTION: "加槓への反応",
    ObservationDecisionKind.ANKAN_REACTION: "暗槓への反応",
}
_RIICHI_LABELS = {
    PublicRiichiStatus.NONE: "",
    PublicRiichiStatus.PENDING: "宣言中",
    PublicRiichiStatus.ESTABLISHED: "立直",
}


class GuiProjectionError(ValueError):
    """player-safe inputを一意なGUI表示へ投影できない場合。"""


@dataclass(frozen=True)
class GuiSeatView:
    """卓上の1席分のpublic表示。"""

    position: TablePosition
    label: str
    score: int
    riichi: str
    melds: tuple[str, ...]
    river: tuple[str, ...]


@dataclass(frozen=True)
class GuiBoardView:
    """1回のHuman decisionで表示するviewer-relative卓。"""

    round_label: str
    decision_label: str
    center_detail: str
    dora_indicators: tuple[str, ...]
    seats: tuple[GuiSeatView, ...]
    hand_tiles: tuple[str, ...]
    drawn_tile: str | None


@dataclass(frozen=True)
class GuiActionView:
    """original option indexへ対応するGUI button表示。"""

    option_index: int
    label: str
    style: ActionStyle
    tile_label: str | None


def _format_discard(discard: PublicDiscard) -> str:
    tile = format_tile(discard.tile)
    if discard.is_tsumogiri:
        tile += "*"
    if discard.is_riichi_declaration:
        tile = f"[{tile}]"
    if discard.called_by is not None:
        tile += f"→{format_seat(discard.called_by)}"
    return tile


def _position(seat: Seat, viewer_seat: Seat) -> TablePosition:
    viewer_index = SEAT_ORDER.index(viewer_seat)
    seat_index = SEAT_ORDER.index(seat)
    return _POSITIONS[(seat_index - viewer_index) % len(SEAT_ORDER)]


def build_gui_board_view(observation: SeatObservation) -> GuiBoardView:
    """SeatObservationだけからviewer-relativeなGUI modelを構築する。"""
    if not isinstance(observation, SeatObservation):
        raise TypeError("observation must be a SeatObservation")

    sources = (
        ("scores", observation.scores),
        ("riichi_states", observation.riichi_states),
        ("melds", observation.melds),
        ("discards", observation.discards),
    )
    expected = set(Seat)
    for name, values in sources:
        if len(values) != len(expected) or {value.seat for value in values} != expected:
            raise GuiProjectionError(f"{name} must contain exactly one value per seat")
    scores = {value.seat: value.points for value in observation.scores}
    riichi = {value.seat: value.status for value in observation.riichi_states}
    melds = {value.seat: value.melds for value in observation.melds}
    discards = {value.seat: value.discards for value in observation.discards}

    seats = []
    for seat in Seat:
        try:
            riichi_label = _RIICHI_LABELS[riichi[seat]]
        except KeyError:
            raise GuiProjectionError(
                f"unsupported riichi status: {riichi[seat]!r}"
            ) from None
        seats.append(
            GuiSeatView(
                position=_position(seat, observation.viewer_seat),
                label=round_seat_label(seat, observation.dealer_seat),
                score=scores[seat],
                riichi=riichi_label,
                melds=tuple(format_meld(value) for value in melds[seat]),
                river=tuple(
                    _format_discard(value)
                    for value in sorted(discards[seat], key=lambda item: item.order)
                ),
            )
        )

    try:
        decision_label = _DECISION_LABELS[observation.decision_kind]
    except KeyError:
        raise GuiProjectionError(
            f"unsupported decision kind: {observation.decision_kind!r}"
        ) from None

    sorted_hand = list(sorted(observation.hand_tiles, key=tile_sort_key))
    if observation.drawn_tile is not None:
        for index in range(len(sorted_hand) - 1, -1, -1):
            if sorted_hand[index] == observation.drawn_tile:
                sorted_hand.pop(index)
                break
        else:  # pragma: no cover - SeatObservation currently guards this contract.
            raise GuiProjectionError("drawn_tile must be present in hand_tiles")
    hand_tiles = tuple(format_tile(tile) for tile in sorted_hand)
    drawn_tile = (
        format_tile(observation.drawn_tile)
        if observation.drawn_tile is not None
        else None
    )
    return GuiBoardView(
        round_label=(
            f"{format_wind(observation.prevailing_wind)}{observation.hand_number}局 "
            f"{observation.honba}本場"
        ),
        decision_label=decision_label,
        center_detail=(
            f"供託 {observation.riichi_sticks}本 / "
            f"残り山 {observation.remaining_live_wall_count}枚"
        ),
        dora_indicators=tuple(
            format_tile(tile) for tile in observation.dora_indicators
        ),
        seats=tuple(seats),
        hand_tiles=hand_tiles,
        drawn_tile=drawn_tile,
    )


def build_gui_action_views(
    options: tuple[ActionDescriptor, ...],
) -> tuple[GuiActionView, ...]:
    """legal option順を維持したGUI button modelを構築する。"""
    try:
        values = tuple(options)
    except TypeError:
        raise TypeError("options must be iterable") from None
    if not values:
        raise ValueError("options must not be empty")
    if any(not is_action_descriptor(value) for value in values):
        raise TypeError("options must contain only current ActionDescriptor values")

    views = []
    for index, option in enumerate(values):
        try:
            label = format_action_descriptor(option)
        except UnsupportedActionDescriptorError:
            raise
        if isinstance(option, DiscardActionDescriptor):
            style: ActionStyle = "tsumogiri" if option.is_tsumogiri else "discard"
            tile_label = format_tile(option.tile)
        elif isinstance(option, PassActionDescriptor):
            style = "pass"
            tile_label = None
        else:
            style = "action"
            tile_label = None
        views.append(
            GuiActionView(
                option_index=index,
                label=label,
                style=style,
                tile_label=tile_label,
            )
        )
    return tuple(views)
