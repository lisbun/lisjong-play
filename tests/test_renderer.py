import unittest

from lisjong_engine.match_state import MatchEndReason
from lisjong_engine.observation import ObservationDecisionKind
from lisjong_engine.public_state import SeatPointDelta, SeatScore
from lisjong_engine.round_completion import (
    MatchCompletionFact,
    RoundCompletionFact,
    RoundOutcomeKind,
    SeatFinalResult,
)
from lisjong_engine.round_progress import DiscardProgress, RiichiEstablishedProgress
from lisjong_engine.seat import Seat
from lisjong_engine.tile import TileCategory
from lisjong_engine.wind import Wind

from lisjong_play.renderer import (
    DeliveryPresenter,
    render_board,
    render_discard_menu,
    render_reaction_board,
)
from tests._fixtures import observation, tile


def round_fact(*, has_next_round: bool) -> RoundCompletionFact:
    return RoundCompletionFact(
        prevailing_wind=Wind.EAST,
        hand_number=1,
        dealer_seat=Seat.EAST,
        honba=0,
        outcome=RoundOutcomeKind.EXHAUSTIVE_DRAW,
        tenpai_seats=(Seat.EAST,),
        point_deltas=tuple(SeatPointDelta(seat, 0) for seat in Seat),
        scores_after=tuple(SeatScore(seat, 25_000) for seat in Seat),
        dealer_continues=True,
        has_next_round=has_next_round,
    )


def match_fact() -> MatchCompletionFact:
    return MatchCompletionFact(
        end_reason=MatchEndReason.FINAL_ROUND,
        final_scores=tuple(SeatScore(seat, 25_000) for seat in Seat),
        final_results=tuple(
            SeatFinalResult(seat=seat, rank=index, final_points=0)
            for index, seat in enumerate(Seat, start=1)
        ),
    )


class RendererTest(unittest.TestCase):
    def test_board_contains_minimum_player_safe_fields(self) -> None:
        text = render_board(observation(drawn=tile(rank=4)))
        for label in (
            "東1局",
            "供託",
            "点数",
            "ドラ表示牌",
            "立直",
            "副露",
            "河",
            "手牌",
            "ツモ",
        ):
            with self.subTest(label=label):
                self.assertIn(label, text)

    def test_discard_menu_aligns_numbers_with_honor_and_red_tiles(self) -> None:
        text = render_discard_menu(
            (
                tile(TileCategory.MANZU, 1),
                tile(TileCategory.HONOR, 1),
                tile(TileCategory.PINZU, 5, red=True),
            ),
            (1, 2, 3),
            tsumogiri_tile=tile(TileCategory.SOUZU, 7),
        )

        lines = text.splitlines()
        self.assertEqual("打牌を選んでください:", lines[0])
        self.assertEqual("手牌: 1m 東 5pr |    7s", lines[1])
        self.assertEqual("番号:  1  2   3 | Enter", lines[2])

    def test_reaction_board_omits_turn_meta_but_keeps_public_decision_state(
        self,
    ) -> None:
        text = render_reaction_board(
            observation(decision_kind=ObservationDecisionKind.DISCARD_REACTION)
        )

        for label in ("判断: 打牌への反応", "点数", "立直", "副露", "河", "手牌"):
            with self.subTest(label=label):
                self.assertIn(label, text)
        self.assertNotIn("ドラ表示牌", text)
        self.assertNotIn("残り山", text)

    def test_progress_batch_is_displayed_in_given_order(self) -> None:
        output: list[str] = []
        presenter = DeliveryPresenter(lambda _: "", output.append)
        presenter(
            (
                DiscardProgress(Seat.SOUTH, tile(rank=3), False),
                RiichiEstablishedProgress(Seat.SOUTH),
            )
        )
        self.assertIn("打", output[0])
        self.assertIn("立直成立", output[1])

    def test_round_result_is_printed_before_enter_confirmation(self) -> None:
        events: list[str] = []

        def read(prompt: str) -> str:
            events.append(f"INPUT:{prompt}")
            return ""

        presenter = DeliveryPresenter(read, lambda text: events.append(f"OUT:{text}"))
        presenter((round_fact(has_next_round=True),))

        result_index = next(
            i
            for i, item in enumerate(events)
            if item.startswith("OUT:--- ") and " 終了 ---" in item
        )
        input_index = next(
            i for i, item in enumerate(events) if item.startswith("INPUT:")
        )
        self.assertLess(result_index, input_index)

    def test_non_empty_round_confirmation_retries(self) -> None:
        values = iter(["x", ""])
        output: list[str] = []
        presenter = DeliveryPresenter(lambda _: next(values), output.append)
        presenter((round_fact(has_next_round=True),))
        self.assertTrue(any("Enterのみ" in line for line in output))

    def test_terminal_round_does_not_prompt_for_next_round(self) -> None:
        calls = 0

        def fail_if_called(_: str) -> str:
            nonlocal calls
            calls += 1
            raise AssertionError("terminal delivery must not ask for next round")

        output: list[str] = []
        presenter = DeliveryPresenter(fail_if_called, output.append)
        presenter((round_fact(has_next_round=False), match_fact()))
        self.assertEqual(0, calls)
        self.assertTrue(any("半荘終了" in line for line in output))

    def test_round_confirmation_keyboard_interrupt_propagates(self) -> None:
        def interrupt(_: str) -> str:
            raise KeyboardInterrupt

        presenter = DeliveryPresenter(interrupt, lambda _: None)
        with self.assertRaises(KeyboardInterrupt):
            presenter((round_fact(has_next_round=True),))


if __name__ == "__main__":
    unittest.main()
