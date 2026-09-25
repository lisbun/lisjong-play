import base64
import json
import re
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from lisjong_play.replay_controller import (
    SPEED_CHOICES,
    ReplayControlError,
    ReplayController,
)
from lisjong_play.replay_html import (
    PAYLOAD_ELEMENT_ID,
    ReplayHtmlOutputError,
    build_replay_payload,
    collect_tile_labels,
    default_output_path,
    main,
    render_replay_html,
    write_replay_html,
)
from lisjong_play.replay_source import ReplayLoadError, load_replay_timeline
from lisjong_play.tile_images import TileImageAssetError, tile_asset_traversable
from tests._replay_fixtures import save_fixture_record

_PAYLOAD_PATTERN = re.compile(
    r'<script type="application/json" id="' + PAYLOAD_ELEMENT_ID + r'">(.*?)</script>',
    re.DOTALL,
)


def _timeline():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "record"
        save_fixture_record(path)
        return load_replay_timeline(path)


def _embedded_payload(html: str) -> dict:
    match = _PAYLOAD_PATTERN.search(html)
    assert match is not None
    return json.loads(match.group(1))


class ReplayPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.timeline = _timeline()

    def test_frames_and_rounds_follow_timeline_order(self) -> None:
        payload = build_replay_payload(self.timeline)

        self.assertEqual(len(payload["frames"]), len(self.timeline.frames))
        for entry, frame in zip(payload["frames"], self.timeline.frames, strict=True):
            self.assertEqual(entry["round_index"], frame.round_index)
            self.assertEqual(entry["board"], asdict(frame.board))
        self.assertEqual(
            payload["rounds"],
            [
                {"label": item.label, "result_text": item.result_text}
                for item in self.timeline.rounds
            ],
        )
        self.assertEqual(payload["final_result_text"], self.timeline.final_result_text)
        self.assertEqual(payload["metadata_text"], self.timeline.metadata_text)

    def test_round_navigation_targets_match_controller(self) -> None:
        payload = build_replay_payload(self.timeline)

        for index, entry in enumerate(payload["frames"]):
            controller = ReplayController(self.timeline)
            controller.to_index(index)
            controller.to_previous_round()
            self.assertEqual(entry["previous_round_index"], controller.index)
            controller.to_index(index)
            controller.to_next_round()
            self.assertEqual(entry["next_round_index"], controller.index)

    def test_position_text_uses_controller_position(self) -> None:
        payload = build_replay_payload(self.timeline)
        controller = ReplayController(self.timeline)
        position = controller.position()

        self.assertEqual(
            payload["frames"][0]["position_text"],
            f"{position.round.label}"
            f"  /  局 1/{position.round_count}"
            f"  /  手順 1/{position.frame_count}"
            f"  /  step {position.frame.step_ordinal}",
        )

    def test_speed_intervals_come_from_controller(self) -> None:
        payload = build_replay_payload(self.timeline)
        controller = ReplayController(self.timeline)
        expected = []
        for speed in SPEED_CHOICES:
            controller.set_speed(speed)
            expected.append(
                {"label": f"{speed}", "interval_ms": controller.frame_interval_ms}
            )

        self.assertEqual(payload["speeds"], expected)
        self.assertEqual(payload["default_speed_label"], "1.0")

    def test_rejects_non_timeline(self) -> None:
        with self.assertRaises(TypeError):
            build_replay_payload(object())


class ControllerToIndexTest(unittest.TestCase):
    def test_to_index_moves_and_rejects_out_of_range(self) -> None:
        timeline = _timeline()
        controller = ReplayController(timeline)
        last = len(timeline.frames) - 1

        self.assertTrue(controller.to_index(last))
        self.assertEqual(controller.index, last)
        self.assertFalse(controller.to_index(last))
        for invalid in (-1, last + 1, True, "0", 1.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ReplayControlError):
                    controller.to_index(invalid)
        self.assertEqual(controller.index, last)


class ReplayHtmlRenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.timeline = _timeline()

    def test_embeds_only_used_tiles_from_vendored_assets(self) -> None:
        html = render_replay_html(self.timeline)
        payload = _embedded_payload(html)
        labels = collect_tile_labels(self.timeline)

        self.assertTrue(labels)
        self.assertEqual(tuple(sorted(payload["tiles"])), labels)
        for label in labels:
            expected = base64.b64encode(
                tile_asset_traversable(label).read_bytes()
            ).decode("ascii")
            self.assertEqual(
                payload["tiles"][label], f"data:image/png;base64,{expected}"
            )

    def test_has_no_external_references(self) -> None:
        html = render_replay_html(self.timeline)

        self.assertNotRegex(html, r"(?i)(src|href)\s*=\s*[\"']?(https?:|//)")
        self.assertNotIn("@import", html)
        self.assertNotRegex(html, r"(?i)url\(")
        self.assertIn("default-src 'none'", html)

    def test_record_strings_cannot_break_out_of_payload(self) -> None:
        hostile = '</script><script>alert("x")</script><!-- &  '
        timeline = replace(self.timeline, metadata_text=hostile)

        html = render_replay_html(timeline)
        payload_text = _PAYLOAD_PATTERN.search(html).group(1)

        self.assertNotIn("<", payload_text)
        self.assertNotIn(">", payload_text)
        self.assertNotIn("&", payload_text)
        self.assertNotIn(" ", payload_text)
        self.assertEqual(json.loads(payload_text)["metadata_text"], hostile)
        self.assertEqual(html.count("</script>"), 2)

    def test_unknown_tile_label_fails_closed(self) -> None:
        frame = self.timeline.frames[0]
        board = replace(frame.board, dora_indicators=("??",))
        frames = (replace(frame, board=board),) + self.timeline.frames[1:]
        timeline = replace(self.timeline, frames=frames)

        with self.assertRaises(TileImageAssetError):
            render_replay_html(timeline)


class WriteReplayHtmlTest(unittest.TestCase):
    def test_refuses_existing_file_unless_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.html"
            path.write_text("old", encoding="utf-8")

            with self.assertRaises(ReplayHtmlOutputError):
                write_replay_html("new", path)
            self.assertEqual(path.read_text(encoding="utf-8"), "old")

            path.chmod(0o644)
            write_replay_html("new", path, overwrite=True)
            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["out.html"])

    def test_failed_write_leaves_no_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.html"

            with self.assertRaises(UnicodeEncodeError):
                write_replay_html("\ud800", path)

            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_failed_overwrite_keeps_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.html"
            path.write_text("old", encoding="utf-8")

            with self.assertRaises(UnicodeEncodeError):
                write_replay_html("\ud800", path, overwrite=True)

            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["out.html"])

    def test_default_output_path_uses_record_directory_name(self) -> None:
        self.assertEqual(
            default_output_path(Path("some") / "record-7"), Path("record-7.html")
        )


class ReplayHtmlMainTest(unittest.TestCase):
    def test_writes_html_from_strict_loaded_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "record"
            save_fixture_record(record)
            output = Path(directory) / "replay.html"
            messages: list[str] = []

            code = main([str(record), "-o", str(output)], writer=messages.append)

            self.assertEqual(code, 0)
            html = output.read_text(encoding="utf-8")
            payload = _embedded_payload(html)
            self.assertEqual(len(payload["frames"]), len(_timeline().frames))
            # record pathのようなlocal環境情報はHTMLへ埋め込まない。
            self.assertNotIn(directory, html)
            self.assertNotIn(Path(directory).name, html)
            self.assertIn(str(output), messages[-1])

    def test_load_failure_writes_nothing(self) -> None:
        def reject(_path):
            raise ReplayLoadError("corrupt bundle")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.html"
            messages: list[str] = []

            code = main(
                ["missing", "-o", str(output)],
                writer=messages.append,
                timeline_loader=reject,
            )

            self.assertEqual(code, 1)
            self.assertFalse(output.exists())
            self.assertIn("corrupt bundle", messages[-1])

    def test_missing_record_fails_closed_through_real_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.html"

            code = main(
                [str(Path(directory) / "nope"), "-o", str(output)],
                writer=lambda _message: None,
            )

            self.assertEqual(code, 1)
            self.assertFalse(output.exists())

    def test_existing_output_is_refused_without_overwrite(self) -> None:
        timeline = _timeline()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.html"
            output.write_text("old", encoding="utf-8")
            messages: list[str] = []

            code = main(
                ["record", "-o", str(output)],
                writer=messages.append,
                timeline_loader=lambda _path: timeline,
            )
            self.assertEqual(code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "old")

            code = main(
                ["record", "-o", str(output), "--overwrite"],
                writer=messages.append,
                timeline_loader=lambda _path: timeline,
            )
            self.assertEqual(code, 0)
            self.assertIn(PAYLOAD_ELEMENT_ID, output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
