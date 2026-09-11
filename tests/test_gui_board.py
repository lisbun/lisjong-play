"""live Human PlayとReplayが共有するboard rendererのtest。"""

import unittest
from typing import Any
from unittest.mock import Mock

from lisjong_play.gui_board import (
    RIVER_ROW_SIZE,
    RIVER_TILE_IMAGE_SUBSAMPLE,
    TILE_IMAGE_SUBSAMPLE,
    GuiBoardRenderer,
    load_river_tile_image,
    load_tile_image,
    river_caption,
)
from lisjong_play.gui_model import (
    ActionStyle,
    GuiActionView,
    GuiBoardView,
    GuiMeldView,
    GuiRiverTile,
    GuiSeatView,
)
from lisjong_play.tile_images import TileImageRegistry


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
    return GuiBoardRenderer(Mock(), Mock(), Mock(), on_select_action=on_select_action)


class GuiBoardRendererContractTest(unittest.TestCase):
    def test_rejects_a_non_callable_selection_handler(self) -> None:
        with self.assertRaises(TypeError):
            GuiBoardRenderer(Mock(), Mock(), Mock(), on_select_action="not callable")


class GuiTileImageRegistrySharingTest(unittest.TestCase):
    """副露 / ドラ表示牌が手牌と同じregistryを、河だけが河専用registryを使うことを検証する。"""

    def test_river_tile_looks_up_its_image_from_the_river_registry(self) -> None:
        board = renderer()

        board.render_river_tile(Mock(), GuiRiverTile("5pr", False, False, None))

        board._river_tile_images.get.assert_called_once_with("5pr")
        board._tile_images.get.assert_not_called()

    def test_river_tile_label_uses_the_river_sized_image(self) -> None:
        board = renderer()

        board.render_river_tile(Mock(), GuiRiverTile("5pr", False, False, None))

        _, kwargs = board._ttk.Label.call_args_list[0]
        self.assertIs(kwargs["image"], board._river_tile_images.get.return_value)

    def test_same_tile_label_resolves_to_distinct_hand_and_river_images(self) -> None:
        """同じtile labelでも、size別registryは別のcache objectを返す。"""
        hand_images = TileImageRegistry(lambda path: ("hand", path))
        river_images = TileImageRegistry(lambda path: ("river", path))
        board = GuiBoardRenderer(Mock(), hand_images, river_images)

        board.tile_control(Mock(), "1m", None)
        board.render_river_tile(Mock(), GuiRiverTile("1m", False, False, None))

        hand_image = board.tile_image("1m")
        river_image = board.river_tile_image("1m")
        self.assertEqual("hand", hand_image[0])
        self.assertEqual("river", river_image[0])
        self.assertIsNot(hand_image, river_image)
        self.assertIs(hand_image, board.tile_image("1m"))
        self.assertIs(river_image, board.river_tile_image("1m"))

    def test_meld_tiles_look_up_their_images_from_the_shared_registry(self) -> None:
        board = renderer()

        board.render_meld(Mock(), GuiMeldView("ポン", ("1m", "1m", "1m"), "P2", "1m"))

        self.assertEqual(
            [("1m",), ("1m",), ("1m",)],
            [call.args for call in board._tile_images.get.call_args_list],
        )
        board._river_tile_images.get.assert_not_called()

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


class GuiRiverLayoutTest(unittest.TestCase):
    def test_a_long_river_keeps_six_tiles_per_row(self) -> None:
        board = renderer()
        board._ttk.Frame.side_effect = lambda *args, **kwargs: Mock()
        rendered: list[tuple[Any, str]] = []
        board.render_river_tile = (  # type: ignore[method-assign]
            lambda row, cell: rendered.append((row, cell.tile)) or Mock()
        )
        river = tuple(
            GuiRiverTile(f"{index % 9 + 1}m", False, False, None) for index in range(25)
        )

        board.render_seat(
            Mock(winfo_children=Mock(return_value=[])),
            GuiSeatView(
                position="bottom",
                label="P1",
                score=25000,
                riichi="",
                melds=(),
                river=river,
            ),
        )

        rows: dict[int, int] = {}
        for row, _ in rendered:
            rows[id(row)] = rows.get(id(row), 0) + 1
        counts = list(rows.values())
        self.assertEqual(len(river), sum(counts))
        self.assertEqual(5, len(counts))
        self.assertEqual([RIVER_ROW_SIZE] * 4 + [1], counts)


class GuiTileImageScaleTest(unittest.TestCase):
    """河牌が手牌より小さいsubsample scaleで生成されることを検証する。"""

    def test_river_scale_is_smaller_than_the_hand_scale(self) -> None:
        self.assertGreater(RIVER_TILE_IMAGE_SUBSAMPLE, TILE_IMAGE_SUBSAMPLE)

    def test_hand_image_factory_uses_the_normal_subsample(self) -> None:
        tk = Mock()

        image = load_tile_image(tk, "1m.png")

        tk.PhotoImage.assert_called_once_with(file="1m.png")
        tk.PhotoImage.return_value.subsample.assert_called_once_with(
            TILE_IMAGE_SUBSAMPLE
        )
        self.assertIs(image, tk.PhotoImage.return_value.subsample.return_value)

    def test_river_image_factory_uses_the_river_subsample(self) -> None:
        tk = Mock()

        image = load_river_tile_image(tk, "1m.png")

        tk.PhotoImage.assert_called_once_with(file="1m.png")
        tk.PhotoImage.return_value.subsample.assert_called_once_with(
            RIVER_TILE_IMAGE_SUBSAMPLE
        )
        self.assertIs(image, tk.PhotoImage.return_value.subsample.return_value)


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
