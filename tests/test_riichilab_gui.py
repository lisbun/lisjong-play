"""RiichiLab live viewer GUIのentry point / event wiring / close semanticsのtest。

Tk displayを使わず、`bare_application()`で配線だけを検証する。real RiichiLab
networkへは接続しない。
"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lisjong_arena.riichilab.live_presentation import BoundedRankedPresentationBuffer

from lisjong_play.gui import GuiUnavailableError
from lisjong_play.gui_board import GuiBoardRenderer
from lisjong_play.riichilab_gui import (
    CLOSE_SCOPE_NOTE,
    LIVE_SCOPE_NOTE,
    PAUSE_SCOPE_NOTE,
    _TkRiichiLabApplication,
    main,
)
from lisjong_play.riichilab_source import (
    RiichiLabLiveController,
    RiichiLabWorkerStatus,
)
from tests._riichilab_fixtures import FINAL_SCORES, completion, decision, failure


def bare_application(controller=None, status=None, buffer=None):
    """Tk displayを使わずにevent handlingとcontrol配線だけを検証する。"""
    application = _TkRiichiLabApplication.__new__(_TkRiichiLabApplication)
    application._buffer = buffer
    application._controller = controller
    application._status = status
    application._worker = None
    application._board_renderer = Mock()
    application._seat_frames = {}
    application._center = Mock()
    application._hand = Mock()
    application._messagebox = Mock()
    application._root = Mock()
    application._frame_var = Mock()
    application._status_var = Mock()
    application._pause_button = Mock()
    application._step_button = Mock()
    application._follow_button = Mock()
    application._profile_box = Mock()
    application._start_button = Mock()
    application._append_info = Mock()
    application._reported_summary = False
    application._reported_error = False
    application._reported_runtime = False
    application._reported_run = False
    return application


def live_application(*, capacity: int = 4):
    buffer = BoundedRankedPresentationBuffer()
    controller = RiichiLabLiveController(buffer, capacity=capacity)
    return buffer, controller, bare_application(controller, buffer=buffer)


class EntryPointTest(unittest.TestCase):
    def test_entry_point_is_separate_from_the_other_presentation_sources(self) -> None:
        with patch("lisjong_play.riichilab_gui.launch_riichilab_gui") as launch:
            exit_code = main(["--profile", "lisjong-dev"])

        self.assertEqual(0, exit_code)
        launch.assert_called_once_with(profile_name="lisjong-dev", record_dir=None)

    def test_record_dir_is_passed_through(self) -> None:
        with patch("lisjong_play.riichilab_gui.launch_riichilab_gui") as launch:
            main(["--profile", "lisjong-dev", "--record-dir", "/tmp/artifacts"])

        launch.assert_called_once_with(
            profile_name="lisjong-dev", record_dir="/tmp/artifacts"
        )

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            main(["--profile", "does-not-exist"])

    def test_unavailable_gui_is_human_readable(self) -> None:
        output: list[str] = []
        with patch(
            "lisjong_play.riichilab_gui.launch_riichilab_gui",
            side_effect=GuiUnavailableError("no display"),
        ):
            exit_code = main([], error_writer=output.append)

        self.assertEqual(1, exit_code)
        self.assertEqual(["RiichiLab live viewerを起動できません: no display"], output)


class PresentationTest(unittest.TestCase):
    def test_board_is_rendered_through_the_shared_renderer_without_actions(
        self,
    ) -> None:
        buffer, controller, application = live_application()
        buffer.publish_decision(decision())
        controller.ingest()

        application._refresh(controller.state())

        application._board_renderer.render_board.assert_called_once()
        args, kwargs = application._board_renderer.render_board.call_args
        self.assertIs(controller.state().frame.board, args[0])
        self.assertEqual((), args[1])
        self.assertEqual({"seat_frames", "center", "hand"}, set(kwargs))

    def test_viewer_renderer_exposes_no_action_control(self) -> None:
        renderer = GuiBoardRenderer(Mock(), Mock())
        parent = Mock()

        renderer.tile_control(parent, "1m", Mock())

        # on_select_actionが無いのでbuttonは作られない(human takeoverなし)。
        self.assertEqual(0, renderer._ttk.Button.call_count)
        self.assertEqual(1, renderer._ttk.Label.call_count)

    def test_status_line_shows_live_facts_only(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_decision(decision(request_id=9))
        controller.ingest()

        application._refresh(controller.state())
        text = application._frame_var.set.call_args[0][0]

        self.assertIn("追従中", text)
        self.assertIn("request 9", text)
        self.assertIn("P2 打牌 東", text)
        self.assertIn("未表示 0", text)

    def test_completion_shows_final_scores_without_inferring_rank(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_completion(completion())
        controller.ingest()

        application._refresh(controller.state())
        reported = " ".join(
            str(call.args[0]) for call in application._append_info.call_args_list
        )

        self.assertIn("final scores", reported)
        for score in FINAL_SCORES:
            self.assertIn(str(score), reported)
        self.assertNotIn("位", reported)
        self.assertNotIn("rank", reported.lower())

    def test_missing_final_scores_are_not_invented(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_completion(completion(scores=None))
        controller.ingest()

        application._refresh(controller.state())
        reported = " ".join(
            str(call.args[0]) for call in application._append_info.call_args_list
        )
        self.assertIn("未提供", reported)

    def test_failure_is_not_presented_as_a_completed_match(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_failure(failure())
        controller.ingest()

        application._refresh(controller.state())
        reported = " ".join(
            str(call.args[0]) for call in application._append_info.call_args_list
        )
        status = application._status_var.set.call_args[0][0]

        self.assertIn("UnexpectedDisconnectError", reported)
        self.assertIn("failure", status)
        self.assertNotIn("final scores", reported)

    def test_terminal_facts_are_reported_once(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_completion(completion())
        controller.ingest()

        application._refresh(controller.state())
        first = application._append_info.call_count
        application._refresh(controller.state())

        self.assertEqual(first, application._append_info.call_count)

    def test_scope_notes_state_the_presentation_boundaries(self) -> None:
        self.assertIn("他家のconcealed hand", LIVE_SCOPE_NOTE)
        self.assertIn("表示だけ", PAUSE_SCOPE_NOTE)
        self.assertIn("中断されません", CLOSE_SCOPE_NOTE)


class DisplayControlTest(unittest.TestCase):
    """Pause / Step / Follow Liveは表示cursorだけを操作する。"""

    def test_pause_does_not_signal_the_worker(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_decision(decision())
        controller.ingest()

        application._toggle_pause()

        self.assertTrue(controller.paused)
        # Arena bufferはattachされたまま。pauseはworkerへ何も送らない。
        self.assertTrue(buffer.is_attached)

    def test_ingest_continues_while_the_display_is_paused(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_decision(decision(request_id=1))
        controller.ingest()
        application._toggle_pause()

        buffer.publish_decision(decision(request_id=2))
        buffer.publish_decision(decision(request_id=3))
        controller.ingest()

        state = controller.state()
        self.assertEqual(1, state.frame.request_id)
        self.assertEqual(3, state.received_frames)
        self.assertEqual(2, state.pending_frames)

    def test_step_advances_exactly_one_frame(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_decision(decision(request_id=1))
        controller.ingest()
        application._toggle_pause()
        buffer.publish_decision(decision(request_id=2))
        buffer.publish_decision(decision(request_id=3))
        controller.ingest()

        application._step()

        self.assertEqual(2, controller.state().frame.request_id)
        self.assertTrue(buffer.is_attached)

    def test_follow_live_returns_to_the_live_edge(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_decision(decision(request_id=1))
        controller.ingest()
        application._toggle_pause()
        for request_id in (2, 3, 4):
            buffer.publish_decision(decision(request_id=request_id))
        controller.ingest()

        application._follow_live()

        state = controller.state()
        self.assertTrue(state.following)
        self.assertEqual(4, state.frame.request_id)

    def test_skipped_frames_are_visible_after_a_long_pause(self) -> None:
        buffer, controller, application = live_application(capacity=2)
        application._toggle_pause()
        for request_id in range(1, 11):
            buffer.publish_decision(decision(request_id=request_id))
            controller.ingest()

        application._refresh(controller.state())
        text = application._frame_var.set.call_args[0][0]

        self.assertIn("保留 2", text)
        self.assertIn("未表示 8", text)


class WindowCloseTest(unittest.TestCase):
    """window closeはpresentationだけをdetachし、ranked gameをabortしない。"""

    def test_close_detaches_the_presentation(self) -> None:
        buffer, controller, application = live_application()
        buffer.publish_decision(decision())
        controller.ingest()

        application._close()

        self.assertFalse(buffer.is_attached)
        application._root.destroy.assert_called_once_with()

    def test_close_sends_no_cancel_or_stop_to_the_worker(self) -> None:
        buffer, controller, application = live_application()
        worker = Mock(spec=threading.Thread)
        application._worker = worker

        application._close()

        # detach以外にworkerへ触れる呼び出しがないこと。
        self.assertEqual([], worker.method_calls)

    def test_worker_keeps_running_after_the_window_is_closed(self) -> None:
        """closeでrunを打ち切らず、Arena lifecycleのまま完走できること。"""
        buffer, controller, application = live_application()
        release = threading.Event()
        completed = threading.Event()

        def worker_body() -> None:
            release.wait(timeout=10)
            # closeでdetach済みでもpublishはno-op相当で、runは壊れない。
            buffer.publish_decision(decision(request_id=2))
            buffer.publish_completion(completion())
            completed.set()

        worker = threading.Thread(target=worker_body, daemon=False)
        application._worker = worker
        worker.start()

        application._close()
        release.set()
        worker.join(timeout=10)

        self.assertTrue(completed.is_set())
        self.assertFalse(worker.is_alive())

    def test_launch_joins_a_non_daemon_worker_after_the_mainloop(self) -> None:
        """daemon threadの強制終了でranked WebSocketを切らないこと。"""
        joined: list[bool] = []
        worker = Mock(spec=threading.Thread)
        worker.join.side_effect = lambda *a, **k: joined.append(True)
        root = Mock()
        application = Mock()
        application.worker = worker

        with (
            patch(
                "lisjong_play.riichilab_gui.load_tk",
                return_value=(Mock(), Mock(), Mock(), Mock(), Mock()),
            ) as load,
            patch(
                "lisjong_play.riichilab_gui._TkRiichiLabApplication",
                return_value=application,
            ),
        ):
            load.return_value[0].Tk.return_value = root
            from lisjong_play.riichilab_gui import launch_riichilab_gui

            launch_riichilab_gui(profile_name="lisjong-dev", record_dir=None)

        root.mainloop.assert_called_once_with()
        self.assertEqual([True], joined)

    def test_started_worker_thread_is_not_a_daemon(self) -> None:
        application = bare_application()
        application._profile_var = SimpleNamespace(get=lambda: "lisjong-dev")
        application._record_dir = None
        application._clear_info = Mock()
        application._refresh_controls = Mock()
        started: list[threading.Thread] = []

        class _FakeThread:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.daemon = kwargs.get("daemon")

            def start(self) -> None:
                started.append(self)

        with patch("lisjong_play.riichilab_gui.threading.Thread", _FakeThread):
            application._start_session()

        self.assertEqual(1, len(started))
        self.assertIs(False, started[0].daemon)
        self.assertEqual("lisjong-dev", started[0].kwargs["kwargs"]["profile_name"])


class WorkerStatusTest(unittest.TestCase):
    def test_profile_failure_is_surfaced_without_presenting_a_completion(self) -> None:
        status = RiichiLabWorkerStatus()
        status.mark_failed("LISJONG_DEV_BOT_TOKEN environment variable is not set")
        application = bare_application(status=status)
        application._end_session = Mock()

        application._refresh_worker_status()

        reported = " ".join(
            str(call.args[0]) for call in application._append_info.call_args_list
        )
        self.assertIn("LISJONG_DEV_BOT_TOKEN", reported)
        application._messagebox.showerror.assert_called_once()
        application._end_session.assert_called_once_with()

    def test_presentation_failure_does_not_suppress_the_worker_session_end(
        self,
    ) -> None:
        """Arena failure fact -> worker failure の順でもsessionを必ず終了する。

        Arena #274はranked failureで`RankedFailurePresentation`をpublishして
        から例外を再送出し、直後に`run_riichilab_worker()`が
        `mark_failed()`する。同じpollで両方が揃っても、error文の重複表示だけ
        を抑止し、`_end_session()`はskipしない。
        """
        buffer = BoundedRankedPresentationBuffer()
        controller = RiichiLabLiveController(buffer)
        status = RiichiLabWorkerStatus()
        application = bare_application(controller, status=status, buffer=buffer)
        application._end_session = Mock()

        buffer.publish_decision(decision())
        buffer.publish_failure(failure())
        status.mark_failed(
            "ranked runは完了しませんでした。(UnexpectedDisconnectError)"
        )

        # 1回のpollと同じ順序: ingest -> presentation refresh -> worker status。
        controller.ingest()
        application._refresh(controller.state())
        application._refresh_worker_status()

        reported = [
            str(call.args[0]) for call in application._append_info.call_args_list
        ]
        joined = " ".join(reported)
        # presentation failureは表示され、completionとしては表示されない。
        self.assertIn("UnexpectedDisconnectError", joined)
        self.assertNotIn("final scores", joined)
        # error textとdialogは重複しない。
        self.assertEqual(
            1, len([line for line in reported if "UnexpectedDisconnectError" in line])
        )
        self.assertEqual(0, application._messagebox.showerror.call_count)
        # workerは終了済みなので、sessionもちょうど1回終了する。
        application._end_session.assert_called_once_with()

    def test_worker_failure_ends_the_session_exactly_once_across_polls(self) -> None:
        buffer = BoundedRankedPresentationBuffer()
        controller = RiichiLabLiveController(buffer)
        status = RiichiLabWorkerStatus()
        application = bare_application(controller, status=status, buffer=buffer)
        ended: list[bool] = []

        def _end_session() -> None:
            ended.append(True)
            application._status = None
            application._controller = None

        application._end_session = _end_session
        buffer.publish_failure(failure())
        status.mark_failed("ranked runは完了しませんでした。(ProtocolError)")

        controller.ingest()
        application._refresh(controller.state())
        application._refresh_worker_status()
        # 次のpollではsessionが終了済みなので何も起きない。
        application._refresh_worker_status()

        self.assertEqual([True], ended)


if __name__ == "__main__":
    unittest.main()
