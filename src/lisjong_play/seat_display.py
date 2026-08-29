"""固定seatと局内自風をHuman-facing表示へ分離するhelper。"""

from lisjong_engine.seat import Seat

SEAT_ORDER = tuple(Seat)

_FIXED_NAMES = {
    Seat.EAST: "P1",
    Seat.SOUTH: "P2",
    Seat.WEST: "P3",
    Seat.NORTH: "P4",
}
_WIND_LABELS = ("東家", "南家", "西家", "北家")


def fixed_seat_name(seat: Seat) -> str:
    """半荘中に変わらないfixed engine seatの表示名を返す。"""
    if not isinstance(seat, Seat):
        raise TypeError("seat must be a Seat")
    return _FIXED_NAMES[seat]


def seat_wind_label(seat: Seat, dealer_seat: Seat) -> str:
    """current dealerを東家として、fixed seatから局内自風を導出する。"""
    if not isinstance(seat, Seat):
        raise TypeError("seat must be a Seat")
    if not isinstance(dealer_seat, Seat):
        raise TypeError("dealer_seat must be a Seat")
    seat_index = SEAT_ORDER.index(seat)
    dealer_index = SEAT_ORDER.index(dealer_seat)
    return _WIND_LABELS[(seat_index - dealer_index) % len(SEAT_ORDER)]


def round_seat_label(seat: Seat, dealer_seat: Seat) -> str:
    """`P1（東家）`のようにfixed seatと局内自風を併記する。"""
    return f"{fixed_seat_name(seat)}（{seat_wind_label(seat, dealer_seat)}）"
