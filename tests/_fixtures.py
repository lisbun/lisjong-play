from lisjong_engine.observation import ObservationDecisionKind, SeatObservation
from lisjong_engine.public_state import (
    PublicRiichiStatus,
    PublicTile,
    SeatDiscards,
    SeatMelds,
    SeatRiichiState,
    SeatScore,
)
from lisjong_engine.seat import Seat
from lisjong_engine.tile import TileCategory, TileType
from lisjong_engine.wind import Wind


def tile(
    category: TileCategory = TileCategory.MANZU,
    rank: int = 1,
    *,
    red: bool = False,
) -> PublicTile:
    return PublicTile(TileType(category, rank), red)


def observation(
    *,
    decision_kind: ObservationDecisionKind = ObservationDecisionKind.TURN,
    self_riichi: PublicRiichiStatus = PublicRiichiStatus.NONE,
    drawn: PublicTile | None = None,
) -> SeatObservation:
    hand_tile = drawn or tile()
    drawn_tile = (
        None
        if decision_kind
        in {
            ObservationDecisionKind.DISCARD_REACTION,
            ObservationDecisionKind.KAKAN_REACTION,
            ObservationDecisionKind.ANKAN_REACTION,
        }
        else drawn
    )
    return SeatObservation(
        viewer_seat=Seat.EAST,
        decision_kind=decision_kind,
        hand_number=1,
        honba=0,
        riichi_sticks=0,
        hand_tiles=(hand_tile,),
        drawn_tile=drawn_tile,
        discards=tuple(SeatDiscards(seat, ()) for seat in Seat),
        melds=tuple(SeatMelds(seat, ()) for seat in Seat),
        dora_indicators=(tile(TileCategory.PINZU, 3),),
        remaining_live_wall_count=60,
        scores=tuple(SeatScore(seat, 25_000) for seat in Seat),
        dealer_seat=Seat.EAST,
        prevailing_wind=Wind.EAST,
        riichi_states=tuple(
            SeatRiichiState(
                seat,
                self_riichi if seat is Seat.EAST else PublicRiichiStatus.NONE,
            )
            for seat in Seat
        ),
    )
