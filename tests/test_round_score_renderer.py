import unittest

from lisjong_engine.public_state import (
    PublicMeld,
    PublicMeldType,
    SeatPointDelta,
    SeatScore,
)
from lisjong_engine.round_completion import (
    RoundCompletionDoraCount,
    RoundCompletionDoraIndicators,
    RoundCompletionFact,
    RoundCompletionRiichiStickAward,
    RoundCompletionScoreCandidate,
    RoundCompletionSettlementTransfer,
    RoundCompletionWinner,
    RoundCompletionYaku,
    RoundOutcomeKind,
)
from lisjong_engine.score import ScoreLimit
from lisjong_engine.seat import Seat
from lisjong_engine.settlement import TransferReason
from lisjong_engine.tile import TileCategory
from lisjong_engine.win_context import WinMethod
from lisjong_engine.wind import Wind
from lisjong_engine.yaku import Yaku

from lisjong_play.renderer import render_round_completion
from tests._fixtures import tile


def _normal_candidate(
    *,
    yaku: tuple[RoundCompletionYaku, ...] | None = None,
    dora_count: RoundCompletionDoraCount | None = None,
    total_han: int = 2,
    rounded_fu: int = 30,
    score_limit: ScoreLimit = ScoreLimit.NONE,
    ron_payment: int | None = 2_000,
    tsumo_dealer_payment: int | None = None,
    tsumo_non_dealer_payment: int | None = None,
) -> RoundCompletionScoreCandidate:
    return RoundCompletionScoreCandidate(
        yaku=(
            yaku
            if yaku is not None
            else (
                RoundCompletionYaku(Yaku.RIICHI, "立直", han=1),
                RoundCompletionYaku(Yaku.PINFU, "平和", han=1),
            )
        ),
        total_han=total_han,
        rounded_fu=rounded_fu,
        yakuman_units=None,
        dora_count=dora_count,
        score_limit=score_limit,
        ron_payment=ron_payment,
        tsumo_dealer_payment=tsumo_dealer_payment,
        tsumo_non_dealer_payment=tsumo_non_dealer_payment,
    )


def _winner(
    *,
    seat: Seat = Seat.EAST,
    win_method: WinMethod = WinMethod.RON,
    candidates: tuple[RoundCompletionScoreCandidate, ...] | None = None,
    with_meld: bool = False,
) -> RoundCompletionWinner:
    winning_tile = tile(TileCategory.PINZU, 5)
    melds = ()
    if with_meld:
        nine_sou = tile(TileCategory.SOUZU, 9)
        melds = (
            PublicMeld(
                PublicMeldType.ANKAN,
                (nine_sou, nine_sou, nine_sou, nine_sou),
                None,
                None,
            ),
        )
    return RoundCompletionWinner(
        seat=seat,
        win_method=win_method,
        winning_tile=winning_tile,
        concealed_tiles=(
            tile(TileCategory.PINZU, 2),
            tile(TileCategory.PINZU, 3),
            tile(TileCategory.PINZU, 4),
            winning_tile,
        ),
        declared_melds=melds,
        max_score_candidates=(candidates or (_normal_candidate(),)),
    )


def _win_fact(
    *,
    winners: tuple[RoundCompletionWinner, ...],
    source_seat: Seat | None = Seat.SOUTH,
    honba: int = 0,
    indicators: RoundCompletionDoraIndicators | None = None,
    transfers: tuple[RoundCompletionSettlementTransfer, ...] = (),
    awards: tuple[RoundCompletionRiichiStickAward, ...] = (),
) -> RoundCompletionFact:
    return RoundCompletionFact(
        prevailing_wind=Wind.EAST,
        hand_number=1,
        dealer_seat=Seat.EAST,
        honba=honba,
        outcome=RoundOutcomeKind.WIN,
        winners=winners,
        source_seat=source_seat,
        revealed_dora_indicators=indicators,
        settlement_transfers=transfers,
        riichi_stick_awards=awards,
        point_deltas=tuple(SeatPointDelta(seat, 0) for seat in Seat),
        scores_after=tuple(SeatScore(seat, 25_000) for seat in Seat),
        dealer_continues=True,
        has_next_round=True,
    )


class RoundScoreRendererTest(unittest.TestCase):
    def test_ron_shows_hand_yaku_dora_limit_payment_and_settlement(self) -> None:
        candidate = _normal_candidate(
            dora_count=RoundCompletionDoraCount(
                visible=2,
                ura=2,
                red=1,
                kan=1,
                kan_ura=0,
            ),
            total_han=8,
            score_limit=ScoreLimit.BAIMAN,
            ron_payment=24_000,
        )
        winner = _winner(candidates=(candidate,), with_meld=True)
        fact = _win_fact(
            winners=(winner,),
            honba=1,
            indicators=RoundCompletionDoraIndicators(
                visible=(tile(TileCategory.PINZU, 4),),
                kan=(tile(TileCategory.SOUZU, 8),),
                ura=(tile(TileCategory.PINZU, 2),),
                kan_ura=(tile(TileCategory.MANZU, 7),),
            ),
            transfers=(
                RoundCompletionSettlementTransfer(
                    Seat.SOUTH,
                    Seat.EAST,
                    24_000,
                    TransferReason.RON,
                    Seat.EAST,
                ),
                RoundCompletionSettlementTransfer(
                    Seat.SOUTH,
                    Seat.EAST,
                    300,
                    TransferReason.HONBA,
                    Seat.EAST,
                ),
            ),
            awards=(RoundCompletionRiichiStickAward(Seat.EAST, 1_000),),
        )

        text = render_round_completion(fact)

        for expected in (
            "和了: P1（東家） ロン / 放銃 P2（南家）",
            "和了牌: 5p",
            "和了手牌:",
            "副露:",
            "立直: 1翻",
            "平和: 1翻",
            "表ドラ: 2",
            "裏ドラ: 2",
            "赤ドラ: 1",
            "槓ドラ: 1",
            "槓裏ドラ: 0",
            "8翻 30符 / 倍満",
            "手役得点: ロン 24000点",
            "表ドラ表示牌: 4p",
            "槓ドラ表示牌: 8s",
            "裏ドラ表示牌: 2p",
            "槓裏ドラ表示牌: 7m",
            "局精算:",
            "24000点（ロン）",
            "300点（本場）",
            "供託 → P1（東家） 1000点",
            "点数移動:",
            "局後点数:",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_non_riichi_win_does_not_render_unrevealed_ura_indicators(self) -> None:
        candidate = _normal_candidate(
            yaku=(RoundCompletionYaku(Yaku.TANYAO, "断么九", han=1),),
            dora_count=RoundCompletionDoraCount(visible=1),
            total_han=2,
            ron_payment=2_000,
        )
        fact = _win_fact(
            winners=(_winner(candidates=(candidate,)),),
            indicators=RoundCompletionDoraIndicators(
                visible=(tile(TileCategory.PINZU, 4),),
            ),
        )

        text = render_round_completion(fact)

        self.assertIn("表ドラ表示牌: 4p", text)
        self.assertNotIn("裏ドラ表示牌", text)
        self.assertNotIn("槓裏ドラ表示牌", text)

    def test_non_dealer_tsumo_shows_dealer_and_child_payments(self) -> None:
        candidate = _normal_candidate(
            yaku=(RoundCompletionYaku(Yaku.MENZEN_TSUMO, "門前清自摸和", han=1),),
            total_han=1,
            rounded_fu=30,
            ron_payment=None,
            tsumo_dealer_payment=1_000,
            tsumo_non_dealer_payment=500,
        )
        winner = _winner(
            seat=Seat.SOUTH,
            win_method=WinMethod.TSUMO,
            candidates=(candidate,),
        )
        fact = _win_fact(winners=(winner,), source_seat=None)

        text = render_round_completion(fact)

        self.assertIn("和了: P2（南家） ツモ", text)
        self.assertIn("手役得点: ツモ 親 1000点 / 子 500点", text)
        self.assertNotIn("放銃", text)

    def test_double_yakuman_is_not_converted_to_han(self) -> None:
        candidate = RoundCompletionScoreCandidate(
            yaku=(
                RoundCompletionYaku(
                    Yaku.SUUANKOU_TANKI,
                    "四暗刻単騎",
                    yakuman_units=2,
                ),
            ),
            total_han=None,
            rounded_fu=None,
            yakuman_units=2,
            dora_count=None,
            score_limit=ScoreLimit.YAKUMAN,
            ron_payment=96_000,
            tsumo_dealer_payment=None,
            tsumo_non_dealer_payment=None,
        )
        fact = _win_fact(winners=(_winner(candidates=(candidate,)),))

        text = render_round_completion(fact)

        self.assertIn("四暗刻単騎: 2倍役満", text)
        self.assertIn("ダブル役満", text)
        self.assertNotIn("翻 / 役満", text)

    def test_equal_max_score_candidates_are_all_rendered(self) -> None:
        first = _normal_candidate()
        second = _normal_candidate(
            yaku=(
                RoundCompletionYaku(Yaku.RIICHI, "立直", han=1),
                RoundCompletionYaku(Yaku.TANYAO, "断么九", han=1),
            ),
        )
        fact = _win_fact(winners=(_winner(candidates=(first, second)),))

        text = render_round_completion(fact)

        self.assertIn("最高得点解釈 1/2:", text)
        self.assertIn("最高得点解釈 2/2:", text)
        self.assertIn("平和: 1翻", text)
        self.assertIn("断么九: 1翻", text)

    def test_multiple_ron_keeps_winner_sections_separate(self) -> None:
        first = _winner(seat=Seat.EAST)
        second = _winner(seat=Seat.WEST)
        fact = _win_fact(winners=(first, second))

        text = render_round_completion(fact)

        self.assertIn("和了: P1（東家） ロン / 放銃 P2（南家）", text)
        self.assertIn("和了: P3（西家） ロン / 放銃 P2（南家）", text)
        self.assertEqual(2, text.count("和了牌: 5p"))


if __name__ == "__main__":
    unittest.main()
