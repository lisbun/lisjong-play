import tempfile
import unittest
from pathlib import Path

from lisjong_play.replay_controller import (
    BASE_FRAME_INTERVAL_MS,
    SPEED_CHOICES,
    ReplayControlError,
    ReplayController,
)
from lisjong_play.replay_source import load_replay_timeline
from tests._replay_fixtures import save_fixture_record


def _timeline():
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "record"
    save_fixture_record(path)
    timeline = load_replay_timeline(path)
    directory.cleanup()
    return timeline


class ReplayNavigationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ReplayController(_timeline())

    def test_starts_at_the_first_recorded_frame(self) -> None:
        position = self.controller.position()
        self.assertEqual(0, self.controller.index)
        self.assertEqual(1, position.frame_number)
        self.assertEqual(4, position.frame_count)
        self.assertTrue(position.at_first)
        self.assertFalse(position.at_last)

    def test_next_and_previous_are_deterministic(self) -> None:
        boards = [self.controller.position().frame.board]
        while self.controller.to_next():
            boards.append(self.controller.position().frame.board)
        self.assertEqual(4, len(boards))

        backwards = [self.controller.position().frame.board]
        while self.controller.to_previous():
            backwards.append(self.controller.position().frame.board)
        self.assertEqual(boards, list(reversed(backwards)))

    def test_forward_backward_forward_returns_to_the_same_state(self) -> None:
        self.controller.to_next()
        self.controller.to_next()
        expected = self.controller.position()

        self.controller.to_next()
        self.controller.to_previous()

        self.assertEqual(expected, self.controller.position())

    def test_does_not_advance_past_the_end_boundary(self) -> None:
        for _ in range(10):
            self.controller.to_next()
        self.assertEqual(3, self.controller.index)
        self.assertFalse(self.controller.to_next())
        self.assertTrue(self.controller.position().at_last)

    def test_does_not_move_before_the_start_boundary(self) -> None:
        self.assertFalse(self.controller.to_previous())
        self.assertEqual(0, self.controller.index)

    def test_first_returns_to_the_start(self) -> None:
        self.controller.to_next()
        self.controller.to_next()
        self.assertTrue(self.controller.to_first())
        self.assertEqual(0, self.controller.index)
        self.assertFalse(self.controller.to_first())


class ReplayRoundNavigationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ReplayController(_timeline())

    def test_next_round_moves_to_the_recorded_round_start(self) -> None:
        self.assertTrue(self.controller.to_next_round())
        position = self.controller.position()
        self.assertEqual(2, self.controller.index)
        self.assertEqual("東2局 0本場", position.round.label)
        self.assertEqual(2, position.round_number)
        self.assertEqual(2, position.round_count)

    def test_next_round_stops_at_the_last_recorded_round(self) -> None:
        self.controller.to_next_round()
        self.assertFalse(self.controller.to_next_round())
        self.assertEqual(2, self.controller.index)

    def test_previous_round_first_returns_to_the_current_round_start(self) -> None:
        self.controller.to_next_round()
        self.controller.to_next()
        self.assertEqual(3, self.controller.index)

        self.assertTrue(self.controller.to_previous_round())
        self.assertEqual(2, self.controller.index)
        self.assertEqual("東2局 0本場", self.controller.position().round.label)

    def test_previous_round_from_a_round_start_moves_to_the_previous_round(
        self,
    ) -> None:
        self.controller.to_next_round()
        self.assertTrue(self.controller.to_previous_round())
        self.assertEqual(0, self.controller.index)
        self.assertEqual("東1局 0本場", self.controller.position().round.label)

    def test_previous_round_stops_at_the_first_recorded_round(self) -> None:
        self.assertFalse(self.controller.to_previous_round())
        self.assertEqual(0, self.controller.index)

    def test_round_navigation_matches_recorded_round_identity(self) -> None:
        seen = []
        self.controller.to_first()
        seen.append(self.controller.position().round.label)
        while self.controller.to_next_round():
            seen.append(self.controller.position().round.label)
        self.assertEqual([item.label for item in self.controller.timeline.rounds], seen)


class ReplayPlaybackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ReplayController(_timeline())

    def test_play_and_pause_toggle_playback_state_only(self) -> None:
        before = self.controller.position()
        self.controller.play()
        self.assertTrue(self.controller.playing)
        self.assertEqual(before, self.controller.position())

        self.controller.pause()
        self.assertFalse(self.controller.playing)
        self.assertEqual(before, self.controller.position())

    def test_playback_advances_one_recorded_frame_per_tick(self) -> None:
        self.controller.play()
        self.assertTrue(self.controller.advance_for_playback())
        self.assertEqual(1, self.controller.index)

    def test_paused_playback_does_not_advance(self) -> None:
        self.controller.pause()
        self.assertFalse(self.controller.advance_for_playback())
        self.assertEqual(0, self.controller.index)

    def test_playback_pauses_itself_at_the_end_boundary(self) -> None:
        self.controller.play()
        while self.controller.advance_for_playback():
            pass
        self.assertEqual(3, self.controller.index)
        self.assertFalse(self.controller.playing)

    def test_play_at_the_end_boundary_does_not_start(self) -> None:
        while self.controller.to_next():
            pass
        self.controller.play()
        self.assertFalse(self.controller.playing)

    def test_speed_changes_only_the_interval(self) -> None:
        self.controller.to_next()
        before = self.controller.position()

        self.controller.set_speed(2.0)

        self.assertEqual(2.0, self.controller.speed)
        self.assertEqual(before, self.controller.position())
        self.assertEqual(BASE_FRAME_INTERVAL_MS // 2, self.controller.frame_interval_ms)

    def test_every_supported_speed_yields_a_positive_interval(self) -> None:
        for speed in SPEED_CHOICES:
            with self.subTest(speed=speed):
                self.controller.set_speed(speed)
                self.assertGreater(self.controller.frame_interval_ms, 0)

    def test_unsupported_speed_fails_closed(self) -> None:
        for value in (0, -1, 3.0, "fast", None):
            with self.subTest(value=value):
                with self.assertRaises(ReplayControlError):
                    self.controller.set_speed(value)
        self.assertEqual(1.0, self.controller.speed)

    def test_navigation_semantics_are_unchanged_by_speed(self) -> None:
        fast = ReplayController(self.controller.timeline)
        fast.set_speed(4.0)

        slow_positions = []
        while True:
            slow_positions.append(self.controller.position())
            if not self.controller.to_next():
                break
        fast_positions = []
        while True:
            fast_positions.append(fast.position())
            if not fast.to_next():
                break

        self.assertEqual(slow_positions, fast_positions)


class ReplayControllerContractTest(unittest.TestCase):
    def test_requires_a_timeline(self) -> None:
        with self.assertRaises(TypeError):
            ReplayController(object())


if __name__ == "__main__":
    unittest.main()
