"""RiichiLab HTML live viewer continuous mode (`lisbun/lisjong-play#48`) のtest。

real RiichiLab networkへは接続しない。Arenaの`ContinuousRankedPresentationFeed`
とinjected worker、またはArena CLIの下で`run_ranked_game`だけをfakeへ差し替えて、
game切り替え、lifecycle、stdout summary、credential境界を固定する。
"""

import contextlib
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from lisjong_arena.riichilab.live_presentation import (
    ContinuousRankedPresentationFeed,
)

from lisjong_play.riichilab_html import (
    LOOPBACK_HOST,
    RiichiLabContinuousHtmlSession,
    RiichiLabHtmlServer,
    build_arena_continuous_argv,
    build_continuous_state_payload,
    main,
)
from lisjong_play.riichilab_source import (
    RiichiLabContinuousController,
    RiichiLabContinuousStatus,
    run_riichilab_continuous_worker,
)
from tests._riichilab_fixtures import FINAL_SCORES, completion, decision, failure

_TOKEN = "test-only-bot-token-value"
_WAIT_SECONDS = 5
#: Arena `aws_run_verify`がrunner logからparseするsummary key。
_ARENA_SUMMARY_KEYS = {
    "profile",
    "requested completed games",
    "requested duration seconds",
    "completed games",
    "failed games",
    "consecutive failures",
    "last failure type",
    "records",
    "stopped reason",
}


def _session(feed: ContinuousRankedPresentationFeed, *, capacity: int = 4):
    controller = RiichiLabContinuousController(feed, capacity=capacity)
    status = RiichiLabContinuousStatus()
    session = RiichiLabContinuousHtmlSession(
        controller, status, profile_name="lisjong-dev"
    )
    return controller, status, session


class ContinuousControllerTest(unittest.TestCase):
    def test_waits_until_the_first_game_opens(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        controller = RiichiLabContinuousController(feed)

        self.assertFalse(controller.ingest())
        state = controller.state()
        self.assertIsNone(state.game_ordinal)
        self.assertIsNone(state.view)

    def test_switches_to_new_game_and_keeps_previous_terminal(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        controller = RiichiLabContinuousController(feed)
        first = feed.open_game()
        first.publish_decision(decision(request_id=1))
        controller.ingest()
        self.assertEqual(1, controller.state().game_ordinal)

        # consumerがdrainする前にterminal publish -> 次gameのopenが起きても
        # 前gameのcompletionは最終drainで受け取れる。
        first.publish_completion(completion())
        second = feed.open_game()
        second.publish_decision(decision(request_id=7))
        controller.ingest()

        state = controller.state()
        self.assertEqual(2, state.game_ordinal)
        self.assertEqual(7, state.view.frame.request_id)
        self.assertEqual(1, state.last_result.game_ordinal)
        self.assertEqual(FINAL_SCORES, state.last_result.completion.scores)
        self.assertIsNone(state.last_result.failure)
        self.assertEqual(0, state.skipped_games)

    def test_games_never_displayed_are_counted_as_skipped(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        controller = RiichiLabContinuousController(feed)
        feed.open_game()
        controller.ingest()
        feed.open_game().publish_failure(failure())
        feed.open_game()
        feed.open_game()
        controller.ingest()

        state = controller.state()
        self.assertEqual(4, state.game_ordinal)
        self.assertEqual(4, state.latest_game_ordinal)
        self.assertEqual(2, state.skipped_games)

    def test_paused_display_does_not_switch_games_until_follow(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        controller = RiichiLabContinuousController(feed)
        feed.open_game().publish_decision(decision(request_id=1))
        controller.ingest()
        controller.pause()

        feed.open_game().publish_decision(decision(request_id=9))
        controller.ingest()
        paused = controller.state()
        self.assertEqual(1, paused.game_ordinal)
        self.assertEqual(2, paused.latest_game_ordinal)
        self.assertEqual(1, paused.view.frame.request_id)

        controller.follow_live()
        controller.ingest()
        resumed = controller.state()
        self.assertEqual(2, resumed.game_ordinal)
        self.assertEqual(9, resumed.view.frame.request_id)

    def test_detach_reaches_the_arena_feed(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        controller = RiichiLabContinuousController(feed)
        buffer = feed.open_game()
        controller.ingest()
        controller.detach()
        self.assertFalse(feed.is_attached)
        self.assertFalse(buffer.is_attached)

    def test_rejects_foreign_values(self) -> None:
        with self.assertRaises(TypeError):
            RiichiLabContinuousController(object())
        with self.assertRaises((TypeError, ValueError)):
            RiichiLabContinuousController(
                ContinuousRankedPresentationFeed(), capacity=0
            )


class ContinuousWorkerTest(unittest.TestCase):
    def test_delegates_to_the_arena_cli_and_records_exit_code(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        status = RiichiLabContinuousStatus()
        captured = {}

        def run_cli(argv, *, presentation, stop_requested):
            captured.update(
                argv=argv, presentation=presentation, stop_requested=stop_requested
            )
            return 0

        def stop() -> bool:
            return False

        run_riichilab_continuous_worker(
            feed,
            status,
            arena_argv=["--profile", "lisjong-dev"],
            stop_requested=stop,
            run_cli=run_cli,
        )

        self.assertEqual(["--profile", "lisjong-dev"], captured["argv"])
        self.assertIs(feed, captured["presentation"])
        self.assertIs(stop, captured["stop_requested"])
        snapshot = status.snapshot()
        self.assertFalse(snapshot.running)
        self.assertEqual(0, snapshot.exit_code)
        self.assertIsNone(snapshot.error_type)

    def test_escaped_exception_keeps_only_the_type_name(self) -> None:
        status = RiichiLabContinuousStatus()
        errors: list[str] = []

        def run_cli(argv, **kwargs):
            raise RuntimeError(f"leaky message {_TOKEN}")

        run_riichilab_continuous_worker(
            ContinuousRankedPresentationFeed(),
            status,
            arena_argv=[],
            stop_requested=lambda: False,
            run_cli=run_cli,
            error_writer=errors.append,
        )

        snapshot = status.snapshot()
        self.assertEqual(1, snapshot.exit_code)
        self.assertEqual("RuntimeError", snapshot.error_type)
        self.assertEqual(
            ["RiichiLab continuous ranked runner failed: RuntimeError"], errors
        )

    def test_system_exit_is_converted_to_an_exit_code(self) -> None:
        for raised, expected in ((SystemExit(2), 2), (SystemExit("x"), 2)):
            with self.subTest(raised=raised):
                status = RiichiLabContinuousStatus()

                def run_cli(argv, **kwargs):
                    raise raised

                run_riichilab_continuous_worker(
                    ContinuousRankedPresentationFeed(),
                    status,
                    arena_argv=[],
                    stop_requested=lambda: False,
                    run_cli=run_cli,
                )
                self.assertEqual(expected, status.snapshot().exit_code)


class ContinuousPayloadTest(unittest.TestCase):
    def test_frame_key_is_unique_across_games(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        _, _, session = _session(feed)
        feed.open_game().publish_decision(decision(request_id=1))
        first = session.state_payload()
        feed.open_game().publish_decision(decision(request_id=1))
        second = session.state_payload()

        self.assertEqual("1:1", first["frame_key"])
        self.assertEqual("2:1", second["frame_key"])
        self.assertEqual(2, second["game"]["ordinal"])
        self.assertIn("前の表示game #1", second["info_text"])

    def test_waiting_and_terminal_status_texts(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        _, status, session = _session(feed)
        waiting = session.state_payload()
        self.assertIsNone(waiting["frame"])
        self.assertIn("最初の半荘", waiting["status_text"])

        buffer = feed.open_game()
        buffer.publish_completion(completion(scores=None))
        done = session.state_payload()
        self.assertIn("end_game", done["status_text"])
        self.assertIn("final scores: 未提供", done["info_text"])

        status.mark_finished(1)
        finished = session.state_payload()
        self.assertTrue(finished["worker_error"])
        self.assertIn("exit code 1", finished["status_text"])

    def test_follow_control_applies_a_deferred_game_switch(self) -> None:
        feed = ContinuousRankedPresentationFeed()
        _, _, session = _session(feed)
        feed.open_game().publish_decision(decision(request_id=1))
        session.state_payload()
        session.control("pause")
        feed.open_game().publish_decision(decision(request_id=5))
        self.assertEqual(1, session.state_payload()["game"]["ordinal"])

        resumed = session.control("follow")
        self.assertEqual(2, resumed["game"]["ordinal"])
        self.assertEqual(5, resumed["frame"]["request_id"])

    def test_unknown_control_fails_closed(self) -> None:
        _, _, session = _session(ContinuousRankedPresentationFeed())
        with self.assertRaises(ValueError):
            session.control("start")

    def test_rejects_foreign_values(self) -> None:
        with self.assertRaises(TypeError):
            build_continuous_state_payload(object(), object(), profile_name="x")

    def test_server_accepts_a_continuous_session_on_loopback_only(self) -> None:
        _, _, session = _session(ContinuousRankedPresentationFeed())
        server = RiichiLabHtmlServer(session, port=0)
        self.addCleanup(server.server_close)
        self.assertEqual(LOOPBACK_HOST, server.server_address[0])


class ArenaArgvTest(unittest.TestCase):
    def test_only_given_bounds_are_forwarded(self) -> None:
        self.assertEqual(
            ["--profile", "lisjong-dev"],
            build_arena_continuous_argv(
                profile_name="lisjong-dev",
                record_dir=None,
                duration_seconds=None,
                games=None,
            ),
        )
        self.assertEqual(
            [
                "--profile",
                "lisjong-dev",
                "--record-dir",
                "records",
                "--duration-seconds",
                "43200",
                "--games",
                "3",
            ],
            build_arena_continuous_argv(
                profile_name="lisjong-dev",
                record_dir="records",
                duration_seconds=43200,
                games=3,
            ),
        )

    def test_stop_file_is_forwarded_unchanged(self) -> None:
        stop_file = "/var/lib/lisjong riichilab/stop-requested"
        self.assertEqual(
            ["--profile", "lisjong-dev", "--stop-file", stop_file],
            build_arena_continuous_argv(
                profile_name="lisjong-dev",
                record_dir=None,
                duration_seconds=None,
                games=None,
                stop_file=stop_file,
            ),
        )


class _FakeContinuousRun:
    """continuous worker_targetの代わりに、feedへ2 game分publishして終わる。"""

    def __init__(self, *, exit_code: int = 0, wait_for_stop: bool = False) -> None:
        self.exit_code = exit_code
        self.wait_for_stop = wait_for_stop
        self.kwargs = None
        self.saw_stop = False
        self.saw_detach = False

    def __call__(self, feed, status, **kwargs) -> None:
        self.kwargs = kwargs
        status.mark_running()
        first = feed.open_game()
        first.publish_decision(decision(request_id=1))
        first.publish_completion(completion())
        if self.wait_for_stop:
            for _ in range(_WAIT_SECONDS * 100):
                if kwargs["stop_requested"]():
                    self.saw_stop = True
                    self.saw_detach = not feed.is_attached
                    break
                threading.Event().wait(0.01)
        status.mark_finished(self.exit_code)


class ContinuousLifecycleTest(unittest.TestCase):
    def test_run_end_closes_the_server_and_returns_the_arena_exit_code(
        self,
    ) -> None:
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                fake = _FakeContinuousRun(exit_code=exit_code)
                lines: list[str] = []

                code = main(
                    [
                        "--profile",
                        "lisjong-dev",
                        "--continuous",
                        "--duration-seconds",
                        "60",
                        "--record-dir",
                        "records",
                        "--port",
                        "0",
                    ],
                    writer=lines.append,
                    continuous_worker_target=fake,
                    worker_target=lambda *a, **k: self.fail("single mode ran"),
                    browser_open=lambda url: self.fail("browser must not open"),
                )

                self.assertEqual(exit_code, code)
                self.assertEqual(
                    [
                        "--profile",
                        "lisjong-dev",
                        "--record-dir",
                        "records",
                        "--duration-seconds",
                        "60",
                    ],
                    fake.kwargs["arena_argv"],
                )
                self.assertTrue(
                    any(f"http://{LOOPBACK_HOST}:" in line for line in lines)
                )

    def test_stop_file_reaches_the_arena_argv_exactly(self) -> None:
        fake = _FakeContinuousRun()
        stop_file = "records/../stop requested"

        code = main(
            [
                "--profile",
                "lisjong-dev",
                "--continuous",
                "--stop-file",
                stop_file,
                "--port",
                "0",
            ],
            writer=lambda line: None,
            continuous_worker_target=fake,
        )

        self.assertEqual(0, code)
        self.assertEqual(
            ["--profile", "lisjong-dev", "--stop-file", stop_file],
            fake.kwargs["arena_argv"],
        )

    def test_viewer_lines_do_not_collide_with_arena_summary_keys(self) -> None:
        lines: list[str] = []
        main(
            ["--profile", "lisjong-dev", "--continuous", "--port", "0"],
            writer=lines.append,
            continuous_worker_target=_FakeContinuousRun(),
        )
        self.assertTrue(lines)
        for line in lines:
            key = line.split(":", 1)[0].strip().lower() if ":" in line else None
            self.assertNotIn(key, _ARENA_SUMMARY_KEYS, line)

    def test_ctrl_c_requests_graceful_stop_and_detaches(self) -> None:
        fake = _FakeContinuousRun(wait_for_stop=True)
        lines: list[str] = []

        def handle_request(server):
            raise KeyboardInterrupt

        with patch.object(RiichiLabHtmlServer, "handle_request", handle_request):
            code = main(
                ["--profile", "lisjong-dev", "--continuous", "--port", "0"],
                writer=lines.append,
                continuous_worker_target=fake,
            )

        self.assertEqual(0, code)
        self.assertTrue(fake.saw_stop)
        self.assertTrue(fake.saw_detach)
        self.assertTrue(any("完走を待っています" in line for line in lines))

    def test_bind_failure_does_not_start_the_continuous_run(self) -> None:
        import socket

        occupied = socket.socket()
        occupied.bind((LOOPBACK_HOST, 0))
        occupied.listen()
        self.addCleanup(occupied.close)
        lines: list[str] = []

        code = main(
            [
                "--profile",
                "lisjong-dev",
                "--continuous",
                "--port",
                str(occupied.getsockname()[1]),
            ],
            writer=lines.append,
            continuous_worker_target=lambda *a, **k: self.fail("run must not start"),
        )

        self.assertEqual(1, code)
        self.assertIn("viewer serverを起動できません", lines[-1])

    def test_bounds_require_continuous_mode(self) -> None:
        for argv in (
            ["--profile", "lisjong-dev", "--games", "2"],
            ["--profile", "lisjong-dev", "--duration-seconds", "10"],
            ["--profile", "lisjong-dev", "--stop-file", "stop-requested"],
            ["--profile", "lisjong-dev", "--continuous", "--games", "0"],
            ["--profile", "lisjong-dev", "--continuous", "--duration-seconds", "x"],
        ):
            with self.subTest(argv=argv):
                with patch("sys.stderr"), self.assertRaises(SystemExit):
                    main(
                        argv,
                        worker_target=lambda *a, **k: None,
                        continuous_worker_target=lambda *a, **k: None,
                    )


class ContinuousArenaIntegrationTest(unittest.TestCase):
    """実Arena `run_continuous_ranked_cli`経由で、one-game primitiveだけfake。"""

    def test_arena_summary_reaches_stdout_and_token_never_leaks(self) -> None:
        received_tokens: list[str] = []
        payloads: list[dict] = []
        request_ids = iter(range(1, 100))

        async def fake_run_ranked_game(policy, token, *, presentation, **kwargs):
            received_tokens.append(token)
            presentation.publish_decision(decision(request_id=next(request_ids)))
            presentation.publish_completion(completion())

        original_handle = RiichiLabHtmlServer.handle_request

        def handle_request(server):
            payloads.append(server.session.state_payload())
            return original_handle(server)

        stdout = io.StringIO()
        stderr = io.StringIO()
        lines: list[str] = []
        with (
            patch.dict(os.environ, {"LISJONG_DEV_BOT_TOKEN": _TOKEN}),
            patch(
                "lisjong_arena.riichilab.continuous_ranked.run_ranked_game",
                fake_run_ranked_game,
            ),
            patch.object(RiichiLabHtmlServer, "handle_request", handle_request),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(
                [
                    "--profile",
                    "lisjong-dev",
                    "--continuous",
                    "--games",
                    "2",
                    "--port",
                    "0",
                ],
                writer=lines.append,
            )

        self.assertEqual(0, code)
        self.assertEqual([_TOKEN, _TOKEN], received_tokens)
        output = stdout.getvalue()
        self.assertIn("mode: ranked-continuous", output)
        self.assertIn("completed games: 2", output)
        self.assertIn("stopped reason: target_completed_games_reached", output)
        rendered = "\n".join([output, stderr.getvalue(), *lines, repr(payloads)])
        self.assertNotIn(_TOKEN, rendered)

    def test_arena_stop_file_finishes_the_hanchan_and_starts_no_new_one(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "stop-requested"
            games: list[int] = []

            async def fake_run_ranked_game(policy, token, *, presentation, **kwargs):
                games.append(len(games) + 1)
                presentation.publish_decision(decision(request_id=len(games)))
                # The operator asks to stop while this hanchan is in progress.
                stop_file.write_text("operator\n", encoding="ascii")
                presentation.publish_completion(completion())

            stdout = io.StringIO()
            with (
                patch.dict(os.environ, {"LISJONG_DEV_BOT_TOKEN": _TOKEN}),
                patch(
                    "lisjong_arena.riichilab.continuous_ranked.run_ranked_game",
                    fake_run_ranked_game,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = main(
                    [
                        "--profile",
                        "lisjong-dev",
                        "--continuous",
                        "--stop-file",
                        str(stop_file),
                        "--port",
                        "0",
                    ],
                    writer=lambda line: None,
                )
            # The viewer neither removes nor rewrites the operator's file.
            self.assertEqual("operator\n", stop_file.read_text(encoding="ascii"))

        self.assertEqual(0, code)
        self.assertEqual([1], games)
        output = stdout.getvalue()
        self.assertIn("stop file: on", output)
        self.assertIn("completed games: 1", output)
        self.assertIn("stopped reason: stop_requested", output)


if __name__ == "__main__":
    unittest.main()
