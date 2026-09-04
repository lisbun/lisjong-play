import unittest
from unittest.mock import patch

from lisjong_play.gui import GuiUnavailableError, main


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


if __name__ == "__main__":
    unittest.main()
