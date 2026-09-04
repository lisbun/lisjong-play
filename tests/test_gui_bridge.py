import threading
import unittest
from queue import Empty
from unittest.mock import patch

from lisjong_engine.action_descriptor import (
    DiscardActionDescriptor,
    RiichiActionDescriptor,
)
from lisjong_engine.observation import ObservationDecisionKind
from lisjong_engine.round_progress import DiscardProgress
from lisjong_engine.seat import Seat

from lisjong_play.gui_bridge import (
    DecisionRequested,
    GuiBridgeError,
    GuiSessionBridge,
    GuiSessionClosed,
    MatchCompleted,
    ProgressDelivered,
    RoundCompleted,
    SessionFailed,
    SessionFinished,
    run_gui_worker,
)
from tests._fixtures import observation, tile
from tests.test_renderer import match_fact, round_fact


class GuiSessionBridgeTest(unittest.TestCase):
    def test_selection_returns_the_original_descriptor_instance(self) -> None:
        bridge = GuiSessionBridge()
        first = DiscardActionDescriptor(tile(rank=1), False)
        second = DiscardActionDescriptor(tile(rank=2), False)
        selected: list[object] = []

        worker = threading.Thread(
            target=lambda: selected.append(
                bridge.select_action(observation(), (first, second))
            )
        )
        worker.start()
        event = bridge.next_event(timeout=1)
        self.assertIsInstance(event, DecisionRequested)
        assert isinstance(event, DecisionRequested)

        bridge.choose_action(event.request_id, 1)
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(selected))
        self.assertIs(second, selected[0])
        with self.assertRaisesRegex(GuiBridgeError, "no longer active"):
            bridge.choose_action(event.request_id, 0)

    def test_invalid_option_index_does_not_release_active_decision(self) -> None:
        bridge = GuiSessionBridge()
        action = DiscardActionDescriptor(tile(), False)
        selected: list[object] = []
        worker = threading.Thread(
            target=lambda: selected.append(
                bridge.select_action(observation(), (action,))
            )
        )
        worker.start()
        event = bridge.next_event(timeout=1)
        assert isinstance(event, DecisionRequested)

        with self.assertRaisesRegex(GuiBridgeError, "outside"):
            bridge.choose_action(event.request_id, 1)
        bridge.choose_action(event.request_id, 0)
        worker.join(timeout=1)

        self.assertEqual([action], selected)

    def test_two_stage_riichi_uses_two_fresh_requests(self) -> None:
        bridge = GuiSessionBridge()
        riichi = RiichiActionDescriptor()
        declaration_discard = DiscardActionDescriptor(tile(rank=5), False)
        selected: list[object] = []

        def make_two_decisions() -> None:
            selected.append(bridge.select_action(observation(), (riichi,)))
            selected.append(
                bridge.select_action(
                    observation(decision_kind=ObservationDecisionKind.RIICHI_DISCARD),
                    (declaration_discard,),
                )
            )

        worker = threading.Thread(target=make_two_decisions)
        worker.start()
        first = bridge.next_event(timeout=1)
        assert isinstance(first, DecisionRequested)
        bridge.choose_action(first.request_id, 0)
        second = bridge.next_event(timeout=1)
        assert isinstance(second, DecisionRequested)
        bridge.choose_action(second.request_id, 0)
        worker.join(timeout=1)

        self.assertNotEqual(first.request_id, second.request_id)
        self.assertFalse(worker.is_alive())
        self.assertIs(riichi, selected[0])
        self.assertIs(declaration_discard, selected[1])

    def test_inconsistent_tsumogiri_fails_before_request_is_published(self) -> None:
        bridge = GuiSessionBridge()
        drawn = tile(rank=5)
        inconsistent = DiscardActionDescriptor(tile(rank=6), True)

        with self.assertRaisesRegex(GuiBridgeError, "must match"):
            bridge.select_action(
                observation(drawn=drawn, hand_tiles=(drawn,)),
                (inconsistent,),
            )
        self.assertEqual((), bridge.drain_events())

    def test_round_completion_blocks_until_matching_confirmation(self) -> None:
        bridge = GuiSessionBridge()
        completed: list[bool] = []
        worker = threading.Thread(
            target=lambda: (
                bridge.deliver((round_fact(has_next_round=True),)),
                completed.append(True),
            )
        )
        worker.start()
        event = bridge.next_event(timeout=1)
        self.assertIsInstance(event, RoundCompleted)
        assert isinstance(event, RoundCompleted)
        self.assertIsNotNone(event.confirmation_id)
        self.assertTrue(worker.is_alive())

        bridge.confirm_round(event.confirmation_id)  # type: ignore[arg-type]
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual([True], completed)

    def test_terminal_delivery_keeps_order_and_needs_no_confirmation(self) -> None:
        bridge = GuiSessionBridge()
        bridge.deliver(
            (
                DiscardProgress(Seat.EAST, tile(rank=3), False),
                round_fact(has_next_round=False),
                match_fact(),
            )
        )

        events = bridge.drain_events()
        self.assertEqual(3, len(events))
        self.assertIsInstance(events[0], ProgressDelivered)
        self.assertIsInstance(events[1], RoundCompleted)
        self.assertIsNone(events[1].confirmation_id)  # type: ignore[union-attr]
        self.assertIsInstance(events[2], MatchCompleted)

    def test_close_releases_blocked_decision(self) -> None:
        bridge = GuiSessionBridge()
        action = DiscardActionDescriptor(tile(), False)
        errors: list[BaseException] = []

        def wait_for_action() -> None:
            try:
                bridge.select_action(observation(), (action,))
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=wait_for_action)
        worker.start()
        bridge.next_event(timeout=1)
        bridge.close()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], GuiSessionClosed)
        bridge.close()

    def test_close_releases_blocked_round_confirmation(self) -> None:
        bridge = GuiSessionBridge()
        errors: list[BaseException] = []

        def wait_for_confirmation() -> None:
            try:
                bridge.deliver((round_fact(has_next_round=True),))
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=wait_for_confirmation)
        worker.start()
        event = bridge.next_event(timeout=1)
        self.assertIsInstance(event, RoundCompleted)
        bridge.close()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], GuiSessionClosed)


class GuiWorkerTest(unittest.TestCase):
    def test_success_and_failure_are_reported_as_events(self) -> None:
        successful = GuiSessionBridge()
        with patch("lisjong_play.gui_bridge._run_session"):
            run_gui_worker(successful, seed=0, opponent="minimal")
        self.assertIsInstance(successful.next_event(timeout=1), SessionFinished)

        failed = GuiSessionBridge()
        with patch(
            "lisjong_play.gui_bridge._run_session", side_effect=RuntimeError("boom")
        ):
            run_gui_worker(failed, seed=0, opponent="minimal")
        event = failed.next_event(timeout=1)
        self.assertIsInstance(event, SessionFailed)
        assert isinstance(event, SessionFailed)
        self.assertEqual("RuntimeError: boom", event.message)

    def test_closed_worker_does_not_publish_terminal_event(self) -> None:
        bridge = GuiSessionBridge()

        def close_during_session(**_kwargs: object) -> None:
            raise GuiSessionClosed("closed")

        with patch("lisjong_play.gui_bridge._run_session", close_during_session):
            run_gui_worker(bridge, seed=0, opponent="minimal")
        with self.assertRaises(Empty):
            bridge.next_event(timeout=0.01)


class GuiSessionIntegrationTest(unittest.TestCase):
    def test_scripted_gui_events_complete_one_hanchan(self) -> None:
        bridge = GuiSessionBridge()
        worker = threading.Thread(
            target=run_gui_worker,
            kwargs={"bridge": bridge, "seed": 0, "opponent": "minimal"},
        )
        worker.start()
        decisions = 0
        rounds = 0
        match_completed = False

        while True:
            event = bridge.next_event(timeout=15)
            if isinstance(event, DecisionRequested):
                decisions += 1
                bridge.choose_action(event.request_id, event.actions[0].option_index)
            elif isinstance(event, RoundCompleted):
                rounds += 1
                if event.confirmation_id is not None:
                    bridge.confirm_round(event.confirmation_id)
            elif isinstance(event, MatchCompleted):
                match_completed = True
            elif isinstance(event, SessionFailed):
                self.fail(event.message)
            elif isinstance(event, SessionFinished):
                break

        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertGreater(decisions, 0)
        self.assertGreater(rounds, 0)
        self.assertTrue(match_completed)


if __name__ == "__main__":
    unittest.main()
