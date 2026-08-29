"""SeatObservation / ordered delivery factsのCLI presentation。"""

import unicodedata
from collections.abc import Callable, Iterable

from lisjong_engine.action_descriptor import ActionDescriptor
from lisjong_engine.observation import ObservationDecisionKind, SeatObservation
from lisjong_engine.public_state import PublicDiscard, PublicRiichiStatus, PublicTile
from lisjong_engine.round_completion import (
    MatchCompletionFact,
    RoundCompletionFact,
    RoundCompletionScoreCandidate,
    RoundCompletionWinner,
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
from lisjong_engine.score import ScoreLimit
from lisjong_engine.seat import Seat
from lisjong_engine.settlement import TransferReason
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
from lisjong_play.seat_display import fixed_seat_name, round_seat_label

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
_SCORE_LIMIT_LABELS = {
    ScoreLimit.MANGAN: "満貫",
    ScoreLimit.HANEMAN: "跳満",
    ScoreLimit.BAIMAN: "倍満",
    ScoreLimit.SANBAIMAN: "三倍満",
    ScoreLimit.YAKUMAN: "役満",
}
_TRANSFER_REASON_LABELS = {
    TransferReason.RON: "ロン",
    TransferReason.TSUMO: "ツモ",
    TransferReason.PAO_RON: "パオ・ロン",
    TransferReason.PAO_TSUMO: "パオ・ツモ",
    TransferReason.HONBA: "本場",
    TransferReason.NOTEN_PENALTY: "不聴罰符",
    TransferReason.NAGASHI_MANGAN: "流し満貫",
}
_RIVER_WRAP_SIZE = 6
_RIVER_CELL_WIDTH = 10
RIVER_LEGEND = "河の表記: * = ツモ切り / [] = 立直宣言牌 / →Pn = 鳴かれた牌"


class UnsupportedDeliveryItemError(TypeError):
    """current CLIが知らないplayer-safe delivery itemを受け取った場合。"""


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _pad_left(text: str, width: int) -> str:
    return " " * max(0, width - _display_width(text)) + text


def _pad_right(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _format_discard(discard: PublicDiscard) -> str:
    tile = format_tile(discard.tile)
    if discard.is_tsumogiri:
        tile += "*"
    if discard.is_riichi_declaration:
        tile = f"[{tile}]"
    if discard.called_by is not None:
        tile += f"→{fixed_seat_name(discard.called_by)}"
    return tile


def _seat_ranks_from_dealer(dealer_seat: Seat) -> dict[Seat, int]:
    seats = tuple(Seat)
    start_index = seats.index(dealer_seat)
    return {seats[(start_index + offset) % len(seats)]: offset for offset in range(4)}


def _build_river_cells(
    observation: SeatObservation,
) -> dict[Seat, tuple[PublicDiscard | None, ...]]:
    """PublicDiscard.orderだけを正本に、巡目相当の表示列を組み立てる。

    global discard orderを順に見て、dealer起点の席順位が前回より後なら同じ列、
    同順位または前へ戻るなら次列へ進める。鳴き・槓等のnon-discard event自体を
    復元する情報はSeatObservationにないため、それらを推測して補完はしない。
    """
    ordered = sorted(
        (
            discard.order,
            seat_discards.seat,
            discard,
        )
        for seat_discards in observation.discards
        for discard in seat_discards.discards
    )
    if not ordered:
        return {seat: () for seat in Seat}

    rank_by_seat = _seat_ranks_from_dealer(observation.dealer_seat)
    assignments: list[tuple[Seat, int, PublicDiscard]] = []
    current_column = 0
    previous_rank: int | None = None
    for _, seat, discard in ordered:
        rank = rank_by_seat[seat]
        if previous_rank is not None and rank <= previous_rank:
            current_column += 1
        assignments.append((seat, current_column, discard))
        previous_rank = rank

    total_columns = current_column + 1
    cells: dict[Seat, list[PublicDiscard | None]] = {
        seat: [None] * total_columns for seat in Seat
    }
    for seat, column, discard in assignments:
        cells[seat][column] = discard
    return {seat: tuple(values) for seat, values in cells.items()}


def _render_rivers(observation: SeatObservation) -> list[str]:
    lines = ["河:"]
    cells_by_seat = _build_river_cells(observation)
    for seat in Seat:
        label = round_seat_label(seat, observation.dealer_seat)
        prefix = f"  {label}: "
        cells = cells_by_seat[seat]
        if not cells:
            lines.append(prefix + "-")
            continue

        rows = tuple(
            cells[index : index + _RIVER_WRAP_SIZE]
            for index in range(0, len(cells), _RIVER_WRAP_SIZE)
        )
        continuation = " " * _display_width(prefix)
        for row_index, row in enumerate(rows):
            rendered = " ".join(
                _pad_right(
                    _format_discard(cell) if cell is not None else "", _RIVER_CELL_WIDTH
                )
                for cell in row
            ).rstrip()
            lines.append((prefix if row_index == 0 else continuation) + rendered)
    return lines


def render_board(observation: SeatObservation, *, include_hand: bool = True) -> str:
    """通常decision向けのplayer-safe盤面を表示する。"""
    return _render_board(observation, include_meta=True, include_hand=include_hand)


def render_reaction_board(observation: SeatObservation) -> str:
    """reaction判断向けにmeta情報を省いたcompact盤面を表示する。"""
    return _render_board(observation, include_meta=False, include_hand=True)


def _render_board(
    observation: SeatObservation,
    *,
    include_meta: bool,
    include_hand: bool,
) -> str:
    if not isinstance(observation, SeatObservation):
        raise TypeError("observation must be a SeatObservation")

    lines = [
        (
            f"=== {format_wind(observation.prevailing_wind)}{observation.hand_number}局 "
            f"{observation.honba}本場 / 供託 {observation.riichi_sticks} ==="
        ),
        f"判断: {_DECISION_LABELS[observation.decision_kind]}",
        f"あなた: {round_seat_label(observation.viewer_seat, observation.dealer_seat)}",
        f"親: {round_seat_label(observation.dealer_seat, observation.dealer_seat)}",
        "点数: "
        + " / ".join(
            f"{round_seat_label(score.seat, observation.dealer_seat)} {score.points}"
            for score in observation.scores
        ),
    ]
    if include_meta:
        lines.extend(
            (
                "ドラ表示牌: "
                + (
                    " ".join(format_tile(tile) for tile in observation.dora_indicators)
                    or "なし"
                ),
                f"残り山: {observation.remaining_live_wall_count}",
            )
        )
    lines.append(
        "立直: "
        + " / ".join(
            f"{round_seat_label(state.seat, observation.dealer_seat)}="
            f"{_RIICHI_LABELS[state.status]}"
            for state in observation.riichi_states
        )
    )

    lines.append("副露:")
    for seat_melds in observation.melds:
        meld_text = " | ".join(format_meld(meld) for meld in seat_melds.melds) or "なし"
        lines.append(
            f"  {round_seat_label(seat_melds.seat, observation.dealer_seat)}: "
            f"{meld_text}"
        )

    lines.extend(_render_rivers(observation))

    if include_hand:
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


def render_discard_menu(
    hand_tiles: tuple[PublicTile, ...],
    hand_numbers: tuple[int | None, ...],
    *,
    tsumogiri_tile: PublicTile | None,
) -> str:
    """通常打牌専用の手牌/番号horizontal menuを表示する。"""
    if len(hand_tiles) != len(hand_numbers):
        raise ValueError("hand_tiles and hand_numbers must have the same length")

    tile_tokens = [format_tile(tile) for tile in hand_tiles]
    number_tokens = [
        str(number) if number is not None else "-" for number in hand_numbers
    ]
    if tsumogiri_tile is not None:
        tile_tokens.extend(("|", format_tile(tsumogiri_tile)))
        number_tokens.extend(("|", "Enter"))
    if not tile_tokens:
        raise ValueError("discard menu must contain at least one tile")

    widths = [
        max(_display_width(tile_token), _display_width(number_token))
        for tile_token, number_token in zip(tile_tokens, number_tokens)
    ]
    tile_line = "手牌: " + " ".join(
        _pad_left(token, width) for token, width in zip(tile_tokens, widths)
    )
    number_line = "番号: " + " ".join(
        _pad_left(token, width) for token, width in zip(number_tokens, widths)
    )
    return "\n".join(("打牌を選んでください:", tile_line, number_line))


def render_action_menu(
    options: Iterable[ActionDescriptor],
    *,
    header: str = "操作を選んでください:",
) -> str:
    values = tuple(options)
    if not values:
        raise ValueError("options must not be empty")
    lines = [header]
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


def _format_yakuman_units(units: int) -> str:
    if units == 1:
        return "役満"
    if units == 2:
        return "ダブル役満"
    return f"{units}倍役満"


def _format_yaku_value(candidate_yaku: object) -> str:
    han = getattr(candidate_yaku, "han")
    if han is not None:
        return f"{han}翻"
    yakuman_units = getattr(candidate_yaku, "yakuman_units")
    assert yakuman_units is not None
    if yakuman_units == 1:
        return "役満"
    return f"{yakuman_units}倍役満"


def _render_score_candidate(candidate: RoundCompletionScoreCandidate) -> list[str]:
    lines = ["役:"]
    for yaku in candidate.yaku:
        lines.append(f"  {yaku.japanese_name}: {_format_yaku_value(yaku)}")

    if candidate.dora_count is not None:
        dora = candidate.dora_count
        lines.extend(
            (
                "ドラ:",
                f"  表ドラ: {dora.visible}",
                f"  裏ドラ: {dora.ura}",
                f"  赤ドラ: {dora.red}",
                f"  槓ドラ: {dora.kan}",
                f"  槓裏ドラ: {dora.kan_ura}",
                (
                    "  合計: "
                    f"{dora.visible + dora.ura + dora.red + dora.kan + dora.kan_ura}翻"
                ),
            )
        )

    if candidate.yakuman_units is not None:
        lines.append(_format_yakuman_units(candidate.yakuman_units))
    else:
        assert candidate.total_han is not None
        assert candidate.rounded_fu is not None
        score_summary = f"{candidate.total_han}翻 {candidate.rounded_fu}符"
        if candidate.score_limit is not ScoreLimit.NONE:
            try:
                limit_label = _SCORE_LIMIT_LABELS[candidate.score_limit]
            except KeyError:
                raise UnsupportedDeliveryItemError(
                    f"unsupported score limit: {candidate.score_limit!r}"
                ) from None
            score_summary += f" / {limit_label}"
        lines.append(score_summary)

    if candidate.ron_payment is not None:
        lines.append(f"手役得点: ロン {candidate.ron_payment}点")
    elif candidate.tsumo_dealer_payment is None:
        assert candidate.tsumo_non_dealer_payment is not None
        lines.append(f"手役得点: ツモ {candidate.tsumo_non_dealer_payment}点オール")
    else:
        assert candidate.tsumo_non_dealer_payment is not None
        lines.append(
            "手役得点: ツモ "
            f"親 {candidate.tsumo_dealer_payment}点 / "
            f"子 {candidate.tsumo_non_dealer_payment}点"
        )
    return lines


def _render_winner_detail(
    fact: RoundCompletionFact,
    winner: RoundCompletionWinner,
) -> list[str]:
    if winner.winning_tile is None:
        return []

    heading = (
        f"和了: {round_seat_label(winner.seat, fact.dealer_seat)} "
        f"{_WIN_METHOD_LABELS[winner.win_method]}"
    )
    if winner.win_method is WinMethod.RON and fact.source_seat is not None:
        heading += f" / 放銃 {round_seat_label(fact.source_seat, fact.dealer_seat)}"

    sorted_hand = tuple(sorted(winner.concealed_tiles, key=tile_sort_key))
    melds = " | ".join(format_meld(meld) for meld in winner.declared_melds) or "なし"
    lines = [
        "",
        heading,
        f"和了牌: {format_tile(winner.winning_tile)}",
        f"和了手牌: {format_tiles(sorted_hand)}",
        f"副露: {melds}",
    ]

    candidate_count = len(winner.max_score_candidates)
    for index, candidate in enumerate(winner.max_score_candidates, start=1):
        lines.append("")
        if candidate_count > 1:
            lines.append(f"最高得点解釈 {index}/{candidate_count}:")
        lines.extend(_render_score_candidate(candidate))
    return lines


def _render_revealed_dora_indicators(fact: RoundCompletionFact) -> list[str]:
    indicators = fact.revealed_dora_indicators
    if indicators is None:
        return []

    entries = (
        ("表ドラ表示牌", indicators.visible),
        ("槓ドラ表示牌", indicators.kan),
        ("裏ドラ表示牌", indicators.ura),
        ("槓裏ドラ表示牌", indicators.kan_ura),
    )
    rendered = [
        f"  {label}: {' '.join(format_tile(tile) for tile in tiles)}"
        for label, tiles in entries
        if tiles
    ]
    if not rendered:
        return []
    return ["", "公開ドラ表示牌:", *rendered]


def _render_round_settlement(fact: RoundCompletionFact) -> list[str]:
    if not fact.settlement_transfers and not fact.riichi_stick_awards:
        return []

    lines = ["", "局精算:"]
    for transfer in fact.settlement_transfers:
        try:
            reason = _TRANSFER_REASON_LABELS[transfer.reason]
        except KeyError:
            raise UnsupportedDeliveryItemError(
                f"unsupported transfer reason: {transfer.reason!r}"
            ) from None
        lines.append(
            "  "
            f"{round_seat_label(transfer.payer, fact.dealer_seat)} → "
            f"{round_seat_label(transfer.recipient, fact.dealer_seat)} "
            f"{transfer.amount}点（{reason}）"
        )
    for award in fact.riichi_stick_awards:
        lines.append(
            "  供託 → "
            f"{round_seat_label(award.recipient, fact.dealer_seat)} "
            f"{award.amount}点"
        )
    return lines


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
        for winner in fact.winners:
            lines.extend(_render_winner_detail(fact, winner))
        lines.extend(_render_revealed_dora_indicators(fact))
        lines.extend(_render_round_settlement(fact))
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
