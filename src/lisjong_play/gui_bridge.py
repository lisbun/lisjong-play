"""同期engine workerとGUI main threadを接続するminimum bridge。"""

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Lock
from typing import TypeAlias, cast

from lisjong_engine.action_descriptor import (
    ActionDescriptor,
    DiscardActionDescriptor,
    PassActionDescriptor,
)
from lisjong_engine.observation import SeatObservation
from lisjong_engine.round_completion import (
    MatchCompletionFact,
    RoundCompletionFact,
)
from lisjong_engine.round_progress import RoundProgressFact

from lisjong_play.gui_model import (
    GuiActionView,
    GuiBoardView,
    build_gui_action_views,
    build_gui_board_view,
)
from lisjong_play.renderer import (
    UnsupportedDeliveryItemError,
    render_match_completion,
    render_progress_fact,
    render_round_completion,
)
from lisjong_play.session import OpponentName, _run_session


class GuiBridgeError(RuntimeError):
    """stale selection等のGUI / worker bridge protocol違反。"""


class GuiSessionClosed(RuntimeError):
    """window closeによりblocked workerを終了させる内部signal。"""


@dataclass(frozen=True)
class DecisionRequested:
    request_id: int
    board: GuiBoardView
    actions: tuple[GuiActionView, ...]


@dataclass(frozen=True)
class ProgressDelivered:
    text: str


@dataclass(frozen=True)
class RoundCompleted:
    text: str
    confirmation_id: int | None


@dataclass(frozen=True)
class MatchCompleted:
    text: str


@dataclass(frozen=True)
class SessionFinished:
    pass


@dataclass(frozen=True)
class SessionFailed:
    message: str


GuiEvent: TypeAlias = (
    DecisionRequested
    | ProgressDelivered
    | RoundCompleted
    | MatchCompleted
    | SessionFinished
    | SessionFailed
)

_CLOSED = object()


@dataclass
class _PendingDecision:
    request_id: int
    options: tuple[ActionDescriptor, ...]
    reply: Queue[object]


@dataclass
class _PendingConfirmation:
    request_id: int
    reply: Queue[object]


class GuiSessionBridge:
    """Tk APIを呼ばずにworker requestとGUI responseを同期する。"""

    def __init__(self) -> None:
        self._events: Queue[GuiEvent] = Queue()
        self._lock = Lock()
        self._closed = False
        self._next_request_id = 1
        self._decision: _PendingDecision | None = None
        self._confirmation: _PendingConfirmation | None = None

    def _reserve_request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _require_open(self) -> None:
        if self._closed:
            raise GuiSessionClosed("GUI session is closed")

    def select_action(
        self,
        observation: SeatObservation,
        options: tuple[ActionDescriptor, ...],
    ) -> ActionDescriptor:
        """worker上でHuman selectionを要求し、GUI responseを待つ。"""
        board = build_gui_board_view(observation)
        values = tuple(options)
        action_views = build_gui_action_views(values)
        self._validate_decision(observation, values)
        reply: Queue[object] = Queue(maxsize=1)
        with self._lock:
            self._require_open()
            if self._decision is not None or self._confirmation is not None:
                raise GuiBridgeError("another GUI interaction is already active")
            request_id = self._reserve_request_id()
            self._decision = _PendingDecision(request_id, values, reply)
            self._events.put(DecisionRequested(request_id, board, action_views))

        result = reply.get()
        if result is _CLOSED:
            raise GuiSessionClosed("GUI closed while waiting for a Human action")
        if not any(result is value for value in values):  # pragma: no cover - internal
            raise AssertionError("GUI reply must be an original ActionDescriptor")
        return cast(ActionDescriptor, result)

    @staticmethod
    def _validate_decision(
        observation: SeatObservation,
        options: tuple[ActionDescriptor, ...],
    ) -> None:
        passes = tuple(
            option for option in options if isinstance(option, PassActionDescriptor)
        )
        if len(passes) > 1:
            raise GuiBridgeError("decision must not expose multiple pass options")
        tsumogiri = tuple(
            option
            for option in options
            if isinstance(option, DiscardActionDescriptor) and option.is_tsumogiri
        )
        if len(tsumogiri) > 1:
            raise GuiBridgeError("decision must not expose multiple tsumogiri options")
        if tsumogiri and tsumogiri[0].tile != observation.drawn_tile:
            raise GuiBridgeError(
                "tsumogiri descriptor must match SeatObservation.drawn_tile"
            )

    def choose_action(self, request_id: int, option_index: int) -> None:
        """GUI main threadからactive requestのoriginal optionを選ぶ。"""
        if type(request_id) is not int:
            raise TypeError("request_id must be an int")
        if type(option_index) is not int:
            raise TypeError("option_index must be an int")
        with self._lock:
            self._require_open()
            pending = self._decision
            if pending is None or pending.request_id != request_id:
                raise GuiBridgeError("decision request is no longer active")
            if not 0 <= option_index < len(pending.options):
                raise GuiBridgeError("option_index is outside the active decision")
            selected = pending.options[option_index]
            self._decision = None
            pending.reply.put(selected)

    def deliver(self, batch: tuple[object, ...]) -> None:
        """worker上のengine deliveryを順番どおりGUI eventへ変換する。"""
        try:
            items = tuple(batch)
        except TypeError:
            raise TypeError("delivery batch must be iterable") from None
        for item in items:
            with self._lock:
                self._require_open()
            if isinstance(item, RoundProgressFact):
                self._events.put(ProgressDelivered(render_progress_fact(item)))
                continue
            if isinstance(item, RoundCompletionFact):
                self._deliver_round_completion(item)
                continue
            if isinstance(item, MatchCompletionFact):
                self._events.put(MatchCompleted(render_match_completion(item)))
                continue
            raise UnsupportedDeliveryItemError(
                f"unsupported delivery item: {type(item).__name__}"
            )

    def _deliver_round_completion(self, fact: RoundCompletionFact) -> None:
        text = render_round_completion(fact)
        if not fact.has_next_round:
            self._events.put(RoundCompleted(text, None))
            return

        reply: Queue[object] = Queue(maxsize=1)
        with self._lock:
            self._require_open()
            if self._decision is not None or self._confirmation is not None:
                raise GuiBridgeError("another GUI interaction is already active")
            request_id = self._reserve_request_id()
            self._confirmation = _PendingConfirmation(request_id, reply)
            self._events.put(RoundCompleted(text, request_id))

        result = reply.get()
        if result is _CLOSED:
            raise GuiSessionClosed("GUI closed while waiting for round confirmation")

    def confirm_round(self, request_id: int) -> None:
        """GUI main threadからactiveな次局確認を完了する。"""
        if type(request_id) is not int:
            raise TypeError("request_id must be an int")
        with self._lock:
            self._require_open()
            pending = self._confirmation
            if pending is None or pending.request_id != request_id:
                raise GuiBridgeError("round confirmation is no longer active")
            self._confirmation = None
            pending.reply.put(None)

    def drain_events(self) -> tuple[GuiEvent, ...]:
        """GUI main thread向けnon-blocking event drain。"""
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                return tuple(events)

    def next_event(self, *, timeout: float) -> GuiEvent:
        """display不要のthreading test向けblocking event read。"""
        return self._events.get(timeout=timeout)

    def publish_finished(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._events.put(SessionFinished())

    def publish_failure(self, error: Exception) -> None:
        if not isinstance(error, Exception):
            raise TypeError("error must be an Exception")
        with self._lock:
            if self._closed:
                return
            self._events.put(SessionFailed(f"{type(error).__name__}: {error}"))

    def close(self) -> None:
        """idempotentにbridgeを閉じ、blocked workerを解除する。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._decision is not None:
                self._decision.reply.put(_CLOSED)
                self._decision = None
            if self._confirmation is not None:
                self._confirmation.reply.put(_CLOSED)
                self._confirmation = None


def run_gui_worker(
    bridge: GuiSessionBridge,
    *,
    seed: int,
    opponent: OpponentName,
) -> None:
    """GUI用worker entry point。exceptionはmain thread向けeventへ変換する。"""
    if not isinstance(bridge, GuiSessionBridge):
        raise TypeError("bridge must be a GuiSessionBridge")
    try:
        _run_session(
            seed=seed,
            opponent=opponent,
            human_selector=bridge.select_action,
            on_delivery=bridge.deliver,
            on_round_evidence_complete=None,
        )
    except GuiSessionClosed:
        return
    except Exception as error:  # GUI must surface worker failures to the user.
        bridge.publish_failure(error)
    else:
        bridge.publish_finished()
