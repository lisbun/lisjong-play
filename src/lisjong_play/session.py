"""Human EAST + selected Policy x3 のminimum hanchan composition。"""

from collections.abc import Callable
from typing import Literal

from lisjong.policies import (
    GenbutsuDefenseFiniteHorizonValueAwarePolicy,
    MinimalPolicy,
    YakuhaiCallGenbutsuDefenseFiniteHorizonHandValueAwarePolicy,
)
from lisjong_arena.lisjong_engine.policy_selector import PolicySeatSelector
from lisjong_engine.driver import (
    ActionSelector,
    DeliveryCallback,
    RoundEvidenceCallback,
    run_hanchan,
)
from lisjong_engine.match_state import MatchState
from lisjong_engine.seat import Seat

from lisjong_play.human_selector import HumanActionSelector
from lisjong_play.renderer import RIVER_LEGEND, DeliveryPresenter
from lisjong_play.session_history import (
    HumanSessionHistory,
    _HumanEastRoundHistoryRecorder,
)

DEFAULT_SEED = 0
OpponentName = Literal["minimal", "combined", "yakuhai-call"]
DEFAULT_OPPONENT: OpponentName = "minimal"
OPPONENT_CHOICES: tuple[OpponentName, ...] = ("minimal", "combined", "yakuhai-call")


def _create_opponent_policy(
    opponent: OpponentName,
) -> (
    MinimalPolicy
    | GenbutsuDefenseFiniteHorizonValueAwarePolicy
    | YakuhaiCallGenbutsuDefenseFiniteHorizonHandValueAwarePolicy
):
    match opponent:
        case "minimal":
            return MinimalPolicy()
        case "combined":
            return GenbutsuDefenseFiniteHorizonValueAwarePolicy()
        case "yakuhai-call":
            return YakuhaiCallGenbutsuDefenseFiniteHorizonHandValueAwarePolicy()
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
    return _build_seat_selectors(human, opponent=opponent)


def _build_seat_selectors(
    human_selector: ActionSelector,
    *,
    opponent: OpponentName,
) -> dict[Seat, ActionSelector]:
    """任意のHuman UI selectorと既存Policy 3席をcompositionする。"""
    if not callable(human_selector):
        raise TypeError("human_selector must be callable")
    return {
        Seat.EAST: human_selector,
        Seat.SOUTH: PolicySeatSelector(Seat.SOUTH, _create_opponent_policy(opponent)),
        Seat.WEST: PolicySeatSelector(Seat.WEST, _create_opponent_policy(opponent)),
        Seat.NORTH: PolicySeatSelector(Seat.NORTH, _create_opponent_policy(opponent)),
    }


def _run_session(
    *,
    seed: int,
    opponent: OpponentName,
    human_selector: ActionSelector,
    on_delivery: DeliveryCallback,
    on_round_evidence_complete: RoundEvidenceCallback | None,
) -> None:
    """UI非依存のminimum Human EAST hanchan composition。"""
    if type(seed) is not int:
        raise TypeError("seed must be an int")
    if not callable(on_delivery):
        raise TypeError("on_delivery must be callable")
    selectors = _build_seat_selectors(human_selector, opponent=opponent)
    match_state = MatchState(seed=seed, rules=None)
    if on_round_evidence_complete is None:
        run_hanchan(match_state, selectors, on_delivery=on_delivery)
        return
    run_hanchan(
        match_state,
        selectors,
        on_delivery=on_delivery,
        on_round_evidence_complete=on_round_evidence_complete,
    )


def _run_cli_session(
    *,
    seed: int,
    opponent: OpponentName,
    input_reader: Callable[[str], str],
    output_writer: Callable[[str], None],
    on_round_evidence_complete: RoundEvidenceCallback | None,
) -> None:
    human = HumanActionSelector(input_reader, output_writer)
    presenter = DeliveryPresenter(input_reader, output_writer)
    output_writer(RIVER_LEGEND)
    _run_session(
        seed=seed,
        opponent=opponent,
        human_selector=human,
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
