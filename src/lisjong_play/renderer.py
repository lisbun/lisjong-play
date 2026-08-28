"""SeatObservation / ordered delivery factsのCLI presentation。"""

from collections.abc import Callable, Iterable

from lisjong_engine.action_descriptor import ActionDescriptor
from lisjong_engine.observation import ObservationDecisionKind, SeatObservation
from lisjong_engine.public_state import PublicDiscard, PublicRiichiStatus
from lisjong_engine.round_completion import (
    MatchCompletionFact,
    RoundCompletionFact,
    RoundOutcomeKind,
)
from lisjong_engine.round_progress import (
    DiscardProgress,
    DoraIndicatorRevealedProgress,
    KanConfirmedProgress,
    KanDeclaredProgress,
    MeldCalledProgress,
    RiichiDeclaredProgress,
    RiichiEstablishedProgress,
    RiichiFailedProgress,
    RoundProgressFact,
)
from lisjong_engine.win_context import WinMethod

from lisjong_play.formatting import (
    format_action_descriptor,
    format_meld,
    format_seat,
    format_tile,
    format_tiles,
    format_wind,
    tile_sort_key,
)

_DECISION_LABELS = {
    ObservationDecisionKind.TURN: "自摸番",
    ObservationDecisionKind.RIICHI_DISCARD: "立直宣言牌選択",
    ObservationDecisionKind.DISCARD_REACTION: "打牌への反応",
    ObservationDecisionKind.KAKAN_REACTION: "加槓への反応",
    ObservationDecisionKind.ANKAN_REACTION: "暗槓への反応",
}
_RIICHI_LABELS = {
    PublicRiichiStatus.NONE: "なし",
    PublicRiichiStatus.PENDING: "宣言中",
    PublicRiichiStatus.ESTABLISHED: "立直",
}
_WIN_METHOD_LABELS = {WinMethod.TSUMO: "ツモ", WinMethod.RON: "ロン"}


class UnsupportedDeliveryItemError(TypeError):
    """current CLIが知らないplayer-safe delivery itemを受け取った場合。"""


def _format_discard(discard: PublicDiscard) -> str:
    tile = format_tile(discard.tile)
    if discard.is_tsumogiri:
        tile += "*"
    if discard.is_riichi_declaration:
        tile = f"[{tile}]"
    if discard.called_by is not None:
        tile += f"→{format_seat(discard.called_by)}"
    return tile


def render_board(observation: SeatObservation) -> str:
    if not isinstance(observation, SeatObservation):
        raise TypeError("observation must be a SeatObservation")

    lines = [
        (
            f"=== {format_wind(observation.prevailing_wind)}{observation.hand_number}局 "
            f"{observation.honba}本場 / 供託 {observation.riichi_sticks} ==="
        ),
        f"判断: {_DECISION_LABELS[observation.decision_kind]}",
        "点数: "
        + " / ".join(
            f"{format_seat(score.seat)} {score.points}" for score in observation.scores
        ),
        "ドラ表示牌: "
        + (
            " ".join(format_tile(tile) for tile in observation.dora_indicators)
            or "なし"
        ),
        f"残り山: {observation.remaining_live_wall_count}",
        "立直: "
        + " / ".join(
            f"{format_seat(state.seat)}={_RIICHI_LABELS[state.status]}"
            for state in observation.riichi_states
        ),
    ]

    lines.append("副露:")
    for seat_melds in observation.melds:
        meld_text = " | ".join(format_meld(meld) for meld in seat_melds.melds) or "なし"
        lines.append(f"  {format_seat(seat_melds.seat)}: {meld_text}")

    lines.append("河:  * = ツモ切り / [] = 立直宣言牌 / → = 鳴かれた牌")
    for seat_discards in observation.discards:
        river = (
            " ".join(_format_discard(item) for item in seat_discards.discards) or "-"
        )
        lines.append(f"  {format_seat(seat_discards.seat)}: {river}")

    sorted_hand = tuple(sorted(observation.hand_tiles, key=tile_sort_key))
    lines.append(f"手牌: {format_tiles(sorted_hand)}")
    lines.append(
        "ツモ: "
        + (
            format_tile(observation.drawn_tile)
            if observation.drawn_tile is not None
            else "-"
        )
    )
    return "\n".join(lines)


def render_action_menu(options: Iterable[ActionDescriptor]) -> str:
    values = tuple(options)
    if not values:
        raise ValueError("options must not be empty")
    lines = ["操作を選んでください:"]
    for number, option in enumerate(values, start=1):
        lines.append(f"  {number}. {format_action_descriptor(option)}")
    return "\n".join(lines)


def render_progress_fact(fact: RoundProgressFact) -> str:
    if isinstance(fact, DiscardProgress):
        suffix = "（ツモ切り）" if fact.is_tsumogiri else ""
        return f"進行: {format_seat(fact.seat)} 打 {format_tile(fact.tile)}{suffix}"
    if isinstance(fact, MeldCalledProgress):
        return f"進行: {format_seat(fact.seat)} {format_meld(fact.meld)}"
    if isinstance(fact, KanDeclaredProgress):
        return f"進行: {format_seat(fact.seat)} 槓宣言 {format_meld(fact.meld)}"
    if isinstance(fact, KanConfirmedProgress):
        return f"進行: {format_seat(fact.seat)} 槓成立 {format_meld(fact.meld)}"
    if isinstance(fact, RiichiDeclaredProgress):
        return f"進行: {format_seat(fact.seat)} 立直宣言 {format_tile(fact.tile)}"
    if isinstance(fact, RiichiEstablishedProgress):
        return f"進行: {format_seat(fact.seat)} 立直成立"
    if isinstance(fact, RiichiFailedProgress):
        return f"進行: {format_seat(fact.seat)} 立直不成立"
    if isinstance(fact, DoraIndicatorRevealedProgress):
        return f"進行: ドラ表示牌 {format_tile(fact.indicator)} 公開"
    raise UnsupportedDeliveryItemError(
        f"unsupported RoundProgressFact type: {type(fact).__name__}"
    )


def render_round_completion(fact: RoundCompletionFact) -> str:
    if not isinstance(fact, RoundCompletionFact):
        raise TypeError("fact must be a RoundCompletionFact")
    lines = [
        (
            f"--- {format_wind(fact.prevailing_wind)}{fact.hand_number}局 "
            f"{fact.honba}本場 終了 ---"
        )
    ]
    if fact.outcome is RoundOutcomeKind.WIN:
        winners = ", ".join(
            f"{format_seat(winner.seat)}({_WIN_METHOD_LABELS[winner.win_method]})"
            for winner in fact.winners
        )
        lines.append(f"結果: 和了 {winners}")
        if fact.source_seat is not None:
            lines.append(f"放銃: {format_seat(fact.source_seat)}")
    elif fact.outcome is RoundOutcomeKind.EXHAUSTIVE_DRAW:
        lines.append("結果: 流局")
        tenpai = ", ".join(format_seat(seat) for seat in fact.tenpai_seats) or "なし"
        lines.append(f"聴牌: {tenpai}")
        if fact.nagashi_mangan_seats:
            nagashi = ", ".join(format_seat(seat) for seat in fact.nagashi_mangan_seats)
            lines.append(f"流し満貫: {nagashi}")
    elif fact.outcome is RoundOutcomeKind.ABORTIVE_DRAW:
        reason = (
            fact.abortive_reason.value
            if fact.abortive_reason is not None
            else "unknown"
        )
        lines.append(f"結果: 途中流局 ({reason})")
    else:  # pragma: no cover - enum exhaustiveness guard
        raise UnsupportedDeliveryItemError(f"unsupported outcome: {fact.outcome!r}")

    lines.append(
        "点数移動: "
        + " / ".join(
            f"{format_seat(delta.seat)} {delta.delta:+d}" for delta in fact.point_deltas
        )
    )
    lines.append(
        "局後点数: "
        + " / ".join(
            f"{format_seat(score.seat)} {score.points}" for score in fact.scores_after
        )
    )
    return "\n".join(lines)


def render_match_completion(fact: MatchCompletionFact) -> str:
    if not isinstance(fact, MatchCompletionFact):
        raise TypeError("fact must be a MatchCompletionFact")
    lines = ["=== 半荘終了 ===", f"終了理由: {fact.end_reason.value}"]
    by_seat = {score.seat: score.points for score in fact.final_scores}
    for result in sorted(fact.final_results, key=lambda item: item.rank):
        lines.append(
            f"{result.rank}位 {format_seat(result.seat)}: "
            f"{by_seat[result.seat]}点 / 最終ポイント {result.final_points}"
        )
    return "\n".join(lines)


class DeliveryPresenter:
    """engine `on_delivery`へ渡す同期presentation callback。"""

    def __init__(
        self,
        input_reader: Callable[[str], str],
        output_writer: Callable[[str], None],
    ) -> None:
        if not callable(input_reader):
            raise TypeError("input_reader must be callable")
        if not callable(output_writer):
            raise TypeError("output_writer must be callable")
        self._input_reader = input_reader
        self._output_writer = output_writer

    def __call__(self, batch: tuple[object, ...]) -> None:
        try:
            items = tuple(batch)
        except TypeError:
            raise TypeError("delivery batch must be iterable") from None
        for item in items:
            if isinstance(item, RoundProgressFact):
                self._output_writer(render_progress_fact(item))
                continue
            if isinstance(item, RoundCompletionFact):
                self._output_writer(render_round_completion(item))
                if item.has_next_round:
                    self._wait_for_next_round()
                continue
            if isinstance(item, MatchCompletionFact):
                self._output_writer(render_match_completion(item))
                continue
            raise UnsupportedDeliveryItemError(
                f"unsupported delivery item: {type(item).__name__}"
            )

    def _wait_for_next_round(self) -> None:
        self._output_writer("Enterで次の局へ進みます。")
        while True:
            if not self._input_reader("> ").strip():
                return
            self._output_writer("Enterのみを入力してください。")
