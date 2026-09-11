"""Replay Viewer test用の、小さいdurable local game record fixture。

実RiichiEnv対局を毎回実行せず、Arena / lisjongのpublic value classだけで
2局分のcompleted inspectionを組み立て、Arena-supported strict loaderで
readbackできる実bundleを生成する。

fixtureは意図的に「1 environment step内に前局のryukyoku / end_kyokuと次局の
start_kyokuが同居する」RiichiEnvの実挙動を再現し、step境界とround境界を
同一視していないことをtestできるようにしている。

durable record schema v2では、Arena strict loaderがtyped ``round_results``と
objective ``GameTrace``のsame-run bindingを検証する。そのためここで作る
``RoundResult``は、``_events()``のstart_kyoku / hora / ryukyokuが持つvalueと
完全に一致していなければならない。
"""

import json

from lisjong.policy_contract import (
    DecisionTrace,
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
from lisjong_arena.durable_local_game_record import save_local_game_record
from lisjong_arena.game_trace import GameTrace, GameTraceEvent
from lisjong_arena.riichienv.local_game_runner import (
    LocalGameInspection,
    LocalGameResult,
    SeatDecisionObservation,
    StepDecisionObservation,
)
from lisjong_arena.riichienv.round_result import (
    RoundDrawFact,
    RoundResult,
    RoundWinFact,
    RoundWinScoring,
    RoundYaku,
)
from lisjong_arena.riichienv.round_stats import SeatRoundStats
from lisjong_arena.single_round_artifact import SingleRoundExecutionProvenance

SEED = 4242
GAME_MODE = "4p-red-half"
FINAL_SCORES = (27000, 31000, 21000, 21000)
FINAL_RANKS = (2, 1, 3, 4)
ROUND_ONE_START_SCORES = (25000, 25000, 25000, 25000)
ROUND_TWO_START_SCORES = (26000, 26000, 24000, 24000)
DRAW_DELTAS = (1000, 1000, -1000, -1000)
WIN_DELTAS = (1000, 5000, -3000, -3000)

_HEX = "0123456789abcdef"
_FAKE_REVISION = (_HEX * 3)[:40]


def tile(category: TileCategory, rank: int, *, is_red: bool = False) -> Tile:
    return Tile(tile_type=TileType(category=category, rank=rank), is_red=is_red)


def provenance() -> SingleRoundExecutionProvenance:
    """VCS metadataへ依存しない、test専用の固定provenance。"""
    return SingleRoundExecutionProvenance(
        execution_environment="riichienv",
        lisjong_arena_version="0.1.0",
        lisjong_arena_revision=_FAKE_REVISION,
        lisjong_version="0.1.0",
        lisjong_revision=_FAKE_REVISION,
        lisjong_engine_version="0.1.0",
        lisjong_engine_revision=_FAKE_REVISION,
        riichienv_version="0.4.8",
        python_version="3.14.0",
    )


def _events() -> tuple[GameTraceEvent, ...]:
    payloads = [
        {"type": "start_game"},
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "kyoku": 1,
            "honba": 0,
            "oya": 0,
            "kyotaku": 0,
            "dora_marker": "5m",
            "scores": list(ROUND_ONE_START_SCORES),
        },
        {"type": "dahai", "actor": 0, "pai": "1m", "tsumogiri": False},
        {
            "type": "ryukyoku",
            "reason": "exhaustive_draw",
            "deltas": list(DRAW_DELTAS),
        },
        {"type": "end_kyoku"},
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "kyoku": 2,
            "honba": 0,
            "oya": 1,
            "kyotaku": 0,
            "dora_marker": "3p",
            "scores": list(ROUND_TWO_START_SCORES),
        },
        {"type": "dahai", "actor": 1, "pai": "9s", "tsumogiri": True},
        {"type": "reach_accepted", "actor": 1},
        {
            "type": "hora",
            "actor": 1,
            "target": 2,
            "deltas": list(WIN_DELTAS),
            "ura_markers": ["1p"],
        },
        {"type": "end_kyoku"},
        {"type": "end_game"},
    ]
    return tuple(
        GameTraceEvent(sequence=index, event=json.dumps(payload, sort_keys=True))
        for index, payload in enumerate(payloads)
    )


def win_scoring() -> RoundWinScoring:
    """backendがcapture できた局のscoring facts。"""
    return RoundWinScoring(
        han=3,
        fu=40,
        yakuman=False,
        yaku=(
            RoundYaku(yaku_id=2, name="立直", name_en="Riichi"),
            RoundYaku(yaku_id=12, name="断幺九", name_en="Tanyao"),
        ),
        ron_points=5200,
        tsumo_points_oya=0,
        tsumo_points_ko=0,
        pao_payer=None,
    )


def round_results(*, scoring: bool = True) -> tuple[RoundResult, ...]:
    """``_events()``のobjective factと完全に一致するtyped round results。

    ``scoring=False``は、RiichiEnvがbackend scoringを公開しなかった局
    (実runでは非最終局で普通に起きる)を表す。
    """
    return (
        RoundResult(
            round_wind=Wind.EAST,
            hand_number=1,
            honba=0,
            dealer_seat=Seat.SEAT_0,
            riichi_sticks_before=0,
            riichi_sticks_after=0,
            start_scores=ROUND_ONE_START_SCORES,
            end_scores=ROUND_TWO_START_SCORES,
            dora_indicators=(tile(TileCategory.MANZU, 5),),
            riichi_seats=(),
            start_event_sequence=1,
            wins=(),
            draw=RoundDrawFact(
                reason="exhaustive_draw",
                exhaustive=True,
                deltas=DRAW_DELTAS,
                event_sequence=3,
            ),
        ),
        RoundResult(
            round_wind=Wind.EAST,
            hand_number=2,
            honba=0,
            dealer_seat=Seat.SEAT_1,
            riichi_sticks_before=0,
            riichi_sticks_after=0,
            start_scores=ROUND_TWO_START_SCORES,
            end_scores=FINAL_SCORES,
            dora_indicators=(tile(TileCategory.PINZU, 3),),
            riichi_seats=(Seat.SEAT_1,),
            start_event_sequence=5,
            wins=(
                RoundWinFact(
                    winner_seat=Seat.SEAT_1,
                    tsumo=False,
                    loser_seat=Seat.SEAT_2,
                    deltas=WIN_DELTAS,
                    ura_indicators=(tile(TileCategory.PINZU, 1),),
                    event_sequence=8,
                    scoring=win_scoring() if scoring else None,
                ),
            ),
            draw=None,
        ),
    )


def _players(*, discards_for_seat_0: int, melds: bool) -> tuple[PlayerPublicState, ...]:
    river = tuple(
        Discard(
            tile=tile(TileCategory.MANZU, index + 1),
            tsumogiri=index % 2 == 1,
            order=index,
            called_by=Seat.SEAT_2 if index == 0 else None,
        )
        for index in range(discards_for_seat_0)
    )
    meld_values = (
        (
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
        if melds
        else ()
    )
    return (
        PlayerPublicState(
            score=26000, discards=river, melds=(), riichi=RiichiState.NONE
        ),
        PlayerPublicState(
            score=26000, discards=(), melds=meld_values, riichi=RiichiState.ACCEPTED
        ),
        PlayerPublicState(
            score=24000, discards=(), melds=(), riichi=RiichiState.DECLARED
        ),
        PlayerPublicState(score=24000, discards=(), melds=(), riichi=RiichiState.NONE),
    )


def _round_state(*, hand_number: int, dealer: Seat, wall: int) -> RoundState:
    return RoundState(
        round_wind=Wind.EAST,
        hand_number=hand_number,
        dealer_seat=dealer,
        honba=0,
        riichi_sticks=1,
        dora_indicators=(tile(TileCategory.MANZU, 5),),
        live_wall_tiles_remaining=wall,
    )


def _decision(
    *, seat: Seat, hand_number: int, dealer: Seat, wall: int, discards: int, melds: bool
) -> SeatDecisionObservation:
    concealed = tuple(
        tile(TileCategory.SOUZU, rank, is_red=rank == 5) for rank in range(1, 10)
    ) + (
        tile(TileCategory.HONOR, 1),
        tile(TileCategory.HONOR, 5),
        tile(TileCategory.PINZU, 4),
        tile(TileCategory.PINZU, 4),
        tile(TileCategory.PINZU, 7),
    )
    policy_input = PolicyInput(
        self_seat=seat,
        round=_round_state(hand_number=hand_number, dealer=dealer, wall=wall),
        players=_players(discards_for_seat_0=discards, melds=melds),
        own_hand=OwnHandState(
            concealed_tiles=concealed, drawn_tile=tile(TileCategory.PINZU, 7)
        ),
    )
    selected = DiscardAction(
        actor=seat, tile=tile(TileCategory.HONOR, 1), tsumogiri=False
    )
    other = DiscardAction(actor=seat, tile=tile(TileCategory.HONOR, 5), tsumogiri=False)
    return SeatDecisionObservation(
        seat=seat,
        policy_input=policy_input,
        decision_trace=DecisionTrace(
            legal_actions=(selected, other), selected_action=selected
        ),
    )


def inspection(*, scoring: bool = True) -> LocalGameInspection:
    """2局 / 4 decisionのcompleted inspection。

    step 1は`ryukyoku` / `end_kyoku` / 次局`start_kyoku` / `dahai`を1 step内に
    含み、step境界 = round境界という単純化が成り立たないことを固定する。
    各stepのdecisionは、そのstepが発行するeventより前の局に属する。
    """
    steps = (
        StepDecisionObservation(
            step_ordinal=0,
            event_sequence_start=2,
            event_sequence_end=3,
            seat_decisions=(
                _decision(
                    seat=Seat.SEAT_0,
                    hand_number=1,
                    dealer=Seat.SEAT_0,
                    wall=70,
                    discards=0,
                    melds=False,
                ),
            ),
        ),
        StepDecisionObservation(
            step_ordinal=1,
            event_sequence_start=3,
            event_sequence_end=7,
            seat_decisions=(
                _decision(
                    seat=Seat.SEAT_1,
                    hand_number=1,
                    dealer=Seat.SEAT_0,
                    wall=69,
                    discards=1,
                    melds=False,
                ),
            ),
        ),
        StepDecisionObservation(
            step_ordinal=2,
            event_sequence_start=7,
            event_sequence_end=9,
            seat_decisions=(
                _decision(
                    seat=Seat.SEAT_2,
                    hand_number=2,
                    dealer=Seat.SEAT_1,
                    wall=68,
                    discards=2,
                    melds=True,
                ),
            ),
        ),
        StepDecisionObservation(
            step_ordinal=3,
            event_sequence_start=9,
            event_sequence_end=11,
            seat_decisions=(
                _decision(
                    seat=Seat.SEAT_3,
                    hand_number=2,
                    dealer=Seat.SEAT_1,
                    wall=67,
                    discards=3,
                    melds=True,
                ),
            ),
        ),
    )
    stats = tuple(
        SeatRoundStats(
            start_score=25000,
            end_score=FINAL_SCORES[seat],
            won=False,
            win_points=None,
            dealt_in=False,
            deal_in_loss=None,
            exhaustive_draw=True,
            tenpai_at_exhaustive_draw=True,
            first_tenpai_turn=None,
        )
        for seat in range(4)
    )
    return LocalGameInspection(
        result=LocalGameResult(
            seed=SEED,
            game_mode=GAME_MODE,
            scores=FINAL_SCORES,
            ranks=FINAL_RANKS,
            steps=len(steps),
            decisions=sum(len(step.seat_decisions) for step in steps),
            seat_round_stats=stats,
        ),
        game_trace=GameTrace(seed=SEED, game_mode=GAME_MODE, events=_events()),
        step_observations=steps,
        round_results=round_results(scoring=scoring),
    )


def save_fixture_record(path, *, scoring: bool = True):
    """fixture inspectionを実bundleとして書き出し、strict recordを返す。"""
    return save_local_game_record(
        inspection(scoring=scoring),
        path,
        policy_identities={seat: f"fixture-policy-{int(seat)}" for seat in Seat},
        max_steps=None,
        provenance=provenance(),
    )
