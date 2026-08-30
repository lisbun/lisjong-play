"""Human EAST + selected Policy x3 のminimum hanchan composition。"""

from collections.abc import Callable
from typing import Literal

from lisjong.policies import (
    GenbutsuDefenseFiniteHorizonValueAwarePolicy,
    MinimalPolicy,
)
from lisjong_arena.lisjong_engine.policy_selector import PolicySeatSelector
from lisjong_engine.driver import ActionSelector, RoundEvidenceCallback, run_hanchan
from lisjong_engine.match_state import MatchState
from lisjong_engine.seat import Seat

from lisjong_play.human_selector import HumanActionSelector
from lisjong_play.renderer import RIVER_LEGEND, DeliveryPresenter
from lisjong_play.session_history import (
    HumanSessionHistory,
    _HumanEastRoundHistoryRecorder,
)

DEFAULT_SEED = 0
OpponentName = Literal["minimal", "combined"]
DEFAULT_OPPONENT: OpponentName = "minimal"
OPPONENT_CHOICES: tuple[OpponentName, ...] = ("minimal", "combined")


def _create_opponent_policy(
    opponent: OpponentName,
) -> MinimalPolicy | GenbutsuDefenseFiniteHorizonValueAwarePolicy:
    match opponent:
        case "minimal":
            return MinimalPolicy()
        case "combined":
            return GenbutsuDefenseFiniteHorizonValueAwarePolicy()
        case _:
            raise ValueError(f"unknown opponent: {opponent}")


def build_seat_selectors(
    *,
    input_reader: Callable[[str], str],
    output_writer: Callable[[str], None],
    opponent: OpponentName = DEFAULT_OPPONENT,
) -> dict[Seat, ActionSelector]:
    """Human EASTと指定Policy 3席をengine selectorへcompositionする。"""
    human = HumanActionSelector(input_reader, output_writer)
    return {
        Seat.EAST: human,
        Seat.SOUTH: PolicySeatSelector(Seat.SOUTH, _create_opponent_policy(opponent)),
        Seat.WEST: PolicySeatSelector(Seat.WEST, _create_opponent_policy(opponent)),
        Seat.NORTH: PolicySeatSelector(Seat.NORTH, _create_opponent_policy(opponent)),
    }


def _run_cli_session(
    *,
    seed: int,
    opponent: OpponentName,
    input_reader: Callable[[str], str],
    output_writer: Callable[[str], None],
    on_round_evidence_complete: RoundEvidenceCallback | None,
) -> None:
    if type(seed) is not int:
        raise TypeError("seed must be an int")
    selectors = build_seat_selectors(
        input_reader=input_reader,
        output_writer=output_writer,
        opponent=opponent,
    )
    presenter = DeliveryPresenter(input_reader, output_writer)
    match_state = MatchState(seed=seed, rules=None)
    output_writer(RIVER_LEGEND)
    if on_round_evidence_complete is None:
        run_hanchan(match_state, selectors, on_delivery=presenter)
        return
    run_hanchan(
        match_state,
        selectors,
        on_delivery=presenter,
        on_round_evidence_complete=on_round_evidence_complete,
    )


def run_cli_session(
    *,
    seed: int = DEFAULT_SEED,
    opponent: OpponentName = DEFAULT_OPPONENT,
    input_reader: Callable[[str], str] = input,
    output_writer: Callable[[str], None] = print,
) -> None:
    """default RuleSetでHuman EAST vs selected Policy x3を1半荘実行する。"""
    _run_cli_session(
        seed=seed,
        opponent=opponent,
        input_reader=input_reader,
        output_writer=output_writer,
        on_round_evidence_complete=None,
    )


def run_cli_session_with_history(
    *,
    seed: int = DEFAULT_SEED,
    opponent: OpponentName = DEFAULT_OPPONENT,
    input_reader: Callable[[str], str] = input,
    output_writer: Callable[[str], None] = print,
) -> HumanSessionHistory:
    """正常終了したHuman EAST sessionのsame-process historyを返す。"""
    recorder = _HumanEastRoundHistoryRecorder()
    _run_cli_session(
        seed=seed,
        opponent=opponent,
        input_reader=input_reader,
        output_writer=output_writer,
        on_round_evidence_complete=recorder,
    )
    return recorder.finalize()
