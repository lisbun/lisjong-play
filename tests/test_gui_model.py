import unittest
from dataclasses import replace

from lisjong_engine.action_descriptor import (
    DiscardActionDescriptor,
    PassActionDescriptor,
    RiichiActionDescriptor,
)
from lisjong_engine.observation import ObservationDecisionKind
from lisjong_engine.public_state import (
    PublicDiscard,
    PublicMeld,
    PublicMeldType,
    PublicRiichiStatus,
    SeatDiscards,
    SeatMelds,
)
from lisjong_engine.seat import Seat
from lisjong_engine.tile import TileCategory

from lisjong_play.gui_model import build_gui_action_views, build_gui_board_view
from tests._fixtures import observation, tile


class GuiBoardViewTest(unittest.TestCase):
    def test_projects_viewer_relative_table_from_seat_observation(self) -> None:
        discards = tuple(
            SeatDiscards(
                seat,
                (
                    PublicDiscard(
                        tile=tile(rank=index),
                        is_tsumogiri=seat is Seat.SOUTH,
                        order=4 - index,
                        is_riichi_declaration=seat is Seat.WEST,
                        called_by=Seat.NORTH if seat is Seat.EAST else None,
                    ),
                ),
            )
            for index, seat in enumerate(Seat, start=1)
        )
        value = build_gui_board_view(
            observation(
                viewer_seat=Seat.SOUTH,
                dealer_seat=Seat.WEST,
                self_riichi=PublicRiichiStatus.ESTABLISHED,
                drawn=tile(TileCategory.PINZU, 5, red=True),
                hand_tiles=(
                    tile(TileCategory.SOUZU, 9),
                    tile(TileCategory.MANZU, 1),
                    tile(TileCategory.PINZU, 5, red=True),
                ),
                discards=discards,
            )
        )

        seats = {seat.position: seat for seat in value.seats}
        self.assertEqual("P2（北家）", seats["bottom"].label)
        self.assertEqual("P3（東家）", seats["right"].label)
        self.assertEqual("P4（南家）", seats["top"].label)
        self.assertEqual("P1（西家）", seats["left"].label)
        self.assertEqual(25_000, seats["bottom"].score)
        self.assertEqual("立直", seats["bottom"].riichi)
        self.assertEqual(("1m→P4",), seats["left"].river)
        self.assertEqual(("2m*",), seats["bottom"].river)
        self.assertEqual(("[3m]",), seats["right"].river)
        self.assertEqual("東1局 0本場", value.round_label)
        self.assertEqual("自摸番", value.decision_label)
        self.assertEqual(("3p",), value.dora_indicators)
        self.assertEqual(("1m", "9s"), value.hand_tiles)
        self.assertEqual("5pr", value.drawn_tile)

    def test_projects_public_meld_without_hidden_state(self) -> None:
        base = observation()
        one = tile(rank=1)
        pon = PublicMeld(
            PublicMeldType.PON,
            (one, one, one),
            Seat.SOUTH,
            one,
        )
        with_meld = replace(
            base,
            melds=tuple(
                SeatMelds(seat, (pon,) if seat is Seat.EAST else ()) for seat in Seat
            ),
        )

        value = build_gui_board_view(with_meld)

        bottom = next(seat for seat in value.seats if seat.position == "bottom")
        self.assertEqual(("ポン / 1m 1m 1m / from P2 / called 1m",), bottom.melds)

    def test_reaction_does_not_invent_a_drawn_tile(self) -> None:
        value = build_gui_board_view(
            observation(decision_kind=ObservationDecisionKind.DISCARD_REACTION)
        )
        self.assertIsNone(value.drawn_tile)


class GuiActionViewTest(unittest.TestCase):
    def test_preserves_option_indices_and_distinguishes_shortcuts(self) -> None:
        one = tile(rank=1)
        red = tile(TileCategory.MANZU, 5, red=True)
        actions = (
            RiichiActionDescriptor(),
            DiscardActionDescriptor(one, False),
            DiscardActionDescriptor(red, True),
            PassActionDescriptor(one, Seat.SOUTH),
        )

        views = build_gui_action_views(actions)

        self.assertEqual((0, 1, 2, 3), tuple(value.option_index for value in views))
        self.assertEqual(
            ("action", "discard", "tsumogiri", "pass"),
            tuple(value.style for value in views),
        )
        self.assertEqual((None, "1m", "5mr", None), tuple(v.tile_label for v in views))

    def test_unknown_action_fails_closed(self) -> None:
        with self.assertRaises(TypeError):
            build_gui_action_views((object(),))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
