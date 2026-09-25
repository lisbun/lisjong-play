"""Arena durable local game recordを単一ファイルのstatic HTML Replayへ書き出す。

Tk Replay Viewerと同じ`ReplayTimeline`だけを入力とし、record bundleを独自に
parseしない。HTMLへ埋め込むのはtimelineが既に保持するplayer-safeな値と、
使われる牌のvendored画像だけであり、record path等のlocal環境情報は含めない。

navigation semantics(前局 / 次局の移動先、位置表示、速度ごとの待ち時間)は
`ReplayController`でframeごとに事前計算してpayloadへ入れる。HTML側のscriptは
表示とcursor移動だけを行い、legality / scoring / progressionを計算しない。
"""

import argparse
import base64
import json
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lisjong_play.gui_board import GUI_RIVER_LEGEND, RIVER_ROW_SIZE
from lisjong_play.gui_model import GuiBoardView
from lisjong_play.replay_controller import SPEED_CHOICES, ReplayController
from lisjong_play.replay_source import (
    ReplayLoadError,
    ReplayTimeline,
    load_replay_timeline,
)
from lisjong_play.tile_images import TileImageAssetError, tile_asset_traversable

PAYLOAD_ELEMENT_ID = "replay-data"
_DEFAULT_OUTPUT_NAME = "replay.html"


class ReplayHtmlOutputError(RuntimeError):
    """HTMLを書き出し先へ安全に作成できない場合。"""


def _board_tile_labels(board: GuiBoardView) -> Iterable[str]:
    yield from board.dora_indicators
    yield from board.hand_tiles
    if board.drawn_tile is not None:
        yield board.drawn_tile
    for seat in board.seats:
        for meld in seat.melds:
            yield from meld.tiles
            if meld.called_tile is not None:
                yield meld.called_tile
        for cell in seat.river:
            yield cell.tile


def collect_tile_labels(timeline: ReplayTimeline) -> tuple[str, ...]:
    """timelineの盤面が実際に参照するcanonical tile labelをsortして返す。"""
    labels: set[str] = set()
    for frame in timeline.frames:
        labels.update(_board_tile_labels(frame.board))
    return tuple(sorted(labels))


def tile_data_uris(labels: Iterable[str]) -> dict[str, str]:
    """canonical tile label -> vendored PNGのdata URI。

    未知labelや欠落assetは`TileImageAssetError`のままfail closedする。
    """
    uris: dict[str, str] = {}
    for label in labels:
        data = tile_asset_traversable(label).read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        uris[label] = f"data:image/png;base64,{encoded}"
    return uris


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


def _script_safe_json(value: Any) -> str:
    """`<script>`内へ置いてもmarkupとして解釈されないJSON文字列。"""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def render_replay_html(timeline: ReplayTimeline) -> str:
    """timelineから外部resourceを参照しない単一HTML文字列を生成する。"""
    payload = build_replay_payload(timeline)
    payload["tiles"] = tile_data_uris(collect_tile_labels(timeline))
    return (
        _HTML_TEMPLATE.replace("__CSS__", _CSS)
        .replace("__SCRIPT__", _SCRIPT)
        .replace("__PAYLOAD_ID__", PAYLOAD_ELEMENT_ID)
        .replace("__PAYLOAD__", _script_safe_json(payload))
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
  <section id="table" class="table">
    <div class="seat pos-top" data-position="top"></div>
    <div class="seat pos-left" data-position="left"></div>
    <div id="center" class="center"></div>
    <div class="seat pos-right" data-position="right"></div>
    <div class="seat pos-bottom" data-position="bottom"></div>
  </section>
  <section class="hand-box">
    <div class="caption">このdecision seatの手牌（recordが保持する範囲）</div>
    <div id="hand" class="hand"></div>
  </section>
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

_CSS = """
:root { color-scheme: light; --felt: #176b4d; --felt-text: #f4f1e6;
  --seat-info: #0d4634; --tsumogiri: #c2c2c2; }
* { box-sizing: border-box; }
body { margin: 0; background: #f3f3f0; color: #1d1d1b;
  font-family: system-ui, "Hiragino Sans", "Yu Gothic UI", "Meiryo", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 12px 16px; }
header { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap;
  margin-bottom: 8px; }
.table { display: grid; grid-template-columns: 1fr auto 1fr;
  grid-template-areas: ". top ." "left center right" ". bottom .";
  gap: 6px 24px; align-items: center; min-height: 520px; padding: 12px;
  background: var(--felt); color: var(--felt-text); border-radius: 6px;
  overflow-x: auto; }
.pos-top { grid-area: top; justify-self: center; align-self: end; }
.pos-bottom { grid-area: bottom; justify-self: center; align-self: start; }
.pos-left { grid-area: left; justify-self: end; }
.pos-right { grid-area: right; justify-self: start; }
.center { grid-area: center; text-align: center; font-size: 13px; }
.center .round { font-weight: bold; font-size: 16px; }
.seat { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.pos-left, .pos-right { align-items: flex-start; }
.status { background: var(--seat-info); font-size: 12px; font-weight: bold;
  padding: 0 6px; white-space: nowrap; }
.body { display: flex; gap: 10px; align-items: flex-start; }
.melds { display: flex; flex-direction: column; gap: 2px; }
.meld-caption { font-size: 10px; }
.tiles { display: flex; }
.river { display: flex; flex-direction: column; gap: 1px; }
.river-row { display: flex; gap: 2px; }
.river-empty { font-size: 12px; }
.cell { display: flex; flex-direction: column; align-items: center; }
.cell .mark { font-size: 9px; line-height: 1.1; }
.tile { display: block; background: #fff; }
.tile.small { width: 24px; height: 32px; }
.tile.large { width: 36px; height: 48px; }
.tsumogiri-face { background: var(--tsumogiri); }
.tsumogiri-face img { mix-blend-mode: multiply; }
.tile-text { display: inline-block; min-width: 24px; padding: 2px; background: #fff;
  color: #1d1d1b; font-size: 12px; text-align: center; }
.dora { display: flex; gap: 3px; justify-content: center; align-items: center;
  margin: 2px 0; }
.hand-box, .info-box { margin-top: 8px; padding: 6px 8px; background: #fff;
  border: 1px solid #d6d6d0; border-radius: 6px; }
.caption { font-size: 12px; color: #55554f; margin-bottom: 4px; }
.hand { display: flex; gap: 2px; justify-content: center; align-items: center;
  min-height: 52px; flex-wrap: wrap; }
.hand .separator { width: 1px; align-self: stretch; background: #999; margin: 0 6px; }
.controls { display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
  margin-top: 8px; }
.controls button { padding: 6px 10px; font-size: 14px; }
.controls .play { margin-left: 10px; }
.controls input[type=range] { flex: 1 1 160px; }
.position, .legend { margin-top: 4px; font-size: 13px; white-space: pre-wrap; }
.legend { color: #55554f; }
pre { margin: 0; max-height: 280px; overflow: auto; white-space: pre-wrap;
  font-size: 13px; }
"""

_SCRIPT = """
"use strict";
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

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function tile(label, size) {
    const uri = data.tiles[label];
    if (uri === undefined) return el("span", "tile-text", label);
    const img = el("img", "tile " + size);
    img.src = uri;
    img.alt = label;
    img.title = label;
    return img;
  }

  function meldCaption(meld) {
    return meld.from_seat === null
      ? meld.type_label : meld.type_label + "→" + meld.from_seat;
  }

  function riverMark(cell) {
    let text = cell.is_riichi_declaration ? "[立]" : "";
    if (cell.called_by !== null) text += "→" + cell.called_by;
    return text;
  }

  function renderSeat(box, seat) {
    box.replaceChildren();
    let status = seat.label + "  " + seat.score + "点";
    if (seat.riichi) status += " / " + seat.riichi;
    const statusNode = el("div", "status", status);

    const melds = el("div", "melds");
    for (const meld of seat.melds) {
      const group = el("div", "meld");
      group.append(el("div", "meld-caption", meldCaption(meld)));
      const row = el("div", "tiles");
      for (const label of meld.tiles) row.append(tile(label, "small"));
      group.append(row);
      melds.append(group);
    }

    const river = el("div", "river");
    if (seat.river.length === 0) {
      river.append(el("div", "river-empty", "河 -"));
    }
    for (let start = 0; start < seat.river.length;
         start += data.river_row_size) {
      const row = el("div", "river-row");
      for (const cell of seat.river.slice(start, start + data.river_row_size)) {
        const node = el("div", "cell");
        const face = el("div", cell.is_tsumogiri ? "tsumogiri-face" : "");
        face.append(tile(cell.tile, "small"));
        node.append(face);
        const mark = riverMark(cell);
        if (mark) node.append(el("div", "mark", mark));
        row.append(node);
      }
      river.append(row);
    }

    // 外周側から中央側へ text情報 -> 副露 -> 河 (Tk rendererと同じ並び)。
    const body = el("div", "body");
    if (seat.position === "left") body.append(melds, river);
    else body.append(river, melds);
    if (seat.position === "bottom") box.append(body, statusNode);
    else box.append(statusNode, body);
  }

  function renderCenter(board) {
    const center = document.getElementById("center");
    center.replaceChildren();
    center.append(el("div", "round", board.round_label));
    center.append(el("div", "detail", board.center_detail));
    const dora = el("div", "dora");
    dora.append(el("span", "", "ドラ"));
    if (board.dora_indicators.length === 0) dora.append(el("span", "", "なし"));
    for (const label of board.dora_indicators) dora.append(tile(label, "small"));
    center.append(dora);
    center.append(el("div", "decision", "判断: " + board.decision_label));
  }

  function renderHand(board) {
    const hand = document.getElementById("hand");
    hand.replaceChildren();
    for (const label of board.hand_tiles) hand.append(tile(label, "large"));
    if (board.drawn_tile !== null) {
      hand.append(el("span", "separator"));
      hand.append(tile(board.drawn_tile, "large"));
    }
  }

  function render() {
    const frame = frames[index];
    const board = frame.board;
    const byPosition = {};
    for (const seat of board.seats) byPosition[seat.position] = seat;
    for (const box of document.querySelectorAll(".seat")) {
      renderSeat(box, byPosition[box.dataset.position]);
    }
    renderCenter(board);
    renderHand(board);
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
