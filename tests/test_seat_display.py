import unittest

from lisjong_engine.seat import Seat

from lisjong_play.seat_display import fixed_seat_name, round_seat_label, seat_wind_label


class SeatDisplayTest(unittest.TestCase):
    def test_fixed_seat_names_are_stable(self) -> None:
        self.assertEqual(
            ["P1", "P2", "P3", "P4"],
            [fixed_seat_name(seat) for seat in Seat],
        )

    def test_each_fixed_seat_can_be_dealer(self) -> None:
        expected = ["東家", "南家", "西家", "北家"]
        seats = tuple(Seat)
        for dealer_index, dealer in enumerate(seats):
            with self.subTest(dealer=dealer):
                actual = [seat_wind_label(seat, dealer) for seat in seats]
                rotated = expected[-dealer_index:] + expected[:-dealer_index]
                self.assertEqual(rotated, actual)
                self.assertEqual(
                    f"{fixed_seat_name(dealer)}（東家）",
                    round_seat_label(dealer, dealer),
                )


if __name__ == "__main__":
    unittest.main()
