"""Replay navigationのsource of truthを持つTk非依存のpure controller。

navigationのsource of truthはrecorded authoritative boundary(=`ReplayTimeline`)と
current frame indexだけであり、Tk widget stateをstateの正本にしない。backward
移動はrecorded frameへのindex移動で成立させ、engineを逆実行しない。
"""

from dataclasses import dataclass

from lisjong_play.replay_source import ReplayFrame, ReplayRound, ReplayTimeline

# 1 frameあたりの基準待ち時間。speedはこの値に対する倍率として適用する。
BASE_FRAME_INTERVAL_MS = 800
SPEED_CHOICES: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)
DEFAULT_SPEED = 1.0


class ReplayControlError(ValueError):
    """controllerがsupportしない速度等を指定された場合。"""


@dataclass(frozen=True)
class ReplayPosition:
    """表示に必要な現在位置のimmutable snapshot。"""

    frame: ReplayFrame
    round: ReplayRound
    frame_number: int
    frame_count: int
    round_number: int
    round_count: int
    at_first: bool
    at_last: bool


class ReplayController:
    """recorded timeline上のcursorとauto-play stateを保持する。

    ここではengine / Policyを一切実行せず、recorded frameのindexだけを動かす。
    """

    def __init__(self, timeline: ReplayTimeline) -> None:
        if not isinstance(timeline, ReplayTimeline):
            raise TypeError("timeline must be a ReplayTimeline")
        self._timeline = timeline
        self._index = 0
        self._playing = False
        self._speed = DEFAULT_SPEED

    @property
    def timeline(self) -> ReplayTimeline:
        return self._timeline

    @property
    def index(self) -> int:
        return self._index

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def frame_interval_ms(self) -> int:
        return max(1, round(BASE_FRAME_INTERVAL_MS / self._speed))

    def position(self) -> ReplayPosition:
        frames = self._timeline.frames
        frame = frames[self._index]
        return ReplayPosition(
            frame=frame,
            round=self._timeline.rounds[frame.round_index],
            frame_number=self._index + 1,
            frame_count=len(frames),
            round_number=frame.round_index + 1,
            round_count=len(self._timeline.rounds),
            at_first=self._index == 0,
            at_last=self._index == len(frames) - 1,
        )

    def _move_to(self, index: int) -> bool:
        bounded = max(0, min(index, len(self._timeline.frames) - 1))
        if bounded == self._index:
            return False
        self._index = bounded
        return True

    def to_first(self) -> bool:
        return self._move_to(0)

    def to_previous(self) -> bool:
        return self._move_to(self._index - 1)

    def to_next(self) -> bool:
        return self._move_to(self._index + 1)

    def to_previous_round(self) -> bool:
        """局頭へ戻り、すでに局頭なら前局の局頭へ移動する。"""
        current = self._timeline.rounds[self._timeline.frames[self._index].round_index]
        if self._index > current.first_frame_index:
            return self._move_to(current.first_frame_index)
        if current.index == 0:
            return False
        return self._move_to(self._timeline.rounds[current.index - 1].first_frame_index)

    def to_next_round(self) -> bool:
        """次局の局頭へ移動する。最終局ではそれ以上進めない。"""
        current = self._timeline.rounds[self._timeline.frames[self._index].round_index]
        if current.index + 1 >= len(self._timeline.rounds):
            return False
        return self._move_to(self._timeline.rounds[current.index + 1].first_frame_index)

    def play(self) -> None:
        """末尾ではauto-playを開始しない。"""
        if self._index >= len(self._timeline.frames) - 1:
            self._playing = False
            return
        self._playing = True

    def pause(self) -> None:
        self._playing = False

    def advance_for_playback(self) -> bool:
        """auto-play 1 tick分だけ進め、末尾に達したら自動でpauseする。

        戻り値はframeが実際に動いたかどうか。
        """
        if not self._playing:
            return False
        moved = self.to_next()
        if not moved or self._index >= len(self._timeline.frames) - 1:
            self._playing = False
        return moved

    def set_speed(self, speed: float) -> None:
        """speed変更はnavigation semanticsを変えず、待ち時間だけを変える。"""
        try:
            value = float(speed)
        except TypeError, ValueError:
            raise ReplayControlError(f"unsupported replay speed: {speed!r}") from None
        if value not in SPEED_CHOICES:
            raise ReplayControlError(f"unsupported replay speed: {speed!r}")
        self._speed = value
