"""RiichiLab ranked対局をブラウザでlive観戦する、local HTML viewer entry point。

Tk RiichiLab live viewer(`riichilab_gui`)と同じArena live presentation seam
(`riichilab_source`)を入力とし、表示先だけをlocal HTTP server + HTML pageへ
置き換える。盤面描画は静的HTML Replayと`html_board`を共有する。

```text
Arena ranked worker (non-daemon thread)
    -> BoundedRankedPresentationBuffer
    -> RiichiLabLiveController.ingest()     (server threadだけがdrain)
    -> GET /state JSON -> browser (polling) -> createBoardRenderer()
```

server boundary
---------------
- bind先は`127.0.0.1`固定でcallerから変更できない。LAN / 外部公開はscope外。
- `HTTPServer`はsingle-threadedで、controllerへのアクセスはserver thread
  だけに直列化される(Tk版で「Tk main threadだけがdrainする」のと同じ位置付け)。
- DNS rebinding / cross-site requestへの最小防御として、`Host`をloopbackの
  自port以外は拒否し、`POST`は`application/json`と同一`Origin`だけを受ける。
- ranked runはcommand起動時に1回だけ開始する。pageからranked開始を
  triggerするendpointは持たない。

timing / information boundary
-----------------------------
pageの操作は`RiichiLabLiveController`の表示cursorだけを動かし、ranked
workerへは何も送らない。表示するのはbound bot seat自身のplayer-visible
`PolicyInput`投影とArenaが報告したterminal factだけで、順位 / 役 / 翻 / 符を
推測しない。token / credential値はpayload、HTML、logのいずれにも載せない。
"""

import argparse
import json
import threading
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from lisjong_arena.riichilab.live_presentation import BoundedRankedPresentationBuffer

from lisjong_play.gui_board import GUI_RIVER_LEGEND, RIVER_ROW_SIZE
from lisjong_play.html_board import (
    BOARD_CSS,
    BOARD_MARKUP,
    BOARD_SCRIPT,
    script_safe_json,
)
from lisjong_play.riichilab_source import (
    PROFILE_NAMES,
    RiichiLabLiveController,
    RiichiLabViewState,
    RiichiLabWorkerSnapshot,
    RiichiLabWorkerStatus,
    resolve_record_path,
    run_riichilab_worker,
)
from lisjong_play.tile_images import (
    TILE_ASSET_FILENAMES,
    TileImageAssetError,
    tile_asset_traversable,
)

__all__ = [
    "CONTROL_COMMANDS",
    "DEFAULT_PORT",
    "LOOPBACK_HOST",
    "RiichiLabHtmlServer",
    "RiichiLabHtmlSession",
    "build_state_payload",
    "main",
    "render_page_html",
    "run_html_viewer",
]

#: bind先。protocol invariantとして固定し、CLI optionを設けない。
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
#: pageから受け付ける表示操作。いずれもpresentation cursorだけを動かす。
CONTROL_COMMANDS = ("pause", "step", "follow")

_PAGE_POLL_INTERVAL_MS = 250
_MAX_CONTROL_BODY_BYTES = 1024
_REQUEST_TIMEOUT_SECONDS = 10
_TILE_CACHE_CONTROL = "private, max-age=86400, immutable"

LIVE_SCOPE_NOTE = (
    "表示範囲: lisjong自身のplayer-visible state のみ"
    "（他家のconcealed handは表示しません）"
)
PAUSE_SCOPE_NOTE = (
    "一時停止は表示だけを止めます。RiichiLab ranked対局とPolicy判断は進み続けます。"
)
CLOSE_SCOPE_NOTE = (
    "browser tabを閉じても、Ctrl+Cでserverを止めてもranked対局は中断されません。"
    "presentationのみdetachし、対局はArena lifecycleに従って完走します。"
)
_HAND_CAPTION = "lisjong自身の手牌（bound bot seatのplayer-visible手牌のみ）"


def _frame_text(view: RiichiLabViewState) -> str:
    parts = [
        "追従中" if view.following else "表示停止中",
        f"受信 {view.received_frames}",
        f"保留 {view.pending_frames}",
        f"未表示 {view.skipped_frames}",
    ]
    frame = view.frame
    if frame is not None:
        parts.insert(1, f"request {frame.request_id} (#{frame.ordinal})")
        parts.insert(2, f"{frame.seat_label} {frame.action_label}")
    return " / ".join(parts)


def _status_text(view: RiichiLabViewState, worker: RiichiLabWorkerSnapshot) -> str:
    if view.failure is not None:
        return f"failure: {view.failure.failure_type}"
    if worker.error_text is not None:
        return "ranked runはエラーで終了しました。"
    if view.completion is not None:
        return "end_game: 半荘が終了しました。"
    if view.frame is not None:
        return "進行中" if view.following else "進行中 (表示停止中)"
    return "ranked接続中"


def _info_lines(view: RiichiLabViewState, worker: RiichiLabWorkerSnapshot) -> list[str]:
    lines = [LIVE_SCOPE_NOTE, PAUSE_SCOPE_NOTE, CLOSE_SCOPE_NOTE]
    if worker.runtime_summary is not None:
        lines += ["", worker.runtime_summary.rstrip()]
    if view.completion is not None:
        scores = view.completion.scores
        # Arenaが`scores`なしの`end_game`を受けた場合は推測しない。順位も出さない。
        body = (
            "未提供" if scores is None else " / ".join(str(score) for score in scores)
        )
        lines += [
            "",
            f"final scores: {body}",
            f"bound seat: {view.completion.seat_label}",
        ]
    if view.failure is not None:
        lines += ["", f"ranked run failure type: {view.failure.failure_type}"]
    if worker.error_text is not None:
        lines += ["", f"ERROR: {worker.error_text}"]
    summary = worker.summary
    if summary is not None:
        lines += [
            "",
            f"完了: seat={summary.seat_label} / requests={summary.requests}"
            f" / responses={summary.responses}",
        ]
        if summary.record_identity is not None:
            lines.append(f"record identity: {summary.record_identity}")
    return lines


def build_state_payload(
    view: RiichiLabViewState, worker: RiichiLabWorkerSnapshot
) -> dict[str, Any]:
    """表示state + worker lifecycleから、pageが描画するJSON値を作る。

    値はすべて`riichilab_source`がsecret-safeに保持しているものだけで、
    credentialやraw RiichiLab payloadを参照しない。
    """
    if not isinstance(view, RiichiLabViewState):
        raise TypeError("view must be a RiichiLabViewState")
    if not isinstance(worker, RiichiLabWorkerSnapshot):
        raise TypeError("worker must be a RiichiLabWorkerSnapshot")
    frame = view.frame
    return {
        "frame": None
        if frame is None
        else {
            "ordinal": frame.ordinal,
            "request_id": frame.request_id,
            "seat_label": frame.seat_label,
            "action_label": frame.action_label,
            "board": asdict(frame.board),
        },
        "following": view.following,
        "can_step": not view.following and view.pending_frames > 0,
        "received_frames": view.received_frames,
        "pending_frames": view.pending_frames,
        "skipped_frames": view.skipped_frames,
        "completion": None
        if view.completion is None
        else {
            "seat_label": view.completion.seat_label,
            "scores": None
            if view.completion.scores is None
            else list(view.completion.scores),
        },
        "failure_type": None if view.failure is None else view.failure.failure_type,
        "worker_running": worker.running,
        "worker_error": worker.error_text is not None,
        "status_text": _status_text(view, worker),
        "frame_text": _frame_text(view),
        "info_text": "\n".join(_info_lines(view, worker)),
    }


class RiichiLabHtmlSession:
    """1 ranked runのpresentation state。server threadだけが操作する。

    `RiichiLabLiveController`のpresentation-only semanticsをそのまま使い、
    ranked workerへはsignalを送らない。
    """

    __slots__ = ("_controller", "_status")

    def __init__(
        self, controller: RiichiLabLiveController, status: RiichiLabWorkerStatus
    ) -> None:
        if not isinstance(controller, RiichiLabLiveController):
            raise TypeError("controller must be a RiichiLabLiveController")
        if not isinstance(status, RiichiLabWorkerStatus):
            raise TypeError("status must be a RiichiLabWorkerStatus")
        self._controller = controller
        self._status = status

    def state_payload(self) -> dict[str, Any]:
        """Arena bufferをdrainしてから現在のstateを返す。pause中もdrainする。"""
        self._controller.ingest()
        return build_state_payload(self._controller.state(), self._status.snapshot())

    def control(self, command: str) -> dict[str, Any]:
        """表示cursorを1操作だけ動かし、更新後のstateを返す。

        未知commandは`ValueError`でfail closedし、stateを変えない。
        """
        if command not in CONTROL_COMMANDS:
            raise ValueError(f"unknown control command: {command!r}")
        self._controller.ingest()
        if command == "pause":
            self._controller.pause()
        elif command == "step":
            self._controller.step()
        else:
            self._controller.follow_live()
        return build_state_payload(self._controller.state(), self._status.snapshot())

    def detach(self) -> None:
        """presentation consumerの離脱をArena bufferへ伝える。対局は続く。"""
        self._controller.detach()


def render_page_html() -> str:
    """live viewer pageのHTML。stateは含まず、`/state`をpollingして描く。"""
    config = {
        "poll_interval_ms": _PAGE_POLL_INTERVAL_MS,
        "river_legend": GUI_RIVER_LEGEND,
        "river_row_size": RIVER_ROW_SIZE,
        # 画像へ解決できるlabelだけを`/tiles/`へ向け、それ以外はtext表示する。
        "tile_labels": sorted(TILE_ASSET_FILENAMES),
    }
    return (
        _HTML_TEMPLATE.replace("__BOARD__", BOARD_MARKUP)
        .replace("__HAND_CAPTION__", _HAND_CAPTION)
        .replace("__CSS__", BOARD_CSS + _CSS)
        .replace("__SCRIPT__", BOARD_SCRIPT + _SCRIPT)
        .replace("__CONFIG__", script_safe_json(config))
    )


_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


class _RequestHandler(BaseHTTPRequestHandler):
    server: "RiichiLabHtmlServer"
    timeout = _REQUEST_TIMEOUT_SECONDS

    def log_message(self, format: str, *args: Any) -> None:
        # pollingごとのaccess logでterminalを埋めない。
        return

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _reject(self, status: HTTPStatus) -> None:
        self._send(status, status.phrase.encode("ascii"), "text/plain; charset=utf-8")

    def _host_allowed(self) -> bool:
        return self.headers.get("Host") in self.server.allowed_hosts

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._reject(HTTPStatus.FORBIDDEN)
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(
                HTTPStatus.OK,
                self.server.page_html.encode("utf-8"),
                "text/html; charset=utf-8",
            )
        elif path == "/state":
            self._send_json(self.server.session.state_payload())
        elif path.startswith("/tiles/"):
            self._send_tile(unquote(path.removeprefix("/tiles/")))
        else:
            self._reject(HTTPStatus.NOT_FOUND)

    def _send_tile(self, label: str) -> None:
        # canonical labelのmapping経由でしかfileを解決しないため、pathを
        # 組み立ててfilesystemを辿ることはない。
        try:
            data = tile_asset_traversable(label).read_bytes()
        except TileImageAssetError:
            self._reject(HTTPStatus.NOT_FOUND)
            return
        # 牌画像はpackage同梱の不変assetなので、盤面を描き直すたびに
        # 再取得しないようcacheさせる。live stateは`no-store`のまま。
        self._send(HTTPStatus.OK, data, "image/png", cache_control=_TILE_CACHE_CONTROL)

    def do_POST(self) -> None:
        if not self._host_allowed():
            self._reject(HTTPStatus.FORBIDDEN)
            return
        if self.path != "/control":
            self._reject(HTTPStatus.NOT_FOUND)
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            self._reject(HTTPStatus.FORBIDDEN)
            return
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            self._reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reject(HTTPStatus.LENGTH_REQUIRED)
            return
        if length < 0 or length > _MAX_CONTROL_BODY_BYTES:
            self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            request = json.loads(self.rfile.read(length))
        except UnicodeDecodeError, json.JSONDecodeError:
            self._reject(HTTPStatus.BAD_REQUEST)
            return
        command = request.get("command") if isinstance(request, dict) else None
        if not isinstance(command, str) or command not in CONTROL_COMMANDS:
            self._reject(HTTPStatus.BAD_REQUEST)
            return
        self._send_json(self.server.session.control(command))


class RiichiLabHtmlServer(HTTPServer):
    """`127.0.0.1`だけにbindする、single-threadedなlive viewer server。"""

    def __init__(self, session: RiichiLabHtmlSession, *, port: int) -> None:
        if not isinstance(session, RiichiLabHtmlSession):
            raise TypeError("session must be a RiichiLabHtmlSession")
        if isinstance(port, bool) or not isinstance(port, int):
            raise TypeError("port must be an int")
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        self.session = session
        self.page_html = render_page_html()
        super().__init__((LOOPBACK_HOST, port), _RequestHandler)
        bound_port = self.server_address[1]
        self.allowed_hosts = frozenset(
            {f"{LOOPBACK_HOST}:{bound_port}", f"localhost:{bound_port}"}
        )
        self.allowed_origins = frozenset(
            f"http://{host}" for host in self.allowed_hosts
        )

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.server_address[1]}/"


def run_html_viewer(
    *,
    profile_name: str,
    record_path: Path | None,
    port: int,
    open_browser: bool,
    writer: Callable[[str], None],
    worker_target: Callable[..., None] = run_riichilab_worker,
    browser_open: Callable[[str], Any] = webbrowser.open,
) -> None:
    """serverをbindしてからexactly 1 ranked hanchanを開始し、Ctrl+Cまで配信する。

    bind失敗時はranked runを開始しない。Ctrl+Cはserverを止めてpresentation
    bufferを`detach()`するだけで、ranked workerへcancel / disconnectを送らない。
    workerはnon-daemonで、完走までjoinする。
    """
    buffer = BoundedRankedPresentationBuffer()
    controller = RiichiLabLiveController(buffer)
    status = RiichiLabWorkerStatus()
    session = RiichiLabHtmlSession(controller, status)
    server = RiichiLabHtmlServer(session, port=port)
    worker: threading.Thread | None = None
    try:
        # daemon threadにしない。server停止後もranked完走まで生存させる。
        worker = threading.Thread(
            target=worker_target,
            args=(buffer, status),
            kwargs={"profile_name": profile_name, "record_path": record_path},
            name="lisjong-play-riichilab-html",
            daemon=False,
        )
        worker.start()
        writer(f"RiichiLab live viewer: {server.url}  (profile={profile_name})")
        if record_path is not None:
            writer(f"durable record: {record_path}")
        writer(PAUSE_SCOPE_NOTE)
        writer(CLOSE_SCOPE_NOTE)
        writer("終了するには Ctrl+C を押してください。")
        if open_browser:
            browser_open(server.url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
        session.detach()
        if worker is not None and worker.is_alive():
            writer("serverを停止しました。ranked対局の完走を待っています...")
        if worker is not None:
            worker.join()


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("portは整数で指定してください") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("portは0から65535の範囲で指定してください")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lisjong-play-riichilab-html",
        description=(
            "RiichiLab ranked対局(1半荘)を開始し、ブラウザでlive観戦するlocal "
            "HTML viewerを127.0.0.1で配信します。profile / credential / ranked実行は"
            "lisjong-arenaのcontractを使います。"
        ),
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_NAMES,
        required=True,
        help="lisjong-arena RiichiLab execution profile (例: lisjong-dev)",
    )
    parser.add_argument(
        "--record-dir",
        default=None,
        help=(
            "指定した場合、Arenaのdurable ranked recordをこのdirectory配下へ"
            "取得します(live presentationとは独立のArena所有path)"
        ),
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        help=f"127.0.0.1上のport (既定: {DEFAULT_PORT}、0で空きportを自動選択)",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="起動後に既定のブラウザでviewerを開きます",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    writer: Callable[[str], None] = print,
    worker_target: Callable[..., None] = run_riichilab_worker,
    browser_open: Callable[[str], Any] = webbrowser.open,
) -> int:
    args = _parser().parse_args(argv)
    try:
        record_path = resolve_record_path(args.record_dir)
    except (OSError, ValueError) as error:
        writer(f"record pathを解決できません: {type(error).__name__}: {error}")
        return 1
    try:
        run_html_viewer(
            profile_name=args.profile,
            record_path=record_path,
            port=args.port,
            open_browser=args.open_browser,
            writer=writer,
            worker_target=worker_target,
            browser_open=browser_open,
        )
    except OSError as error:
        # bind失敗はranked開始前に起きる(run_html_viewerはbind後にworkerを起動する)。
        writer(f"viewer serverを起動できません: {type(error).__name__}: {error}")
        return 1
    return 0


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>lisjong RiichiLab live viewer</title>
<style>__CSS__</style>
</head>
<body>
<main>
  <header>
    <strong>lisjong RiichiLab live viewer</strong>
    <span id="status">接続中</span>
  </header>
  __BOARD__
  <nav class="controls">
    <button type="button" id="pause">⏸ 表示のみ一時停止</button>
    <button type="button" id="step" disabled>⏭ ステップ</button>
    <button type="button" id="follow" disabled>⏩ 最新へ追従</button>
    <span id="frame" class="frame"></span>
  </nav>
  <div id="legend" class="legend"></div>
  <section class="info-box">
    <div class="caption">runtime / 結果</div>
    <pre id="info"></pre>
  </section>
</main>
<script type="application/json" id="viewer-config">__CONFIG__</script>
<script>__SCRIPT__</script>
</body>
</html>
"""

_CSS = """
.controls .frame { margin-left: 12px; font-size: 13px; }
.center .waiting { font-size: 14px; }
"""

_SCRIPT = """
(() => {
  const config = JSON.parse(
    document.getElementById("viewer-config").textContent);
  const knownTiles = new Set(config.tile_labels);
  const board = createBoardRenderer(
    (label) => knownTiles.has(label)
      ? "/tiles/" + encodeURIComponent(label) : undefined,
    config.river_row_size);
  const byId = (id) => document.getElementById(id);
  let shownOrdinal = null;
  let following = true;
  let pollTimer = null;

  function apply(state) {
    // 盤面はcumulative snapshotなので、表示frameが変わったときだけ描き直す。
    if (state.frame !== null && state.frame.ordinal !== shownOrdinal) {
      board.render(state.frame.board);
      shownOrdinal = state.frame.ordinal;
    }
    following = state.following;
    byId("status").textContent = state.status_text;
    byId("frame").textContent = state.frame_text;
    byId("info").textContent = state.info_text;
    byId("pause").textContent =
      following ? "⏸ 表示のみ一時停止" : "▶ 表示を再開";
    byId("step").disabled = !state.can_step;
    byId("follow").disabled = following;
  }

  function schedule() {
    if (pollTimer !== null) clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, config.poll_interval_ms);
  }

  async function poll() {
    pollTimer = null;
    try {
      const response = await fetch("/state", { cache: "no-store" });
      if (!response.ok) throw new Error(String(response.status));
      apply(await response.json());
    } catch (error) {
      byId("status").textContent =
        "viewer serverへ接続できません（serverが停止した可能性があります）";
    }
    schedule();
  }

  async function control(command) {
    try {
      const response = await fetch("/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command }),
        cache: "no-store",
      });
      if (!response.ok) throw new Error(String(response.status));
      apply(await response.json());
    } catch (error) {
      byId("status").textContent = "表示操作を送れませんでした";
    }
  }

  byId("legend").textContent = config.river_legend;
  byId("pause").addEventListener(
    "click", () => control(following ? "pause" : "follow"));
  byId("step").addEventListener("click", () => control("step"));
  byId("follow").addEventListener("click", () => control("follow"));
  byId("center").append(
    board.el("div", "waiting", "卓情報は最初のdecision受信後に更新されます"));
  poll();
})();
"""

if __name__ == "__main__":
    raise SystemExit(main())
