"""Human EAST向けのsame-process round evidence history。"""

from dataclasses import dataclass

from lisjong_engine.round_evidence import RoundEvidence
from lisjong_engine.round_evidence_completion import RoundEvidenceCompletion
from lisjong_engine.seat import Seat
from lisjong_engine.wind import Wind


@dataclass(frozen=True, kw_only=True)
class HumanRoundHistory:
    """1局分のHuman EAST視点ordered evidenceとengine提供の局identity。"""

    prevailing_wind: Wind
    hand_number: int
    dealer_seat: Seat
    honba: int
    evidence: tuple[RoundEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.prevailing_wind, Wind):
            raise TypeError("prevailing_wind must be a Wind")
        if type(self.hand_number) is not int:
            raise TypeError("hand_number must be an int")
        if not isinstance(self.dealer_seat, Seat):
            raise TypeError("dealer_seat must be a Seat")
        if type(self.honba) is not int:
            raise TypeError("honba must be an int")
        try:
            evidence = tuple(self.evidence)
        except TypeError:
            raise TypeError("evidence must be an iterable of RoundEvidence") from None
        if any(not isinstance(item, RoundEvidence) for item in evidence):
            raise TypeError("evidence must contain only RoundEvidence values")
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True)
class HumanSessionHistory:
    """正常終了した1半荘のimmutableなHuman EAST round history。"""

    rounds: tuple[HumanRoundHistory, ...]

    def __post_init__(self) -> None:
        try:
            rounds = tuple(self.rounds)
        except TypeError:
            raise TypeError("rounds must be an iterable of HumanRoundHistory") from None
        if any(not isinstance(round_, HumanRoundHistory) for round_ in rounds):
            raise TypeError("rounds must contain only HumanRoundHistory values")
        object.__setattr__(self, "rounds", rounds)


class _HumanEastRoundHistoryRecorder:
    """4-viewer completionを受信時にHuman EAST projectionへnarrowする。"""

    def __init__(self) -> None:
        self._rounds: list[HumanRoundHistory] = []

    def __call__(self, completion: RoundEvidenceCompletion) -> None:
        if not isinstance(completion, RoundEvidenceCompletion):
            raise TypeError("completion must be a RoundEvidenceCompletion")

        human_projections = tuple(
            projection
            for projection in completion.projections
            if projection.viewer_seat is Seat.EAST
        )
        if len(human_projections) != 1:
            raise ValueError("completion must contain exactly one EAST projection")
        human_projection = human_projections[0]

        self._rounds.append(
            HumanRoundHistory(
                prevailing_wind=completion.prevailing_wind,
                hand_number=completion.hand_number,
                dealer_seat=completion.dealer_seat,
                honba=completion.honba,
                evidence=human_projection.evidence,
            )
        )

    def finalize(self) -> HumanSessionHistory:
        return HumanSessionHistory(tuple(self._rounds))
