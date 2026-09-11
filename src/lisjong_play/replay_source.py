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

from lisjong.policy_contract import (
    AnkanAction,
    ChiAction,
    DaiminkanAction,
    DiscardAction,
    KakanAction,
    KyuushuKyuuhaiAction,
    PassAction,
    PonAction,
    RiichiAction,
    RonAction,
    Seat,
    TsumoAction,
)

from lisjong_play.gui_model import (
    GuiBoardView,
    GuiMeldView,
    GuiRiverTile,
    GuiSeatView,
    TablePosition,
)
from lisjong_play.tile_images import TILE_ASSET_FILENAMES

_POSITIONS: tuple[TablePosition, ...] = ("bottom", "right", "top", "left")

_SEAT_NAMES = ("P1", "P2", "P3", "P4")
_SEAT_WIND_LABELS = ("東家", "南家", "西家", "北家")
_ROUND_WIND_LABELS = {
    "east": "東",
    "south": "南",
    "west": "西",
    "north": "北",
}
_SUIT_SUFFIX = {"manzu": "m", "pinzu": "p", "souzu": "s"}
_HONOR_LABELS = {1: "東", 2: "南", 3: "西", 4: "北", 5: "白", 6: "發", 7: "中"}
_MELD_LABELS = {
    "chi": "チー",
    "pon": "ポン",
    "daiminkan": "大明槓",
    "ankan": "暗槓",
    "kakan": "加槓",
}
# recorded `RiichiState`のdisplay label。live GUIの`PublicRiichiStatus`表示と
# 同じ語彙を使うが、engine statusとrecorded statusを同一semanticsとして
# 再定義しないよう、対応表はここに閉じる。
_RIICHI_LABELS = {"none": "", "declared": "宣言中", "accepted": "立直"}

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


class ReplayLoadError(RuntimeError):
    """durable recordをReplay presentationとして安全に開けない場合。

    loader rejectionもrecord内部の不整合も、partial replayへ降格させずに
    ここでfail closedする。
    """


def _seat_index(seat: Any) -> int:
    """strict loaderが復元済みのtyped `Seat`を0..3のfixed seat indexへ変換する。

    ここはArena / lisjongのtyped contractとして既に検証済みのvalueだけを扱う。
    """
    if not isinstance(seat, Seat):
        raise ReplayLoadError(f"record contains an unusable seat value: {seat!r}")
    index = int(seat)
    if not 0 <= index < len(_SEAT_NAMES):
        raise ReplayLoadError(f"record contains an out-of-range seat: {index}")
    return index


def seat_name(seat: Any) -> str:
    """半荘中変わらないfixed seatの表示名を返す。"""
    return _SEAT_NAMES[_seat_index(seat)]


def _seat_round_label(index: int, dealer_seat: Any) -> str:
    dealer = _seat_index(dealer_seat)
    wind = _SEAT_WIND_LABELS[(index - dealer) % len(_SEAT_NAMES)]
    return f"{_SEAT_NAMES[index]}（{wind}）"


def tile_label(tile: Any) -> str:
    """recorded `Tile`をcanonical tile labelへ変換する。

    変換結果は必ず既存の牌画像registryが解決できるlabelでなければならない。
    未知の牌はsilentに別牌へ落とさずfail closedする。
    """
    try:
        tile_type = tile.tile_type
        category = tile_type.category.value
        rank = int(tile_type.rank)
        is_red = bool(tile.is_red)
    except AttributeError:
        raise ReplayLoadError(f"record contains an unusable tile value: {tile!r}")
    if category == "honor":
        label = _HONOR_LABELS.get(rank, "")
    else:
        suffix = _SUIT_SUFFIX.get(category)
        label = "" if suffix is None else f"{rank}{suffix}{'r' if is_red else ''}"
    if label not in TILE_ASSET_FILENAMES:
        raise ReplayLoadError(
            f"record contains a tile this viewer cannot display: "
            f"category={category!r} rank={rank!r} is_red={is_red!r}"
        )
    return label


# 手牌 / 副露の表示順は live GUIのcanonical order(萬子 -> 筒子 -> 索子 -> 字牌)へ揃える。
_CATEGORY_ORDER = {"manzu": 0, "pinzu": 1, "souzu": 2, "honor": 3}


def _tile_sort_key(tile: Any) -> tuple[int, int, bool]:
    category = tile.tile_type.category.value
    try:
        order = _CATEGORY_ORDER[category]
    except KeyError:
        raise ReplayLoadError(
            f"record contains an unsupported tile category: {category!r}"
        ) from None
    return (order, int(tile.tile_type.rank), bool(tile.is_red))


def _river_tile(discard: Any) -> GuiRiverTile:
    """recorded discardを河表示へ投影する。

    durable recordの`Discard`は立直宣言牌markerを持たないため、宣言牌を
    推測せず常にmarkerなしとして扱う。
    """
    called_by = discard.called_by
    return GuiRiverTile(
        tile=tile_label(discard.tile),
        is_tsumogiri=bool(discard.tsumogiri),
        is_riichi_declaration=False,
        called_by=None if called_by is None else seat_name(called_by),
    )


def _meld_view(meld: Any) -> GuiMeldView:
    kind = meld.kind.value
    try:
        type_label = _MELD_LABELS[kind]
    except KeyError:
        raise ReplayLoadError(f"record contains an unsupported meld kind: {kind!r}")
    from_seat = meld.from_seat
    called_tile = meld.called_tile
    return GuiMeldView(
        type_label=type_label,
        tiles=tuple(
            tile_label(tile) for tile in sorted(meld.tiles, key=_tile_sort_key)
        ),
        from_seat=None if from_seat is None else seat_name(from_seat),
        called_tile=None if called_tile is None else tile_label(called_tile),
    )


_CALL_LABELS = {ChiAction: "チー", PonAction: "ポン", DaiminkanAction: "大明槓"}


def _action_label(action: Any) -> str:
    """recorded `InternalAction`を人間向けlabelへ変換する。

    未知のvariantはfail closedし、Pass等へfallbackしない。
    """
    if isinstance(action, DiscardAction):
        suffix = "（ツモ切り）" if action.tsumogiri else ""
        return f"打牌 {tile_label(action.tile)}{suffix}"
    if isinstance(action, RiichiAction):
        return "立直宣言"
    if isinstance(action, (ChiAction, PonAction, DaiminkanAction)):
        consumed = " ".join(
            tile_label(tile)
            for tile in sorted(action.consumed_tiles, key=_tile_sort_key)
        )
        return (
            f"{_CALL_LABELS[type(action)]} {tile_label(action.called_tile)}"
            f" / 使用 {consumed} / from {seat_name(action.target)}"
        )
    if isinstance(action, AnkanAction):
        tiles = " ".join(
            tile_label(tile) for tile in sorted(action.tiles, key=_tile_sort_key)
        )
        return f"暗槓 {tiles}"
    if isinstance(action, KakanAction):
        return f"加槓 {tile_label(action.added_tile)}"
    if isinstance(action, RonAction):
        return (
            f"ロン {tile_label(action.winning_tile)} / from {seat_name(action.target)}"
        )
    if isinstance(action, TsumoAction):
        return f"ツモ {tile_label(action.winning_tile)}"
    if isinstance(action, PassAction):
        return "パス"
    if isinstance(action, KyuushuKyuuhaiAction):
        return "九種九牌"
    raise ReplayLoadError(
        f"record contains an unsupported action variant: {type(action).__name__}"
    )


def _round_label(round_state: Any) -> str:
    wind = _ROUND_WIND_LABELS.get(round_state.round_wind.value)
    if wind is None:
        raise ReplayLoadError(
            f"record contains an unsupported round wind: {round_state.round_wind!r}"
        )
    return f"{wind}{round_state.hand_number}局 {round_state.honba}本場"


def _board_view(policy_input: Any, action_label: str) -> GuiBoardView:
    """recorded `PolicyInput`のpublic stateからviewer-relative盤面を構築する。

    viewerはそのdecisionのseatであり、表示するconcealed handはrecordが
    player-safeに保持しているそのseat自身の手牌だけである。
    """
    players = tuple(policy_input.players)
    if len(players) != len(_SEAT_NAMES):
        raise ReplayLoadError("recorded PolicyInput must contain four seat states")
    round_state = policy_input.round
    viewer_index = _seat_index(policy_input.self_seat)
    dealer_seat = round_state.dealer_seat

    seats = []
    for index, player in enumerate(players):
        riichi = player.riichi.value
        try:
            riichi_label = _RIICHI_LABELS[riichi]
        except KeyError:
            raise ReplayLoadError(
                f"record contains an unsupported riichi state: {riichi!r}"
            )
        seats.append(
            GuiSeatView(
                position=_POSITIONS[(index - viewer_index) % len(_POSITIONS)],
                label=_seat_round_label(index, dealer_seat),
                score=int(player.score),
                riichi=riichi_label,
                melds=tuple(_meld_view(meld) for meld in player.melds),
                river=tuple(
                    _river_tile(discard)
                    for discard in sorted(player.discards, key=lambda item: item.order)
                ),
            )
        )

    own_hand = policy_input.own_hand
    concealed = sorted(own_hand.concealed_tiles, key=_tile_sort_key)
    drawn = own_hand.drawn_tile
    if drawn is not None:
        for position in range(len(concealed) - 1, -1, -1):
            if concealed[position] == drawn:
                concealed.pop(position)
                break
        else:
            raise ReplayLoadError(
                "recorded drawn tile is absent from the recorded hand"
            )
    return GuiBoardView(
        round_label=_round_label(round_state),
        decision_label=f"{seat_name(policy_input.self_seat)} {action_label}",
        center_detail=(
            f"供託 {round_state.riichi_sticks}本 / "
            f"残り山 {round_state.live_wall_tiles_remaining}枚"
        ),
        dora_indicators=tuple(tile_label(tile) for tile in round_state.dora_indicators),
        seats=tuple(seats),
        hand_tiles=tuple(tile_label(tile) for tile in concealed),
        drawn_tile=None if drawn is None else tile_label(drawn),
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
    """strict-loaded recordからimmutableなReplay timelineを構築する。"""
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
