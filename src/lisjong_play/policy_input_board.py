"""player-safe `PolicyInput`を既存`GuiBoardView`へ投影する共通presentation。

Replay Viewer(durable recordのrecorded `PolicyInput`)とRiichiLab live source
(Arenaのlive presentation seamが渡す`PolicyInput`)は、どちらも同じ
player-safe `PolicyInput`から同じ盤面を作る。両者が同じ変換を二重実装しない
ための、concreteな`PolicyInput -> GuiBoardView`共通化だけをここへ置く。

generic `ViewerState`、event bus、backend abstraction、universal presentation
frameworkはここへ持ち込まない。ここが扱うのは1つのdecisionに対応する1つの
`PolicyInput`だけである。

information boundary
--------------------
表示するconcealed handは、その`PolicyInput`が持つ当該seat自身のplayer-safeな
手牌だけである。複数`PolicyInput`をmergeして4席分のomniscient stateを作らず、
wall / dead wallを再構成せず、legality / scoring / progression / shanten /
ukeire / 役 / 翻 / 符をここで再計算・推測しない。
"""

from typing import Any

from lisjong.policy_contract import (
    AnkanAction,
    ChiAction,
    DaiminkanAction,
    DiscardAction,
    KakanAction,
    KyuushuKyuuhaiAction,
    PassAction,
    PonAction,
    RiichiAction,
    RonAction,
    Seat,
    TsumoAction,
)

from lisjong_play.gui_model import (
    GuiBoardView,
    GuiMeldView,
    GuiRiverTile,
    GuiSeatView,
    TablePosition,
)
from lisjong_play.tile_images import TILE_ASSET_FILENAMES

_POSITIONS: tuple[TablePosition, ...] = ("bottom", "right", "top", "left")

SEAT_NAMES = ("P1", "P2", "P3", "P4")
_SEAT_WIND_LABELS = ("東家", "南家", "西家", "北家")
ROUND_WIND_LABELS = {
    "east": "東",
    "south": "南",
    "west": "西",
    "north": "北",
}
_SUIT_SUFFIX = {"manzu": "m", "pinzu": "p", "souzu": "s"}
_HONOR_LABELS = {1: "東", 2: "南", 3: "西", 4: "北", 5: "白", 6: "發", 7: "中"}
_MELD_LABELS = {
    "chi": "チー",
    "pon": "ポン",
    "daiminkan": "大明槓",
    "ankan": "暗槓",
    "kakan": "加槓",
}
# `RiichiState`のdisplay label。live GUIの`PublicRiichiStatus`表示と同じ語彙を
# 使うが、engine statusと`PolicyInput` statusを同一semanticsとして再定義しない
# よう、対応表はここに閉じる。
_RIICHI_LABELS = {"none": "", "declared": "宣言中", "accepted": "立直"}

# 手牌 / 副露の表示順は live GUIのcanonical order(萬子 -> 筒子 -> 索子 -> 字牌)へ揃える。
_CATEGORY_ORDER = {"manzu": 0, "pinzu": 1, "souzu": 2, "honor": 3}

_CALL_LABELS = {ChiAction: "チー", PonAction: "ポン", DaiminkanAction: "大明槓"}


class PolicyInputProjectionError(RuntimeError):
    """player-safe `PolicyInput`を一意なGUI表示へ投影できない場合。

    未知の値をsilentに別の牌 / 別の状態へ落とさず、partial presentationへ
    降格させずにここでfail closedする。
    """


def seat_index(seat: Any) -> int:
    """typed `Seat`を0..3のfixed seat indexへ変換する。

    ここはArena / lisjongのtyped contractとして既に検証済みのvalueだけを扱う。
    """
    if not isinstance(seat, Seat):
        raise PolicyInputProjectionError(f"unusable seat value: {seat!r}")
    index = int(seat)
    if not 0 <= index < len(SEAT_NAMES):
        raise PolicyInputProjectionError(f"out-of-range seat: {index}")
    return index


def seat_name(seat: Any) -> str:
    """半荘中変わらないfixed seatの表示名を返す。"""
    return SEAT_NAMES[seat_index(seat)]


def seat_round_label(index: int, dealer_seat: Any) -> str:
    dealer = seat_index(dealer_seat)
    wind = _SEAT_WIND_LABELS[(index - dealer) % len(SEAT_NAMES)]
    return f"{SEAT_NAMES[index]}（{wind}）"


def tile_label(tile: Any) -> str:
    """typed `Tile`をcanonical tile labelへ変換する。

    変換結果は必ず既存の牌画像registryが解決できるlabelでなければならない。
    未知の牌はsilentに別牌へ落とさずfail closedする。
    """
    try:
        tile_type = tile.tile_type
        category = tile_type.category.value
        rank = int(tile_type.rank)
        is_red = bool(tile.is_red)
    except AttributeError:
        raise PolicyInputProjectionError(f"unusable tile value: {tile!r}")
    if category == "honor":
        label = _HONOR_LABELS.get(rank, "")
    else:
        suffix = _SUIT_SUFFIX.get(category)
        label = "" if suffix is None else f"{rank}{suffix}{'r' if is_red else ''}"
    if label not in TILE_ASSET_FILENAMES:
        raise PolicyInputProjectionError(
            f"this viewer cannot display the tile: "
            f"category={category!r} rank={rank!r} is_red={is_red!r}"
        )
    return label


def tile_sort_key(tile: Any) -> tuple[int, int, bool]:
    category = tile.tile_type.category.value
    try:
        order = _CATEGORY_ORDER[category]
    except KeyError:
        raise PolicyInputProjectionError(
            f"unsupported tile category: {category!r}"
        ) from None
    return (order, int(tile.tile_type.rank), bool(tile.is_red))


def _river_tile(discard: Any) -> GuiRiverTile:
    """player-safe discardを河表示へ投影する。

    `PolicyInput`の`Discard`は立直宣言牌markerを持たないため、宣言牌を
    推測せず常にmarkerなしとして扱う。
    """
    called_by = discard.called_by
    return GuiRiverTile(
        tile=tile_label(discard.tile),
        is_tsumogiri=bool(discard.tsumogiri),
        is_riichi_declaration=False,
        called_by=None if called_by is None else seat_name(called_by),
    )


def _meld_view(meld: Any) -> GuiMeldView:
    kind = meld.kind.value
    try:
        type_label = _MELD_LABELS[kind]
    except KeyError:
        raise PolicyInputProjectionError(f"unsupported meld kind: {kind!r}")
    from_seat = meld.from_seat
    called_tile = meld.called_tile
    return GuiMeldView(
        type_label=type_label,
        tiles=tuple(tile_label(tile) for tile in sorted(meld.tiles, key=tile_sort_key)),
        from_seat=None if from_seat is None else seat_name(from_seat),
        called_tile=None if called_tile is None else tile_label(called_tile),
    )


def action_label(action: Any) -> str:
    """canonical `InternalAction`を人間向けlabelへ変換する。

    未知のvariantはfail closedし、Pass等へfallbackしない。
    """
    if isinstance(action, DiscardAction):
        suffix = "（ツモ切り）" if action.tsumogiri else ""
        return f"打牌 {tile_label(action.tile)}{suffix}"
    if isinstance(action, RiichiAction):
        return "立直宣言"
    if isinstance(action, (ChiAction, PonAction, DaiminkanAction)):
        consumed = " ".join(
            tile_label(tile)
            for tile in sorted(action.consumed_tiles, key=tile_sort_key)
        )
        return (
            f"{_CALL_LABELS[type(action)]} {tile_label(action.called_tile)}"
            f" / 使用 {consumed} / from {seat_name(action.target)}"
        )
    if isinstance(action, AnkanAction):
        tiles = " ".join(
            tile_label(tile) for tile in sorted(action.tiles, key=tile_sort_key)
        )
        return f"暗槓 {tiles}"
    if isinstance(action, KakanAction):
        return f"加槓 {tile_label(action.added_tile)}"
    if isinstance(action, RonAction):
        return (
            f"ロン {tile_label(action.winning_tile)} / from {seat_name(action.target)}"
        )
    if isinstance(action, TsumoAction):
        return f"ツモ {tile_label(action.winning_tile)}"
    if isinstance(action, PassAction):
        return "パス"
    if isinstance(action, KyuushuKyuuhaiAction):
        return "九種九牌"
    raise PolicyInputProjectionError(
        f"unsupported action variant: {type(action).__name__}"
    )


def round_label(round_state: Any) -> str:
    wind = ROUND_WIND_LABELS.get(round_state.round_wind.value)
    if wind is None:
        raise PolicyInputProjectionError(
            f"unsupported round wind: {round_state.round_wind!r}"
        )
    return f"{wind}{round_state.hand_number}局 {round_state.honba}本場"


def build_policy_input_board_view(
    policy_input: Any, *, decision_label: str
) -> GuiBoardView:
    """`PolicyInput`のpublic stateからviewer-relative盤面を構築する。

    viewerは常にその`PolicyInput`の`self_seat`であり、`bottom`に置かれる。
    表示するconcealed handは、その`PolicyInput`がplayer-safeに保持している
    そのseat自身の手牌だけである。

    `decision_label`はconsumerが決める見出し文字列で、この投影自体は
    decision semanticsを解釈しない。
    """
    if not isinstance(decision_label, str):
        raise TypeError("decision_label must be a str")
    players = tuple(policy_input.players)
    if len(players) != len(SEAT_NAMES):
        raise PolicyInputProjectionError("PolicyInput must contain four seat states")
    round_state = policy_input.round
    viewer_index = seat_index(policy_input.self_seat)
    dealer_seat = round_state.dealer_seat

    seats = []
    for index, player in enumerate(players):
        riichi = player.riichi.value
        try:
            riichi_label = _RIICHI_LABELS[riichi]
        except KeyError:
            raise PolicyInputProjectionError(f"unsupported riichi state: {riichi!r}")
        seats.append(
            GuiSeatView(
                position=_POSITIONS[(index - viewer_index) % len(_POSITIONS)],
                label=seat_round_label(index, dealer_seat),
                score=int(player.score),
                riichi=riichi_label,
                melds=tuple(_meld_view(meld) for meld in player.melds),
                river=tuple(
                    _river_tile(discard)
                    for discard in sorted(player.discards, key=lambda item: item.order)
                ),
            )
        )

    own_hand = policy_input.own_hand
    concealed = sorted(own_hand.concealed_tiles, key=tile_sort_key)
    drawn = own_hand.drawn_tile
    if drawn is not None:
        for position in range(len(concealed) - 1, -1, -1):
            if concealed[position] == drawn:
                concealed.pop(position)
                break
        else:
            raise PolicyInputProjectionError("drawn tile is absent from the hand")
    return GuiBoardView(
        round_label=round_label(round_state),
        decision_label=decision_label,
        center_detail=(
            f"供託 {round_state.riichi_sticks}本 / "
            f"残り山 {round_state.live_wall_tiles_remaining}枚"
        ),
        dora_indicators=tuple(tile_label(tile) for tile in round_state.dora_indicators),
        seats=tuple(seats),
        hand_tiles=tuple(tile_label(tile) for tile in concealed),
        drawn_tile=None if drawn is None else tile_label(drawn),
    )


__all__ = [
    "ROUND_WIND_LABELS",
    "SEAT_NAMES",
    "PolicyInputProjectionError",
    "action_label",
    "build_policy_input_board_view",
    "round_label",
    "seat_index",
    "seat_name",
    "seat_round_label",
    "tile_label",
    "tile_sort_key",
]
