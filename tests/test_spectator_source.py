import threading
import time
import unittest
from unittest.mock import Mock

from lisjong.policies import (
    GenbutsuDefenseFiniteHorizonValueAwarePolicy,
    MinimalPolicy,
)
from lisjong_arena.lisjong_engine.policy_selector import PolicySeatSelector
from lisjong_engine.action_descriptor import DiscardActionDescriptor
from lisjong_engine.observation import ObservationDecisionKind
from lisjong_engine.round_progress import DiscardProgress
from lisjong_engine.seat import Seat

from lisjong_play.gui_model import build_gui_board_view
from lisjong_play.human_selector import HumanActionSelector
from lisjong_play.renderer import UnsupportedDeliveryItemError
from lisjong_play.spectator_source import (
    SPECTATOR_ORIENTATION_SEAT,
    SPEED_CHOICES,
    SpectatorControl,
    SpectatorControlError,
    SpectatorDecisionPresented,
    SpectatorFailed,
    SpectatorFinished,
    SpectatorMatchResult,
    SpectatorProgress,
    SpectatorRoundResult,
    SpectatorSeatSelector,
    SpectatorSessionBridge,
    SpectatorSessionClosed,
    build_spectator_board_view,
    build_spectator_seat_selectors,
    run_spectator_worker,
)
from tests._fixtures import observation, tile
from tests.test_renderer import match_fact, round_fact


def immediate_control() -> SpectatorControl:
    """pacing待ちのないcontrol。pause / step semanticsだけを検証する。"""
    return SpectatorControl(base_interval_ms=0)


def await_boundaries(
    control: SpectatorControl,
    count: int,
    passed: list[int],
    finished: threading.Event,
) -> None:
    """closeまでboundaryを消化するworker stub。closeはtestの正常終了経路。"""
    try:
        for index in range(count):
            control.await_boundary()
            passed.append(index)
    except SpectatorSessionClosed:
        return
    finished.set()


class SpectatorSeatCompositionTest(unittest.TestCase):
    def build(self, policy: str | None = None):
        kwargs = {} if policy is None else {"policy": policy}
        return build_spectator_seat_selectors(lambda seat, obs: None, **kwargs)

    def test_all_four_seats_are_ai_without_a_human_selector(self) -> None:
        selectors = self.build()

        self.assertEqual(set(Seat), set(selectors))
        for seat, selector in selectors.items():
            self.assertIsInstance(selector, SpectatorSeatSelector)
            self.assertEqual(seat, selector.seat)
            self.assertIsInstance(selector.selector, PolicySeatSelector)
            self.assertNotIsInstance(selector.selector, HumanActionSelector)

    def test_every_seat_receives_a_distinct_fresh_policy_instance(self) -> None:
        selectors = self.build()

        policies = [selectors[seat].selector.policy for seat in Seat]
        self.assertEqual(4, len({id(policy) for policy in policies}))
        for policy in policies:
            self.assertIsInstance(policy, MinimalPolicy)

    def test_selected_policy_is_reused_from_human_play_selection_semantics(
        self,
    ) -> None:
        selectors = self.build("combined")

        for seat in Seat:
            self.assertIsInstance(
                selectors[seat].selector.policy,
                GenbutsuDefenseFiniteHorizonValueAwarePolicy,
            )

    def test_unknown_policy_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown opponent"):
            self.build("does-not-exist")


class SpectatorSeatSelectorTest(unittest.TestCase):
    def test_boundary_is_presented_before_the_policy_decides(self) -> None:
        calls: list[str] = []
        first = DiscardActionDescriptor(tile(rank=1), False)
        second = DiscardActionDescriptor(tile(rank=2), False)

        def present(seat: Seat, obs: object) -> None:
            calls.append(f"present:{seat.name}")

        def wrapped(obs: object, options: tuple[object, ...]) -> object:
            calls.append("decide")
            return options[1]

        selector = SpectatorSeatSelector(Seat.SOUTH, wrapped, present)
        chosen = selector(observation(viewer_seat=Seat.SOUTH), (first, second))

        self.assertEqual(["present:SOUTH", "decide"], calls)
        self.assertIs(second, chosen)

    def test_selector_delegates_options_untouched(self) -> None:
        options = (DiscardActionDescriptor(tile(), False),)
        inner = Mock(return_value=options[0])
        view = observation()

        selector = SpectatorSeatSelector(Seat.EAST, inner, lambda seat, obs: None)
        selector(view, options)

        inner.assert_called_once_with(view, options)

    def test_invalid_construction_fails_closed(self) -> None:
        with self.assertRaisesRegex(TypeError, "seat must be"):
            SpectatorSeatSelector("EAST", lambda o, p: None, lambda s, o: None)
        with self.assertRaisesRegex(TypeError, "selector must be callable"):
            SpectatorSeatSelector(Seat.EAST, None, lambda s, o: None)
        with self.assertRaisesRegex(TypeError, "present must be callable"):
            SpectatorSeatSelector(Seat.EAST, lambda o, p: None, None)


class SpectatorBoardViewTest(unittest.TestCase):
    def test_table_orientation_is_fixed_and_does_not_rotate_per_seat(self) -> None:
        east = build_spectator_board_view(observation(viewer_seat=Seat.EAST))
        west = build_spectator_board_view(observation(viewer_seat=Seat.WEST))

        positions = {seat.label: seat.position for seat in east.seats}
        self.assertEqual(positions, {seat.label: seat.position for seat in west.seats})
        self.assertEqual(Seat.EAST, SPECTATOR_ORIENTATION_SEAT)

    def test_decision_label_names_the_deciding_seat(self) -> None:
        board = build_spectator_board_view(
            observation(
                viewer_seat=Seat.SOUTH,
                decision_kind=ObservationDecisionKind.DISCARD_REACTION,
            )
        )

        self.assertEqual("P2 打牌への反応", board.decision_label)

    def test_hand_comes_only_from_the_deciding_seat_observation(self) -> None:
        view = observation(viewer_seat=Seat.WEST, drawn=tile(rank=4))

        board = build_spectator_board_view(view)

        self.assertEqual(
            build_gui_board_view(view).hand_tiles + (board.drawn_tile or "",),
            board.hand_tiles + (board.drawn_tile or "",),
        )

    def test_default_orientation_keeps_human_play_and_replay_unchanged(self) -> None:
        view = observation(viewer_seat=Seat.WEST)

        self.assertEqual(
            build_gui_board_view(view),
            build_gui_board_view(view, orientation_seat=Seat.WEST),
        )

    def test_orientation_seat_must_be_a_seat(self) -> None:
        with self.assertRaisesRegex(TypeError, "orientation_seat"):
            build_gui_board_view(observation(), orientation_seat="EAST")


class SpectatorControlTest(unittest.TestCase):
    def test_unpaused_control_passes_the_boundary(self) -> None:
        control = immediate_control()

        control.await_boundary()

        self.assertFalse(control.paused)

    def test_pause_blocks_the_next_boundary_and_resume_releases_it(self) -> None:
        control = immediate_control()
        control.pause()
        released = threading.Event()

        worker = threading.Thread(
            target=await_boundaries, args=(control, 1, [], released)
        )
        worker.start()
        self.assertFalse(released.wait(timeout=0.2))

        control.resume()
        worker.join(timeout=1)

        self.assertTrue(released.is_set())
        self.assertFalse(control.paused)

    def test_paused_step_allows_exactly_one_boundary(self) -> None:
        control = immediate_control()
        control.pause()
        passed: list[int] = []
        blocked = threading.Event()

        worker = threading.Thread(
            target=await_boundaries,
            args=(control, 3, passed, blocked),
            daemon=True,
        )
        worker.start()
        control.request_step()

        deadline = time.monotonic() + 1
        while len(passed) < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        # 2つ目のboundaryはpauseのまま止まっていることを確認する。
        self.assertFalse(blocked.wait(timeout=0.2))
        self.assertEqual([0], passed)

        control.request_step()
        deadline = time.monotonic() + 1
        while len(passed) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual([0, 1], passed)
        self.assertFalse(blocked.is_set())

        control.close()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())

    def test_step_requires_a_paused_session(self) -> None:
        control = immediate_control()

        with self.assertRaisesRegex(SpectatorControlError, "paused"):
            control.request_step()

    def test_resume_discards_unconsumed_step_credit(self) -> None:
        control = immediate_control()
        control.pause()
        control.request_step()

        control.resume()
        control.pause()
        released = threading.Event()
        worker = threading.Thread(
            target=await_boundaries,
            args=(control, 1, [], released),
            daemon=True,
        )
        worker.start()

        self.assertFalse(released.wait(timeout=0.2))
        control.close()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())

    def test_close_releases_a_paused_worker(self) -> None:
        control = immediate_control()
        control.pause()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                control.await_boundary()
            except SpectatorSessionClosed as error:
                errors.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        control.close()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(errors))
        self.assertTrue(control.closed)

    def test_close_is_idempotent(self) -> None:
        control = immediate_control()
        control.close()
        control.close()

        with self.assertRaises(SpectatorSessionClosed):
            control.await_boundary()

    def test_speed_only_scales_presentation_delay(self) -> None:
        control = SpectatorControl(base_interval_ms=800)

        self.assertEqual(0.8, control.boundary_delay_seconds)
        control.set_speed(4.0)
        self.assertEqual(0.2, control.boundary_delay_seconds)
        control.set_speed(0.5)
        self.assertEqual(1.6, control.boundary_delay_seconds)
        self.assertFalse(control.paused)
        self.assertFalse(control.closed)

    def test_unsupported_speed_fails_closed(self) -> None:
        control = immediate_control()

        for value in (3.0, "fast", None):
            with self.assertRaises(SpectatorControlError):
                control.set_speed(value)
        self.assertEqual(1.0, control.speed)
        self.assertIn(control.speed, SPEED_CHOICES)

    def test_pacing_waits_before_releasing_the_next_boundary(self) -> None:
        control = SpectatorControl(base_interval_ms=120)

        started = time.monotonic()
        control.await_boundary()
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.05)

    def test_close_interrupts_a_pacing_wait(self) -> None:
        control = SpectatorControl(base_interval_ms=60_000)
        errors: list[BaseException] = []

        def run() -> None:
            try:
                control.await_boundary()
            except SpectatorSessionClosed as error:
                errors.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.05)
        control.close()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(errors))


class SpectatorSessionBridgeTest(unittest.TestCase):
    def test_decision_presentation_is_published_per_boundary(self) -> None:
        bridge = SpectatorSessionBridge(immediate_control())

        bridge.present_decision(Seat.SOUTH, observation(viewer_seat=Seat.SOUTH))
        bridge.present_decision(Seat.WEST, observation(viewer_seat=Seat.WEST))
        events = bridge.drain_events()

        self.assertEqual(2, len(events))
        self.assertEqual([1, 2], [event.boundary_ordinal for event in events])
        self.assertEqual(["P2", "P3"], [event.seat_label for event in events])
        for event in events:
            self.assertIsInstance(event, SpectatorDecisionPresented)

    def test_reaction_window_seats_each_get_their_own_boundary(self) -> None:
        """同じengine revisionでも、seatごとのselector requestは別boundary。"""
        bridge = SpectatorSessionBridge(immediate_control())

        for seat in (Seat.SOUTH, Seat.WEST, Seat.NORTH):
            bridge.present_decision(
                seat,
                observation(
                    viewer_seat=seat,
                    decision_kind=ObservationDecisionKind.DISCARD_REACTION,
                ),
            )

        self.assertEqual(
            [1, 2, 3],
            [event.boundary_ordinal for event in bridge.drain_events()],
        )

    def test_delivery_is_projected_to_round_and_match_result_events(self) -> None:
        bridge = SpectatorSessionBridge(immediate_control())

        bridge.deliver(
            (DiscardProgress(seat=Seat.EAST, tile=tile(), is_tsumogiri=False),)
        )
        bridge.deliver((round_fact(has_next_round=False), match_fact()))
        events = bridge.drain_events()

        self.assertIsInstance(events[0], SpectatorProgress)
        self.assertIsInstance(events[1], SpectatorRoundResult)
        self.assertIsInstance(events[2], SpectatorMatchResult)
        self.assertIn("局", events[1].text)
        self.assertIn("半荘終了", events[2].text)

    def test_unknown_delivery_item_fails_closed(self) -> None:
        bridge = SpectatorSessionBridge(immediate_control())

        with self.assertRaises(UnsupportedDeliveryItemError):
            bridge.deliver((object(),))

    def test_close_releases_a_worker_blocked_at_a_boundary(self) -> None:
        bridge = SpectatorSessionBridge(immediate_control())
        bridge.control.pause()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                bridge.present_decision(Seat.EAST, observation())
            except SpectatorSessionClosed as error:
                errors.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        bridge.close()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(errors))

    def test_closed_bridge_publishes_no_finished_or_failure_event(self) -> None:
        bridge = SpectatorSessionBridge(immediate_control())
        bridge.close()

        bridge.publish_finished()
        bridge.publish_failure(RuntimeError("boom"))

        self.assertEqual((), bridge.drain_events())


class SpectatorWorkerTest(unittest.TestCase):
    def test_worker_failure_is_not_reported_as_successful_completion(self) -> None:
        bridge = SpectatorSessionBridge(immediate_control())

        def explode(seat: Seat, view: object) -> None:
            raise RuntimeError("policy exploded")

        bridge.present_decision = explode  # type: ignore[method-assign]
        run_spectator_worker(bridge, seed=0, policy="minimal")
        events = bridge.drain_events()

        self.assertEqual(1, len(events))
        self.assertIsInstance(events[0], SpectatorFailed)
        self.assertIn("policy exploded", events[0].message)
        self.assertFalse(any(isinstance(event, SpectatorFinished) for event in events))

    def test_closed_session_ends_the_worker_without_a_failure_event(self) -> None:
        bridge = SpectatorSessionBridge(immediate_control())
        bridge.control.pause()
        worker = threading.Thread(
            target=run_spectator_worker,
            kwargs={"bridge": bridge, "seed": 0, "policy": "minimal"},
            daemon=True,
        )
        worker.start()
        first = bridge.next_event(timeout=5)
        self.assertIsInstance(first, SpectatorDecisionPresented)

        bridge.close()
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual((), bridge.drain_events())

    def test_bridge_type_is_required(self) -> None:
        with self.assertRaisesRegex(TypeError, "SpectatorSessionBridge"):
            run_spectator_worker(object(), seed=0, policy="minimal")


class SpectatorLiveHanchanTest(unittest.TestCase):
    """AI x4のfixed-seed 1半荘を、pacingの有無で2回だけ実行する重いtest。"""

    @staticmethod
    def collect(control: SpectatorControl) -> dict[str, object]:
        bridge = SpectatorSessionBridge(control)
        run_spectator_worker(bridge, seed=0, policy="minimal")
        events = bridge.drain_events()
        return {
            "boundaries": [
                (event.seat_label, event.board.round_label, event.board.decision_label)
                for event in events
                if isinstance(event, SpectatorDecisionPresented)
            ],
            "rounds": [
                event.text
                for event in events
                if isinstance(event, SpectatorRoundResult)
            ],
            "match": [
                event.text
                for event in events
                if isinstance(event, SpectatorMatchResult)
            ],
            "finished": [
                event for event in events if isinstance(event, SpectatorFinished)
            ],
        }

    def test_ai_only_hanchan_completes_and_pacing_keeps_the_same_result(self) -> None:
        """同じseed / 同じPolicy compositionなら、pacingの有無で結果が変わらない。

        real hanchanは重いため、この確認だけで完走・局結果・半荘結果・
        pacing不変性をまとめて固定する。
        """
        without_pacing = self.collect(SpectatorControl(base_interval_ms=0))
        with_pacing = self.collect(SpectatorControl(base_interval_ms=1, speed=4.0))

        self.assertEqual(without_pacing["boundaries"], with_pacing["boundaries"])
        self.assertEqual(without_pacing["rounds"], with_pacing["rounds"])
        self.assertEqual(without_pacing["match"], with_pacing["match"])

        self.assertGreater(len(without_pacing["boundaries"]), 0)
        self.assertGreater(len(without_pacing["rounds"]), 0)
        self.assertEqual(1, len(without_pacing["match"]))
        self.assertEqual(1, len(without_pacing["finished"]))
        self.assertEqual(1, len(with_pacing["finished"]))
        self.assertIn("半荘終了", without_pacing["match"][0])
        self.assertIn("1位", without_pacing["match"][0])
        self.assertEqual(
            {"P1", "P2", "P3", "P4"},
            {seat for seat, _round, _label in without_pacing["boundaries"]},
        )


if __name__ == "__main__":
    unittest.main()
