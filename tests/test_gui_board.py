"""live Human PlayとReplayが共有するboard rendererのtest。"""

import unittest
from typing import Any
from unittest.mock import Mock

from lisjong_play.gui_board import (
    BOARD_TILE_BACKGROUND,
    BOARD_TILE_IMAGE_SUBSAMPLE,
    CENTER_CLEARANCE,
    HAND_TILE_IMAGE_SUBSAMPLE,
    MELD_RIVER_GAP,
    RIVER_ROW_SIZE,
    RIVER_TILE_GAP,
    RIVER_TILE_IMAGE_SUBSAMPLE,
    TSUMOGIRI_FACE_COLOR,
    BoardTileImages,
    GuiBoardRenderer,
    build_board_tile_images,
    configure_board_styles,
    gray_tile_face,
    grayed_face_pixel_rows,
    load_board_tile_image,
    load_river_tile_image,
    load_tile_image,
    meld_caption,
    place_seats_around_center,
    river_caption,
    seat_center_offset,
    seat_melds_come_first,
    seat_part_side,
    seat_status_side,
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


def tile_images(**overrides: Any) -> BoardTileImages:
    fields = {
        "hand": Mock(),
        "board": Mock(),
        "river": Mock(),
        "river_tsumogiri": Mock(),
    }
    fields.update(overrides)
    return BoardTileImages(**fields)  # type: ignore[arg-type]


def board_widget(*, table_width: int = 2000) -> Mock:
    """中央ブロックのように実寸を返すwidget double。"""
    return Mock(
        winfo_children=Mock(return_value=[]),
        winfo_reqwidth=Mock(return_value=200),
        winfo_reqheight=Mock(return_value=100),
        master=Mock(winfo_width=Mock(return_value=table_width)),
    )


def seat_widget(*, width: int = 100) -> Mock:
    return Mock(winfo_reqwidth=Mock(return_value=width))


def renderer(*, on_select_action=None) -> GuiBoardRenderer:
    return GuiBoardRenderer(Mock(), tile_images(), on_select_action=on_select_action)


class GuiBoardRendererContractTest(unittest.TestCase):
    def test_rejects_a_non_callable_selection_handler(self) -> None:
        with self.assertRaises(TypeError):
            GuiBoardRenderer(Mock(), tile_images(), on_select_action="not callable")

    def test_seat_title_compacts_label_score_and_riichi_into_one_line(self) -> None:
        board = renderer()
        frame = Mock(winfo_children=Mock(return_value=[]))

        board.render_seat(
            frame,
            GuiSeatView(
                position="right",
                label="P2（南家）",
                score=24000,
                riichi="立直",
                melds=(),
                river=(),
            ),
        )

        frame.configure.assert_not_called()
        status_calls = [
            call.kwargs
            for call in board._ttk.Label.call_args_list
            if call.kwargs.get("style") == "SeatInfo.TLabel"
        ]
        self.assertEqual(1, len(status_calls))
        self.assertEqual("P2（南家）  24000点 / 立直", status_calls[0]["text"])


class GuiTileImageRegistrySharingTest(unittest.TestCase):
    """副露 / ドラ表示牌が手牌と同じregistryを、河だけが河専用registryを使うことを検証する。"""

    def test_river_tile_looks_up_its_image_from_the_river_registry(self) -> None:
        board = renderer()

        board.render_river_tile(Mock(), GuiRiverTile("5pr", False, False, None))

        board._tile_images.river.get.assert_called_once_with("5pr")
        board._tile_images.hand.get.assert_not_called()

    def test_river_tile_label_uses_the_river_sized_image(self) -> None:
        board = renderer()

        board.render_river_tile(Mock(), GuiRiverTile("5pr", False, False, None))

        _, kwargs = board._ttk.Label.call_args_list[0]
        self.assertIs(kwargs["image"], board._tile_images.river.get.return_value)

    def test_a_tsumogiri_river_tile_uses_the_grayed_face_registry(self) -> None:
        """ツモ切りは白地をグレーにした牌画像とグレー背景で示す。"""
        board = renderer()

        board.render_river_tile(Mock(), GuiRiverTile("5pr", True, False, None))

        board._tile_images.river_tsumogiri.get.assert_called_once_with("5pr")
        board._tile_images.river.get.assert_not_called()
        _, kwargs = board._ttk.Label.call_args
        self.assertIs(
            kwargs["image"], board._tile_images.river_tsumogiri.get.return_value
        )
        self.assertEqual("TsumogiriTile.TLabel", kwargs["style"])

    def test_same_tile_label_resolves_to_distinct_images_per_usage(self) -> None:
        board = GuiBoardRenderer(
            Mock(),
            BoardTileImages(
                hand=TileImageRegistry(lambda path: ("hand", path)),
                board=TileImageRegistry(lambda path: ("board", path)),
                river=TileImageRegistry(lambda path: ("river", path)),
                river_tsumogiri=TileImageRegistry(lambda path: ("tsumogiri", path)),
            ),
        )

        hand_image = board.tile_image("1m")
        board_image = board.board_tile_image("1m")
        river_image = board.river_tile_image("1m")

        self.assertEqual("board", board_image[0])
        self.assertIs(board_image, board.board_tile_image("1m"))
        self.assertEqual("hand", hand_image[0])
        self.assertEqual("river", river_image[0])
        self.assertIs(hand_image, board.tile_image("1m"))
        self.assertIs(river_image, board.river_tile_image("1m"))

    def test_meld_tiles_look_up_their_images_from_the_shared_registry(self) -> None:
        board = renderer()

        board.render_meld(Mock(), GuiMeldView("ポン", ("1m", "1m", "1m"), "P2", "1m"))

        self.assertEqual(
            [("1m",), ("1m",), ("1m",)],
            [call.args for call in board._tile_images.board.get.call_args_list],
        )
        board._tile_images.river.get.assert_not_called()

    def test_meld_caption_names_only_the_type_and_the_called_seat(self) -> None:
        """鳴いた牌は晒された牌に見えているため、textでは繰り返さない。"""
        board = renderer()

        board.render_meld(Mock(), GuiMeldView("ポン", ("1m", "1m", "1m"), "P2", "1m"))

        label_texts = [
            call.kwargs.get("text")
            for call in board._ttk.Label.call_args_list
            if "text" in call.kwargs
        ]
        self.assertEqual(["ポン→P2"], label_texts)

    def test_meld_caption_omits_called_tile_for_a_concealed_meld(self) -> None:
        board = renderer()

        board.render_meld(
            Mock(), GuiMeldView("暗槓", ("1m", "1m", "1m", "1m"), None, None)
        )

        label_texts = [
            call.kwargs.get("text")
            for call in board._ttk.Label.call_args_list
            if "text" in call.kwargs
        ]
        self.assertEqual(["暗槓"], label_texts)

    def test_dora_indicators_look_up_their_images_from_the_shared_registry(
        self,
    ) -> None:
        board = renderer()
        board.render_seat = Mock()  # type: ignore[method-assign]

        board.render_board(
            board_view(dora_indicators=("東", "5sr")),
            (),
            seat_frames={
                position: seat_widget()
                for position in ("top", "bottom", "left", "right")
            },
            center=board_widget(),
            hand=Mock(winfo_children=Mock(return_value=[])),
        )

        self.assertEqual(
            [("東",), ("5sr",)],
            [call.args for call in board._tile_images.board.get.call_args_list],
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
    def test_the_hand_is_larger_than_the_tiles_on_the_table(self) -> None:
        """手牌だけが大きく、河と副露・ドラ表示牌は同じsize。"""
        self.assertLess(HAND_TILE_IMAGE_SUBSAMPLE, RIVER_TILE_IMAGE_SUBSAMPLE)
        self.assertEqual(BOARD_TILE_IMAGE_SUBSAMPLE, RIVER_TILE_IMAGE_SUBSAMPLE)

    def test_hand_image_factory_uses_the_hand_subsample(self) -> None:
        tk = Mock()

        image = load_tile_image(tk, "1m.png")

        tk.PhotoImage.assert_called_once_with(file="1m.png")
        tk.PhotoImage.return_value.subsample.assert_called_once_with(
            HAND_TILE_IMAGE_SUBSAMPLE
        )
        self.assertIs(image, tk.PhotoImage.return_value.subsample.return_value)

    def test_board_image_factory_uses_the_board_subsample(self) -> None:
        tk = Mock()

        image = load_board_tile_image(tk, "1m.png")

        tk.PhotoImage.assert_called_once_with(file="1m.png")
        tk.PhotoImage.return_value.subsample.assert_called_once_with(
            BOARD_TILE_IMAGE_SUBSAMPLE
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
        board._tile_images.hand.get.assert_called_once_with("5pr")
        _, kwargs = board._ttk.Button.call_args
        self.assertIs(kwargs["image"], board._tile_images.hand.get.return_value)
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
        board._tile_images.hand.get.assert_called_once_with("1p")
        _, kwargs = board._ttk.Button.call_args
        kwargs["command"]()
        select.assert_called_once_with(9)

    def test_tile_control_stays_a_label_for_a_non_legal_tile(self) -> None:
        board = renderer(on_select_action=Mock())

        control = board.tile_control(Mock(), "3s", None)

        self.assertIs(control, board._ttk.Label.return_value)
        board._tile_images.hand.get.assert_called_once_with("3s")
        board._ttk.Button.assert_not_called()

    def test_duplicate_hand_tiles_resolve_to_the_same_cached_image(self) -> None:
        board = renderer(on_select_action=Mock())

        board.tile_control(Mock(), "5m", action_view(1, tile_label="5m"))
        board.tile_control(Mock(), "5m", None)

        self.assertEqual(
            [("5m",), ("5m",)],
            [call.args for call in board._tile_images.hand.get.call_args_list],
        )

    def test_without_a_selection_handler_every_tile_stays_a_label(self) -> None:
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
                position: seat_widget()
                for position in ("top", "bottom", "left", "right")
            },
            center=board_widget(),
            hand=Mock(winfo_children=Mock(return_value=[])),
        )

        board._ttk.Button.assert_not_called()


class FakePhotoImage:
    """`PhotoImage`のpixel APIだけを真似た、Tk非依存のtest double。"""

    def __init__(self, pixels: list[list[tuple[int, int, int]]]) -> None:
        self.pixels = pixels
        self.transparent: set[tuple[int, int]] = set()
        self.put_calls: list[Any] = []

    def width(self) -> int:
        return len(self.pixels[0])

    def height(self) -> int:
        return len(self.pixels)

    def get(self, x: int, y: int) -> tuple[int, int, int]:
        return self.pixels[y][x]

    def put(self, rows: Any) -> None:
        self.put_calls.append(rows)
        self.transparent.clear()

    def transparency_get(self, x: int, y: int) -> bool:
        return (x, y) in self.transparent

    def transparency_set(self, x: int, y: int, value: bool) -> None:
        if value:
            self.transparent.add((x, y))
        else:
            self.transparent.discard((x, y))


class GuiTsumogiriFaceTest(unittest.TestCase):
    """ツモ切り牌は白地だけをグレーへ置き換える。"""

    def test_white_face_pixels_become_gray(self) -> None:
        image = FakePhotoImage([[(255, 255, 255), (250, 248, 252)]])

        rows = grayed_face_pixel_rows(image)

        self.assertEqual([[TSUMOGIRI_FACE_COLOR, TSUMOGIRI_FACE_COLOR]], rows)

    def test_colored_glyph_pixels_keep_their_color(self) -> None:
        image = FakePhotoImage([[(200, 20, 20), (0, 0, 0), (20, 120, 40)]])

        rows = grayed_face_pixel_rows(image)

        self.assertEqual([["#c81414", "#000000", "#147828"]], rows)

    def test_a_string_pixel_value_is_accepted(self) -> None:
        image = FakePhotoImage([[(0, 0, 0)]])
        image.get = lambda x, y: "255 255 255"  # type: ignore[assignment]

        self.assertEqual([[TSUMOGIRI_FACE_COLOR]], grayed_face_pixel_rows(image))

    def test_transparent_pixels_stay_transparent(self) -> None:
        image = FakePhotoImage([[(255, 255, 255), (10, 20, 30)]])
        image.transparency_set(0, 0, True)

        converted = gray_tile_face(image)

        self.assertIs(converted, image)
        self.assertEqual(1, len(image.put_calls))
        self.assertTrue(image.transparency_get(0, 0))
        self.assertFalse(image.transparency_get(1, 0))


class GuiSeatOrientationTest(unittest.TestCase):
    """text情報 / 副露を外周側、河を中央側へ向ける配置のtest。"""

    def test_side_seats_stack_their_status_above_the_river(self) -> None:
        self.assertEqual("top", seat_status_side("left"))
        self.assertEqual("top", seat_status_side("right"))
        self.assertEqual("top", seat_status_side("top"))
        self.assertEqual("bottom", seat_status_side("bottom"))

    def test_each_position_packs_toward_its_own_edge(self) -> None:
        self.assertEqual("top", seat_part_side("top"))
        self.assertEqual("bottom", seat_part_side("bottom"))
        self.assertEqual("left", seat_part_side("left"))
        self.assertEqual("right", seat_part_side("right"))

    def test_an_unknown_position_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            seat_part_side("middle")
        with self.assertRaises(ValueError):
            seat_melds_come_first("middle")

    def test_melds_are_exposed_on_the_outer_side_of_the_river(self) -> None:
        self.assertTrue(seat_melds_come_first("left"))
        for position in ("top", "bottom", "right"):
            self.assertFalse(seat_melds_come_first(position))

    def test_the_seat_places_its_melds_next_to_the_river_not_above_it(self) -> None:
        board = renderer()
        frame = Mock(winfo_children=Mock(return_value=[]))
        body = Mock()
        created: list[Any] = []

        def frame_factory(parent, **_kwargs):
            widget = Mock()
            created.append((parent, widget))
            return body if parent is frame else widget

        board._ttk.Frame.side_effect = frame_factory
        board.render_seat(
            frame,
            GuiSeatView(
                position="top",
                label="P3",
                score=25000,
                riichi="",
                melds=(),
                river=(GuiRiverTile("1m", False, False, None),),
            ),
        )

        sides = [call.kwargs.get("side") for call in body.pack.call_args_list]
        self.assertEqual(["top"], sides)

    def test_seat_parts_are_packed_status_then_melds_then_river(self) -> None:
        for position in ("top", "bottom", "left", "right"):
            with self.subTest(position=position):
                board = renderer()
                frame = Mock(winfo_children=Mock(return_value=[]))
                packed: list[tuple[str, Any]] = []

                def record(kind: str, parent: Any):
                    widget = Mock()
                    if parent is frame:
                        widget.pack.side_effect = lambda **kwargs: packed.append(
                            (kind, kwargs.get("side"))
                        )
                    return widget

                board._ttk.Label.side_effect = lambda parent, **kwargs: record(
                    "text", parent
                )
                board._ttk.Frame.side_effect = lambda parent, **kwargs: record(
                    "frame", parent
                )

                board.render_seat(
                    frame,
                    GuiSeatView(
                        position=position,
                        label="P1",
                        score=25000,
                        riichi="",
                        melds=(GuiMeldView("ポン", ("1m",), "P2", "1m"),),
                        river=(GuiRiverTile("1m", False, False, None),),
                    ),
                )

                status_side = "top" if position in ("left", "right") else position
                self.assertEqual(
                    [("text", status_side), ("frame", position)],
                    packed,
                )


class GuiBoardTileImageBundleTest(unittest.TestCase):
    def test_the_bundle_builds_one_registry_per_display_size(self) -> None:
        images = build_board_tile_images(Mock())

        registries = (
            images.hand,
            images.board,
            images.river,
            images.river_tsumogiri,
        )
        self.assertEqual(4, len({id(registry) for registry in registries}))


class GuiBoardStyleTest(unittest.TestCase):
    def test_table_tiles_sit_on_a_white_backing_not_the_felt(self) -> None:
        """牌画像の角は透過しているため、背景が緑だと牌の中に緑が透ける。"""
        style = Mock()

        configure_board_styles(style)

        configured = {
            call.args[0]: call.kwargs
            for call in style.configure.call_args_list
            if call.args
        }
        self.assertEqual(
            BOARD_TILE_BACKGROUND, configured["BoardTile.TLabel"]["background"]
        )
        self.assertEqual("#ffffff", BOARD_TILE_BACKGROUND)
        self.assertEqual(
            TSUMOGIRI_FACE_COLOR, configured["TsumogiriTile.TLabel"]["background"]
        )


class GuiMeldCaptionTest(unittest.TestCase):
    def test_a_called_meld_names_the_seat_it_was_called_from(self) -> None:
        self.assertEqual(
            "ポン→P2", meld_caption(GuiMeldView("ポン", ("1m",), "P2", "1m"))
        )

    def test_a_concealed_meld_is_only_the_type(self) -> None:
        self.assertEqual("暗槓", meld_caption(GuiMeldView("暗槓", ("1m",), None, None)))


class GuiCenterClearanceTest(unittest.TestCase):
    """中央情報と河 / 副露が重ならないことを検証する。"""

    def test_offsets_clear_half_the_center_block_plus_a_margin(self) -> None:
        self.assertEqual(
            (0, -(50 + CENTER_CLEARANCE)), seat_center_offset("top", 200, 100)
        )
        self.assertEqual(
            (0, 50 + CENTER_CLEARANCE), seat_center_offset("bottom", 200, 100)
        )
        self.assertEqual(
            (-(100 + CENTER_CLEARANCE), 0), seat_center_offset("left", 200, 100)
        )
        self.assertEqual(
            (100 + CENTER_CLEARANCE, 0), seat_center_offset("right", 200, 100)
        )

    def test_a_taller_center_pushes_the_seats_further_out(self) -> None:
        _, near = seat_center_offset("bottom", 200, 100)
        _, far = seat_center_offset("bottom", 200, 160)

        self.assertGreater(far, near)

    def test_an_unknown_position_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            seat_center_offset("middle", 200, 100)

    def test_a_wide_center_never_pushes_seats_off_the_table(self) -> None:
        """中央情報が横に長くても、左右のseatは卓の中に残す。"""
        center = board_widget(table_width=900)
        center.winfo_reqwidth.return_value = 700
        seat_frames = {
            position: seat_widget(width=300)
            for position in ("top", "bottom", "left", "right")
        }

        offsets = place_seats_around_center(seat_frames, center)

        x, _ = offsets["left"]
        self.assertGreaterEqual(x, -(900 // 2 - 300))

    def test_every_seat_is_replaced_from_the_measured_center(self) -> None:
        center = board_widget()
        seat_frames = {
            position: seat_widget() for position in ("top", "bottom", "left", "right")
        }

        offsets = place_seats_around_center(seat_frames, center)

        for position, frame in seat_frames.items():
            x, y = seat_center_offset(position, 200, 100)
            frame.place_configure.assert_called_once_with(x=x, y=y)
            self.assertEqual((x, y), offsets[position])

    def test_rendering_a_board_repositions_the_seats(self) -> None:
        board = renderer()
        board.render_seat = Mock()  # type: ignore[method-assign]
        seat_frames = {
            position: seat_widget() for position in ("top", "bottom", "left", "right")
        }

        board.render_board(
            board_view(),
            (),
            seat_frames=seat_frames,
            center=board_widget(),
            hand=board_widget(),
        )

        for frame in seat_frames.values():
            frame.place_configure.assert_called_once()


class GuiBoardSpacingTest(unittest.TestCase):
    """河牌どうし / 河と副露は離し、同じ組の副露牌は密着させる。"""

    def test_river_tiles_are_spaced_apart(self) -> None:
        board = renderer()
        board._ttk.Frame.side_effect = lambda *args, **kwargs: Mock()
        packed: list[dict[str, Any]] = []
        board.render_river_tile = (  # type: ignore[method-assign]
            lambda row, cell: Mock(
                pack=Mock(side_effect=lambda **kw: packed.append(kw))
            )
        )

        board.render_seat(
            Mock(winfo_children=Mock(return_value=[])),
            GuiSeatView(
                position="bottom",
                label="P1",
                score=25000,
                riichi="",
                melds=(),
                river=tuple(GuiRiverTile("1m", False, False, None) for _ in range(3)),
            ),
        )

        self.assertEqual([RIVER_TILE_GAP] * 3, [kw["padx"] for kw in packed])
        self.assertGreater(RIVER_TILE_GAP, 0)

    def test_the_river_and_the_melds_are_separated(self) -> None:
        board = renderer()
        frame = Mock(winfo_children=Mock(return_value=[]))
        body = Mock()
        children: list[Any] = []

        def frame_factory(parent, **_kwargs):
            widget = Mock()
            if parent is frame:
                return body
            if parent is body:
                children.append(widget)
            return widget

        board._ttk.Frame.side_effect = frame_factory
        board.render_seat(
            frame,
            GuiSeatView(
                position="bottom",
                label="P1",
                score=25000,
                riichi="",
                melds=(GuiMeldView("ポン", ("1m",), "P2", "1m"),),
                river=(GuiRiverTile("1m", False, False, None),),
            ),
        )

        pads = [
            call.kwargs["padx"]
            for child in children
            for call in child.pack.call_args_list
        ]
        # 生成順は副露 -> 河。bottom席では河が中央側(先頭)なので、
        # 副露は左に、河は右に隙間が付く。
        self.assertEqual([(MELD_RIVER_GAP, 0), (0, MELD_RIVER_GAP)], pads)

    def test_tiles_inside_one_meld_group_stay_flush(self) -> None:
        board = renderer()

        board.render_meld(Mock(), GuiMeldView("ポン", ("1m", "1m", "1m"), "P2", "1m"))

        tile_packs = [
            call.kwargs
            for call in board._ttk.Label.return_value.pack.call_args_list
            if call.kwargs.get("side") == "left"
        ]
        self.assertTrue(tile_packs)
        for kwargs in tile_packs:
            self.assertNotIn("padx", kwargs)


class GuiMeldStackingTest(unittest.TestCase):
    def test_each_meld_group_is_stacked_vertically(self) -> None:
        """副露が複数組でも、横幅ではなく縦へ積む。"""
        board = renderer()
        parent = Mock()
        boxes: list[Any] = []

        def frame_factory(frame_parent, **_kwargs):
            widget = Mock()
            if frame_parent is parent:
                boxes.append(widget)
            return widget

        board._ttk.Frame.side_effect = frame_factory

        for _ in range(3):
            board.render_meld(parent, GuiMeldView("ポン", ("1m",), "P2", "1m"))

        self.assertEqual(3, len(boxes))
        for box in boxes:
            box.pack.assert_called_once_with(side="top", anchor="w")


class GuiRiverCaptionTest(unittest.TestCase):
    def test_plain_discard_has_no_caption(self) -> None:
        self.assertEqual("", river_caption(GuiRiverTile("1m", False, False, None)))

    def test_tsumogiri_has_no_caption_because_the_tile_itself_is_grayed(
        self,
    ) -> None:
        self.assertEqual("", river_caption(GuiRiverTile("1m", True, False, None)))

    def test_riichi_declaration_is_marked(self) -> None:
        self.assertEqual("[立]", river_caption(GuiRiverTile("1m", False, True, None)))

    def test_riichi_declaration_tsumogiri_keeps_the_riichi_caption(self) -> None:
        self.assertEqual("[立]", river_caption(GuiRiverTile("1m", True, True, None)))

    def test_called_discard_appends_the_calling_seat(self) -> None:
        self.assertEqual("→P3", river_caption(GuiRiverTile("1m", False, False, "P3")))


if __name__ == "__main__":
    unittest.main()
