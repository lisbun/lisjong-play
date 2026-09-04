import unittest
from unittest.mock import Mock, patch

from lisjong_play.gui import (
    _ACTION_CONTROL_WIDTH,
    _ACTION_ROW_CAPACITY,
    _TILE_CONTROL_WIDTH,
    GuiUnavailableError,
    _action_button_attributes,
    _action_units,
    _only_pass_option_index,
    _partition_action_rows,
    _TkGuiApplication,
    main,
)
from lisjong_play.gui_bridge import DecisionRequested
from lisjong_play.gui_model import ActionStyle, GuiActionView


def action_view(
    option_index: int,
    *,
    style: ActionStyle = "discard",
    tile_label: str | None = "1m",
) -> GuiActionView:
    return GuiActionView(
        option_index=option_index,
        label="操作 / 詳細",
        style=style,
        tile_label=tile_label,
    )


class GuiEntryPointTest(unittest.TestCase):
    def test_forwards_initial_seed_and_opponent_without_importing_tk_in_test(
        self,
    ) -> None:
        with patch("lisjong_play.gui.launch_gui") as launch:
            exit_code = main(["--seed", "42", "--opponent", "combined"])

        self.assertEqual(0, exit_code)
        launch.assert_called_once_with(seed=42, opponent="combined")

    def test_unavailable_gui_is_human_readable(self) -> None:
        output: list[str] = []
        with patch(
            "lisjong_play.gui.launch_gui",
            side_effect=GuiUnavailableError("no display"),
        ):
            exit_code = main([], error_writer=output.append)

        self.assertEqual(1, exit_code)
        self.assertEqual(["GUIを起動できません: no display"], output)


class GuiActionLayoutTest(unittest.TestCase):
    def test_discard_controls_are_compact_single_line_tile_size(self) -> None:
        discard = action_view(0)
        tsumogiri = action_view(1, style="tsumogiri", tile_label="5pr")

        self.assertEqual(
            ("1m", "Tile.TButton", _TILE_CONTROL_WIDTH),
            _action_button_attributes(discard),
        )
        self.assertEqual(
            ("5pr*", "RedTile.TButton", _TILE_CONTROL_WIDTH),
            _action_button_attributes(tsumogiri),
        )

    def test_wide_action_label_wraps_at_semantic_separator(self) -> None:
        action = action_view(0, style="action", tile_label=None)

        self.assertEqual(
            ("操作\n詳細", "Primary.TButton", _ACTION_CONTROL_WIDTH),
            _action_button_attributes(action),
        )

    def test_actions_are_partitioned_into_bounded_rows(self) -> None:
        actions = tuple(action_view(index) for index in range(10)) + (
            action_view(10, style="action", tile_label=None),
            action_view(11, style="pass", tile_label=None),
        )

        rows = _partition_action_rows(actions)

        self.assertEqual((11, 1), tuple(len(row) for row in rows))
        self.assertTrue(
            all(
                sum(_action_units(action) for action in row) <= _ACTION_ROW_CAPACITY
                for row in rows
            )
        )

    def test_only_pass_is_the_only_automatic_gui_action(self) -> None:
        only_pass = (action_view(7, style="pass", tile_label=None),)
        only_discard = (action_view(3),)
        pass_or_action = (
            action_view(7, style="pass", tile_label=None),
            action_view(8, style="action", tile_label=None),
        )

        self.assertEqual(7, _only_pass_option_index(only_pass))
        self.assertIsNone(_only_pass_option_index(only_discard))
        self.assertIsNone(_only_pass_option_index(pass_or_action))

    def test_only_pass_event_skips_rendering_and_selects_original_index(self) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._active_decision_id = None
        application._render_board = Mock()  # type: ignore[method-assign]
        application._render_actions = Mock()  # type: ignore[method-assign]
        application._choose_action = Mock()  # type: ignore[method-assign]
        event = DecisionRequested(
            request_id=42,
            board=Mock(),
            actions=(action_view(7, style="pass", tile_label=None),),
        )

        application._handle_event(event)

        self.assertEqual(42, application._active_decision_id)
        application._choose_action.assert_called_once_with(7)
        application._render_board.assert_not_called()
        application._render_actions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
