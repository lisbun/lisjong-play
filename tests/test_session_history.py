import gc
import unittest
import weakref
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from lisjong_engine.round_event import DrawSource
from lisjong_engine.round_evidence import (
    DrawEvidence,
    RoundEndedEvidence,
    RoundEndKind,
    RoundEvidence,
    RoundStartedEvidence,
)
from lisjong_engine.round_evidence_completion import (
    RoundEvidenceCompletion,
    SeatRoundEvidence,
)
from lisjong_engine.seat import Seat
from lisjong_engine.wind import Wind

from lisjong_play.session import run_cli_session, run_cli_session_with_history
from lisjong_play.session_history import _HumanEastRoundHistoryRecorder
from tests._fixtures import tile


def completion(
    *,
    prevailing_wind: Wind = Wind.EAST,
    hand_number: int = 1,
    dealer_seat: Seat = Seat.EAST,
    honba: int = 0,
    east_evidence: tuple[RoundEvidence, ...] | None = None,
) -> RoundEvidenceCompletion:
    human_evidence = (
        east_evidence
        if east_evidence is not None
        else (RoundStartedEvidence(dealer_seat, prevailing_wind),)
    )
    private_draw_rank = {
        Seat.SOUTH: 2,
        Seat.WEST: 3,
        Seat.NORTH: 4,
    }
    projections = []
    for viewer_seat in Seat:
        evidence = human_evidence
        if viewer_seat is not Seat.EAST:
            evidence = (
                DrawEvidence(
                    viewer_seat,
                    DrawSource.LIVE_WALL,
                    tile(rank=private_draw_rank[viewer_seat]),
                ),
            )
        projections.append(SeatRoundEvidence(viewer_seat, evidence))
    return RoundEvidenceCompletion(
        prevailing_wind=prevailing_wind,
        hand_number=hand_number,
        dealer_seat=dealer_seat,
        honba=honba,
        projections=tuple(projections),
    )


class HumanEastRoundHistoryRecorderTest(unittest.TestCase):
    def test_retains_only_east_projection_without_retaining_bundle(self) -> None:
        first = RoundStartedEvidence(Seat.WEST, Wind.SOUTH)
        second = DrawEvidence(Seat.EAST, DrawSource.RINSHAN, tile(rank=9))
        value = completion(
            prevailing_wind=Wind.SOUTH,
            hand_number=3,
            dealer_seat=Seat.WEST,
            honba=2,
            east_evidence=(first, second),
        )
        completion_reference = weakref.ref(value)
        projection_references = tuple(weakref.ref(item) for item in value.projections)

        recorder = _HumanEastRoundHistoryRecorder()
        recorder(value)
        history = recorder.finalize()

        self.assertEqual(1, len(history.rounds))
        round_history = history.rounds[0]
        self.assertIs(Wind.SOUTH, round_history.prevailing_wind)
        self.assertEqual(3, round_history.hand_number)
        self.assertIs(Seat.WEST, round_history.dealer_seat)
        self.assertEqual(2, round_history.honba)
        self.assertEqual((first, second), round_history.evidence)
        self.assertIs(first, round_history.evidence[0])
        self.assertIs(second, round_history.evidence[1])
        for projection in value.projections[1:]:
            self.assertNotIn(projection.evidence[0], round_history.evidence)

        del projection
        del value
        gc.collect()
        self.assertIsNone(completion_reference())
        self.assertTrue(all(reference() is None for reference in projection_references))

    def test_snapshot_and_nested_round_are_immutable(self) -> None:
        recorder = _HumanEastRoundHistoryRecorder()
        recorder(completion())
        history = recorder.finalize()

        with self.assertRaises(FrozenInstanceError):
            history.rounds = ()
        with self.assertRaises(FrozenInstanceError):
            history.rounds[0].honba = 3
        with self.assertRaises(TypeError):
            history.rounds[0].evidence[0] = RoundStartedEvidence(Seat.SOUTH, Wind.SOUTH)


class SessionHistoryCompositionTest(unittest.TestCase):
    def test_preserves_multiple_rounds_through_terminal_callback(self) -> None:
        first = completion(honba=1)
        terminal_evidence = (
            RoundStartedEvidence(Seat.SOUTH, Wind.EAST),
            RoundEndedEvidence(kind=RoundEndKind.EXHAUSTIVE_DRAW),
        )
        terminal = completion(
            hand_number=2,
            dealer_seat=Seat.SOUTH,
            honba=0,
            east_evidence=terminal_evidence,
        )

        def run_successfully(_match_state, _selectors, **kwargs) -> object:
            callback = kwargs["on_round_evidence_complete"]
            callback(first)
            callback(terminal)
            return object()

        with patch("lisjong_play.session.run_hanchan", side_effect=run_successfully):
            history = run_cli_session_with_history(
                input_reader=lambda _: "",
                output_writer=lambda _: None,
            )

        self.assertEqual(2, len(history.rounds))
        self.assertEqual((1, 0), tuple(item.honba for item in history.rounds))
        self.assertEqual((1, 2), tuple(item.hand_number for item in history.rounds))
        self.assertIs(terminal_evidence[-1], history.rounds[-1].evidence[-1])

    def test_failed_session_does_not_finalize_partial_history(self) -> None:
        recorder = _HumanEastRoundHistoryRecorder()

        def fail_after_one_round(_match_state, _selectors, **kwargs) -> None:
            kwargs["on_round_evidence_complete"](completion())
            raise RuntimeError("later session failure")

        with (
            patch(
                "lisjong_play.session._HumanEastRoundHistoryRecorder",
                return_value=recorder,
            ),
            patch.object(recorder, "finalize", wraps=recorder.finalize) as finalize,
            patch(
                "lisjong_play.session.run_hanchan",
                side_effect=fail_after_one_round,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "later session failure"):
                run_cli_session_with_history(
                    input_reader=lambda _: "",
                    output_writer=lambda _: None,
                )

        finalize.assert_not_called()

    def test_recorder_callback_failure_propagates(self) -> None:
        def deliver_invalid_completion(_match_state, _selectors, **kwargs) -> None:
            kwargs["on_round_evidence_complete"](object())

        with patch(
            "lisjong_play.session.run_hanchan",
            side_effect=deliver_invalid_completion,
        ):
            with self.assertRaisesRegex(
                TypeError, "completion must be a RoundEvidenceCompletion"
            ):
                run_cli_session_with_history(
                    input_reader=lambda _: "",
                    output_writer=lambda _: None,
                )

    def test_existing_session_does_not_enable_history_callback(self) -> None:
        with patch("lisjong_play.session.run_hanchan") as run_hanchan:
            run_cli_session(
                input_reader=lambda _: "",
                output_writer=lambda _: None,
            )

        self.assertNotIn(
            "on_round_evidence_complete",
            run_hanchan.call_args.kwargs,
        )


if __name__ == "__main__":
    unittest.main()
