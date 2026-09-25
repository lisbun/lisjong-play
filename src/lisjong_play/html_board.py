"""ブラウザ向けpresentationが共有する、`GuiBoardView`のHTML描画部品。

静的HTML Replay(`replay_html`)とRiichiLab live HTML viewer
(`riichilab_html`)は、同じ`GuiBoardView`をJSONとして受け取り、ここにある
同じCSS / markup / scriptで卓を描く。Tk側の`GuiBoardRenderer`と同じく
「どのsourceから来た盤面か」は知らず、legality / scoring / progressionを
計算しない。

牌画像の参照先だけはpresentationごとに異なる(static fileはdata URI、local
serverは`/tiles/...`)ため、scriptの`createBoardRenderer()`へ
`tileSource(label)`として渡す。未知labelは画像にせずtextで示す。
"""

import base64
import json
from collections.abc import Iterable
from typing import Any

from lisjong_play.gui_model import GuiBoardView
from lisjong_play.tile_images import tile_asset_traversable

__all__ = [
    "BOARD_CSS",
    "BOARD_MARKUP",
    "BOARD_SCRIPT",
    "board_tile_labels",
    "script_safe_json",
    "tile_data_uris",
]


def board_tile_labels(board: GuiBoardView) -> Iterable[str]:
    """盤面が参照するcanonical tile labelを列挙する(重複あり)。"""
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


def script_safe_json(value: Any) -> str:
    """`<script>`内へ置いてもmarkupとして解釈されないJSON文字列。"""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


#: 卓と手牌枠のmarkup。`__HAND_CAPTION__`はpresentationごとの手牌範囲説明。
BOARD_MARKUP = """<section id="table" class="table">
    <div class="seat pos-top" data-position="top"></div>
    <div class="seat pos-left" data-position="left"></div>
    <div id="center" class="center"></div>
    <div class="seat pos-right" data-position="right"></div>
    <div class="seat pos-bottom" data-position="bottom"></div>
  </section>
  <section class="hand-box">
    <div class="caption">__HAND_CAPTION__</div>
    <div id="hand" class="hand"></div>
  </section>"""

BOARD_CSS = """
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
.position, .legend { margin-top: 4px; font-size: 13px; white-space: pre-wrap; }
.legend { color: #55554f; }
pre { margin: 0; max-height: 280px; overflow: auto; white-space: pre-wrap;
  font-size: 13px; }
"""

#: `createBoardRenderer(tileSource, riverRowSize)`を定義するscript。
#: 返り値の`render(board)`が`BOARD_MARKUP`の卓 / 手牌を`GuiBoardView`のJSONで
#: 描き直す。文字列はすべて`textContent`で挿入し、markupとして解釈しない。
BOARD_SCRIPT = """
"use strict";
function createBoardRenderer(tileSource, riverRowSize) {
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function tile(label, size) {
    const uri = tileSource(label);
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
    for (let start = 0; start < seat.river.length; start += riverRowSize) {
      const row = el("div", "river-row");
      for (const cell of seat.river.slice(start, start + riverRowSize)) {
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

  function render(board) {
    const byPosition = {};
    for (const seat of board.seats) byPosition[seat.position] = seat;
    for (const box of document.querySelectorAll(".seat")) {
      renderSeat(box, byPosition[box.dataset.position]);
    }
    renderCenter(board);
    renderHand(board);
  }

  return { el, render };
}
"""
