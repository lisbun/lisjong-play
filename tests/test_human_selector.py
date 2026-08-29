import unittest

from lisjong_engine.action_descriptor import (
    AnkanActionDescriptor,
    DiscardActionDescriptor,
    PassActionDescriptor,
    PonActionDescriptor,
    RiichiActionDescriptor,
    TsumoActionDescriptor,
)
from lisjong_engine.observation import ObservationDecisionKind
from lisjong_engine.public_state import PublicRiichiStatus
from lisjong_engine.seat import Seat

from lisjong_play.human_selector import HumanActionSelector
from tests._fixtures import observation, tile


class _Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = values
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if not self.values:
            raise AssertionError(f"unexpected input prompt: {prompt}")
        return self.values.pop(0)


class HumanActionSelectorTest(unittest.TestCase):
    def test_invalid_numeric_input_retries_until_valid(self) -> None:
        first_tile = tile(rank=1)
        second_tile = tile(rank=2)
        first = DiscardActionDescriptor(first_tile, False)
        second = DiscardActionDescriptor(second_tile, False)
        inputs = _Inputs(["abc", "0", "-1", "3", " 2 "])
        output: list[str] = []
        selector = HumanActionSelector(inputs, output.append)

        selected = selector(
            observation(hand_tiles=(first_tile, second_tile)),
            (first, second),
        )

        self.assertIs(selected, second)
        self.assertEqual(5, inputs.calls)
        self.assertTrue(any("数字" in line for line in output))
        self.assertTrue(any("1から2" in line for line in output))

    def test_pure_discard_uses_hand_aligned_compact_menu(self) -> None:
        one = tile(rank=1)
        two = tile(rank=2)
        drawn = tile(rank=3)
        first = DiscardActionDescriptor(one, False)
        second = DiscardActionDescriptor(two, False)
        tsumogiri = DiscardActionDescriptor(drawn, True)
        output: list[str] = []
        selector = HumanActionSelector(_Inputs(["1"]), output.append)

        selected = selector(
            observation(drawn=drawn, hand_tiles=(one, one, two, drawn)),
            (second, tsumogiri, first),
        )

        self.assertIs(selected, first)
        text = "\n".join(output)
        self.assertIn("打牌を選んでください:", text)
        self.assertIn("手牌: 1m 1m 2m", text)
        self.assertIn("番号:", text)
        self.assertIn("| Enter", text)
        self.assertNotIn("1. 打牌", text)

    def test_tsumogiri_is_not_a_numeric_alias_in_pure_discard(self) -> None:
        one = tile(rank=1)
        drawn = tile(rank=3)
        hand_discard = DiscardActionDescriptor(one, False)
        tsumogiri = DiscardActionDescriptor(drawn, True)
        inputs = _Inputs(["2", ""])
        output: list[str] = []
        selector = HumanActionSelector(inputs, output.append)

        selected = selector(
            observation(drawn=drawn, hand_tiles=(one, drawn)),
            (hand_discard, tsumogiri),
        )

        self.assertIs(selected, tsumogiri)
        self.assertEqual(2, inputs.calls)
        self.assertTrue(any("1から1" in line for line in output))

    def test_reaction_enter_selects_pass_and_pass_only_still_reads_input(self) -> None:
        pass_action = PassActionDescriptor(tile(), Seat.SOUTH)
        inputs = _Inputs(["x", ""])
        output: list[str] = []
        selector = HumanActionSelector(inputs, output.append)

        selected = selector(
            observation(decision_kind=ObservationDecisionKind.DISCARD_REACTION),
            (pass_action,),
        )

        self.assertIs(selected, pass_action)
        self.assertEqual(2, inputs.calls)
        self.assertTrue(any("Enterのみ" in line for line in output))

    def test_reaction_pass_is_removed_from_numbered_menu(self) -> None:
        target = tile(rank=3)
        consumed = tile(rank=3)
        pon = PonActionDescriptor(target, (consumed, consumed), Seat.SOUTH)
        pass_action = PassActionDescriptor(target, Seat.SOUTH)
        output: list[str] = []
        selector = HumanActionSelector(_Inputs(["1"]), output.append)

        selected = selector(
            observation(decision_kind=ObservationDecisionKind.DISCARD_REACTION),
            (pass_action, pon),
        )

        self.assertIs(selected, pon)
        menu = "\n".join(output)
        self.assertIn("Enter=パス", menu)
        self.assertIn("1. ポン", menu)
        self.assertNotIn("1. パス", menu)
        self.assertNotIn("2. パス", menu)

    def test_enter_selects_explicit_tsumogiri(self) -> None:
        drawn = tile(rank=4)
        hand_tile = tile(rank=3)
        hand_discard = DiscardActionDescriptor(hand_tile, False)
        tsumogiri = DiscardActionDescriptor(drawn, True)
        selector = HumanActionSelector(_Inputs([""]), lambda _: None)

        selected = selector(
            observation(drawn=drawn, hand_tiles=(hand_tile, drawn)),
            (hand_discard, tsumogiri),
        )

        self.assertIs(selected, tsumogiri)

    def test_two_stage_riichi_is_two_fresh_selector_calls(self) -> None:
        outputs: list[str] = []
        inputs = _Inputs(["1", "1"])
        selector = HumanActionSelector(inputs, outputs.append)
        riichi = RiichiActionDescriptor()
        ordinary_discard = DiscardActionDescriptor(tile(rank=2), False)

        first = selector(observation(), (riichi, ordinary_discard))
        declaration_discard = DiscardActionDescriptor(tile(rank=5), False)
        second = selector(
            observation(decision_kind=ObservationDecisionKind.RIICHI_DISCARD),
            (declaration_discard,),
        )

        self.assertIs(first, riichi)
        self.assertIs(second, declaration_discard)
        self.assertEqual(2, inputs.calls)
        self.assertTrue(any("立直宣言牌選択" in line for line in outputs))

    def test_riichi_established_turn_keeps_tsumo_and_ankan_selectable(self) -> None:
        drawn = tile(rank=5)
        tsumogiri = DiscardActionDescriptor(drawn, True)
        tsumo = TsumoActionDescriptor(drawn)
        four = tile(rank=4)
        ankan = AnkanActionDescriptor((four, four, four, four))
        outputs: list[str] = []
        inputs = _Inputs(["1"])
        selector = HumanActionSelector(inputs, outputs.append)

        selected = selector(
            observation(
                drawn=drawn,
                hand_tiles=(four, four, four, four, drawn),
                self_riichi=PublicRiichiStatus.ESTABLISHED,
            ),
            (tsumogiri, tsumo, ankan),
        )

        self.assertIs(selected, tsumo)
        menu = "\n".join(outputs)
        self.assertIn("1. ツモ", menu)
        self.assertIn("2. 暗槓", menu)
        self.assertNotIn("打牌 5m（ツモ切り）", menu)
        self.assertIn("Enter=ツモ切り", inputs.prompts[0])

    def test_tsumogiri_only_still_waits_for_enter(self) -> None:
        drawn = tile(rank=5)
        tsumogiri = DiscardActionDescriptor(drawn, True)
        inputs = _Inputs(["1", ""])
        output: list[str] = []
        selector = HumanActionSelector(inputs, output.append)

        selected = selector(
            observation(
                drawn=drawn,
                hand_tiles=(drawn,),
                self_riichi=PublicRiichiStatus.ESTABLISHED,
            ),
            (tsumogiri,),
        )

        self.assertIs(selected, tsumogiri)
        self.assertEqual(2, inputs.calls)
        self.assertTrue(any("Enterのみ" in line for line in output))

    def test_unknown_action_value_fails_closed(self) -> None:
        selector = HumanActionSelector(_Inputs([]), lambda _: None)
        with self.assertRaises(TypeError):
            selector(observation(), (object(),))  # type: ignore[arg-type]

    def test_keyboard_interrupt_propagates(self) -> None:
        def interrupt(_: str) -> str:
            raise KeyboardInterrupt

        selector = HumanActionSelector(interrupt, lambda _: None)
        with self.assertRaises(KeyboardInterrupt):
            selector(observation(), (DiscardActionDescriptor(tile(), False),))


if __name__ == "__main__":
    unittest.main()
