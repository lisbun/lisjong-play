"""ReplayとRiichiLab liveが共有する`PolicyInput -> GuiBoardView`投影のtest。

Replay固有の局結果 / record metadataはここでは扱わない
(`tests/test_replay_source.py`の責務)。ここで固定するのは、player-safeな
1 `PolicyInput`から1盤面を作る共通投影の情報境界とfail closedである。
"""

import unittest
from types import SimpleNamespace

from lisjong.policy_contract import (
    AnkanAction,
    ChiAction,
    DiscardAction,
    KyuushuKyuuhaiAction,
    PassAction,
    PonAction,
    RiichiAction,
    RonAction,
    Seat,
    TileCategory,
    TsumoAction,
)

from lisjong_play.gui_model import GuiBoardView
from lisjong_play.policy_input_board import (
    PolicyInputProjectionError,
    action_label,
    build_policy_input_board_view,
    seat_name,
    tile_label,
)
from tests._riichilab_fixtures import policy_input, tile


def _StubTile(*, category: str, rank: int) -> SimpleNamespace:
    """typed `Tile`が作れない値でのfail closedを固定するためのstub。"""
    return SimpleNamespace(
        tile_type=SimpleNamespace(category=SimpleNamespace(value=category), rank=rank),
        is_red=False,
    )


def board(**overrides) -> GuiBoardView:
    return build_policy_input_board_view(
        policy_input(**overrides), decision_label="P2 打牌 東"
    )


class BoundSeatOrientationTest(unittest.TestCase):
    """bound seat(= `PolicyInput.self_seat`)を常に手前へ置く。"""

    def test_self_seat_is_always_at_the_bottom(self) -> None:
        for seat in Seat:
            with self.subTest(seat=int(seat)):
                view = board(seat=seat)
                positions = {index: view.seats[index].position for index in range(4)}
                self.assertEqual("bottom", positions[int(seat)])

    def test_remaining_seats_keep_their_relative_table_order(self) -> None:
        view = board(seat=Seat.SEAT_1)
        self.assertEqual(
            ["left", "bottom", "right", "top"],
            [seat.position for seat in view.seats],
        )

    def test_every_table_position_is_used_exactly_once(self) -> None:
        view = board(seat=Seat.SEAT_3)
        self.assertEqual(
            {"bottom", "right", "top", "left"},
            {seat.position for seat in view.seats},
        )


class InformationBoundaryTest(unittest.TestCase):
    """自身のplayer-visible stateだけを投影する。"""

    def test_only_the_bound_seat_hand_is_projected(self) -> None:
        source = policy_input(seat=Seat.SEAT_1)
        view = build_policy_input_board_view(source, decision_label="x")

        expected = len(source.own_hand.concealed_tiles) - 1
        self.assertEqual(expected, len(view.hand_tiles))
        self.assertEqual(tile_label(source.own_hand.drawn_tile), view.drawn_tile)

    def test_board_view_carries_no_opponent_concealed_hand_field(self) -> None:
        view = board()
        for seat in view.seats:
            self.assertEqual(
                {"position", "label", "score", "riichi", "melds", "river"},
                set(vars(seat)),
            )

    def test_public_facts_come_from_the_policy_input_unchanged(self) -> None:
        source = policy_input(seat=Seat.SEAT_1, wall=37, discards=3)
        view = build_policy_input_board_view(source, decision_label="x")

        self.assertEqual("東1局 0本場", view.round_label)
        self.assertIn("残り山 37枚", view.center_detail)
        self.assertIn("供託 1本", view.center_detail)
        self.assertEqual(("5m",), view.dora_indicators)
        self.assertEqual([26000, 26000, 24000, 24000], [s.score for s in view.seats])
        self.assertEqual(3, len(view.seats[0].river))
        self.assertEqual(1, len(view.seats[1].melds))
        self.assertEqual("立直", view.seats[1].riichi)
        self.assertEqual("宣言中", view.seats[2].riichi)

    def test_decision_label_is_supplied_by_the_consumer(self) -> None:
        self.assertEqual("P2 打牌 東", board().decision_label)

    def test_riichi_declaration_marker_is_not_inferred(self) -> None:
        """`PolicyInput`の`Discard`は宣言牌markerを持たないため推測しない。"""
        view = board(discards=3)
        self.assertEqual(
            [False, False, False],
            [river.is_riichi_declaration for river in view.seats[0].river],
        )


class FailClosedTest(unittest.TestCase):
    def test_unknown_seat_value_fails_closed(self) -> None:
        for value in (0, True, "0", 1.0, None):
            with self.subTest(value=value):
                with self.assertRaises(PolicyInputProjectionError):
                    seat_name(value)

    def test_undisplayable_tile_fails_closed(self) -> None:
        """typed contractの外から来た牌はsilentに別牌へ落とさない。

        lisjongの`TileType`自体はrank範囲を検証するため、この分岐は
        registryが解決できない値が来たときのfail closedを固定する。
        """
        with self.assertRaises(PolicyInputProjectionError):
            tile_label(_StubTile(category="manzu", rank=99))

    def test_unsupported_tile_category_fails_closed(self) -> None:
        with self.assertRaises(PolicyInputProjectionError):
            tile_label(_StubTile(category="flower", rank=1))

    def test_unusable_tile_value_fails_closed(self) -> None:
        with self.assertRaises(PolicyInputProjectionError):
            tile_label(object())

    def test_drawn_tile_absent_from_the_hand_fails_closed(self) -> None:
        """`OwnHandState`自体がこの不整合を禁じるため、stubで分岐を固定する。"""
        source = policy_input()
        broken = SimpleNamespace(
            self_seat=source.self_seat,
            round=source.round,
            players=source.players,
            own_hand=SimpleNamespace(
                concealed_tiles=source.own_hand.concealed_tiles,
                drawn_tile=tile(TileCategory.MANZU, 9),
            ),
        )
        with self.assertRaises(PolicyInputProjectionError):
            build_policy_input_board_view(broken, decision_label="x")

    def test_four_seat_states_are_required(self) -> None:
        source = policy_input()
        broken = SimpleNamespace(
            self_seat=source.self_seat,
            round=source.round,
            players=source.players[:3],
            own_hand=source.own_hand,
        )
        with self.assertRaises(PolicyInputProjectionError):
            build_policy_input_board_view(broken, decision_label="x")

    def test_non_string_decision_label_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            build_policy_input_board_view(policy_input(), decision_label=None)


class ActionLabelTest(unittest.TestCase):
    """canonical `InternalAction`表示helperをliveとReplayで共有する。"""

    def test_supported_variants_are_labelled(self) -> None:
        seat = Seat.SEAT_1
        cases = (
            (
                DiscardAction(
                    actor=seat, tile=tile(TileCategory.HONOR, 1), tsumogiri=True
                ),
                "打牌 東（ツモ切り）",
            ),
            (RiichiAction(actor=seat), "立直宣言"),
            (
                PonAction(
                    actor=seat,
                    target=Seat.SEAT_0,
                    called_tile=tile(TileCategory.PINZU, 2),
                    consumed_tiles=(
                        tile(TileCategory.PINZU, 2),
                        tile(TileCategory.PINZU, 2),
                    ),
                ),
                "ポン 2p / 使用 2p 2p / from P1",
            ),
            (
                ChiAction(
                    actor=seat,
                    target=Seat.SEAT_0,
                    called_tile=tile(TileCategory.MANZU, 3),
                    consumed_tiles=(
                        tile(TileCategory.MANZU, 4),
                        tile(TileCategory.MANZU, 5),
                    ),
                ),
                "チー 3m / 使用 4m 5m / from P1",
            ),
            (
                AnkanAction(
                    actor=seat,
                    tiles=(
                        tile(TileCategory.SOUZU, 3),
                        tile(TileCategory.SOUZU, 3),
                        tile(TileCategory.SOUZU, 3),
                        tile(TileCategory.SOUZU, 3),
                    ),
                ),
                "暗槓 3s 3s 3s 3s",
            ),
            (
                TsumoAction(actor=seat, winning_tile=tile(TileCategory.PINZU, 7)),
                "ツモ 7p",
            ),
            (
                RonAction(
                    actor=seat,
                    target=Seat.SEAT_0,
                    winning_tile=tile(TileCategory.PINZU, 7),
                ),
                "ロン 7p / from P1",
            ),
            (PassAction(actor=seat), "パス"),
            (KyuushuKyuuhaiAction(actor=seat), "九種九牌"),
        )
        for action, expected in cases:
            with self.subTest(action=type(action).__name__):
                self.assertEqual(expected, action_label(action))

    def test_unknown_action_variant_fails_closed(self) -> None:
        class UnknownFutureAction:
            actor = Seat.SEAT_0

        with self.assertRaises(PolicyInputProjectionError):
            action_label(UnknownFutureAction())


if __name__ == "__main__":
    unittest.main()
