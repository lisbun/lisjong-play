"""RiichiLab ranked liveを既存GUI presentationへ渡す、Tk非依存のlive source。

Human Play / Replay / Local Spectatorに続く4つ目のpresentation sourceであり、
盤面描画は`GuiBoardRenderer`をそのまま再利用する。

producer / consumer boundary
----------------------------
producerはArenaのsupported live presentation seam
(`lisjong-arena` PR #274、`BoundedRankedPresentationBuffer`)であり、
lisjong-playはraw RiichiLab `request_action` JSONやbase64 Observationを
独自parseしない。WebSocket transport、ranked protocol、profile / credential
解決、durable record writerもArenaのcontractをそのまま使い、ここで複製しない。

```text
Arena ranked worker (別thread)
    -> BoundedRankedPresentationBuffer          (bounded / non-blocking)
    -> RiichiLabLiveController.ingest()         (Tk main threadがdrain)
    -> RiichiLabFrame -> GuiBoardRenderer
```

continuous mode(`lisbun/lisjong-play#48`)ではArenaの
`ContinuousRankedPresentationFeed`がgame attemptごとに新しいbufferを開き、
`RiichiLabContinuousController`が最新gameのbufferへ表示を切り替える。
ranked実行・profile / credential解決・summary出力はArenaの
`run_continuous_ranked_cli()`へ委譲し、ここで複製しない。

timing boundary
---------------
このsourceのPause / Step / Follow Liveは**presentation cursorだけ**を操作する。
Local Spectatorの`SpectatorControl`はselector boundaryでengine workerを止める
gateであり、semanticsが異なるためreuseしない。RiichiLab rankedにはresponse
time budgetがあるので、GUI側の操作・描画遅延をranked executionへ逆流させない。
Pause中もArena bufferは毎回drainし、terminal factとArena側coalesce countの
受信が遅れないようにする。

information boundary
--------------------
表示するのはArenaが渡したbound bot seat自身のplayer-visible
`PolicyInput`だけである。他家concealed handを推測せず、複数`PolicyInput`を
mergeせず、wall / dead wallを再構成せず、legality / scoring / progression /
shanten / ukeire / 役 / 翻 / 符 / 順位をここで計算・推測しない。token /
Authorization / credential値はこのmoduleのpresentation valueへ一切載せない。
"""

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from lisjong_arena.riichilab.cli import resolve_ranked_record_path
from lisjong_arena.riichilab.live_presentation import (
    BoundedRankedPresentationBuffer,
    ContinuousRankedPresentationFeed,
    RankedCompletionPresentation,
    RankedDecisionPresentation,
    RankedFailurePresentation,
    RankedPresentationBatch,
)
from lisjong_arena.riichilab.profile import (
    PROFILE_NAMES,
    ProfileError,
    build_runtime_summary,
    format_runtime_summary,
    resolve_credential,
    resolve_profile,
)

from lisjong_play.gui_model import GuiBoardView
from lisjong_play.policy_input_board import (
    action_label,
    build_policy_input_board_view,
    seat_name,
)

__all__ = [
    "DEFAULT_PENDING_FRAME_CAPACITY",
    "PROFILE_NAMES",
    "RiichiLabCompletion",
    "RiichiLabContinuousController",
    "RiichiLabContinuousStatus",
    "RiichiLabContinuousViewState",
    "RiichiLabGameResult",
    "RiichiLabFailure",
    "RiichiLabFrame",
    "RiichiLabLiveController",
    "RiichiLabRunSummary",
    "RiichiLabViewState",
    "RiichiLabWorkerSnapshot",
    "RiichiLabWorkerStatus",
    "build_decision_frame",
    "resolve_record_path",
    "run_riichilab_continuous_worker",
    "run_riichilab_worker",
]

#: Pause中に保持する未表示decision frameの明示的な有限capacity。
#: 盤面はcumulative snapshotなので、超過分は古い方からcoalesce(drop)する。
#: 「すべてのdecisionを永久保存する」ことはこのviewerのrequirementではない。
DEFAULT_PENDING_FRAME_CAPACITY = 128

#: Arena ranked runのfailure factは例外type名しか持たない。raw transport
#: exception messageを独自取得してpresentationへ混ぜない。
_RANKED_FAILURE_NOTE = (
    "ranked runは完了しませんでした（Arena presentation seamはfailure typeだけを"
    "公開します）。"
)


@dataclass(frozen=True)
class RiichiLabFrame:
    """1 ranked decisionへ対応する、表示cursorの最小presentation単位。"""

    ordinal: int
    request_id: int
    seat_label: str
    action_label: str
    board: GuiBoardView


@dataclass(frozen=True)
class RiichiLabCompletion:
    """完走した`end_game`のterminal presentation fact。

    `scores`はArenaが受理したfinal scoresそのものである。順位はArena
    presentation seamが提供しないため、scoresからtie-break等で推測しない。
    """

    seat_label: str
    scores: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class RiichiLabFailure:
    """ranked runが完走しなかったことを示すterminal presentation fact。"""

    failure_type: str


@dataclass(frozen=True)
class RiichiLabViewState:
    """GUIが描画する現在のpresentation状態のimmutable snapshot。"""

    frame: RiichiLabFrame | None
    following: bool
    received_frames: int
    pending_frames: int
    skipped_frames: int
    completion: RiichiLabCompletion | None
    failure: RiichiLabFailure | None


def build_decision_frame(
    decision: RankedDecisionPresentation, *, ordinal: int
) -> RiichiLabFrame:
    """Arenaのdecision factを既存`GuiBoardView`へ投影する。

    盤面はReplayと共有する`policy_input_board`の投影をそのまま使う。bound bot
    seatは`PolicyInput.self_seat`であり、共通投影がそのseatを常に`bottom`へ
    置くため、live sourceで向きを回さない。
    """
    if not isinstance(decision, RankedDecisionPresentation):
        raise TypeError("decision must be a RankedDecisionPresentation")
    seat_label = seat_name(decision.self_seat)
    selected = action_label(decision.selected_action)
    board = build_policy_input_board_view(
        decision.policy_input,
        decision_label=f"{seat_label} {selected}",
    )
    return RiichiLabFrame(
        ordinal=ordinal,
        request_id=decision.request_id,
        seat_label=seat_label,
        action_label=selected,
        board=board,
    )


class RiichiLabLiveController:
    """Arena presentation bufferの上に載る、presentation-onlyな表示cursor。

    このcontrollerはranked workerへsignalを送らない。Pauseは表示cursorを
    freezeするだけで、`ingest()`はpause中も毎回Arena bufferをdrainする。
    """

    __slots__ = (
        "_buffer",
        "_capacity",
        "_pending",
        "_current",
        "_following",
        "_received",
        "_skipped",
        "_completion",
        "_failure",
    )

    def __init__(
        self,
        buffer: BoundedRankedPresentationBuffer,
        *,
        capacity: int = DEFAULT_PENDING_FRAME_CAPACITY,
    ) -> None:
        if not isinstance(buffer, BoundedRankedPresentationBuffer):
            raise TypeError("buffer must be a BoundedRankedPresentationBuffer")
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an int")
        if capacity < 1:
            raise ValueError("capacity must be a positive int")
        self._buffer = buffer
        self._capacity = capacity
        self._pending: deque[RiichiLabFrame] = deque(maxlen=capacity)
        self._current: RiichiLabFrame | None = None
        self._following = True
        self._received = 0
        self._skipped = 0
        self._completion: RiichiLabCompletion | None = None
        self._failure: RiichiLabFailure | None = None

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def following(self) -> bool:
        return self._following

    @property
    def paused(self) -> bool:
        return not self._following

    def ingest(self) -> bool:
        """Arena bufferを1回drainし、表示stateへ取り込む。

        pause中でも必ずdrainする。drainしないとterminal factの受信と
        Arena buffer側のoverflow把握が遅れるためである。戻り値は表示state
        が変化したかどうか。
        """
        batch = self._buffer.drain()
        return self._apply(batch)

    def _apply(self, batch: RankedPresentationBatch) -> bool:
        if not isinstance(batch, RankedPresentationBatch):
            raise TypeError("batch must be a RankedPresentationBatch")
        changed = False
        # Arena側でcoalesceされた古いdecision snapshotも、そのままskipped
        # countへ加算して可視化する。
        if batch.coalesced_decisions:
            self._skipped += int(batch.coalesced_decisions)
            changed = True

        # skipped countは「受信したが一度も表示されなかったframe」だけを数える。
        # 直前のdrainで表示済みのframeは、次のframeへ進んでもskippedにしない。
        replaced_before_display = False
        for decision in batch.decisions:
            self._received += 1
            frame = build_decision_frame(decision, ordinal=self._received)
            if self._following:
                # follow中は盤面がcumulative snapshotなので、最新だけを保持する。
                # 同じdrain内で置き換わったframeは表示されていない。
                if replaced_before_display:
                    self._skipped += 1
                self._current = frame
                replaced_before_display = True
            else:
                if len(self._pending) == self._capacity:
                    self._skipped += 1
                self._pending.append(frame)
            changed = True

        if batch.completion is not None and self._completion is None:
            self._completion = _completion_fact(batch.completion)
            changed = True
        if batch.failure is not None and self._failure is None:
            self._failure = _failure_fact(batch.failure)
            changed = True
        return changed

    def pause(self) -> None:
        """表示cursorだけをfreezeする。ranked workerへは何も送らない。"""
        self._following = False

    def follow_live(self) -> bool:
        """保持済みの最新frameへジャンプし、follow状態へ戻す。"""
        moved = False
        if self._pending:
            # 飛ばしたretained frameだけをskippedへ数える。表示済みの現frameは
            # skippedではない。
            while len(self._pending) > 1:
                self._pending.popleft()
                self._skipped += 1
            self._current = self._pending.popleft()
            moved = True
        self._following = True
        return moved

    def step(self) -> bool:
        """pause中に、保持済みframeをちょうど1件だけ進める。"""
        if self._following or not self._pending:
            return False
        self._current = self._pending.popleft()
        return True

    def detach(self) -> None:
        """presentation consumerの離脱をArena bufferへ伝える。

        ranked sessionはArenaのauthoritative lifecycleに従って継続する。
        """
        self._buffer.detach()

    def state(self) -> RiichiLabViewState:
        return RiichiLabViewState(
            frame=self._current,
            following=self._following,
            received_frames=self._received,
            pending_frames=len(self._pending),
            skipped_frames=self._skipped,
            completion=self._completion,
            failure=self._failure,
        )


def _completion_fact(completion: RankedCompletionPresentation) -> RiichiLabCompletion:
    return RiichiLabCompletion(
        seat_label=seat_name(completion.self_seat), scores=completion.scores
    )


def _failure_fact(failure: RankedFailurePresentation) -> RiichiLabFailure:
    return RiichiLabFailure(failure_type=failure.failure_type)


@dataclass(frozen=True)
class RiichiLabRunSummary:
    """完了したranked runのsecret-safeなsummary。

    token / Authorization / credential値もrecord bytesも含まない。
    """

    profile: str
    seat_label: str
    requests: int
    responses: int
    scores: tuple[int, int, int, int] | None
    record_identity: str | None


@dataclass(frozen=True)
class RiichiLabWorkerSnapshot:
    """worker lifecycleのsecret-safeなsnapshot。"""

    running: bool
    started: bool
    runtime_summary: str | None
    summary: RiichiLabRunSummary | None
    error_text: str | None


class RiichiLabWorkerStatus:
    """worker threadとTk main threadが共有する、最小限のstatus holder。

    presentation factはArena bufferが運ぶ。このholderはArena bufferの外で
    起きるlifecycle(起動、profile / credential解決失敗、完了summary)だけを
    扱う。tokenを保持しない。
    """

    __slots__ = (
        "_lock",
        "_running",
        "_started",
        "_runtime_summary",
        "_summary",
        "_error",
    )

    def __init__(self) -> None:
        self._lock = Lock()
        self._running = False
        self._started = False
        self._runtime_summary: str | None = None
        self._summary: RiichiLabRunSummary | None = None
        self._error: str | None = None

    def mark_running(self, runtime_summary: str) -> None:
        if not isinstance(runtime_summary, str):
            raise TypeError("runtime_summary must be a str")
        with self._lock:
            self._running = True
            self._started = True
            self._runtime_summary = runtime_summary

    def mark_finished(self, summary: RiichiLabRunSummary) -> None:
        if not isinstance(summary, RiichiLabRunSummary):
            raise TypeError("summary must be a RiichiLabRunSummary")
        with self._lock:
            self._running = False
            self._summary = summary

    def mark_failed(self, error_text: str) -> None:
        if not isinstance(error_text, str) or not error_text:
            raise TypeError("error_text must be a non-empty str")
        with self._lock:
            self._running = False
            self._error = error_text

    def snapshot(self) -> RiichiLabWorkerSnapshot:
        with self._lock:
            return RiichiLabWorkerSnapshot(
                running=self._running,
                started=self._started,
                runtime_summary=self._runtime_summary,
                summary=self._summary,
                error_text=self._error,
            )


def resolve_record_path(record_dir: str | None) -> Path | None:
    """Arenaのrecord path解決をそのまま使う。"""
    return resolve_ranked_record_path(record_dir)


def _default_run_game(
    policy: Any, token: str, *, presentation: BoundedRankedPresentationBuffer
) -> Any:
    from lisjong_arena.riichilab.ranked import run_ranked_game

    return asyncio.run(run_ranked_game(policy, token, presentation=presentation))


def _default_acquire_record(
    policy: Any,
    token: str,
    *,
    destination: Path,
    profile_identity: str,
    policy_identity: str,
    presentation: BoundedRankedPresentationBuffer,
) -> Any:
    from lisjong_arena.riichilab.durable_ranked_game_record import (
        acquire_ranked_game_record,
    )

    return asyncio.run(
        acquire_ranked_game_record(
            policy,
            token,
            destination=destination,
            profile_identity=profile_identity,
            policy_identity=policy_identity,
            presentation=presentation,
        )
    )


def run_riichilab_worker(
    presentation: BoundedRankedPresentationBuffer,
    status: RiichiLabWorkerStatus,
    *,
    profile_name: str | None,
    record_path: Path | None = None,
    run_game: Callable[..., Any] = _default_run_game,
    acquire_record: Callable[..., Any] = _default_acquire_record,
) -> None:
    """worker thread entry point。exactly 1 ranked hanchanを実行する。

    profile / credential解決、Policy生成、ranked実行、durable record取得は
    すべてArenaのsupported contractへ委譲する。requeue / retry / reconnect /
    multiple gamesはこのscopeに含めない。

    Arena bufferがdetach済み(window closed)でも、ranked runはArenaの
    authoritative lifecycleに従って最後まで進む。ここでcancelしない。
    """
    if not isinstance(presentation, BoundedRankedPresentationBuffer):
        raise TypeError("presentation must be a BoundedRankedPresentationBuffer")
    if not isinstance(status, RiichiLabWorkerStatus):
        raise TypeError("status must be a RiichiLabWorkerStatus")

    try:
        profile = resolve_profile(profile_name)
        token = resolve_credential(profile)
    except ProfileError as error:
        # Arenaのprofile errorは環境変数の「名前」だけを含み、値は含まない。
        status.mark_failed(str(error))
        return

    policy = profile.policy_factory()
    status.mark_running(
        format_runtime_summary(
            build_runtime_summary(
                profile, mode="ranked", trace_path=None, policy=policy
            )
        )
    )

    try:
        if record_path is None:
            result = run_game(policy, token, presentation=presentation)
            record_identity = None
        else:
            record = acquire_record(
                policy,
                token,
                destination=record_path,
                profile_identity=profile.name,
                policy_identity=type(policy).__name__,
                presentation=presentation,
            )
            result = record.result
            record_identity = record.record_identity
    except Exception as error:
        # tokenやraw transport payloadを露出させないため、typeだけを出す。
        status.mark_failed(f"{_RANKED_FAILURE_NOTE} ({type(error).__name__})")
        return

    status.mark_finished(
        RiichiLabRunSummary(
            profile=profile.name,
            seat_label=seat_name(result.seat),
            requests=int(result.requests_received),
            responses=int(result.responses_sent),
            scores=result.scores,
            record_identity=record_identity,
        )
    )


@dataclass(frozen=True)
class RiichiLabGameResult:
    """表示を終えたgame attemptのterminal fact。

    Arenaが報告したcompletion / failureだけを持ち、順位等を推測しない。
    terminal factが届く前にrunが終わった場合は両方`None`のままである。
    """

    game_ordinal: int
    completion: RiichiLabCompletion | None
    failure: RiichiLabFailure | None


@dataclass(frozen=True)
class RiichiLabContinuousViewState:
    """continuous modeで描画する現在のpresentation状態のimmutable snapshot。"""

    game_ordinal: int | None
    latest_game_ordinal: int | None
    view: RiichiLabViewState | None
    last_result: RiichiLabGameResult | None
    skipped_games: int


class RiichiLabContinuousController:
    """Arena continuous feedの上に載る、presentation-onlyな表示cursor。

    表示中gameの`RiichiLabLiveController`へPause / Step / Followを委譲する。
    Arena feedの最新gameが変わったら、follow中に限り、手元の前game bufferを
    最終drainしてterminal factを`last_result`へ残してから新gameへ切り替える。
    Arenaは前gameのterminal factをpublishした後にだけ次gameのbufferを開く
    ため、この順序で取りこぼしは起きない。

    表示停止中はgameを切り替えない(停止中の盤面を差し替えない)。その間の
    新game bufferはArena側のbounded capacityでcoalesceされる。一度も表示
    されずに次のgameへ進んだgame attemptは`skipped_games`へ数える。
    """

    __slots__ = (
        "_feed",
        "_capacity",
        "_controller",
        "_game_ordinal",
        "_latest_game_ordinal",
        "_last_result",
        "_skipped_games",
    )

    def __init__(
        self,
        feed: ContinuousRankedPresentationFeed,
        *,
        capacity: int = DEFAULT_PENDING_FRAME_CAPACITY,
    ) -> None:
        if not isinstance(feed, ContinuousRankedPresentationFeed):
            raise TypeError("feed must be a ContinuousRankedPresentationFeed")
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an int")
        if capacity < 1:
            raise ValueError("capacity must be a positive int")
        self._feed = feed
        self._capacity = capacity
        self._controller: RiichiLabLiveController | None = None
        self._game_ordinal: int | None = None
        self._latest_game_ordinal: int | None = None
        self._last_result: RiichiLabGameResult | None = None
        self._skipped_games = 0

    def ingest(self) -> bool:
        """最新gameへの切り替えを判定し、表示中gameのbufferをdrainする。"""
        changed = False
        latest = self._feed.current()
        if latest is not None:
            self._latest_game_ordinal = latest.game_ordinal
            if self._controller is None:
                self._attach(latest.game_ordinal, latest.buffer)
                changed = True
            elif (
                latest.game_ordinal != self._game_ordinal and self._controller.following
            ):
                self._finish_current()
                self._skipped_games += latest.game_ordinal - self._game_ordinal - 1
                self._attach(latest.game_ordinal, latest.buffer)
                changed = True
        if self._controller is not None and self._controller.ingest():
            changed = True
        return changed

    def _attach(
        self, game_ordinal: int, buffer: BoundedRankedPresentationBuffer
    ) -> None:
        self._controller = RiichiLabLiveController(buffer, capacity=self._capacity)
        self._game_ordinal = game_ordinal

    def _finish_current(self) -> None:
        # 前gameのterminal factはこの最終drainで必ず受け取れる。
        self._controller.ingest()
        view = self._controller.state()
        self._last_result = RiichiLabGameResult(
            game_ordinal=self._game_ordinal,
            completion=view.completion,
            failure=view.failure,
        )

    def pause(self) -> None:
        if self._controller is not None:
            self._controller.pause()

    def step(self) -> bool:
        return self._controller is not None and self._controller.step()

    def follow_live(self) -> bool:
        return self._controller is not None and self._controller.follow_live()

    def detach(self) -> None:
        """presentation consumerの離脱をArena feedへ伝える。runは継続する。"""
        self._feed.detach()

    def state(self) -> RiichiLabContinuousViewState:
        return RiichiLabContinuousViewState(
            game_ordinal=self._game_ordinal,
            latest_game_ordinal=self._latest_game_ordinal,
            view=None if self._controller is None else self._controller.state(),
            last_result=self._last_result,
            skipped_games=self._skipped_games,
        )


@dataclass(frozen=True)
class RiichiLabContinuousSnapshot:
    """continuous worker lifecycleのsecret-safeなsnapshot。"""

    running: bool
    exit_code: int | None
    error_type: str | None


class RiichiLabContinuousStatus:
    """continuous workerとviewer threadが共有する、最小限のstatus holder。

    tokenもArenaの出力文字列も保持しない。exit codeと、Arena CLIの外へ
    漏れた例外のtype名だけを持つ。
    """

    __slots__ = ("_lock", "_running", "_exit_code", "_error_type")

    def __init__(self) -> None:
        self._lock = Lock()
        self._running = False
        self._exit_code: int | None = None
        self._error_type: str | None = None

    def mark_running(self) -> None:
        with self._lock:
            self._running = True

    def mark_finished(self, exit_code: int, *, error_type: str | None = None) -> None:
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise TypeError("exit_code must be an int")
        with self._lock:
            self._running = False
            self._exit_code = exit_code
            self._error_type = error_type

    def snapshot(self) -> RiichiLabContinuousSnapshot:
        with self._lock:
            return RiichiLabContinuousSnapshot(
                running=self._running,
                exit_code=self._exit_code,
                error_type=self._error_type,
            )


def _default_run_continuous_cli(
    argv: list[str],
    *,
    presentation: ContinuousRankedPresentationFeed,
    stop_requested: Callable[[], bool],
) -> int:
    from lisjong_arena.riichilab.continuous_ranked import run_continuous_ranked_cli

    return run_continuous_ranked_cli(
        argv, presentation=presentation, stop_requested=stop_requested
    )


def run_riichilab_continuous_worker(
    feed: ContinuousRankedPresentationFeed,
    status: RiichiLabContinuousStatus,
    *,
    arena_argv: list[str],
    stop_requested: Callable[[], bool],
    run_cli: Callable[..., int] = _default_run_continuous_cli,
    error_writer: Callable[[str], None] | None = None,
) -> None:
    """continuous worker thread entry point。

    profile / credential解決、ranked実行、durable record、summary出力、
    exit codeはすべてArenaの`run_continuous_ranked_cli()`へ委譲する。
    Arena CLIの外へ漏れた例外はtype名だけを記録し、messageやtracebackを
    出さない(RiichiLab由来の生dataやcredentialを含み得るため)。
    """
    if not isinstance(feed, ContinuousRankedPresentationFeed):
        raise TypeError("feed must be a ContinuousRankedPresentationFeed")
    if not isinstance(status, RiichiLabContinuousStatus):
        raise TypeError("status must be a RiichiLabContinuousStatus")

    status.mark_running()
    try:
        exit_code = run_cli(
            list(arena_argv), presentation=feed, stop_requested=stop_requested
        )
    except SystemExit as error:
        # argparse等のSystemExitをthread内で握りつぶさず、exit codeへ変換する。
        code = error.code
        exit_code = code if isinstance(code, int) and not isinstance(code, bool) else 2
        status.mark_finished(exit_code)
        return
    except Exception as error:
        error_type = type(error).__name__
        if error_writer is not None:
            error_writer(f"RiichiLab continuous ranked runner failed: {error_type}")
        status.mark_finished(1, error_type=error_type)
        return
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = 1
    status.mark_finished(exit_code)
