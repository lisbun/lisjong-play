import unittest
from unittest.mock import patch

from lisjong_engine.seat import Seat

from lisjong_play.cli import main
from lisjong_play.human_selector import HumanActionSelector
from lisjong_play.renderer import RIVER_LEGEND
from lisjong_play.session import build_seat_selectors, run_cli_session


class SessionTest(unittest.TestCase):
    def test_initial_seat_assignment_is_human_east(self) -> None:
        selectors = build_seat_selectors(
            input_reader=lambda _: "1",
            output_writer=lambda _: None,
        )
        self.assertIsInstance(selectors[Seat.EAST], HumanActionSelector)
        self.assertEqual(set(Seat), set(selectors))
        for seat in (Seat.SOUTH, Seat.WEST, Seat.NORTH):
            self.assertEqual(seat, selectors[seat].seat)
            self.assertEqual("MinimalPolicy", type(selectors[seat].policy).__name__)

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
