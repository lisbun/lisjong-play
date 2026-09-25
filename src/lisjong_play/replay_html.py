"""Arena durable local game recordを単一ファイルのstatic HTML Replayへ書き出す。

Tk Replay Viewerと同じ`ReplayTimeline`だけを入力とし、record bundleを独自に
parseしない。HTMLへ埋め込むのはtimelineが既に保持するplayer-safeな値と、
使われる牌のvendored画像だけであり、record path等のlocal環境情報は含めない。

navigation semantics(前局 / 次局の移動先、位置表示、速度ごとの待ち時間)は
`ReplayController`でframeごとに事前計算してpayloadへ入れる。HTML側のscriptは
表示とcursor移動だけを行い、legality / scoring / progressionを計算しない。
"""

import argparse
import os
import stat
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lisjong_play.gui_board import GUI_RIVER_LEGEND, RIVER_ROW_SIZE
from lisjong_play.html_board import (
    BOARD_CSS,
    BOARD_MARKUP,
    BOARD_SCRIPT,
    board_tile_labels,
    script_safe_json,
    tile_data_uris,
)
from lisjong_play.replay_controller import SPEED_CHOICES, ReplayController
from lisjong_play.replay_source import (
    ReplayLoadError,
    ReplayTimeline,
    load_replay_timeline,
)
from lisjong_play.tile_images import TileImageAssetError

PAYLOAD_ELEMENT_ID = "replay-data"
_DEFAULT_OUTPUT_NAME = "replay.html"


class ReplayHtmlOutputError(RuntimeError):
    """HTMLを書き出し先へ安全に作成できない場合。"""


def collect_tile_labels(timeline: ReplayTimeline) -> tuple[str, ...]:
    """timelineの盤面が実際に参照するcanonical tile labelをsortして返す。"""
    labels: set[str] = set()
    for frame in timeline.frames:
        labels.update(board_tile_labels(frame.board))
    return tuple(sorted(labels))


def build_replay_payload(timeline: ReplayTimeline) -> dict[str, Any]:
    """HTML viewerが描画とnavigationに使うJSON化可能なpayloadを構築する。"""
    if not isinstance(timeline, ReplayTimeline):
        raise TypeError("timeline must be a ReplayTimeline")
    controller = ReplayController(timeline)
    frames: list[dict[str, Any]] = []
    for index, frame in enumerate(timeline.frames):
        controller.to_index(index)
        position = controller.position()
        position_text = (
            f"{position.round.label}"
            f"  /  局 {position.round_number}/{position.round_count}"
            f"  /  手順 {position.frame_number}/{position.frame_count}"
            f"  /  step {frame.step_ordinal}"
        )
        controller.to_previous_round()
        previous_round = controller.index
        controller.to_index(index)
        controller.to_next_round()
        next_round = controller.index
        frames.append(
            {
                "round_index": frame.round_index,
                "position_text": position_text,
                "previous_round_index": previous_round,
                "next_round_index": next_round,
                "board": asdict(frame.board),
            }
        )

    speeds: list[dict[str, Any]] = []
    for speed in SPEED_CHOICES:
        controller.set_speed(speed)
        speeds.append(
            {"label": f"{speed}", "interval_ms": controller.frame_interval_ms}
        )
    default_speed = ReplayController(timeline).speed

    return {
        "status_text": f"seed={timeline.seed} / {timeline.game_mode}",
        "final_result_text": timeline.final_result_text,
        "metadata_text": timeline.metadata_text,
        "river_legend": GUI_RIVER_LEGEND,
        "river_row_size": RIVER_ROW_SIZE,
        "speeds": speeds,
        "default_speed_label": f"{default_speed}",
        "rounds": [
            {"label": item.label, "result_text": item.result_text}
            for item in timeline.rounds
        ],
        "frames": frames,
    }


def render_replay_html(timeline: ReplayTimeline) -> str:
    """timelineから外部resourceを参照しない単一HTML文字列を生成する。"""
    payload = build_replay_payload(timeline)
    payload["tiles"] = tile_data_uris(collect_tile_labels(timeline))
    return (
        _HTML_TEMPLATE.replace("__BOARD__", BOARD_MARKUP)
        .replace("__HAND_CAPTION__", _HAND_CAPTION)
        .replace("__CSS__", BOARD_CSS + _CSS)
        .replace("__SCRIPT__", BOARD_SCRIPT + _SCRIPT)
        .replace("__PAYLOAD_ID__", PAYLOAD_ELEMENT_ID)
        .replace("__PAYLOAD__", script_safe_json(payload))
    )


def write_replay_html(
    html: str, output: str | Path, *, overwrite: bool = False
) -> None:
    """HTMLを書き出す。既存fileはoverwrite指定時だけ置き換える。

    書き込み途中で失敗してもpartial HTMLを残さない。
    """
    path = Path(output)
    if path.is_dir():
        raise ReplayHtmlOutputError(f"出力先がdirectoryです: {path}")
    if not overwrite:
        try:
            stream = path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            raise ReplayHtmlOutputError(
                f"出力先が既に存在します(--overwriteで置き換えます): {path}"
            ) from None
        try:
            with stream:
                stream.write(html)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(html)
        # mkstempは0600で作るため、置き換える既存fileのpermissionを引き継ぐ。
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def default_output_path(record_path: str | Path) -> Path:
    """record bundle directory名から、current directory上の出力名を決める。"""
    name = Path(record_path).resolve().name
    return Path(f"{name}.html" if name else _DEFAULT_OUTPUT_NAME)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lisjong-play-replay-html",
        description=(
            "lisjong-arenaのdurable local game recordを、ブラウザで開ける"
            "単一ファイルのHTML Replayへ書き出します。"
        ),
    )
    parser.add_argument("path", help="durable record bundle directory")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="出力HTML file (省略時は <record directory名>.html)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存の出力fileを置き換えます",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    writer: Callable[[str], None] = print,
    timeline_loader: Callable[[str], ReplayTimeline] = load_replay_timeline,
) -> int:
    args = _parser().parse_args(argv)
    output = (
        Path(args.output) if args.output is not None else default_output_path(args.path)
    )
    try:
        timeline = timeline_loader(args.path)
        html = render_replay_html(timeline)
        write_replay_html(html, output, overwrite=args.overwrite)
    except ReplayLoadError as error:
        writer(f"牌譜を読み込めませんでした: {error}")
        return 1
    except TileImageAssetError as error:
        writer(f"牌画像を解決できませんでした: {error}")
        return 1
    except (ReplayHtmlOutputError, OSError) as error:
        writer(f"HTMLを書き出せませんでした: {error}")
        return 1
    writer(f"HTML Replayを書き出しました: {output}")
    return 0


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>lisjong 牌譜Replay</title>
<style>__CSS__</style>
</head>
<body>
<main>
  <header>
    <strong>lisjong 牌譜Replay</strong>
    <span id="status"></span>
  </header>
  __BOARD__
  <nav class="controls">
    <button type="button" data-nav="first">|&lt; 先頭</button>
    <button type="button" data-nav="previous_round">&lt;&lt; 前局</button>
    <button type="button" data-nav="previous">&lt; 前へ</button>
    <button type="button" data-nav="next">次へ &gt;</button>
    <button type="button" data-nav="next_round">次局 &gt;&gt;</button>
    <button type="button" id="play" class="play">▶ 再生</button>
    <label>速度 <select id="speed"></select></label>
    <input id="scrub" type="range" min="0" value="0" aria-label="手順">
  </nav>
  <div id="position" class="position"></div>
  <div id="legend" class="legend"></div>
  <section class="info-box">
    <div class="caption">局結果 / 半荘結果 / record情報</div>
    <pre id="info"></pre>
  </section>
</main>
<script type="application/json" id="__PAYLOAD_ID__">__PAYLOAD__</script>
<script>__SCRIPT__</script>
</body>
</html>
"""

_HAND_CAPTION = "このdecision seatの手牌（recordが保持する範囲）"

_CSS = """
.controls .play { margin-left: 10px; }
.controls input[type=range] { flex: 1 1 160px; }
"""

_SCRIPT = """
(() => {
  const data = JSON.parse(
    document.getElementById("__PAYLOAD_ID__").textContent);
  const frames = data.frames;
  const last = frames.length - 1;
  let index = 0;
  let playing = false;
  let timer = null;
  let speedIndex = Math.max(0,
    data.speeds.findIndex((s) => s.label === data.default_speed_label));

  const board = createBoardRenderer(
    (label) => data.tiles[label], data.river_row_size);
  const el = board.el;

  function render() {
    const frame = frames[index];
    board.render(frame.board);
    document.getElementById("position").textContent = frame.position_text;
    document.getElementById("info").textContent = [
      data.rounds[frame.round_index].result_text,
      data.final_result_text,
      data.metadata_text,
    ].join("\\n\\n");
    document.getElementById("scrub").value = String(index);
    document.getElementById("play").textContent =
      playing ? "⏸ 一時停止" : "▶ 再生";
    const nav = (name) => document.querySelector('[data-nav="' + name + '"]');
    nav("first").disabled = index === 0;
    nav("previous").disabled = index === 0;
    nav("next").disabled = index === last;
  }

  function cancelTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function schedule() {
    cancelTimer();
    if (playing) timer = setTimeout(tick, data.speeds[speedIndex].interval_ms);
  }

  function tick() {
    timer = null;
    if (!playing) return;
    if (index < last) index += 1;
    if (index >= last) playing = false;
    schedule();
    render();
  }

  function moveTo(target) {
    // 手動navigationはauto-playを止めてからcursorを動かす。
    playing = false;
    cancelTimer();
    index = Math.max(0, Math.min(last, target));
    render();
  }

  const moves = {
    first: () => 0,
    previous: () => index - 1,
    next: () => index + 1,
    previous_round: () => frames[index].previous_round_index,
    next_round: () => frames[index].next_round_index,
  };

  function togglePlayback() {
    if (playing) {
      playing = false;
      cancelTimer();
    } else if (index < last) {
      playing = true;
      schedule();
    }
    render();
  }

  document.getElementById("status").textContent = data.status_text;
  document.getElementById("legend").textContent = data.river_legend;
  for (const button of document.querySelectorAll("[data-nav]")) {
    button.addEventListener("click", () => moveTo(moves[button.dataset.nav]()));
  }
  document.getElementById("play").addEventListener("click", togglePlayback);
  const speed = document.getElementById("speed");
  data.speeds.forEach((item, i) => {
    const option = el("option", "", item.label);
    option.value = String(i);
    speed.append(option);
  });
  speed.value = String(speedIndex);
  speed.addEventListener("change", () => {
    speedIndex = Number(speed.value);
    if (playing) schedule();
  });
  const scrub = document.getElementById("scrub");
  scrub.max = String(last);
  scrub.addEventListener("input", () => moveTo(Number(scrub.value)));
  document.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLSelectElement) return;
    if (event.target instanceof HTMLInputElement) return;
    const keys = {
      ArrowLeft: "previous", ArrowRight: "next",
      PageUp: "previous_round", PageDown: "next_round", Home: "first",
    };
    if (event.key === " ") {
      // focus中のbuttonはSpaceでそのbutton自身を押せるよう、既定動作に任せる。
      if (event.target instanceof HTMLButtonElement) return;
      event.preventDefault();
      togglePlayback();
    } else if (keys[event.key] !== undefined) {
      event.preventDefault();
      moveTo(moves[keys[event.key]]());
    }
  });
  render();
})();
"""

if __name__ == "__main__":
    raise SystemExit(main())
