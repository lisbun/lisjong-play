import unittest
from unittest.mock import Mock, patch

from lisjong_play.gui import (
    _ACTION_CONTROL_WIDTH,
    _ACTION_ROW_CAPACITY,
    _HAND_DISCARD_INSTRUCTION,
    _LOG_VISIBLE_LINES,
    GuiUnavailableError,
    _action_button_attributes,
    _action_units,
    _non_hand_actions,
    _only_pass_option_index,
    _partition_action_rows,
    _TkGuiApplication,
    main,
)
from lisjong_play.gui_board import (
    CENTER_PLACE,
    TABLE_PLACE,
    drawn_tile_tsumogiri_action,
    hand_discard_actions,
)
from lisjong_play.gui_bridge import DecisionRequested, MatchCompleted, RoundCompleted
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
    def test_progress_log_is_bounded_to_a_compact_number_of_lines(self) -> None:
        """logは卓の縦幅を優先して2行に抑える。スクロールで過去分は読める。"""
        self.assertEqual(2, _LOG_VISIBLE_LINES)

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


class GuiTableLayoutTest(unittest.TestCase):
    def test_seat_cards_float_at_table_edges_instead_of_stretching_grid_cells(
        self,
    ) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._root = Mock()
        application._tk = Mock()
        application._ttk = Mock()
        application._scrolledtext = Mock()

        main = Mock()
        setup = Mock()
        table = Mock()
        center = Mock()
        seat_frames = [Mock() for _ in range(4)]
        frame_values = iter([main, setup, table, *seat_frames, center])
        application._ttk.Frame.side_effect = lambda *args, **kwargs: next(frame_values)

        hand = Mock()
        actions = Mock()
        log_frame = Mock()
        labelframes = iter([hand, actions, log_frame])
        application._ttk.LabelFrame.side_effect = lambda *args, **kwargs: next(
            labelframes
        )
        application._ttk.Label.return_value = Mock()
        application._ttk.Entry.return_value = Mock()
        application._ttk.Combobox.return_value = Mock()
        application._ttk.Button.return_value = Mock()
        application._scrolledtext.ScrolledText.return_value = Mock()

        application._build_layout(seed=0, opponent="minimal")

        for frame, position in zip(seat_frames, TABLE_PLACE, strict=True):
            relx, rely, anchor, x, y = TABLE_PLACE[position]
            frame.place.assert_called_once_with(
                relx=relx, rely=rely, anchor=anchor, x=x, y=y
            )
        relx, rely, anchor = CENTER_PLACE
        center.place.assert_called_once_with(relx=relx, rely=rely, anchor=anchor)
        table.rowconfigure.assert_not_called()


class GuiHandTileSelectionTest(unittest.TestCase):
    def test_concealed_discard_resolves_to_its_original_option_index(self) -> None:
        discard = action_view(2, style="discard", tile_label="5m")
        other = action_view(5, style="action", tile_label=None)

        resolved = hand_discard_actions((discard, other))

        self.assertEqual({"5m": discard}, resolved)

    def test_duplicate_hand_tile_labels_resolve_to_the_same_discard_option(
        self,
    ) -> None:
        discard = action_view(2, style="discard", tile_label="5m")
        resolved = hand_discard_actions((discard,))

        self.assertIs(resolved["5m"], resolved.get("5m"))
        self.assertEqual(2, resolved["5m"].option_index)
        self.assertNotIn("6m", resolved)

    def test_drawn_tile_resolves_only_the_matching_tsumogiri_action(self) -> None:
        tsumogiri = action_view(4, style="tsumogiri", tile_label="7p")
        concealed_discard_same_label = action_view(1, style="discard", tile_label="7p")
        actions = (concealed_discard_same_label, tsumogiri)

        self.assertIs(tsumogiri, drawn_tile_tsumogiri_action(actions, "7p"))
        self.assertIsNone(
            drawn_tile_tsumogiri_action((concealed_discard_same_label,), "7p")
        )
        self.assertIsNone(drawn_tile_tsumogiri_action(actions, "8p"))
        self.assertIsNone(drawn_tile_tsumogiri_action(actions, None))

    def test_non_hand_actions_filters_out_discard_and_tsumogiri(self) -> None:
        discard = action_view(0, style="discard")
        tsumogiri = action_view(1, style="tsumogiri", tile_label="9s")
        pass_action = action_view(2, style="pass", tile_label=None)
        wide_action = action_view(3, style="action", tile_label=None)

        self.assertEqual(
            (pass_action, wide_action),
            _non_hand_actions((discard, tsumogiri, pass_action, wide_action)),
        )

    def test_render_actions_shows_instruction_when_only_hand_actions_remain(
        self,
    ) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._ttk = Mock()
        application._actions = Mock(winfo_children=Mock(return_value=[]))
        event = DecisionRequested(
            request_id=3,
            board=Mock(),
            actions=(
                action_view(0, style="discard"),
                action_view(1, style="tsumogiri", tile_label="1p"),
            ),
        )

        application._render_actions(event)

        self.assertEqual(3, application._active_decision_id)
        application._ttk.Label.assert_called_once_with(
            application._actions, text=_HAND_DISCARD_INSTRUCTION
        )


class GuiResultPresentationTest(unittest.TestCase):
    def test_round_result_is_logged_and_shown_before_next_round(self) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._root = Mock()
        application._messagebox = Mock()
        application._append_log = Mock()  # type: ignore[method-assign]
        application._render_round_confirmation = Mock()  # type: ignore[method-assign]

        application._handle_event(RoundCompleted("東1局: 流局", 17))

        application._append_log.assert_called_once_with("東1局: 流局", separator=True)
        application._render_round_confirmation.assert_called_once_with(17)
        application._messagebox.showinfo.assert_called_once_with(
            "局結果", "東1局: 流局", parent=application._root
        )

    def test_match_result_is_logged_and_shown(self) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._root = Mock()
        application._messagebox = Mock()
        application._append_log = Mock()  # type: ignore[method-assign]

        application._handle_event(MatchCompleted("最終順位: P1 1位"))

        application._append_log.assert_called_once_with(
            "最終順位: P1 1位", separator=True
        )
        application._messagebox.showinfo.assert_called_once_with(
            "半荘結果", "最終順位: P1 1位", parent=application._root
        )


if __name__ == "__main__":
    unittest.main()
