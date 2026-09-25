"""RiichiLab live HTML viewerのtest。

real RiichiLab networkへは接続しない。Arenaのsupported presentation valueと
injected worker、`127.0.0.1`上のephemeral portだけで、presentation-only
semantics、server boundary、lifecycleを固定する。
"""

import http.client
import json
import socket
import threading
import unittest
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from lisjong.policy_contract import Seat
from lisjong_arena.riichilab.live_presentation import BoundedRankedPresentationBuffer

from lisjong_play.riichilab_html import (
    CONTROL_COMMANDS,
    LOOPBACK_HOST,
    RiichiLabHtmlServer,
    RiichiLabHtmlSession,
    build_state_payload,
    main,
    render_page_html,
)
from lisjong_play.riichilab_source import (
    RiichiLabLiveController,
    RiichiLabRunSummary,
    RiichiLabWorkerStatus,
    build_decision_frame,
    run_riichilab_worker,
)
from lisjong_play.tile_images import tile_asset_traversable
from tests._riichilab_fixtures import FINAL_SCORES, completion, decision, failure

_TOKEN = "test-only-bot-token-value"
_WAIT_SECONDS = 5


def _session(*, capacity: int = 4):
    buffer = BoundedRankedPresentationBuffer()
    controller = RiichiLabLiveController(buffer, capacity=capacity)
    status = RiichiLabWorkerStatus()
    return buffer, status, RiichiLabHtmlSession(controller, status)


class StatePayloadTest(unittest.TestCase):
    def test_frame_carries_the_shared_board_projection(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_decision(decision(request_id=7, seat=Seat.SEAT_2))

        payload = session.state_payload()

        expected = build_decision_frame(
            decision(request_id=7, seat=Seat.SEAT_2), ordinal=1
        )
        self.assertEqual(asdict(expected.board), payload["frame"]["board"])
        self.assertEqual(7, payload["frame"]["request_id"])
        self.assertEqual("P3", payload["frame"]["seat_label"])
        self.assertEqual("打牌 東", payload["frame"]["action_label"])
        self.assertTrue(payload["following"])
        self.assertFalse(payload["can_step"])
        self.assertEqual("進行中", payload["status_text"])
        # JSONへ変換できる値だけで構成される。
        json.dumps(payload)

    def test_waiting_state_before_the_first_decision(self) -> None:
        _buffer, _status, session = _session()

        payload = session.state_payload()

        self.assertIsNone(payload["frame"])
        self.assertEqual("ranked接続中", payload["status_text"])
        self.assertIsNone(payload["completion"])
        self.assertIsNone(payload["failure_type"])

    def test_completion_shows_reported_scores_without_a_rank(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_decision(decision())
        buffer.publish_completion(completion())

        payload = session.state_payload()

        self.assertEqual(list(FINAL_SCORES), payload["completion"]["scores"])
        self.assertEqual("end_game: 半荘が終了しました。", payload["status_text"])
        self.assertIn(
            "final scores: 32000 / 24000 / 23000 / 21000", payload["info_text"]
        )
        self.assertNotIn("順位", payload["info_text"])
        # 順位はseamが提供しないため、payloadへ項目自体を持たない。
        self.assertEqual({"seat_label", "scores"}, set(payload["completion"]))

    def test_missing_final_scores_are_not_inferred(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_completion(completion(scores=None))

        payload = session.state_payload()

        self.assertIsNone(payload["completion"]["scores"])
        self.assertIn("final scores: 未提供", payload["info_text"])

    def test_failure_is_not_presented_as_a_completion(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_failure(failure())

        payload = session.state_payload()

        self.assertIsNone(payload["completion"])
        self.assertEqual("UnexpectedDisconnectError", payload["failure_type"])
        self.assertEqual("failure: UnexpectedDisconnectError", payload["status_text"])
        self.assertNotIn("end_game", payload["status_text"])

    def test_worker_error_is_reported_as_an_error(self) -> None:
        _buffer, status, session = _session()
        status.mark_failed("profile error: LISJONG_DEV_BOT_TOKEN is not set")

        payload = session.state_payload()

        self.assertTrue(payload["worker_error"])
        self.assertEqual("ranked runはエラーで終了しました。", payload["status_text"])
        self.assertIn("ERROR: profile error", payload["info_text"])

    def test_worker_summary_and_record_identity_are_listed(self) -> None:
        _buffer, status, session = _session()
        status.mark_running("profile: lisjong-dev / mode: ranked")
        status.mark_finished(
            RiichiLabRunSummary(
                profile="lisjong-dev",
                seat_label="P2",
                requests=12,
                responses=12,
                scores=FINAL_SCORES,
                record_identity="sha256:abc",
            )
        )

        info = session.state_payload()["info_text"]

        self.assertIn("profile: lisjong-dev / mode: ranked", info)
        self.assertIn("完了: seat=P2 / requests=12 / responses=12", info)
        self.assertIn("record identity: sha256:abc", info)

    def test_rejects_foreign_values(self) -> None:
        with self.assertRaises(TypeError):
            build_state_payload(object(), RiichiLabWorkerStatus().snapshot())
        with self.assertRaises(TypeError):
            RiichiLabHtmlSession(object(), RiichiLabWorkerStatus())


class SessionControlTest(unittest.TestCase):
    def test_pause_freezes_display_but_still_drains(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_decision(decision(request_id=1))
        session.state_payload()

        paused = session.control("pause")
        buffer.publish_decision(decision(request_id=2))
        buffer.publish_completion(completion())
        payload = session.state_payload()

        self.assertFalse(paused["following"])
        self.assertEqual(1, payload["frame"]["request_id"])
        self.assertEqual(2, payload["received_frames"])
        self.assertEqual(1, payload["pending_frames"])
        self.assertTrue(payload["can_step"])
        # terminal factはpause中も受信される。
        self.assertIsNotNone(payload["completion"])
        self.assertEqual(0, buffer.pending_decisions)

    def test_step_advances_exactly_one_retained_frame(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_decision(decision(request_id=1))
        session.state_payload()
        session.control("pause")
        for request_id in (2, 3):
            buffer.publish_decision(decision(request_id=request_id))

        stepped = session.control("step")

        self.assertEqual(2, stepped["frame"]["request_id"])
        self.assertEqual(1, stepped["pending_frames"])
        self.assertFalse(stepped["following"])

    def test_step_while_following_is_a_no_op(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_decision(decision(request_id=1))

        payload = session.control("step")

        self.assertTrue(payload["following"])
        self.assertEqual(1, payload["frame"]["request_id"])

    def test_follow_jumps_to_the_latest_retained_frame(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_decision(decision(request_id=1))
        session.state_payload()
        session.control("pause")
        for request_id in (2, 3, 4):
            buffer.publish_decision(decision(request_id=request_id))

        payload = session.control("follow")

        self.assertTrue(payload["following"])
        self.assertEqual(4, payload["frame"]["request_id"])
        self.assertEqual(0, payload["pending_frames"])
        self.assertEqual(2, payload["skipped_frames"])

    def test_unknown_command_fails_closed_without_changing_state(self) -> None:
        buffer, _status, session = _session()
        buffer.publish_decision(decision(request_id=1))
        session.state_payload()

        for command in ("start", "resume", "", "PAUSE"):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    session.control(command)
        self.assertTrue(session.state_payload()["following"])
        self.assertEqual(("pause", "step", "follow"), CONTROL_COMMANDS)

    def test_detach_only_detaches_the_presentation(self) -> None:
        buffer, _status, session = _session()

        session.detach()

        self.assertFalse(buffer.is_attached)


class _LiveServer:
    """ephemeral portの実serverをthreadで動かすtest helper。"""

    def __init__(self, session: RiichiLabHtmlSession) -> None:
        self.server = RiichiLabHtmlServer(session, port=0)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        connection = http.client.HTTPConnection(
            LOOPBACK_HOST, self.port, timeout=_WAIT_SECONDS
        )
        try:
            connection.putrequest(method, path, skip_host=True)
            merged = {"Host": f"{LOOPBACK_HOST}:{self.port}"}
            merged.update(headers or {})
            for name, value in merged.items():
                connection.putheader(name, value)
            if body is not None:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def control(self, payload: object, *, headers: dict[str, str] | None = None):
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        return self.request(
            "POST", "/control", body=json.dumps(payload).encode(), headers=merged
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(_WAIT_SECONDS)


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.buffer, self.status, self.session = _session()
        self.live = _LiveServer(self.session)
        self.addCleanup(self.live.close)

    def test_binds_only_to_loopback(self) -> None:
        self.assertEqual(LOOPBACK_HOST, self.live.server.server_address[0])
        self.assertEqual(
            f"http://{LOOPBACK_HOST}:{self.live.port}/", self.live.server.url
        )

    def test_page_is_served_with_a_restrictive_csp(self) -> None:
        status, headers, body = self.live.request("GET", "/")

        self.assertEqual(200, status)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual(render_page_html().encode(), body)

    def test_state_drains_the_arena_buffer(self) -> None:
        self.buffer.publish_decision(decision(request_id=5))

        status, headers, body = self.live.request("GET", "/state")

        self.assertEqual(200, status)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual(5, json.loads(body)["frame"]["request_id"])
        self.assertEqual(0, self.buffer.pending_decisions)

    def test_known_tiles_are_served_from_vendored_assets(self) -> None:
        for label in ("東", "5mr", "9s"):
            with self.subTest(label=label):
                status, headers, body = self.live.request(
                    "GET", "/tiles/" + quote(label)
                )
                self.assertEqual(200, status)
                self.assertEqual("image/png", headers["Content-Type"])
                # 不変assetなので描き直しごとに再取得しない。
                self.assertIn("immutable", headers["Cache-Control"])
                self.assertEqual(tile_asset_traversable(label).read_bytes(), body)

    def test_unknown_tiles_and_paths_are_not_found(self) -> None:
        for path in (
            "/tiles/0m",
            "/tiles/Ton.png",
            "/tiles/..%2F..%2F__init__.py",
            "/tiles/../../__init__.py",
            "/tiles/",
            "/missing",
            "/index.html",
        ):
            with self.subTest(path=path):
                status, _headers, _body = self.live.request("GET", path)
                self.assertEqual(404, status)

    def test_foreign_host_is_rejected(self) -> None:
        for host in ("evil.example", f"evil.example:{self.live.port}", "127.0.0.1"):
            with self.subTest(host=host):
                status, _h, _b = self.live.request(
                    "GET", "/state", headers={"Host": host}
                )
                self.assertEqual(403, status)
                status, _h, _b = self.live.control(
                    {"command": "pause"}, headers={"Host": host}
                )
                self.assertEqual(403, status)
        self.assertTrue(self.session.state_payload()["following"])

    def test_localhost_host_is_accepted(self) -> None:
        status, _h, _b = self.live.request(
            "GET", "/state", headers={"Host": f"localhost:{self.live.port}"}
        )
        self.assertEqual(200, status)

    def test_control_moves_only_the_display_cursor(self) -> None:
        self.buffer.publish_decision(decision(request_id=1))

        status, _headers, body = self.live.control(
            {"command": "pause"},
            headers={"Origin": f"http://{LOOPBACK_HOST}:{self.live.port}"},
        )

        self.assertEqual(200, status)
        self.assertFalse(json.loads(body)["following"])
        self.assertTrue(self.buffer.is_attached)

    def test_cross_origin_control_is_rejected(self) -> None:
        status, _h, _b = self.live.control(
            {"command": "pause"}, headers={"Origin": "http://evil.example"}
        )

        self.assertEqual(403, status)
        self.assertTrue(self.session.state_payload()["following"])

    def test_non_json_control_is_rejected(self) -> None:
        status, _h, _b = self.live.request(
            "POST",
            "/control",
            body=b"command=pause",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        self.assertEqual(415, status)
        self.assertTrue(self.session.state_payload()["following"])

    def test_malformed_or_unknown_control_is_rejected(self) -> None:
        cases = (
            b"{not json",
            b"\xff\xfe",
            json.dumps(["pause"]).encode(),
            json.dumps({"command": "start"}).encode(),
            json.dumps({"command": 1}).encode(),
            json.dumps({}).encode(),
        )
        for body in cases:
            with self.subTest(body=body):
                status, _h, _b = self.live.request(
                    "POST",
                    "/control",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(400, status)
        self.assertTrue(self.session.state_payload()["following"])

    def test_oversized_control_body_is_rejected(self) -> None:
        body = json.dumps({"command": "pause", "pad": "x" * 2048}).encode()

        status, _h, _b = self.live.request(
            "POST", "/control", body=body, headers={"Content-Type": "application/json"}
        )

        self.assertEqual(413, status)

    def test_control_on_other_paths_is_not_found(self) -> None:
        status, _h, _b = self.live.request(
            "POST",
            "/state",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(404, status)

    def test_server_rejects_invalid_ports(self) -> None:
        for port, error in ((-1, ValueError), (65536, ValueError), (True, TypeError)):
            with self.subTest(port=port):
                with self.assertRaises(error):
                    RiichiLabHtmlServer(self.session, port=port)


class PageTest(unittest.TestCase):
    def test_page_has_no_external_references_or_start_control(self) -> None:
        html = render_page_html()

        self.assertNotRegex(html, r"(?i)(src|href)\s*=\s*[\"']?(https?:|//)")
        self.assertNotIn("@import", html)
        self.assertNotIn('"start"', html)
        for command in CONTROL_COMMANDS:
            self.assertIn(f'"{command}"', html)
        self.assertIn("createBoardRenderer", html)


class _FakeRun:
    """worker_targetの代わりに、Arena bufferへpublishしてdetachを待つ。"""

    def __init__(self) -> None:
        self.published = threading.Event()
        self.saw_detach = False
        self.finished = False
        self.kwargs = None

    def __call__(self, buffer, status, **kwargs) -> None:
        self.kwargs = kwargs
        status.mark_running("profile: lisjong-dev / mode: ranked")
        buffer.publish_decision(decision(request_id=1))
        self.published.set()
        # server停止(detach)後も、workerはcancelされずに自分で完走する。
        for _ in range(_WAIT_SECONDS * 100):
            if not buffer.is_attached:
                self.saw_detach = True
                break
            threading.Event().wait(0.01)
        buffer.publish_completion(completion())
        self.finished = True


class LifecycleTest(unittest.TestCase):
    def _serve_until_interrupt(self, fake: _FakeRun, captured: list):
        def serve_forever(server, *args, **kwargs):
            self.assertTrue(fake.published.wait(_WAIT_SECONDS))
            captured.append(server.session.state_payload())
            captured.append(server.url)
            raise KeyboardInterrupt

        return patch.object(RiichiLabHtmlServer, "serve_forever", serve_forever)

    def test_ctrl_c_detaches_and_joins_the_ranked_worker(self) -> None:
        fake = _FakeRun()
        captured: list = []
        lines: list[str] = []

        with self._serve_until_interrupt(fake, captured):
            code = main(
                ["--profile", "lisjong-dev", "--port", "0"],
                writer=lines.append,
                worker_target=fake,
                browser_open=lambda url: self.fail("browser must not open"),
            )

        self.assertEqual(0, code)
        self.assertTrue(fake.saw_detach)
        self.assertTrue(fake.finished)
        self.assertEqual(
            {"profile_name": "lisjong-dev", "record_path": None}, fake.kwargs
        )
        self.assertEqual(1, captured[0]["frame"]["request_id"])
        self.assertTrue(any(captured[1] in line for line in lines))
        self.assertTrue(any("完走を待っています" in line for line in lines))

    def test_open_browser_opens_the_loopback_url(self) -> None:
        fake = _FakeRun()
        captured: list = []
        opened: list[str] = []

        with self._serve_until_interrupt(fake, captured):
            main(
                ["--profile", "lisjong-dev", "--port", "0", "--open-browser"],
                writer=lambda line: None,
                worker_target=fake,
                browser_open=opened.append,
            )

        self.assertEqual([captured[1]], opened)
        self.assertTrue(opened[0].startswith(f"http://{LOOPBACK_HOST}:"))

    def test_bind_failure_does_not_start_the_ranked_run(self) -> None:
        occupied = socket.socket()
        occupied.bind((LOOPBACK_HOST, 0))
        occupied.listen()
        self.addCleanup(occupied.close)
        port = occupied.getsockname()[1]
        lines: list[str] = []

        code = main(
            ["--profile", "lisjong-dev", "--port", str(port)],
            writer=lines.append,
            worker_target=lambda *a, **k: self.fail("worker must not start"),
        )

        self.assertEqual(1, code)
        self.assertIn("viewer serverを起動できません", lines[-1])

    def test_profile_is_required_and_port_is_validated(self) -> None:
        for argv in (
            [],
            ["--profile", "unknown-profile"],
            ["--profile", "lisjong-dev", "--port", "70000"],
            ["--profile", "lisjong-dev", "--port", "x"],
            ["--profile", "lisjong-dev", "--host", "0.0.0.0"],
        ):
            with self.subTest(argv=argv):
                with (
                    patch("sys.stderr"),
                    self.assertRaises(SystemExit),
                ):
                    main(argv, worker_target=lambda *a, **k: None)


class CredentialBoundaryTest(unittest.TestCase):
    """実`run_riichilab_worker`経由でも、tokenがviewerへ現れないことを固定する。"""

    @contextmanager
    def _resolved(self):
        profile = SimpleNamespace(
            name="lisjong-dev",
            credential_env_var="LISJONG_DEV_BOT_TOKEN",
            policy_factory=lambda: SimpleNamespace(),
            runtime_namespace="lisjong-dev",
        )
        with (
            patch(
                "lisjong_play.riichilab_source.resolve_profile", return_value=profile
            ),
            patch(
                "lisjong_play.riichilab_source.resolve_credential", return_value=_TOKEN
            ),
            patch(
                "lisjong_play.riichilab_source.build_runtime_summary",
                return_value=SimpleNamespace(),
            ),
            patch(
                "lisjong_play.riichilab_source.format_runtime_summary",
                return_value="profile: lisjong-dev / mode: ranked",
            ),
        ):
            yield

    def test_token_never_reaches_payload_page_or_output(self) -> None:
        received_tokens: list[str] = []
        published = threading.Event()

        def run_game(policy, token, *, presentation):
            received_tokens.append(token)
            presentation.publish_decision(decision(request_id=1))
            presentation.publish_completion(completion())
            published.set()
            return SimpleNamespace(
                seat=Seat.SEAT_1,
                requests_received=1,
                responses_sent=1,
                scores=FINAL_SCORES,
            )

        def worker_target(buffer, status, **kwargs):
            run_riichilab_worker(buffer, status, run_game=run_game, **kwargs)

        captured: list = []

        def serve_forever(server, *args, **kwargs):
            self.assertTrue(published.wait(_WAIT_SECONDS))
            captured.append(json.dumps(server.session.state_payload()))
            captured.append(server.page_html)
            raise KeyboardInterrupt

        lines: list[str] = []
        with (
            self._resolved(),
            patch.object(RiichiLabHtmlServer, "serve_forever", serve_forever),
        ):
            code = main(
                ["--profile", "lisjong-dev", "--port", "0"],
                writer=lines.append,
                worker_target=worker_target,
            )

        self.assertEqual(0, code)
        self.assertEqual([_TOKEN], received_tokens)
        self.assertIn('"request_id": 1', captured[0])
        for text in [*captured, *lines]:
            self.assertNotIn(_TOKEN, text)


if __name__ == "__main__":
    unittest.main()
