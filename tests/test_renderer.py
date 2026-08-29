import unicodedata
import unittest

from lisjong_engine.match_state import MatchEndReason
from lisjong_engine.observation import ObservationDecisionKind
from lisjong_engine.public_state import (
    PublicDiscard,
    PublicTile,
    SeatDiscards,
    SeatPointDelta,
    SeatScore,
)
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
    RIVER_LEGEND,
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


def _display_width(text: str) -> int:
    return sum(
        2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text
    )


def _seat_discards(
    entries: list[tuple[Seat, PublicTile, bool, bool, Seat | None]],
) -> tuple[SeatDiscards, ...]:
    by_seat: dict[Seat, list[PublicDiscard]] = {seat: [] for seat in Seat}
    for order, (seat, public_tile, tsumogiri, riichi, called_by) in enumerate(entries):
        by_seat[seat].append(
            PublicDiscard(
                tile=public_tile,
                is_tsumogiri=tsumogiri,
                order=order,
                is_riichi_declaration=riichi,
                called_by=called_by,
            )
        )
    return tuple(SeatDiscards(seat, tuple(by_seat[seat])) for seat in Seat)


class RendererTest(unittest.TestCase):
    def test_board_contains_minimum_player_safe_fields(self) -> None:
        text = render_board(observation(drawn=tile(rank=4)))
        for label in (
            "東1局",
            "供託",
            "あなた",
            "親",
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

    def test_board_shows_viewer_and_dealer_as_round_relative_seats(self) -> None:
        text = render_board(observation(viewer_seat=Seat.SOUTH, dealer_seat=Seat.WEST))
        self.assertIn("あなた: P2（北家）", text)
        self.assertIn("親: P3（東家）", text)
        self.assertIn("P1（西家） 25000", text)
        self.assertIn("P4（南家） 25000", text)

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

    def test_rivers_follow_public_order_and_leave_skipped_seat_cell(self) -> None:
        discards = _seat_discards(
            [
                (Seat.EAST, tile(rank=1), False, False, None),
                (Seat.WEST, tile(rank=2), False, False, None),
                (Seat.NORTH, tile(rank=3), False, False, None),
                (Seat.EAST, tile(rank=4), False, False, None),
                (Seat.SOUTH, tile(rank=5), False, False, None),
            ]
        )
        text = render_board(observation(discards=discards))
        south_line = next(
            line for line in text.splitlines() if line.startswith("  P2（南家）:")
        )
        river = south_line.split(": ", 1)[1]
        self.assertGreater(_display_width(river[: river.index("5m")]), 0)
        self.assertEqual(11, _display_width(river[: river.index("5m")]))

    def test_river_wraps_after_six_columns(self) -> None:
        entries = []
        order_tile_rank = 1
        for _ in range(7):
            for seat in Seat:
                entries.append(
                    (
                        seat,
                        tile(rank=((order_tile_rank - 1) % 9) + 1),
                        False,
                        False,
                        None,
                    )
                )
                order_tile_rank += 1
        text = render_board(observation(discards=_seat_discards(entries)))
        lines = text.splitlines()
        east_index = next(
            i for i, line in enumerate(lines) if line.startswith("  P1（東家）:")
        )
        self.assertTrue(lines[east_index + 1].startswith(" " * 14))
        self.assertIn("7", lines[east_index + 1])

    def test_river_fixed_cells_keep_public_markers_and_called_discard(self) -> None:
        red_five = tile(TileCategory.PINZU, 5, red=True)
        honor = tile(TileCategory.HONOR, 1)
        discards = _seat_discards(
            [
                (Seat.EAST, red_five, True, True, Seat.SOUTH),
                (Seat.SOUTH, honor, False, False, None),
            ]
        )
        text = render_board(observation(discards=discards))
        east_line = next(
            line for line in text.splitlines() if line.startswith("  P1（東家）:")
        )
        south_line = next(
            line for line in text.splitlines() if line.startswith("  P2（南家）:")
        )
        self.assertIn("[5pr*]→P2", east_line)
        self.assertIn("東", south_line)
        self.assertNotIn(RIVER_LEGEND, text)

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
        self.assertNotIn(RIVER_LEGEND, text)

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
