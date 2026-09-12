import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lisjong_play.gui import GuiUnavailableError
from lisjong_play.replay_controller import BASE_FRAME_INTERVAL_MS
from lisjong_play.replay_gui import _TkReplayApplication, main
from lisjong_play.replay_source import ReplayLoadError, load_replay_timeline
from tests._replay_fixtures import save_fixture_record


def _timeline():
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "record"
    save_fixture_record(path)
    timeline = load_replay_timeline(path)
    directory.cleanup()
    return timeline


class _FakeRoot:
    """`after` / `after_cancel`のcall順だけを記録するTk root double。"""

    def __init__(self) -> None:
        self.scheduled: list[tuple[int, object]] = []
        self.cancelled: list[str] = []
        self.destroyed = False
        self._next = 0
        self.protocol_handler = None

    def title(self, _value):  # pragma: no cover - trivial Tk surface
        return None

    def geometry(self, _value):  # pragma: no cover - trivial Tk surface
        return None

    def minsize(self, *_args):  # pragma: no cover - trivial Tk surface
        return None

    def protocol(self, _name, handler):
        self.protocol_handler = handler

    def after(self, delay, callback):
        self._next += 1
        job = f"job-{self._next}"
        self.scheduled.append((delay, callback))
        self._pending = (job, callback)
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)

    def destroy(self):
        self.destroyed = True


def _widget(*_args, **_kwargs):
    widget = Mock()
    widget.winfo_children.return_value = []
    # 中央ブロックの実寸からseat位置を計算するため、実widgetと同じくintを返す。
    widget.winfo_reqwidth.return_value = 200
    widget.winfo_reqheight.return_value = 100
    widget.master.winfo_width.return_value = 1000
    return widget


def _fake_ttk() -> Mock:
    ttk = Mock()
    for name in (
        "Frame",
        "Label",
        "Button",
        "LabelFrame",
        "Combobox",
        "Separator",
        "Style",
    ):
        getattr(ttk, name).side_effect = _widget
    return ttk


class _FakePhotoImage:
    """pixel APIを持つ`PhotoImage`のtest double。

    ツモ切り牌のgrayscale変換がpixelを読み書きするため、Mockのままでは
    Viewerの生成自体が失敗する。最小の1x1画像として振る舞わせる。
    """

    def __init__(self, **_kwargs: object) -> None:
        self._transparent: set[tuple[int, int]] = set()

    def subsample(self, _factor: int) -> "_FakePhotoImage":
        return self

    def width(self) -> int:
        return 1

    def height(self) -> int:
        return 1

    def get(self, _x: int, _y: int) -> tuple[int, int, int]:
        return (120, 60, 30)

    def put(self, _rows: object) -> None:
        self._transparent.clear()

    def transparency_get(self, x: int, y: int) -> bool:
        return (x, y) in self._transparent

    def transparency_set(self, x: int, y: int, value: bool) -> None:
        if value:
            self._transparent.add((x, y))
        else:
            self._transparent.discard((x, y))


def _fake_tk() -> Mock:
    tk = Mock()
    tk.StringVar.side_effect = lambda value="": Mock(**{"get.return_value": value})
    tk.PhotoImage.side_effect = _FakePhotoImage
    return tk


def _application(
    *, timeline_loader=None, initial_path=None
) -> tuple[_TkReplayApplication, _FakeRoot]:
    root = _FakeRoot()
    application = _TkReplayApplication(
        root,
        tk=_fake_tk(),
        ttk=_fake_ttk(),
        messagebox=Mock(),
        scrolledtext=Mock(),
        filedialog=Mock(),
        initial_path=initial_path,
        timeline_loader=timeline_loader or (lambda _path: _timeline()),
    )
    return application, root


class ReplayEntryPointTest(unittest.TestCase):
    def test_forwards_the_optional_record_path(self) -> None:
        with patch("lisjong_play.replay_gui.launch_replay_gui") as launch:
            self.assertEqual(0, main(["/records/one"]))
        launch.assert_called_once_with(path="/records/one")

    def test_path_is_optional(self) -> None:
        with patch("lisjong_play.replay_gui.launch_replay_gui") as launch:
            self.assertEqual(0, main([]))
        launch.assert_called_once_with(path=None)

    def test_unavailable_gui_is_human_readable(self) -> None:
        output: list[str] = []
        with patch(
            "lisjong_play.replay_gui.launch_replay_gui",
            side_effect=GuiUnavailableError("no display"),
        ):
            exit_code = main([], error_writer=output.append)

        self.assertEqual(1, exit_code)
        self.assertEqual(1, len(output))
        self.assertIn("no display", output[0])


class ReplayOpenBoundaryTest(unittest.TestCase):
    def test_rejected_record_is_not_shown_as_a_partial_replay(self) -> None:
        def failing_loader(_path):
            raise ReplayLoadError("corrupt record")

        application, _root = _application(
            timeline_loader=failing_loader, initial_path="/records/bad"
        )

        self.assertIsNone(application._controller)
        application._messagebox.showerror.assert_called_once()
        self.assertIn(
            "corrupt record", application._messagebox.showerror.call_args.args[1]
        )

    def test_opening_a_valid_record_enables_navigation(self) -> None:
        application, _root = _application(initial_path="/records/good")

        self.assertIsNotNone(application._controller)
        self.assertEqual(0, application._controller.index)

    def test_rejecting_a_record_clears_the_previous_board(self) -> None:
        """rejectされたrecordが直前のrecordの盤面を残したまま見えないこと。"""
        application, _root = _application(initial_path="/records/good")
        seat_frames = list(application._seat_frames.values())
        for frame in seat_frames:
            frame.winfo_children.return_value = [Mock()]

        def failing_loader(_path):
            raise ReplayLoadError("corrupt record")

        application._load_timeline = failing_loader
        application._open_path("/records/bad")

        self.assertIsNone(application._controller)
        for frame in seat_frames:
            frame.winfo_children.return_value[0].destroy.assert_called()

    def test_cancelled_file_dialog_leaves_state_untouched(self) -> None:
        application, _root = _application()
        application._filedialog.askdirectory.return_value = ""

        application._choose_record()

        self.assertIsNone(application._controller)


class ReplayPlaybackLifecycleTest(unittest.TestCase):
    """auto-play timerはTk main threadの`after`だけで駆動し、確実にcancelされる。"""

    def test_play_schedules_a_single_timer_at_the_current_speed(self) -> None:
        application, root = _application(initial_path="/records/good")

        application._toggle_playback()

        self.assertTrue(application._controller.playing)
        self.assertEqual(1, len(root.scheduled))
        self.assertEqual(BASE_FRAME_INTERVAL_MS, root.scheduled[0][0])
        self.assertIsNotNone(application._playback_job)

    def test_pause_cancels_the_pending_timer(self) -> None:
        application, root = _application(initial_path="/records/good")
        application._toggle_playback()
        job = application._playback_job

        application._toggle_playback()

        self.assertFalse(application._controller.playing)
        self.assertIsNone(application._playback_job)
        self.assertEqual([job], root.cancelled)

    def test_each_tick_schedules_at_most_one_successor(self) -> None:
        application, root = _application(initial_path="/records/good")
        application._toggle_playback()

        _delay, callback = root.scheduled[-1]
        callback()

        self.assertEqual(1, application._controller.index)
        self.assertEqual(2, len(root.scheduled))
        self.assertEqual([], root.cancelled)

    def test_playback_stops_scheduling_at_the_end_boundary(self) -> None:
        application, root = _application(initial_path="/records/good")
        application._toggle_playback()
        for _ in range(10):
            if not application._controller.playing:
                break
            root.scheduled[-1][1]()

        self.assertFalse(application._controller.playing)
        self.assertIsNone(application._playback_job)
        self.assertEqual(3, application._controller.index)

    def test_manual_navigation_stops_playback_and_cancels_the_timer(self) -> None:
        application, root = _application(initial_path="/records/good")
        application._toggle_playback()
        job = application._playback_job

        application._to_next()

        self.assertFalse(application._controller.playing)
        self.assertIsNone(application._playback_job)
        self.assertEqual([job], root.cancelled)
        self.assertEqual(1, application._controller.index)

    def test_rapid_navigation_never_leaves_more_than_one_pending_timer(self) -> None:
        application, root = _application(initial_path="/records/good")
        for _ in range(5):
            application._toggle_playback()
            application._to_next()
            application._to_previous()
            application._to_next_round()
            application._to_previous_round()

        self.assertIsNone(application._playback_job)
        self.assertFalse(application._controller.playing)

    def test_speed_change_reschedules_without_leaking_the_old_timer(self) -> None:
        application, root = _application(initial_path="/records/good")
        application._toggle_playback()
        first = application._playback_job
        application._speed_var.get.return_value = "4.0"

        application._apply_speed()

        self.assertEqual([first], root.cancelled)
        self.assertEqual(4.0, application._controller.speed)
        self.assertEqual(BASE_FRAME_INTERVAL_MS // 4, root.scheduled[-1][0])

    def test_unsupported_speed_selection_is_reverted(self) -> None:
        application, _root = _application(initial_path="/records/good")
        application._speed_var.get.return_value = "9.0"

        application._apply_speed()

        self.assertEqual(1.0, application._controller.speed)
        application._speed_var.set.assert_called_with("1.0")

    def test_close_cancels_the_timer_and_destroys_the_window(self) -> None:
        application, root = _application(initial_path="/records/good")
        application._toggle_playback()
        job = application._playback_job

        root.protocol_handler()

        self.assertEqual([job], root.cancelled)
        self.assertIsNone(application._playback_job)
        self.assertFalse(application._controller.playing)
        self.assertTrue(root.destroyed)

    def test_close_without_a_record_is_safe(self) -> None:
        application, root = _application()

        root.protocol_handler()

        self.assertEqual([], root.cancelled)
        self.assertTrue(root.destroyed)


class ReplayDoesNotRunEngineOrPolicyTest(unittest.TestCase):
    def test_opening_and_navigating_never_starts_a_session_or_policy(self) -> None:
        with (
            patch("lisjong_play.session._run_session") as run_session,
            patch("lisjong_play.gui_bridge.run_gui_worker") as worker,
        ):
            application, root = _application(initial_path="/records/good")
            application._toggle_playback()
            root.scheduled[-1][1]()
            application._to_next_round()
            application._to_previous_round()
            application._to_first()

        run_session.assert_not_called()
        worker.assert_not_called()

    def test_replay_gui_does_not_import_the_live_session_worker(self) -> None:
        import lisjong_play.replay_controller as controller
        import lisjong_play.replay_gui as replay_gui
        import lisjong_play.replay_source as source

        for module in (replay_gui, controller, source):
            with self.subTest(module=module.__name__):
                self.assertFalse(hasattr(module, "run_gui_worker"))
                self.assertFalse(hasattr(module, "_run_session"))
                self.assertFalse(hasattr(module, "execute_policy"))

    def test_replay_source_and_controller_do_not_touch_tk(self) -> None:
        import lisjong_play.replay_controller as controller
        import lisjong_play.replay_source as source

        for module in (source, controller):
            with self.subTest(module=module.__name__):
                text = Path(module.__file__).read_text(encoding="utf-8")
                self.assertNotIn("import tkinter", text)
                self.assertNotIn("tkinter", text)


if __name__ == "__main__":
    unittest.main()
