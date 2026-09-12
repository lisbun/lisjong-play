import unittest
from unittest.mock import Mock, patch

from lisjong_engine.seat import Seat

from lisjong_play.gui import GuiUnavailableError
from lisjong_play.gui_board import GuiBoardRenderer
from lisjong_play.spectator_gui import (
    CONCEALED_HAND_SCOPE_NOTE,
    _TkSpectatorApplication,
    main,
)
from lisjong_play.spectator_source import (
    SpectatorControl,
    SpectatorControlError,
    SpectatorDecisionPresented,
    SpectatorFailed,
    SpectatorFinished,
    SpectatorMatchResult,
    SpectatorProgress,
    SpectatorRoundResult,
    SpectatorSessionBridge,
    build_spectator_board_view,
)
from tests._fixtures import observation


def bare_application(bridge: SpectatorSessionBridge | None = None):
    """Tk displayを使わずにevent handlingとcontrol配線だけを検証する。"""
    application = _TkSpectatorApplication.__new__(_TkSpectatorApplication)
    application._bridge = bridge
    application._worker = None
    application._board_renderer = Mock()
    application._seat_frames = {}
    application._center = Mock()
    application._hand = Mock()
    application._messagebox = Mock()
    application._boundary_var = Mock()
    application._status_var = Mock()
    application._pause_button = Mock()
    application._step_button = Mock()
    application._speed_var = Mock()
    application._append_info = Mock()
    application._end_session = Mock()
    return application


class SpectatorEntryPointTest(unittest.TestCase):
    def test_entry_point_is_separate_from_human_play_and_replay(self) -> None:
        with patch("lisjong_play.spectator_gui.launch_spectator_gui") as launch:
            exit_code = main(["--seed", "42", "--policy", "combined"])

        self.assertEqual(0, exit_code)
        launch.assert_called_once_with(seed=42, policy="combined")

    def test_default_policy_and_seed(self) -> None:
        with patch("lisjong_play.spectator_gui.launch_spectator_gui") as launch:
            main([])

        launch.assert_called_once_with(seed=0, policy="minimal")

    def test_unknown_policy_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            main(["--policy", "does-not-exist"])

    def test_unavailable_gui_is_human_readable(self) -> None:
        output: list[str] = []
        with patch(
            "lisjong_play.spectator_gui.launch_spectator_gui",
            side_effect=GuiUnavailableError("no display"),
        ):
            exit_code = main([], error_writer=output.append)

        self.assertEqual(1, exit_code)
        self.assertEqual(["Spectator GUIを起動できません: no display"], output)


class SpectatorPresentationTest(unittest.TestCase):
    def test_board_is_rendered_through_the_shared_renderer_without_actions(
        self,
    ) -> None:
        application = bare_application()
        board = build_spectator_board_view(observation(viewer_seat=Seat.SOUTH))

        application._handle_event(SpectatorDecisionPresented(3, "P2", board))

        application._board_renderer.render_board.assert_called_once()
        args, kwargs = application._board_renderer.render_board.call_args
        self.assertIs(board, args[0])
        self.assertEqual((), args[1])
        self.assertEqual(
            {"seat_frames", "center", "hand"},
            set(kwargs),
        )

    def test_spectator_renderer_exposes_no_human_action_control(self) -> None:
        renderer = GuiBoardRenderer(Mock(), Mock())
        parent = Mock()

        renderer.tile_control(parent, "1m", Mock())

        # on_select_actionが無いのでbuttonは作られない。
        self.assertEqual(0, renderer._ttk.Button.call_count)
        self.assertEqual(1, renderer._ttk.Label.call_count)

    def test_round_and_match_results_are_presented(self) -> None:
        application = bare_application()

        application._handle_event(SpectatorRoundResult("東1局 終了"))
        application._handle_event(SpectatorMatchResult("=== 半荘終了 ==="))

        self.assertEqual(
            [("東1局 終了",), ("=== 半荘終了 ===",)],
            [call.args for call in application._append_info.call_args_list],
        )

    def test_progress_is_appended_without_a_separator(self) -> None:
        application = bare_application()

        application._handle_event(SpectatorProgress("P1 打 1m"))

        application._append_info.assert_called_once_with("P1 打 1m")

    def test_failure_is_not_presented_as_a_finished_match(self) -> None:
        application = bare_application()

        application._handle_event(SpectatorFailed("RuntimeError: boom"))

        application._messagebox.showerror.assert_called_once()
        application._end_session.assert_called_once_with()
        self.assertIn(
            "ERROR: RuntimeError: boom",
            [call.args[0] for call in application._append_info.call_args_list],
        )

    def test_finished_ends_the_session(self) -> None:
        application = bare_application()

        application._handle_event(SpectatorFinished())

        application._end_session.assert_called_once_with()
        application._messagebox.showerror.assert_not_called()

    def test_unknown_event_fails_closed(self) -> None:
        application = bare_application()

        with self.assertRaises(AssertionError):
            application._handle_event(object())

    def test_only_the_latest_board_of_one_drain_is_rendered(self) -> None:
        """描画がpacingに追いつかなくても、表示がworkerから際限なく遅れない。"""
        application = bare_application()
        boards = [
            build_spectator_board_view(observation(viewer_seat=seat))
            for seat in (Seat.EAST, Seat.SOUTH, Seat.WEST)
        ]

        application._handle_events(
            (
                SpectatorDecisionPresented(1, "P1", boards[0]),
                SpectatorProgress("P1 打 1m"),
                SpectatorDecisionPresented(2, "P2", boards[1]),
                SpectatorDecisionPresented(3, "P3", boards[2]),
            )
        )

        application._board_renderer.render_board.assert_called_once()
        self.assertIs(
            boards[2], application._board_renderer.render_board.call_args.args[0]
        )
        application._append_info.assert_called_once_with("P1 打 1m")

    def test_drained_result_text_is_never_dropped(self) -> None:
        application = bare_application()
        board = build_spectator_board_view(observation())

        application._handle_events(
            (
                SpectatorDecisionPresented(1, "P1", board),
                SpectatorRoundResult("東1局 終了"),
                SpectatorMatchResult("=== 半荘終了 ==="),
            )
        )

        self.assertEqual(
            ["東1局 終了", "=== 半荘終了 ==="],
            [call.args[0] for call in application._append_info.call_args_list],
        )
        application._board_renderer.render_board.assert_called_once()

    def test_paused_step_still_renders_its_single_board(self) -> None:
        application = bare_application()
        board = build_spectator_board_view(observation())

        application._handle_events((SpectatorDecisionPresented(9, "P1", board),))

        application._board_renderer.render_board.assert_called_once()
        self.assertIs(board, application._board_renderer.render_board.call_args.args[0])

    def test_concealed_hand_scope_is_stated_as_public_board_only(self) -> None:
        self.assertIn("public board", CONCEALED_HAND_SCOPE_NOTE)
        self.assertIn("4席同時のconcealed hand", CONCEALED_HAND_SCOPE_NOTE)


class SpectatorControlWiringTest(unittest.TestCase):
    def build(self):
        bridge = SpectatorSessionBridge(SpectatorControl(base_interval_ms=0))
        return bare_application(bridge), bridge

    def test_pause_and_resume_toggle_the_shared_control(self) -> None:
        application, bridge = self.build()

        application._toggle_pause()
        self.assertTrue(bridge.control.paused)

        application._toggle_pause()
        self.assertFalse(bridge.control.paused)

    def test_step_is_only_offered_while_paused(self) -> None:
        application, bridge = self.build()

        application._step()
        application._messagebox.showerror.assert_called_once()
        self.assertEqual(
            "disabled",
            application._step_button.configure.call_args.kwargs["state"],
        )

        bridge.control.pause()
        application._messagebox.showerror.reset_mock()
        application._step()

        application._messagebox.showerror.assert_not_called()
        self.assertEqual(
            "normal",
            application._step_button.configure.call_args.kwargs["state"],
        )

    def test_speed_change_only_updates_pacing(self) -> None:
        bridge = SpectatorSessionBridge(SpectatorControl(base_interval_ms=800))
        application = bare_application(bridge)
        application._speed_var.get.return_value = "4.0"

        application._apply_speed()

        self.assertEqual(4.0, bridge.control.speed)
        self.assertEqual(0.2, bridge.control.boundary_delay_seconds)
        self.assertFalse(bridge.control.paused)
        self.assertFalse(bridge.control.closed)

    def test_unsupported_speed_reverts_to_the_control_value(self) -> None:
        application, bridge = self.build()
        application._speed_var.get.return_value = "9.0"

        application._apply_speed()

        self.assertEqual(1.0, bridge.control.speed)
        application._speed_var.set.assert_called_once_with("1.0")

    def test_controls_are_inert_without_an_active_session(self) -> None:
        application = bare_application()

        application._toggle_pause()
        application._step()
        application._apply_speed()
        application._refresh_controls()

        application._messagebox.showerror.assert_not_called()

    def test_close_releases_a_paused_worker_and_destroys_the_window(self) -> None:
        application, bridge = self.build()
        bridge.control.pause()
        application._root = Mock()

        application._close()

        self.assertTrue(bridge.control.closed)
        application._root.destroy.assert_called_once_with()

    def test_step_control_error_is_surfaced_not_swallowed(self) -> None:
        application, bridge = self.build()

        with self.assertRaises(SpectatorControlError):
            bridge.control.request_step()

        application._step()
        application._messagebox.showerror.assert_called_once()


if __name__ == "__main__":
    unittest.main()
