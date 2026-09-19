"""RiichiLab live presentation source / controller / workerのtest。

real RiichiLab networkへは接続しない。Arenaのsupported presentation value
(`BoundedRankedPresentationBuffer` / `RankedDecisionPresentation` 等)と
injected runnerだけで、presentation-only semanticsとworker境界を固定する。
"""

import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lisjong.policy_contract import Seat
from lisjong_arena.riichilab.live_presentation import BoundedRankedPresentationBuffer
from lisjong_arena.riichilab.profile import ProfileError

from lisjong_play.riichilab_source import (
    DEFAULT_PENDING_FRAME_CAPACITY,
    RiichiLabLiveController,
    RiichiLabWorkerStatus,
    build_decision_frame,
    run_riichilab_worker,
)
from tests._riichilab_fixtures import FINAL_SCORES, completion, decision, failure

_TOKEN = "test-only-bot-token-value"


def _controller(*, capacity: int = 4):
    buffer = BoundedRankedPresentationBuffer()
    return buffer, RiichiLabLiveController(buffer, capacity=capacity)


def _publish(buffer, count: int, *, start: int = 1) -> None:
    for offset in range(count):
        buffer.publish_decision(decision(request_id=start + offset))


class DecisionFrameTest(unittest.TestCase):
    def test_frame_projects_the_arena_policy_input_through_the_shared_board(
        self,
    ) -> None:
        frame = build_decision_frame(
            decision(request_id=7, seat=Seat.SEAT_2), ordinal=3
        )

        self.assertEqual(7, frame.request_id)
        self.assertEqual(3, frame.ordinal)
        self.assertEqual("P3", frame.seat_label)
        self.assertEqual("打牌 東", frame.action_label)
        # bound bot seatは常にbottom。
        self.assertEqual("bottom", frame.board.seats[2].position)
        self.assertEqual("P3 打牌 東", frame.board.decision_label)

    def test_foreign_values_are_rejected(self) -> None:
        with self.assertRaises(TypeError):
            build_decision_frame(object(), ordinal=1)


class FollowLiveTest(unittest.TestCase):
    def test_follow_live_shows_the_latest_frame(self) -> None:
        buffer, controller = _controller()
        _publish(buffer, 3)

        self.assertTrue(controller.ingest())
        state = controller.state()
        self.assertTrue(state.following)
        self.assertEqual(3, state.frame.request_id)
        self.assertEqual(3, state.received_frames)
        self.assertEqual(0, state.pending_frames)
        # 同じdrain内で表示されずに置き換わった2件だけがskipped。
        self.assertEqual(2, state.skipped_frames)

    def test_sequential_drains_do_not_count_displayed_frames_as_skipped(self) -> None:
        buffer, controller = _controller()
        for request_id in (1, 2, 3):
            buffer.publish_decision(decision(request_id=request_id))
            controller.ingest()

        state = controller.state()
        self.assertEqual(3, state.frame.request_id)
        self.assertEqual(0, state.skipped_frames)

    def test_ingest_without_new_facts_reports_no_change(self) -> None:
        _buffer, controller = _controller()
        self.assertFalse(controller.ingest())


class PauseAndStepTest(unittest.TestCase):
    """Pauseは表示cursorだけを止める。workerへは何も送らない。"""

    def test_pause_freezes_the_display_but_ingest_continues(self) -> None:
        buffer, controller = _controller()
        _publish(buffer, 1)
        controller.ingest()
        controller.pause()

        _publish(buffer, 2, start=2)
        self.assertTrue(controller.ingest())

        state = controller.state()
        self.assertFalse(state.following)
        # 表示は止まったまま、受信は進む。
        self.assertEqual(1, state.frame.request_id)
        self.assertEqual(3, state.received_frames)
        self.assertEqual(2, state.pending_frames)

    def test_pause_sends_nothing_to_the_arena_buffer(self) -> None:
        buffer, controller = _controller()
        controller.pause()
        controller.step()
        controller.follow_live()

        # detachだけがArena buffer側の状態を変える唯一の操作である。
        self.assertTrue(buffer.is_attached)

    def test_step_advances_exactly_one_retained_frame(self) -> None:
        buffer, controller = _controller()
        _publish(buffer, 1)
        controller.ingest()
        controller.pause()
        _publish(buffer, 3, start=2)
        controller.ingest()

        self.assertTrue(controller.step())
        state = controller.state()
        self.assertEqual(2, state.frame.request_id)
        self.assertEqual(2, state.pending_frames)
        self.assertEqual(0, state.skipped_frames)

        self.assertTrue(controller.step())
        self.assertEqual(3, controller.state().frame.request_id)

    def test_step_is_rejected_while_following(self) -> None:
        buffer, controller = _controller()
        _publish(buffer, 2)
        controller.ingest()

        self.assertFalse(controller.step())

    def test_step_without_retained_frames_is_a_no_op(self) -> None:
        _buffer, controller = _controller()
        controller.pause()
        self.assertFalse(controller.step())

    def test_follow_live_jumps_to_the_latest_retained_frame(self) -> None:
        buffer, controller = _controller()
        _publish(buffer, 1)
        controller.ingest()
        controller.pause()
        _publish(buffer, 3, start=2)
        controller.ingest()

        self.assertTrue(controller.follow_live())
        state = controller.state()
        self.assertTrue(state.following)
        self.assertEqual(4, state.frame.request_id)
        self.assertEqual(0, state.pending_frames)
        # 飛ばした2件だけがskipped。
        self.assertEqual(2, state.skipped_frames)


class BoundedHistoryTest(unittest.TestCase):
    def test_default_capacity_is_explicit_and_finite(self) -> None:
        _buffer, controller = _controller(capacity=DEFAULT_PENDING_FRAME_CAPACITY)
        self.assertEqual(DEFAULT_PENDING_FRAME_CAPACITY, controller.capacity)
        self.assertIsInstance(DEFAULT_PENDING_FRAME_CAPACITY, int)

    def test_capacity_must_be_a_positive_int(self) -> None:
        buffer = BoundedRankedPresentationBuffer()
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    RiichiLabLiveController(buffer, capacity=value)
        for value in (True, None, 1.0):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    RiichiLabLiveController(buffer, capacity=value)

    def test_paused_history_stays_bounded_and_reports_skipped_frames(self) -> None:
        buffer, controller = _controller(capacity=3)
        controller.pause()
        for request_id in range(1, 21):
            buffer.publish_decision(decision(request_id=request_id))
            controller.ingest()

        state = controller.state()
        self.assertEqual(3, state.pending_frames)
        self.assertEqual(20, state.received_frames)
        self.assertEqual(17, state.skipped_frames)
        self.assertTrue(controller.step())
        # 最新側の3件だけが残る。
        self.assertEqual(18, controller.state().frame.request_id)

    def test_arena_side_coalescing_is_surfaced_as_skipped_frames(self) -> None:
        buffer = BoundedRankedPresentationBuffer(capacity=1)
        controller = RiichiLabLiveController(buffer, capacity=4)
        _publish(buffer, 5)

        controller.ingest()
        state = controller.state()
        # Arena bufferが4件coalesceし、残った1件だけが届く。
        self.assertEqual(1, state.received_frames)
        self.assertEqual(4, state.skipped_frames)
        self.assertEqual(5, state.frame.request_id)


class TerminalFactTest(unittest.TestCase):
    def test_completion_is_not_dropped_while_paused(self) -> None:
        buffer, controller = _controller(capacity=1)
        controller.pause()
        _publish(buffer, 4)
        buffer.publish_completion(completion())
        controller.ingest()

        state = controller.state()
        self.assertIsNotNone(state.completion)
        self.assertEqual(FINAL_SCORES, state.completion.scores)
        self.assertEqual("P2", state.completion.seat_label)
        self.assertIsNone(state.failure)

    def test_failure_is_not_presented_as_a_completion(self) -> None:
        buffer, controller = _controller()
        buffer.publish_failure(failure())
        controller.ingest()

        state = controller.state()
        self.assertIsNone(state.completion)
        self.assertEqual("UnexpectedDisconnectError", state.failure.failure_type)

    def test_missing_final_scores_are_not_inferred(self) -> None:
        buffer, controller = _controller()
        buffer.publish_completion(completion(scores=None))
        controller.ingest()

        self.assertIsNone(controller.state().completion.scores)

    def test_terminal_facts_are_delivered_once(self) -> None:
        buffer, controller = _controller()
        buffer.publish_completion(completion())
        controller.ingest()
        first = controller.state().completion
        controller.ingest()

        self.assertIs(first, controller.state().completion)


class DetachTest(unittest.TestCase):
    def test_detach_only_detaches_the_presentation(self) -> None:
        buffer, controller = _controller()
        _publish(buffer, 1)
        controller.ingest()

        controller.detach()

        self.assertFalse(buffer.is_attached)
        # detach後のpublishはno-op相当で、ranked側を壊さない。
        _publish(buffer, 1, start=2)
        buffer.publish_completion(completion())
        self.assertFalse(controller.ingest())
        # 直前まで表示していたframeはそのまま残る。
        self.assertEqual(1, controller.state().frame.request_id)


class WorkerTest(unittest.TestCase):
    """Arena runtimeをinjected runner / patched resolverで置き換えて確認する。

    real RiichiLabへは接続せず、profile / credential解決もArena APIの
    呼び出し境界だけを見る。
    """

    def setUp(self) -> None:
        self.profile = SimpleNamespace(
            name="lisjong-dev",
            credential_env_var="LISJONG_DEV_BOT_TOKEN",
            policy_factory=lambda: SimpleNamespace(),
            runtime_namespace="lisjong-dev",
        )

    def _result(self):
        return SimpleNamespace(
            seat=Seat.SEAT_1,
            requests_received=12,
            responses_sent=12,
            scores=FINAL_SCORES,
            end_game_received=True,
        )

    @contextmanager
    def _resolved(self, *, credential_error: Exception | None = None):
        """Arenaのprofile / credential解決だけをtest doubleへ差し替える。"""
        with (
            patch(
                "lisjong_play.riichilab_source.resolve_profile",
                return_value=self.profile,
            ) as resolve_profile,
            patch(
                "lisjong_play.riichilab_source.resolve_credential",
                side_effect=credential_error,
                return_value=None if credential_error else _TOKEN,
            ) as resolve_credential,
            patch(
                "lisjong_play.riichilab_source.build_runtime_summary",
                return_value=SimpleNamespace(),
            ),
            patch(
                "lisjong_play.riichilab_source.format_runtime_summary",
                return_value="profile: lisjong-dev / mode: ranked",
            ),
        ):
            yield resolve_profile, resolve_credential

    def _run(self, **kwargs):
        buffer = BoundedRankedPresentationBuffer()
        status = RiichiLabWorkerStatus()
        with self._resolved():
            run_riichilab_worker(buffer, status, profile_name="lisjong-dev", **kwargs)
        return buffer, status

    def test_profile_and_credential_resolution_reuse_the_arena_contract(self) -> None:
        buffer = BoundedRankedPresentationBuffer()
        status = RiichiLabWorkerStatus()
        with self._resolved() as (resolve_profile, resolve_credential):
            run_riichilab_worker(
                buffer,
                status,
                profile_name="lisjong-dev",
                run_game=lambda *a, **k: self._result(),
            )

        resolve_profile.assert_called_once_with("lisjong-dev")
        resolve_credential.assert_called_once_with(self.profile)
        self.assertEqual(
            "profile: lisjong-dev / mode: ranked",
            status.snapshot().runtime_summary,
        )

    def test_successful_run_reports_a_secret_safe_summary(self) -> None:
        calls = []

        def run_game(policy, token, *, presentation):
            calls.append((policy, token, presentation))
            presentation.publish_decision(decision())
            presentation.publish_completion(completion())
            return self._result()

        buffer, status = self._run(run_game=run_game)
        snapshot = status.snapshot()

        self.assertEqual(1, len(calls))
        self.assertIsNone(snapshot.error_text)
        self.assertEqual("P2", snapshot.summary.seat_label)
        self.assertEqual(12, snapshot.summary.requests)
        self.assertIsNone(snapshot.summary.record_identity)
        self.assertEqual(FINAL_SCORES, snapshot.summary.scores)
        # presentationはArena bufferで届く。
        batch = buffer.drain()
        self.assertEqual(1, len(batch.decisions))
        self.assertIsNotNone(batch.completion)

    def test_no_token_reaches_the_presentation_or_the_summary(self) -> None:
        def run_game(policy, token, *, presentation):
            self.assertEqual(_TOKEN, token)
            presentation.publish_decision(decision())
            presentation.publish_completion(completion())
            return self._result()

        buffer, status = self._run(run_game=run_game)

        snapshot = status.snapshot()
        rendered = repr(snapshot) + repr(buffer.drain())
        self.assertNotIn(_TOKEN, rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("Authorization", rendered)
        for field in vars(snapshot.summary):
            self.assertNotIn("token", field.lower())
            self.assertNotIn("credential", field.lower())

    def test_record_dir_uses_the_arena_durable_acquisition(self) -> None:
        captured = {}

        def acquire_record(policy, token, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(result=self._result(), record_identity="a" * 64)

        def run_game(policy, token, *, presentation):
            raise AssertionError("run_ranked_game must not be used with --record-dir")

        _buffer, status = self._run(
            record_path=Path("/tmp/record"),
            run_game=run_game,
            acquire_record=acquire_record,
        )

        self.assertEqual(Path("/tmp/record"), captured["destination"])
        self.assertEqual("lisjong-dev", captured["profile_identity"])
        self.assertIn("presentation", captured)
        self.assertEqual("a" * 64, status.snapshot().summary.record_identity)

    def test_profile_failure_is_reported_without_a_credential_value(self) -> None:
        buffer = BoundedRankedPresentationBuffer()
        status = RiichiLabWorkerStatus()
        error = ProfileError(
            "LISJONG_DEV_BOT_TOKEN environment variable is not set for profile "
            "'lisjong-dev'."
        )

        with self._resolved(credential_error=error):
            run_riichilab_worker(
                buffer,
                status,
                profile_name="lisjong-dev",
                run_game=lambda *a, **k: self._result(),
            )

        snapshot = status.snapshot()
        # Arenaのprofile errorは環境変数の名前だけを含み、値は含まない。
        self.assertIn("LISJONG_DEV_BOT_TOKEN", snapshot.error_text)
        self.assertNotIn(_TOKEN, snapshot.error_text)
        self.assertIsNone(snapshot.summary)
        self.assertFalse(snapshot.started)

    def test_ranked_failure_reports_only_the_exception_type(self) -> None:
        class _Boom(RuntimeError):
            pass

        def run_game(policy, token, *, presentation):
            raise _Boom("wss://secret.example/ranked?token=leak")

        _buffer, status = self._run(run_game=run_game)
        snapshot = status.snapshot()

        self.assertIn("_Boom", snapshot.error_text)
        self.assertNotIn("secret.example", snapshot.error_text)
        self.assertNotIn("token=leak", snapshot.error_text)
        self.assertIsNone(snapshot.summary)

    def test_detached_presentation_does_not_abort_the_run(self) -> None:
        def run_game(policy, token, *, presentation):
            presentation.publish_decision(decision())
            return self._result()

        buffer = BoundedRankedPresentationBuffer()
        status = RiichiLabWorkerStatus()
        buffer.detach()

        with self._resolved():
            run_riichilab_worker(
                buffer, status, profile_name="lisjong-dev", run_game=run_game
            )

        snapshot = status.snapshot()
        self.assertIsNone(snapshot.error_text)
        self.assertEqual(12, snapshot.summary.requests)

    def test_worker_runs_on_a_thread_without_touching_tk(self) -> None:
        started = threading.Event()

        def run_game(policy, token, *, presentation):
            started.set()
            presentation.publish_decision(decision())
            return self._result()

        buffer = BoundedRankedPresentationBuffer()
        status = RiichiLabWorkerStatus()
        with self._resolved():
            worker = threading.Thread(
                target=run_riichilab_worker,
                args=(buffer, status),
                kwargs={"profile_name": "lisjong-dev", "run_game": run_game},
                daemon=False,
            )
            worker.start()
            worker.join(timeout=10)

        self.assertTrue(started.is_set())
        self.assertFalse(worker.is_alive())
        self.assertIsNotNone(status.snapshot().summary)

    def test_worker_rejects_foreign_arguments(self) -> None:
        with self.assertRaises(TypeError):
            run_riichilab_worker(object(), RiichiLabWorkerStatus(), profile_name="x")
        with self.assertRaises(TypeError):
            run_riichilab_worker(
                BoundedRankedPresentationBuffer(), object(), profile_name="x"
            )


if __name__ == "__main__":
    unittest.main()
