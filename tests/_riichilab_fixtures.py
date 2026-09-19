"""RiichiLab live source test用の、小さいsynthetic presentation fixture。

real RiichiLab networkへは接続せず、Arenaのsupported presentation value
(`RankedDecisionPresentation` 等)だけを組み立てる。`PolicyInput`はReplay
fixtureと同じlisjong public value classから作る。
"""

from lisjong.policy_contract import (
    Discard,
    DiscardAction,
    MeldKind,
    OwnHandState,
    PlayerPublicState,
    PolicyInput,
    PublicMeld,
    RiichiState,
    RoundState,
    Seat,
    Tile,
    TileCategory,
    TileType,
    Wind,
)
from lisjong_arena.riichilab.live_presentation import (
    RankedCompletionPresentation,
    RankedDecisionPresentation,
    RankedFailurePresentation,
    RankedPresentationBatch,
)

FINAL_SCORES = (32000, 24000, 23000, 21000)


def tile(category: TileCategory, rank: int, *, is_red: bool = False) -> Tile:
    return Tile(tile_type=TileType(category=category, rank=rank), is_red=is_red)


def _players(*, discards_for_seat_0: int = 2) -> tuple[PlayerPublicState, ...]:
    river = tuple(
        Discard(
            tile=tile(TileCategory.MANZU, index + 1),
            tsumogiri=index % 2 == 1,
            order=index,
            called_by=Seat.SEAT_2 if index == 0 else None,
        )
        for index in range(discards_for_seat_0)
    )
    melds = (
        PublicMeld(
            kind=MeldKind.PON,
            tiles=(
                tile(TileCategory.PINZU, 2),
                tile(TileCategory.PINZU, 2),
                tile(TileCategory.PINZU, 2),
            ),
            from_seat=Seat.SEAT_3,
            called_tile=tile(TileCategory.PINZU, 2),
        ),
    )
    return (
        PlayerPublicState(
            score=26000, discards=river, melds=(), riichi=RiichiState.NONE
        ),
        PlayerPublicState(
            score=26000, discards=(), melds=melds, riichi=RiichiState.ACCEPTED
        ),
        PlayerPublicState(
            score=24000, discards=(), melds=(), riichi=RiichiState.DECLARED
        ),
        PlayerPublicState(score=24000, discards=(), melds=(), riichi=RiichiState.NONE),
    )


def policy_input(
    *, seat: Seat = Seat.SEAT_1, wall: int = 60, discards: int = 2
) -> PolicyInput:
    """bound bot seat自身のplayer-visible `PolicyInput`。"""
    concealed = tuple(
        tile(TileCategory.SOUZU, rank, is_red=rank == 5) for rank in range(1, 10)
    ) + (
        tile(TileCategory.HONOR, 1),
        tile(TileCategory.HONOR, 5),
        tile(TileCategory.PINZU, 4),
        tile(TileCategory.PINZU, 4),
        tile(TileCategory.PINZU, 7),
    )
    return PolicyInput(
        self_seat=seat,
        round=RoundState(
            round_wind=Wind.EAST,
            hand_number=1,
            dealer_seat=Seat.SEAT_0,
            honba=0,
            riichi_sticks=1,
            dora_indicators=(tile(TileCategory.MANZU, 5),),
            live_wall_tiles_remaining=wall,
        ),
        players=_players(discards_for_seat_0=discards),
        own_hand=OwnHandState(
            concealed_tiles=concealed, drawn_tile=tile(TileCategory.PINZU, 7)
        ),
    )


def decision(
    *, request_id: int = 1, seat: Seat = Seat.SEAT_1, wall: int = 60
) -> RankedDecisionPresentation:
    """Arenaが渡す1 decision分のpresentation fact。"""
    return RankedDecisionPresentation(
        request_id=request_id,
        self_seat=seat,
        policy_input=policy_input(seat=seat, wall=wall),
        selected_action=DiscardAction(
            actor=seat, tile=tile(TileCategory.HONOR, 1), tsumogiri=False
        ),
    )


def completion(
    *, seat: Seat = Seat.SEAT_1, scores: tuple[int, int, int, int] | None = FINAL_SCORES
) -> RankedCompletionPresentation:
    return RankedCompletionPresentation(self_seat=seat, scores=scores)


def failure(*, failure_type: str = "UnexpectedDisconnectError"):
    return RankedFailurePresentation(failure_type=failure_type)


def batch(
    *,
    decisions=(),
    completion_fact: RankedCompletionPresentation | None = None,
    failure_fact: RankedFailurePresentation | None = None,
    coalesced: int = 0,
) -> RankedPresentationBatch:
    return RankedPresentationBatch(
        decisions=tuple(decisions),
        completion=completion_fact,
        failure=failure_fact,
        coalesced_decisions=coalesced,
    )
