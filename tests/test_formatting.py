import unittest

from lisjong_engine.action_descriptor import (
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
from lisjong_engine.seat import Seat
from lisjong_engine.tile import TileCategory

from lisjong_play.formatting import (
    UnsupportedActionDescriptorError,
    format_action_descriptor,
    format_seat,
    format_tile,
)
from tests._fixtures import tile


class ActionFormattingTest(unittest.TestCase):
    def test_all_current_descriptor_variants_are_rendered(self) -> None:
        one = tile()
        two = tile(rank=2)
        three = tile(rank=3)
        four = tile(rank=4)
        variants = (
            DiscardActionDescriptor(one, False),
            RiichiActionDescriptor(),
            ChiActionDescriptor(three, (one, two), Seat.NORTH),
            PonActionDescriptor(one, (one, one), Seat.SOUTH),
            DaiminkanActionDescriptor(one, (one, one, one), Seat.WEST),
            AnkanActionDescriptor((four, four, four, four)),
            KakanActionDescriptor(one),
            RonActionDescriptor(one, Seat.NORTH),
            TsumoActionDescriptor(one),
            PassActionDescriptor(one, Seat.SOUTH),
            NineTerminalsActionDescriptor(),
        )
        self.assertEqual(11, len(variants))
        for action in variants:
            with self.subTest(action=type(action).__name__):
                rendered = format_action_descriptor(action)
                self.assertIsInstance(rendered, str)
                self.assertTrue(rendered)

    def test_fixed_seats_use_non_wind_labels(self) -> None:
        self.assertEqual(
            ["P1", "P2", "P3", "P4"],
            [format_seat(seat) for seat in Seat],
        )

    def test_unknown_descriptor_fails_closed(self) -> None:
        with self.assertRaises(UnsupportedActionDescriptorError):
            format_action_descriptor(object())  # type: ignore[arg-type]

    def test_red_five_is_visibly_distinct(self) -> None:
        self.assertEqual("5mr", format_tile(tile(TileCategory.MANZU, 5, red=True)))


if __name__ == "__main__":
    unittest.main()
