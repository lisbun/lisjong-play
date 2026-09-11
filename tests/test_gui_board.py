"""live Human PlayとReplayが共有するboard rendererのtest。"""

import unittest
from unittest.mock import Mock

from lisjong_play.gui_board import GuiBoardRenderer, river_caption
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


def board_view(
    *,
    dora_indicators: tuple[str, ...] = (),
    hand_tiles: tuple[str, ...] = (),
    drawn_tile: str | None = None,
) -> GuiBoardView:
    return GuiBoardView(
        round_label="東1局 0本場",
        decision_label="自摸番",
        center_detail="供託 0本 / 残り山 70枚",
        dora_indicators=dora_indicators,
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
        hand_tiles=hand_tiles,
        drawn_tile=drawn_tile,
    )


def renderer(*, on_select_action=None) -> GuiBoardRenderer:
    return GuiBoardRenderer(Mock(), Mock(), on_select_action=on_select_action)


class GuiBoardRendererContractTest(unittest.TestCase):
    def test_rejects_a_non_callable_selection_handler(self) -> None:
        with self.assertRaises(TypeError):
            GuiBoardRenderer(Mock(), Mock(), on_select_action="not callable")


class GuiTileImageRegistrySharingTest(unittest.TestCase):
    """河 / 副露 / ドラ表示牌が手牌と同じtile image registryを利用することを検証する。"""

    def test_river_tile_looks_up_its_image_from_the_shared_registry(self) -> None:
        board = renderer()

        board.render_river_tile(Mock(), GuiRiverTile("5pr", False, False, None))

        board._tile_images.get.assert_called_once_with("5pr")

    def test_meld_tiles_look_up_their_images_from_the_shared_registry(self) -> None:
        board = renderer()

        board.render_meld(Mock(), GuiMeldView("ポン", ("1m", "1m", "1m"), "P2", "1m"))

        self.assertEqual(
            [("1m",), ("1m",), ("1m",)],
            [call.args for call in board._tile_images.get.call_args_list],
        )

    def test_meld_caption_shows_the_called_tile_when_present(self) -> None:
        board = renderer()

        board.render_meld(Mock(), GuiMeldView("ポン", ("1m", "1m", "1m"), "P2", "1m"))

        label_texts = [
            call.kwargs.get("text")
            for call in board._ttk.Label.call_args_list
            if "text" in call.kwargs
        ]
        self.assertIn("called 1m", label_texts)

    def test_meld_caption_omits_the_called_tile_for_a_concealed_meld(self) -> None:
        board = renderer()

        board.render_meld(
            Mock(), GuiMeldView("暗槓", ("1m", "1m", "1m", "1m"), None, None)
        )

        label_texts = [
            call.kwargs.get("text")
            for call in board._ttk.Label.call_args_list
            if "text" in call.kwargs
        ]
        self.assertFalse(any(text.startswith("called") for text in label_texts))

    def test_dora_indicators_look_up_their_images_from_the_shared_registry(
        self,
    ) -> None:
        board = renderer()
        board.render_seat = Mock()  # type: ignore[method-assign]

        board.render_board(
            board_view(dora_indicators=("東", "5sr")),
            (),
            seat_frames={
                position: Mock() for position in ("top", "bottom", "left", "right")
            },
            center=Mock(winfo_children=Mock(return_value=[])),
            hand=Mock(winfo_children=Mock(return_value=[])),
        )

        self.assertEqual(
            [("東",), ("5sr",)],
            [call.args for call in board._tile_images.get.call_args_list],
        )


class GuiTileControlTest(unittest.TestCase):
    def test_tile_control_builds_an_image_button_for_a_legal_discard(self) -> None:
        select = Mock()
        board = renderer(on_select_action=select)
        action = action_view(6, style="discard", tile_label="5pr")

        control = board.tile_control(Mock(), "5pr", action)

        self.assertIs(control, board._ttk.Button.return_value)
        board._tile_images.get.assert_called_once_with("5pr")
        _, kwargs = board._ttk.Button.call_args
        self.assertIs(kwargs["image"], board._tile_images.get.return_value)
        self.assertEqual("TileImage.TButton", kwargs["style"])
        kwargs["command"]()
        select.assert_called_once_with(6)

    def test_tile_control_builds_a_tsumogiri_button_from_the_same_image_lookup(
        self,
    ) -> None:
        select = Mock()
        board = renderer(on_select_action=select)
        action = action_view(9, style="tsumogiri", tile_label="1p")

        control = board.tile_control(Mock(), "1p", action)

        self.assertIs(control, board._ttk.Button.return_value)
        board._tile_images.get.assert_called_once_with("1p")
        _, kwargs = board._ttk.Button.call_args
        kwargs["command"]()
        select.assert_called_once_with(9)

    def test_tile_control_stays_a_label_for_a_non_legal_tile(self) -> None:
        board = renderer(on_select_action=Mock())

        control = board.tile_control(Mock(), "3s", None)

        self.assertIs(control, board._ttk.Label.return_value)
        board._tile_images.get.assert_called_once_with("3s")
        board._ttk.Button.assert_not_called()

    def test_duplicate_hand_tiles_resolve_to_the_same_cached_image(self) -> None:
        board = renderer(on_select_action=Mock())

        board.tile_control(Mock(), "5m", action_view(1, tile_label="5m"))
        board.tile_control(Mock(), "5m", None)

        self.assertEqual(
            [("5m",), ("5m",)],
            [call.args for call in board._tile_images.get.call_args_list],
        )

    def test_without_a_selection_handler_every_tile_stays_a_label(self) -> None:
        """Replayのように選択が存在しない表示ではbuttonを作らない。"""
        board = renderer()

        control = board.tile_control(Mock(), "5m", action_view(1, tile_label="5m"))

        self.assertIs(control, board._ttk.Label.return_value)
        board._ttk.Button.assert_not_called()

    def test_replay_style_board_renders_no_action_buttons(self) -> None:
        board = renderer()
        board.render_seat = Mock()  # type: ignore[method-assign]

        board.render_board(
            board_view(hand_tiles=("1m", "2m"), drawn_tile="3m"),
            (),
            seat_frames={
                position: Mock() for position in ("top", "bottom", "left", "right")
            },
            center=Mock(winfo_children=Mock(return_value=[])),
            hand=Mock(winfo_children=Mock(return_value=[])),
        )

        board._ttk.Button.assert_not_called()


class GuiRiverCaptionTest(unittest.TestCase):
    def test_plain_discard_has_no_caption(self) -> None:
        self.assertEqual("", river_caption(GuiRiverTile("1m", False, False, None)))

    def test_tsumogiri_discard_is_marked_with_an_asterisk(self) -> None:
        self.assertEqual("*", river_caption(GuiRiverTile("1m", True, False, None)))

    def test_riichi_declaration_is_bracketed(self) -> None:
        self.assertEqual("[]", river_caption(GuiRiverTile("1m", False, True, None)))

    def test_riichi_declaration_tsumogiri_keeps_the_asterisk_inside_brackets(
        self,
    ) -> None:
        self.assertEqual("[*]", river_caption(GuiRiverTile("1m", True, True, None)))

    def test_called_discard_appends_the_calling_seat(self) -> None:
        self.assertEqual("→P3", river_caption(GuiRiverTile("1m", False, False, "P3")))


if __name__ == "__main__":
    unittest.main()
