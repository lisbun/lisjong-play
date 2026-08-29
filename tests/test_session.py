import unittest
from unittest.mock import patch

from lisjong.policies import (
    GenbutsuDefenseFiniteHorizonValueAwarePolicy,
    MinimalPolicy,
)
from lisjong_arena.lisjong_engine.policy_selector import PolicySeatSelector
from lisjong_engine.seat import Seat

from lisjong_play.cli import main
from lisjong_play.human_selector import HumanActionSelector
from lisjong_play.renderer import RIVER_LEGEND
from lisjong_play.session import build_seat_selectors, run_cli_session


class SessionTest(unittest.TestCase):
    def build_selectors(self, opponent: str | None = None):
        kwargs = {}
        if opponent is not None:
            kwargs["opponent"] = opponent
        return build_seat_selectors(
            input_reader=lambda _: "1",
            output_writer=lambda _: None,
            **kwargs,
        )

    def assert_ai_policy_type(self, selectors, policy_type: type) -> None:
        policies = []
        for seat in (Seat.SOUTH, Seat.WEST, Seat.NORTH):
            selector = selectors[seat]
            self.assertIsInstance(selector, PolicySeatSelector)
            self.assertEqual(seat, selector.seat)
            self.assertIsInstance(selector.policy, policy_type)
            policies.append(selector.policy)
        self.assertEqual(3, len({id(policy) for policy in policies}))

    def test_default_opponent_is_minimal_with_independent_instances(self) -> None:
        selectors = self.build_selectors()

        self.assertIsInstance(selectors[Seat.EAST], HumanActionSelector)
        self.assertEqual(set(Seat), set(selectors))
        self.assert_ai_policy_type(selectors, MinimalPolicy)

    def test_explicit_minimal_opponent(self) -> None:
        self.assert_ai_policy_type(self.build_selectors("minimal"), MinimalPolicy)

    def test_combined_opponent_with_independent_instances(self) -> None:
        self.assert_ai_policy_type(
            self.build_selectors("combined"),
            GenbutsuDefenseFiniteHorizonValueAwarePolicy,
        )

    def test_cli_defaults_to_minimal_opponent(self) -> None:
        def input_reader(_: str) -> str:
            return ""

        output: list[str] = []
        with patch("lisjong_play.cli.run_cli_session") as run_session:
            exit_code = main([], input_reader=input_reader, output_writer=output.append)

        self.assertEqual(0, exit_code)
        run_session.assert_called_once_with(
            seed=0,
            opponent="minimal",
            input_reader=input_reader,
            output_writer=output.append,
        )

    def test_cli_forwards_opponent_and_seed(self) -> None:
        def input_reader(_: str) -> str:
            return ""

        output: list[str] = []
        with patch("lisjong_play.cli.run_cli_session") as run_session:
            exit_code = main(
                ["--opponent", "combined", "--seed", "12345"],
                input_reader=input_reader,
                output_writer=output.append,
            )

        self.assertEqual(0, exit_code)
        run_session.assert_called_once_with(
            seed=12345,
            opponent="combined",
            input_reader=input_reader,
            output_writer=output.append,
        )

    def test_cli_rejects_unknown_opponent(self) -> None:
        with patch("lisjong_play.cli.run_cli_session") as run_session:
            with self.assertRaises(SystemExit) as raised:
                main(["--opponent", "unknown"])

        self.assertEqual(2, raised.exception.code)
        run_session.assert_not_called()

    def test_top_level_keyboard_interrupt_is_human_readable(self) -> None:
        output: list[str] = []
        with patch("lisjong_play.cli.run_cli_session", side_effect=KeyboardInterrupt):
            exit_code = main([], output_writer=output.append)
        self.assertEqual(130, exit_code)
        self.assertEqual(["対局を終了しました。"], output)

    def test_scripted_input_completes_one_hanchan_without_stdin(self) -> None:
        output: list[str] = []

        def scripted_input(prompt: str) -> str:
            if prompt == "> " or "Enter" in prompt:
                return ""
            return "1"

        run_cli_session(
            seed=0,
            input_reader=scripted_input,
            output_writer=output.append,
        )

        self.assertEqual(1, output.count(RIVER_LEGEND))
        self.assertTrue(any("半荘終了" in line for line in output))
        self.assertTrue(
            any("位" in line for line in output if "最終" in line or "位" in line)
        )


if __name__ == "__main__":
    unittest.main()
