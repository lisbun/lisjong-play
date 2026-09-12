"""AI x4のlive対局を既存GUI presentationへ渡す、Tk非依存のspectator source。

Human Play / Replayに続く3つ目のpresentation sourceであり、盤面描画は
`GuiBoardRenderer`をそのまま再利用する。ここではrule / legality / scoring /
progressionを一切再実装せず、engineのplayer-safe公開contractだけを読む。

presentation boundary
---------------------
1 spectator presentation boundary = **one selector decision presentation
boundary**、すなわちengine driverが1席のselectorへ渡す1回のdecision request
である。reaction windowでは同じengine revisionに対して3席分のselector request
が発生するため、`1 engine revision = 1 boundary`ではない。Pause / Stepはこの
boundary単位でだけ進行を止める。

information boundary
--------------------
各boundaryで表示するconcealed handは、そのdecision seat自身の`SeatObservation`
が持つplayer-safeな手牌だけである。複数seatのobservationをmergeして4席分の
omniscient stateを作らず、`MatchState` / `RoundState` / wall等のprivate stateも
読まない。spectator側の表示はPolicy inputへ戻らない。
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from queue import Empty, Queue
from threading import Condition, Lock
from typing import TypeAlias

from lisjong_arena.lisjong_engine.policy_selector import PolicySeatSelector
from lisjong_engine.action_descriptor import ActionDescriptor
from lisjong_engine.driver import ActionSelector
from lisjong_engine.observation import SeatObservation
from lisjong_engine.round_completion import MatchCompletionFact, RoundCompletionFact
from lisjong_engine.round_progress import RoundProgressFact
from lisjong_engine.seat import Seat

from lisjong_play.formatting import format_seat
from lisjong_play.gui_model import GuiBoardView, build_gui_board_view
from lisjong_play.renderer import (
    UnsupportedDeliveryItemError,
    render_match_completion,
    render_progress_fact,
    render_round_completion,
)
from lisjong_play.session import (
    DEFAULT_OPPONENT,
    OpponentName,
    _create_opponent_policy,
    _run_ai_only_session,
)

# spectatorはHuman seatを持たないため、盤面の向きは決定席ごとに回さず、
# fixed EAST seatを手前に固定する。どの席の判断かは`decision_label`で示す。
SPECTATOR_ORIENTATION_SEAT = Seat.EAST

# 1 boundaryあたりの基準待ち時間。speedはこの値に対する倍率として適用する。
# recorded replayのcursor pacingとは別のlive gateなので、`ReplayController`へ
# 無理に統合せず、live側の値としてここで定義する。
BASE_BOUNDARY_INTERVAL_MS = 450
SPEED_CHOICES: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)
DEFAULT_SPEED = 1.0


class SpectatorControlError(ValueError):
    """controllerがsupportしないspeedやstep要求を受け取った場合。"""


class SpectatorSessionClosed(RuntimeError):
    """window closeによりblocked workerを終了させる内部signal。"""


@dataclass(frozen=True)
class SpectatorDecisionPresented:
    """1 selector decision presentation boundary分の盤面。"""

    boundary_ordinal: int
    seat_label: str
    board: GuiBoardView


@dataclass(frozen=True)
class SpectatorProgress:
    text: str


@dataclass(frozen=True)
class SpectatorRoundResult:
    text: str


@dataclass(frozen=True)
class SpectatorMatchResult:
    text: str


@dataclass(frozen=True)
class SpectatorFinished:
    pass


@dataclass(frozen=True)
class SpectatorFailed:
    message: str


SpectatorEvent: TypeAlias = (
    SpectatorDecisionPresented
    | SpectatorProgress
    | SpectatorRoundResult
    | SpectatorMatchResult
    | SpectatorFinished
    | SpectatorFailed
)


def build_spectator_board_view(observation: SeatObservation) -> GuiBoardView:
    """decision seatのplayer-safe observationだけからspectator盤面を構築する。

    向きだけをfixed EAST seatへ固定し、それ以外は Human Play / Replayと同じ
    `GuiBoardView`射影を再利用する。
    """
    board = build_gui_board_view(
        observation, orientation_seat=SPECTATOR_ORIENTATION_SEAT
    )
    seat_label = format_seat(observation.viewer_seat)
    return replace(board, decision_label=f"{seat_label} {board.decision_label}")


class SpectatorControl:
    """live実行のpacing gate。presentation進行だけを止め、engineを操作しない。

    workerだけが`await_boundary()`を呼び、Tk main threadは pause / resume /
    step / speed を呼ぶ。どちらも同じ`Condition`しか掴まないため、lock順序に
    よるdeadlockが生じない。`close()`は待機中のworkerを必ず解放する。
    """

    def __init__(
        self,
        *,
        base_interval_ms: int = BASE_BOUNDARY_INTERVAL_MS,
        speed: float = DEFAULT_SPEED,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(base_interval_ms) is not int or base_interval_ms < 0:
            raise SpectatorControlError("base_interval_ms must be a non-negative int")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._condition = Condition()
        self._base_interval_ms = base_interval_ms
        self._clock = clock
        self._paused = False
        self._closed = False
        self._step_credits = 0
        self._speed = DEFAULT_SPEED
        self.set_speed(speed)

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def speed(self) -> float:
        with self._condition:
            return self._speed

    @property
    def boundary_delay_seconds(self) -> float:
        """1 boundaryあたりのpresentation待ち時間。game semanticsを変えない。"""
        with self._condition:
            return self._base_interval_ms / 1000.0 / self._speed

    def set_speed(self, speed: float) -> None:
        """speed変更はboundary列を変えず、boundary間の待ち時間だけを変える。"""
        try:
            value = float(speed)
        except TypeError, ValueError:
            raise SpectatorControlError(
                f"unsupported spectator speed: {speed!r}"
            ) from None
        if value not in SPEED_CHOICES:
            raise SpectatorControlError(f"unsupported spectator speed: {speed!r}")
        with self._condition:
            self._speed = value
            self._condition.notify_all()

    def pause(self) -> None:
        with self._condition:
            self._paused = True
            self._condition.notify_all()

    def resume(self) -> None:
        """pauseを解除する。未消化のstep creditは残さない。"""
        with self._condition:
            self._paused = False
            self._step_credits = 0
            self._condition.notify_all()

    def request_step(self) -> None:
        """pause中に、次の1 selector decision boundaryだけを許可する。"""
        with self._condition:
            if self._closed:
                raise SpectatorControlError("spectator session is closed")
            if not self._paused:
                raise SpectatorControlError("step requires a paused spectator session")
            self._step_credits += 1
            self._condition.notify_all()

    def close(self) -> None:
        """idempotentに閉じ、boundaryで待機中のworkerを解放する。"""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()

    def await_boundary(self) -> None:
        """worker上で、次のboundaryへ進んでよくなるまで待つ。

        pause中はstepが1件与えられるまで進まない。pauseしていない場合は
        speedに応じたpacingだけ待つ。closeは待機を必ず解除する。
        """
        with self._condition:
            while True:
                if self._closed:
                    raise SpectatorSessionClosed("spectator session is closed")
                if self._step_credits > 0:
                    self._step_credits -= 1
                    return
                if self._paused:
                    self._condition.wait()
                    continue
                delay = self._base_interval_ms / 1000.0 / self._speed
                if delay <= 0:
                    return
                deadline = self._clock() + delay
                while not self._closed and not self._paused and self._step_credits == 0:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return
                    self._condition.wait(remaining)


class SpectatorSeatSelector:
    """1席分のPolicy selectorの前へ、presentation boundaryを1つ挟むwrapper。

    Policy conversion / legality / action mappingには一切触れず、engineが
    渡したoptionsをそのままwrapped selectorへ委譲する。
    """

    __slots__ = ("_seat", "_selector", "_present")

    def __init__(
        self,
        seat: Seat,
        selector: ActionSelector,
        present: Callable[[Seat, SeatObservation], None],
    ) -> None:
        if not isinstance(seat, Seat):
            raise TypeError("seat must be a lisjong-engine Seat")
        if not callable(selector):
            raise TypeError("selector must be callable")
        if not callable(present):
            raise TypeError("present must be callable")
        self._seat = seat
        self._selector = selector
        self._present = present

    @property
    def seat(self) -> Seat:
        return self._seat

    @property
    def selector(self) -> ActionSelector:
        return self._selector

    def __call__(
        self,
        observation: SeatObservation,
        options: tuple[ActionDescriptor, ...],
    ) -> ActionDescriptor:
        self._present(self._seat, observation)
        return self._selector(observation, options)


class SpectatorSessionBridge:
    """worker上のlive実行とTk main threadを接続するspectator bridge。

    Tk APIを呼ばず、event queueと`SpectatorControl`だけを共有する。
    """

    def __init__(self, control: SpectatorControl | None = None) -> None:
        if control is not None and not isinstance(control, SpectatorControl):
            raise TypeError("control must be a SpectatorControl")
        self._events: Queue[SpectatorEvent] = Queue()
        self._control = control if control is not None else SpectatorControl()
        self._lock = Lock()
        self._closed = False
        self._boundary_ordinal = 0

    @property
    def control(self) -> SpectatorControl:
        return self._control

    def _require_open(self) -> None:
        if self._closed:
            raise SpectatorSessionClosed("spectator session is closed")

    def present_decision(self, seat: Seat, observation: SeatObservation) -> None:
        """1 selector decision presentation boundaryを公開し、gateで待つ。"""
        board = build_spectator_board_view(observation)
        with self._lock:
            self._require_open()
            self._boundary_ordinal += 1
            ordinal = self._boundary_ordinal
            self._events.put(
                SpectatorDecisionPresented(ordinal, format_seat(seat), board)
            )
        self._control.await_boundary()

    def deliver(self, batch: tuple[object, ...]) -> None:
        """engine deliveryを順序どおりspectator eventへ変換する。"""
        try:
            items = tuple(batch)
        except TypeError:
            raise TypeError("delivery batch must be iterable") from None
        for item in items:
            if isinstance(item, RoundProgressFact):
                self._publish(SpectatorProgress(render_progress_fact(item)))
                continue
            if isinstance(item, RoundCompletionFact):
                self._publish(SpectatorRoundResult(render_round_completion(item)))
                continue
            if isinstance(item, MatchCompletionFact):
                self._publish(SpectatorMatchResult(render_match_completion(item)))
                continue
            raise UnsupportedDeliveryItemError(
                f"unsupported delivery item: {type(item).__name__}"
            )

    def _publish(self, event: SpectatorEvent) -> None:
        with self._lock:
            self._require_open()
            self._events.put(event)

    def drain_events(self) -> tuple[SpectatorEvent, ...]:
        """Tk main thread向けnon-blocking event drain。"""
        events: list[SpectatorEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                return tuple(events)

    def next_event(self, *, timeout: float) -> SpectatorEvent:
        """display不要のthreading test向けblocking event read。"""
        return self._events.get(timeout=timeout)

    def publish_finished(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._events.put(SpectatorFinished())

    def publish_failure(self, error: Exception) -> None:
        if not isinstance(error, Exception):
            raise TypeError("error must be an Exception")
        with self._lock:
            if self._closed:
                return
            self._events.put(SpectatorFailed(f"{type(error).__name__}: {error}"))

    def close(self) -> None:
        """idempotentにbridgeを閉じ、gateで待機中のworkerを解放する。"""
        with self._lock:
            self._closed = True
        self._control.close()


def build_spectator_seat_selectors(
    present: Callable[[Seat, SeatObservation], None],
    *,
    policy: OpponentName = DEFAULT_OPPONENT,
) -> dict[Seat, ActionSelector]:
    """4席すべてを、独立Policy instanceのAI seatとしてcompositionする。

    Human selectorは使わない。Policy instanceはseatごとに新規生成する。
    """
    selectors: dict[Seat, ActionSelector] = {}
    for seat in Seat:
        policy_selector = PolicySeatSelector(seat, _create_opponent_policy(policy))
        selectors[seat] = SpectatorSeatSelector(seat, policy_selector, present)
    return selectors


def run_spectator_session(
    *,
    seed: int,
    policy: OpponentName,
    present: Callable[[Seat, SeatObservation], None],
    on_delivery: Callable[[tuple[object, ...]], None],
) -> None:
    """AI x4の1半荘を、boundaryごとにpresentしながら完走させる。"""
    selectors = build_spectator_seat_selectors(present, policy=policy)
    _run_ai_only_session(seed=seed, selectors=selectors, on_delivery=on_delivery)


def run_spectator_worker(
    bridge: SpectatorSessionBridge,
    *,
    seed: int,
    policy: OpponentName,
) -> None:
    """spectator worker entry point。exceptionを成功終了として扱わない。"""
    if not isinstance(bridge, SpectatorSessionBridge):
        raise TypeError("bridge must be a SpectatorSessionBridge")
    try:
        run_spectator_session(
            seed=seed,
            policy=policy,
            present=bridge.present_decision,
            on_delivery=bridge.deliver,
        )
    except SpectatorSessionClosed:
        return
    except Exception as error:  # GUI must surface worker failures to the user.
        bridge.publish_failure(error)
    else:
        bridge.publish_finished()


def spectator_speed_labels() -> Sequence[str]:
    """GUI combobox向けのspeed表示値。"""
    return tuple(f"{speed}" for speed in SPEED_CHOICES)
