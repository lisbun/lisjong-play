import unittest
from unittest.mock import Mock, patch

from lisjong_play.gui import (
    _ACTION_CONTROL_WIDTH,
    _ACTION_ROW_CAPACITY,
    _HAND_DISCARD_INSTRUCTION,
    GuiUnavailableError,
    _action_button_attributes,
    _action_units,
    _drawn_tile_tsumogiri_action,
    _hand_discard_actions,
    _non_hand_actions,
    _only_pass_option_index,
    _partition_action_rows,
    _river_caption,
    _TkGuiApplication,
    main,
)
from lisjong_play.gui_bridge import DecisionRequested, MatchCompleted, RoundCompleted
from lisjong_play.gui_model import (
    ActionStyle,
    GuiActionView,
    GuiBoardView,
    GuiMeldView,
    GuiRiverTile,
    GuiSeatView,
)


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


class GuiHandTileSelectionTest(unittest.TestCase):
    def test_concealed_discard_resolves_to_its_original_option_index(self) -> None:
        discard = action_view(2, style="discard", tile_label="5m")
        other = action_view(5, style="action", tile_label=None)

        resolved = _hand_discard_actions((discard, other))

        self.assertEqual({"5m": discard}, resolved)

    def test_duplicate_hand_tile_labels_resolve_to_the_same_discard_option(
        self,
    ) -> None:
        discard = action_view(2, style="discard", tile_label="5m")
        resolved = _hand_discard_actions((discard,))

        self.assertIs(resolved["5m"], resolved.get("5m"))
        self.assertEqual(2, resolved["5m"].option_index)
        self.assertNotIn("6m", resolved)

    def test_drawn_tile_resolves_only_the_matching_tsumogiri_action(self) -> None:
        tsumogiri = action_view(4, style="tsumogiri", tile_label="7p")
        concealed_discard_same_label = action_view(1, style="discard", tile_label="7p")
        actions = (concealed_discard_same_label, tsumogiri)

        self.assertIs(tsumogiri, _drawn_tile_tsumogiri_action(actions, "7p"))
        self.assertIsNone(
            _drawn_tile_tsumogiri_action((concealed_discard_same_label,), "7p")
        )
        self.assertIsNone(_drawn_tile_tsumogiri_action(actions, "8p"))
        self.assertIsNone(_drawn_tile_tsumogiri_action(actions, None))

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
        application._actions = Mock()
        application._clear_frame = Mock()  # type: ignore[method-assign]
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

    def test_tile_control_builds_an_image_button_for_a_legal_discard(self) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._ttk = Mock()
        application._tile_images = Mock()
        application._choose_action = Mock()  # type: ignore[method-assign]
        parent = Mock()
        action = action_view(6, style="discard", tile_label="5pr")

        control = application._tile_control(parent, "5pr", action)

        self.assertIs(control, application._ttk.Button.return_value)
        application._tile_images.get.assert_called_once_with("5pr")
        _, kwargs = application._ttk.Button.call_args
        self.assertIs(kwargs["image"], application._tile_images.get.return_value)
        self.assertEqual("TileImage.TButton", kwargs["style"])
        kwargs["command"]()
        application._choose_action.assert_called_once_with(6)

    def test_tile_control_builds_a_tsumogiri_button_from_the_same_image_lookup(
        self,
    ) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._ttk = Mock()
        application._tile_images = Mock()
        application._choose_action = Mock()  # type: ignore[method-assign]
        parent = Mock()
        action = action_view(9, style="tsumogiri", tile_label="1p")

        control = application._tile_control(parent, "1p", action)

        self.assertIs(control, application._ttk.Button.return_value)
        application._tile_images.get.assert_called_once_with("1p")
        _, kwargs = application._ttk.Button.call_args
        self.assertIs(kwargs["image"], application._tile_images.get.return_value)
        kwargs["command"]()
        application._choose_action.assert_called_once_with(9)

    def test_tile_control_stays_a_label_for_a_non_legal_tile(self) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._ttk = Mock()
        application._tile_images = Mock()
        parent = Mock()

        control = application._tile_control(parent, "3s", None)

        self.assertIs(control, application._ttk.Label.return_value)
        application._tile_images.get.assert_called_once_with("3s")
        application._ttk.Button.assert_not_called()

    def test_duplicate_hand_tiles_resolve_to_the_same_cached_image(self) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._ttk = Mock()
        application._tile_images = Mock()
        parent = Mock()

        application._tile_control(parent, "5m", action_view(1, tile_label="5m"))
        application._tile_control(parent, "5m", None)

        self.assertEqual(
            [("5m",), ("5m",)],
            [call.args for call in application._tile_images.get.call_args_list],
        )


class GuiTileImageRegistrySharingTest(unittest.TestCase):
    """河 / 副露 / ドラ表示牌が手牌と同じtile image registryを利用することを検証する。"""

    def test_river_tile_looks_up_its_image_from_the_shared_registry(self) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._ttk = Mock()
        application._tile_images = Mock()

        application._render_river_tile(Mock(), GuiRiverTile("5pr", False, False, None))

        application._tile_images.get.assert_called_once_with("5pr")

    def test_meld_tiles_look_up_their_images_from_the_shared_registry(self) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._ttk = Mock()
        application._tile_images = Mock()

        application._render_meld(Mock(), GuiMeldView("ポン", ("1m", "1m", "1m"), "P2"))

        self.assertEqual(
            [("1m",), ("1m",), ("1m",)],
            [call.args for call in application._tile_images.get.call_args_list],
        )

    def test_dora_indicators_look_up_their_images_from_the_shared_registry(
        self,
    ) -> None:
        application = _TkGuiApplication.__new__(_TkGuiApplication)
        application._ttk = Mock()
        application._tile_images = Mock()
        application._center = Mock(winfo_children=Mock(return_value=[]))
        application._hand = Mock(winfo_children=Mock(return_value=[]))
        application._render_seat = Mock()  # type: ignore[method-assign]
        application._seat_frames = {
            position: Mock() for position in ("top", "bottom", "left", "right")
        }
        board = GuiBoardView(
            round_label="東1局 0本場",
            decision_label="自摸番",
            center_detail="供託 0本 / 残り山 70枚",
            dora_indicators=("東", "5sr"),
            seats=tuple(
                GuiSeatView(
                    position=position,
                    label="P",
                    score=0,
                    riichi="",
                    melds=(),
                    river=(),
                )
                for position in ("top", "bottom", "left", "right")
            ),
            hand_tiles=(),
            drawn_tile=None,
        )

        application._render_board(board, ())

        self.assertEqual(
            [("東",), ("5sr",)],
            [call.args for call in application._tile_images.get.call_args_list],
        )


class GuiRiverCaptionTest(unittest.TestCase):
    def test_plain_discard_has_no_caption(self) -> None:
        self.assertEqual("", _river_caption(GuiRiverTile("1m", False, False, None)))

    def test_tsumogiri_discard_is_marked_with_an_asterisk(self) -> None:
        self.assertEqual("*", _river_caption(GuiRiverTile("1m", True, False, None)))

    def test_riichi_declaration_is_bracketed(self) -> None:
        self.assertEqual("[]", _river_caption(GuiRiverTile("1m", False, True, None)))

    def test_riichi_declaration_tsumogiri_keeps_the_asterisk_inside_brackets(
        self,
    ) -> None:
        self.assertEqual("[*]", _river_caption(GuiRiverTile("1m", True, True, None)))

    def test_called_discard_appends_the_calling_seat(self) -> None:
        self.assertEqual("→P2", _river_caption(GuiRiverTile("1m", False, False, "P2")))


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
