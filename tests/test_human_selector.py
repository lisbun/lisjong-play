import unittest

from lisjong_engine.action_descriptor import (
    AnkanActionDescriptor,
    DiscardActionDescriptor,
    PassActionDescriptor,
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

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        if not self.values:
            raise AssertionError(f"unexpected input prompt: {prompt}")
        return self.values.pop(0)


class HumanActionSelectorTest(unittest.TestCase):
    def test_invalid_numeric_input_retries_until_valid(self) -> None:
        first = DiscardActionDescriptor(tile(rank=1), False)
        second = DiscardActionDescriptor(tile(rank=2), False)
        inputs = _Inputs(["abc", "0", "-1", "3", " 2 "])
        output: list[str] = []
        selector = HumanActionSelector(inputs, output.append)

        selected = selector(observation(), (first, second))

        self.assertIs(selected, second)
        self.assertEqual(5, inputs.calls)
        self.assertTrue(any("数字" in line for line in output))
        self.assertTrue(any("1から2" in line for line in output))

    def test_reaction_enter_selects_pass_and_pass_only_still_reads_input(self) -> None:
        pass_action = PassActionDescriptor(tile(), Seat.SOUTH)
        inputs = _Inputs([""])
        selector = HumanActionSelector(inputs, lambda _: None)

        selected = selector(
            observation(decision_kind=ObservationDecisionKind.DISCARD_REACTION),
            (pass_action,),
        )

        self.assertIs(selected, pass_action)
        self.assertEqual(1, inputs.calls)

    def test_enter_selects_explicit_tsumogiri(self) -> None:
        drawn = tile(rank=4)
        hand_discard = DiscardActionDescriptor(tile(rank=3), False)
        tsumogiri = DiscardActionDescriptor(drawn, True)
        selector = HumanActionSelector(_Inputs([""]), lambda _: None)

        selected = selector(observation(drawn=drawn), (hand_discard, tsumogiri))

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
        selector = HumanActionSelector(_Inputs(["2"]), outputs.append)

        selected = selector(
            observation(drawn=drawn, self_riichi=PublicRiichiStatus.ESTABLISHED),
            (tsumogiri, tsumo, ankan),
        )

        self.assertIs(selected, tsumo)
        menu = "\n".join(outputs)
        self.assertIn("ツモ", menu)
        self.assertIn("暗槓", menu)
        self.assertIn("ツモ切り", menu)

    def test_keyboard_interrupt_propagates(self) -> None:
        def interrupt(_: str) -> str:
            raise KeyboardInterrupt

        selector = HumanActionSelector(interrupt, lambda _: None)
        with self.assertRaises(KeyboardInterrupt):
            selector(observation(), (DiscardActionDescriptor(tile(), False),))


if __name__ == "__main__":
    unittest.main()
