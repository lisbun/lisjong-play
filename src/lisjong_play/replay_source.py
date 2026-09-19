"""Arena durable local game recordをReplay presentationへ投影するpure source。

このmoduleは`lisjong-arena`のsupported strict loaderだけをrecord入口として使い、
raw bundle fileを独自にparseしない。復元済みのrecorded valueから、既存GUIが
そのまま描画できる`GuiBoardView`列とrecorded round boundaryを構築するだけであり、
legality / scoring / yaku / fu / call priority / round progression / hidden hand /
shanten / ukeireをここで再計算・推測しない。

盤面はrecorded `PolicyInput`のplayer-safe public stateから構築する。durable
recordの`PolicyInput`は各decision seat自身のconcealed handしか保証しないため、
複数seatの`PolicyInput`をmergeしてomniscientな4-seat concealed stateを合成しない。

局結果はdurable record schema v2がArena側でcaptureしたtyped
`LocalGameInspection.round_results`のvalueだけをそのまま提示する。objective
`GameTrace`のraw eventをこちらで再parseして局結果を組み立て直さない。
`RoundWinFact.scoring`が`None`の局はbackendがscoringを公開していない局であり、
点数移動や他局のvalueからhan / fu / 役 / 点数を逆算しない。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lisjong_play.gui_model import GuiBoardView
from lisjong_play.policy_input_board import ROUND_WIND_LABELS as _ROUND_WIND_LABELS
from lisjong_play.policy_input_board import SEAT_NAMES as _SEAT_NAMES
from lisjong_play.policy_input_board import (
    PolicyInputProjectionError,
    build_policy_input_board_view,
    seat_name,
    tile_label,
)
from lisjong_play.policy_input_board import action_label as _action_label
from lisjong_play.policy_input_board import seat_index as _seat_index

# `lisjong-play`はこのschemaのconsumerであり、record schema ownerではない。
LOCAL_GAME_RECORD_SCHEMA_LABEL = (
    "lisjong-arena durable local game record v2 (Arena owned / read-only)"
)

RECORD_RESULT_COVERAGE_NOTE = (
    "durable record v2は局結果として round identity / 点数 / 供託 / ドラ / "
    "和了seat / 放銃seat / 点数移動 / 流局種別 を保持します。"
    "和了牌・和了手牌・流局時の聴牌者はrecordに含まれないため表示しません。"
)

SCORING_UNAVAILABLE_NOTE = (
    "得点内訳: 記録なし（RiichiEnvがこの局のbackend scoringを公開していません）"
)


class ReplayLoadError(PolicyInputProjectionError):
    """durable recordをReplay presentationとして安全に開けない場合。

    loader rejectionもrecord内部の不整合も、partial replayへ降格させずに
    ここでfail closedする。共通`PolicyInput`投影が送出する
    `PolicyInputProjectionError`も、`build_timeline()`境界でこの型へ
    まとめて変換する。
    """


def _board_view(policy_input: Any, action_label: str) -> GuiBoardView:
    """recorded `PolicyInput`のpublic stateからviewer-relative盤面を構築する。

    viewerはそのdecisionのseatであり、表示するconcealed handはrecordが
    player-safeに保持しているそのseat自身の手牌だけである。投影自体は
    RiichiLab live sourceと共有する`policy_input_board`が行う。
    """
    return build_policy_input_board_view(
        policy_input,
        decision_label=f"{seat_name(policy_input.self_seat)} {action_label}",
    )


@dataclass(frozen=True)
class ReplayFrame:
    """recorded 1 seat decisionへ対応する、navigationの最小presentation単位。

    `step_ordinal`と`seat`はrecorded decision identityであり、後続Issueが
    analysis resultからこのframeを参照できるように保持する。
    """

    index: int
    round_index: int
    step_ordinal: int
    seat: int
    board: GuiBoardView


@dataclass(frozen=True)
class ReplayRound:
    """recorded start_kyoku boundaryで区切った1局。"""

    index: int
    label: str
    first_frame_index: int
    last_frame_index: int
    result_text: str


@dataclass(frozen=True)
class ReplayTimeline:
    """strict-loaded recordから構築したimmutableなReplay presentation source。"""

    record_identity: str
    seed: int
    game_mode: str
    policy_identities: tuple[str, ...]
    metadata_text: str
    final_result_text: str
    frames: tuple[ReplayFrame, ...]
    rounds: tuple[ReplayRound, ...]

    def round_of(self, frame_index: int) -> ReplayRound:
        return self.rounds[self.frames[frame_index].round_index]


def _identity(round_result: Any) -> tuple[str, int, int, int]:
    """typed round-result factからround identityを取り出す。"""
    return (
        round_result.round_wind.value,
        int(round_result.hand_number),
        int(round_result.honba),
        _seat_index(round_result.dealer_seat),
    )


def _identity_label(identity: tuple[str, int, int, int]) -> str:
    wind, hand_number, honba, _dealer = identity
    label = _ROUND_WIND_LABELS.get(wind)
    if label is None:
        raise ReplayLoadError(f"record contains an unsupported round wind: {wind!r}")
    return f"{label}{hand_number}局 {honba}本場"


def _scores_line(caption: str, scores: Sequence[int]) -> str:
    body = " / ".join(
        f"{name} {score}" for name, score in zip(_SEAT_NAMES, scores, strict=True)
    )
    return f"{caption}: {body}"


def _deltas_line(deltas: Sequence[int]) -> str:
    body = " / ".join(
        f"{name} {delta:+d}" for name, delta in zip(_SEAT_NAMES, deltas, strict=True)
    )
    return f"点数移動: {body}"


def _tiles_line(caption: str, tiles: Sequence[Any]) -> str | None:
    if not tiles:
        return None
    return f"{caption}: {' '.join(tile_label(tile) for tile in tiles)}"


def _scoring_lines(scoring: Any, win: Any) -> list[str]:
    """backendがcaptureしたscoring factsだけを提示する。

    han / fu / 役 / 支払い額はすべてRiichiEnvが計算した値をそのまま出す。
    ここで点数を再計算したり、翻・符から点数を導出したりしない。
    """
    lines: list[str] = []
    if scoring.yakuman:
        # RiichiEnvの`WinResult.han`は役満でも倍率ではなく13 / 26等の翻数であり、
        # yakuman countそのものはrecordに存在しない。`han`から倍率を逆算しない
        # ため、ここでは役満であることだけを示し、内訳はrecorded yakuと
        # backendが計算した支払い額へ委ねる。
        lines.append("役満")
    else:
        lines.append(f"翻符: {int(scoring.fu)}符{int(scoring.han)}翻")
    if scoring.yaku:
        lines.append("役: " + " / ".join(item.name for item in scoring.yaku))
    if win.tsumo:
        lines.append(
            f"支払い: ツモ 親 {int(scoring.tsumo_points_oya)}点 / "
            f"子 {int(scoring.tsumo_points_ko)}点"
        )
    else:
        lines.append(f"支払い: ロン {int(scoring.ron_points)}点")
    if scoring.pao_payer is not None:
        lines.append(f"包: {seat_name(scoring.pao_payer)}")
    return lines


def _win_lines(win: Any, round_result: Any) -> list[str]:
    """1つのrecorded和了factを局結果行へ投影する。"""
    winner = seat_name(win.winner_seat)
    if win.tsumo:
        lines = [f"結果: 和了 {winner}（ツモ）"]
    else:
        lines = [
            f"結果: 和了 {winner}（ロン）",
            f"放銃: {seat_name(win.loser_seat)}",
        ]
    lines.append(_deltas_line(win.deltas))
    # 裏ドラは和了者が立直していた局だけ意味を持つ。Arena側のrecordは`hora`
    # eventが載せた表示牌をそのまま保持するため、表示可否はrecorded
    # `riichi_seats`で判断する(Arenaのdurable record文書の指示どおり)。
    if win.winner_seat in round_result.riichi_seats:
        ura = _tiles_line("裏ドラ表示牌", win.ura_indicators)
        if ura is not None:
            lines.append(ura)
    if win.scoring is None:
        lines.append(SCORING_UNAVAILABLE_NOTE)
    else:
        lines.extend(_scoring_lines(win.scoring, win))
    return lines


def _draw_lines(draw: Any) -> list[str]:
    kind = "荒牌平局" if draw.exhaustive else "途中流局"
    return [
        f"結果: 流局（{kind}）",
        f"流局理由: {draw.reason}",
        _deltas_line(draw.deltas),
    ]


def _round_result_text(round_result: Any) -> str:
    """typed `RoundResult`のvalueだけから局結果テキストを作る。

    役 / 符 / 翻 / 点数計算 / 聴牌判定をここで再構成しない。
    """
    lines = [f"--- {_identity_label(_identity(round_result))} 終了 ---"]
    dora = _tiles_line("ドラ表示牌", round_result.dora_indicators)
    if dora is not None:
        lines.append(dora)
    if round_result.wins:
        for win in round_result.wins:
            lines.extend(_win_lines(win, round_result))
    elif round_result.draw is not None:
        lines.extend(_draw_lines(round_result.draw))
    else:
        raise ReplayLoadError(
            "recorded round result has neither a win nor a draw outcome"
        )
    if round_result.riichi_seats:
        lines.append(
            "立直: " + " / ".join(seat_name(seat) for seat in round_result.riichi_seats)
        )
    lines.append(_scores_line("局開始時点数", round_result.start_scores))
    lines.append(_scores_line("局終了時点数", round_result.end_scores))
    lines.append(
        f"供託: {int(round_result.riichi_sticks_before)}本 -> "
        f"{int(round_result.riichi_sticks_after)}本"
    )
    lines.append(RECORD_RESULT_COVERAGE_NOTE)
    return "\n".join(lines)


def _final_result_text(result: Any) -> str:
    """recorded `LocalGameResult`のscores / ranksだけから半荘結果を作る。"""
    # `LocalGameResult`はArenaのtyped contractとして4 int scores / ranksを
    # construction時に検証済みなので、ここでraw JSON扱いの再検証はしない。
    scores = tuple(result.scores)
    ranks = tuple(result.ranks)
    if sorted(ranks) != [1, 2, 3, 4]:
        raise ReplayLoadError("recorded final ranks are not a complete 1..4 ordering")
    lines = ["=== 半荘結果 ==="]
    for rank in (1, 2, 3, 4):
        seat = ranks.index(rank)
        lines.append(f"{rank}位 {_SEAT_NAMES[seat]}: {scores[seat]}点")
    return "\n".join(lines)


def _metadata_text(record: Any) -> str:
    result = record.inspection.result
    provenance = record.provenance
    assignments = " / ".join(
        f"{name}={identity}"
        for name, identity in zip(_SEAT_NAMES, record.policy_identities, strict=True)
    )
    return "\n".join(
        [
            f"record identity: {record.record_identity}",
            f"seed: {result.seed}",
            f"game mode: {result.game_mode}",
            f"Policy assignment: {assignments}",
            f"steps: {result.steps} / decisions: {result.decisions}",
            f"schema: {LOCAL_GAME_RECORD_SCHEMA_LABEL}",
            f"lisjong-arena revision: {provenance.lisjong_arena_revision}",
            f"lisjong revision: {provenance.lisjong_revision}",
            f"lisjong-engine revision: {provenance.lisjong_engine_revision}",
        ]
    )


def build_timeline(record: Any) -> ReplayTimeline:
    """strict-loaded recordからimmutableなReplay timelineを構築する。

    共有`PolicyInput`投影が送出する`PolicyInputProjectionError`も、Replay
    consumerからは従来どおり`ReplayLoadError`として観測される。
    """
    try:
        return _build_timeline(record)
    except ReplayLoadError:
        raise
    except PolicyInputProjectionError as error:
        raise ReplayLoadError(str(error)) from error


def _build_timeline(record: Any) -> ReplayTimeline:
    inspection = record.inspection
    frames: list[ReplayFrame] = []
    group_identities: list[tuple[str, int, int, int]] = []
    group_bounds: list[list[int]] = []
    previous_identity: tuple[str, int, int, int] | None = None

    for step in inspection.step_observations:
        for decision in step.seat_decisions:
            policy_input = decision.policy_input
            round_state = policy_input.round
            identity = (
                round_state.round_wind.value,
                int(round_state.hand_number),
                int(round_state.honba),
                _seat_index(round_state.dealer_seat),
            )
            if previous_identity is None or identity != previous_identity:
                group_identities.append(identity)
                group_bounds.append([len(frames), len(frames)])
                previous_identity = identity
            group_bounds[-1][1] = len(frames)
            frames.append(
                ReplayFrame(
                    index=len(frames),
                    round_index=len(group_identities) - 1,
                    step_ordinal=int(step.step_ordinal),
                    seat=_seat_index(decision.seat),
                    board=_board_view(
                        policy_input,
                        _action_label(decision.decision_trace.selected_action),
                    ),
                )
            )

    if not frames:
        raise ReplayLoadError("record contains no replayable decision")

    round_results = tuple(inspection.round_results)
    if len(round_results) != len(group_identities):
        raise ReplayLoadError(
            "recorded decision rounds and recorded round results do not correspond "
            f"({len(group_identities)} decision rounds, "
            f"{len(round_results)} recorded results)"
        )
    for index, (round_result, from_decisions) in enumerate(
        zip(round_results, group_identities, strict=True)
    ):
        recorded = _identity(round_result)
        if recorded != from_decisions:
            raise ReplayLoadError(
                f"recorded round {index} identity does not match its decisions: "
                f"{_identity_label(recorded)!r} != {_identity_label(from_decisions)!r}"
            )

    rounds = tuple(
        ReplayRound(
            index=index,
            label=_identity_label(_identity(round_result)),
            first_frame_index=bounds[0],
            last_frame_index=bounds[1],
            result_text=_round_result_text(round_result),
        )
        for index, (round_result, bounds) in enumerate(
            zip(round_results, group_bounds, strict=True)
        )
    )
    result = inspection.result
    return ReplayTimeline(
        record_identity=record.record_identity,
        seed=int(result.seed),
        game_mode=str(result.game_mode),
        policy_identities=tuple(record.policy_identities),
        metadata_text=_metadata_text(record),
        final_result_text=_final_result_text(result),
        frames=tuple(frames),
        rounds=rounds,
    )


def _default_loader(path: str | Path) -> Any:
    """Arenaのsupported strict loaderだけをrecord入口として使う。"""
    from lisjong_arena import load_local_game_record

    return load_local_game_record(path)


def load_replay_timeline(
    path: str | Path,
    *,
    loader: Callable[[str | Path], Any] | None = None,
) -> ReplayTimeline:
    """durable record bundleをstrictに読み、Replay timelineへ投影する。

    loader rejection(unsupported schema / corrupt / digest mismatch等)は
    `ReplayLoadError`として伝播し、partial replayへ降格させない。
    """
    load = _default_loader if loader is None else loader
    try:
        record = load(path)
    except ReplayLoadError:
        raise
    except Exception as error:
        raise ReplayLoadError(
            f"durable recordを読み込めません: {type(error).__name__}: {error}"
        ) from error
    return build_timeline(record)
