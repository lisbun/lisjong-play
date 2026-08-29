"""Human EAST + MinimalPolicy x3 のminimum hanchan composition。"""

from collections.abc import Callable

from lisjong.policies import MinimalPolicy
from lisjong_arena.lisjong_engine.policy_selector import PolicySeatSelector
from lisjong_engine.driver import ActionSelector, run_hanchan
from lisjong_engine.match_state import MatchState
from lisjong_engine.seat import Seat

from lisjong_play.human_selector import HumanActionSelector
from lisjong_play.renderer import RIVER_LEGEND, DeliveryPresenter

DEFAULT_SEED = 0


def build_seat_selectors(
    *,
    input_reader: Callable[[str], str],
    output_writer: Callable[[str], None],
) -> dict[Seat, ActionSelector]:
    """Human EASTとMinimalPolicy 3席をengine selectorへcompositionする。"""
    human = HumanActionSelector(input_reader, output_writer)
    return {
        Seat.EAST: human,
        Seat.SOUTH: PolicySeatSelector(Seat.SOUTH, MinimalPolicy()),
        Seat.WEST: PolicySeatSelector(Seat.WEST, MinimalPolicy()),
        Seat.NORTH: PolicySeatSelector(Seat.NORTH, MinimalPolicy()),
    }


def run_cli_session(
    *,
    seed: int = DEFAULT_SEED,
    input_reader: Callable[[str], str] = input,
    output_writer: Callable[[str], None] = print,
) -> None:
    """default RuleSetでHuman EAST vs MinimalPolicy x3を1半荘実行する。"""
    if type(seed) is not int:
        raise TypeError("seed must be an int")
    selectors = build_seat_selectors(
        input_reader=input_reader,
        output_writer=output_writer,
    )
    presenter = DeliveryPresenter(input_reader, output_writer)
    match_state = MatchState(seed=seed, rules=None)
    output_writer(RIVER_LEGEND)
    run_hanchan(match_state, selectors, on_delivery=presenter)
