# lisjong-play

Human-facing play and presentation consumer for the lisjong ecosystem.

Current surfaces:

```text
Human Play CLI      implemented
Human Play GUI      implemented
Replay Viewer       implemented
Spectator GUI       implemented
```

The three GUI surfaces are distinct presentation sources over one shared board presentation:

```text
Human Play GUI
    human seat + AI seats

Replay Viewer
    durable record -> playback

Spectator GUI
    live AI x4 -> presentation
```

Game rules, legal actions, scoring, settlement, and match progression are owned by `lisjong-engine`. `lisjong-play` presents existing owner contracts; it does not reimplement Mahjong rules.

## Requirements

Python 3.14 is required.

```powershell
python -m pip install -e ".[dev]"
python -m lisjong_arena.environment_verify --project pyproject.toml
```

## Human Play CLI

The current playable slice is **Human EAST vs one selected first-party Policy x3**, one hanchan, using `lisjong-engine` and its default `RuleSet`.

```powershell
python -m lisjong_play
python -m lisjong_play --opponent minimal
python -m lisjong_play --opponent combined
python -m lisjong_play --opponent yakuhai-call
python -m lisjong_play --opponent yakuhai-call --seed 12345
```

The opponent defaults to `minimal`. `combined` selects `GenbutsuDefenseFiniteHorizonValueAwarePolicy`; `yakuhai-call` selects `YakuhaiCallGenbutsuDefenseFiniteHorizonHandValueAwarePolicy`. Each AI seat receives an independent Policy instance through the existing first-party bridge from `lisjong-arena`.

Human decisions consume the engine's player-safe `SeatObservation` and original legal `ActionDescriptor` values. The UI never reconstructs action legality or synthesizes a replacement action.

## Human Play GUI

The Tkinter GUI exposes the same Human EAST match:

```powershell
python -m lisjong_play.gui
python -m lisjong_play.gui --opponent combined --seed 12345
lisjong-play-gui --opponent yakuhai-call --seed 12345
```

The launch screen selects the deterministic seed and opponent. The GUI presents a viewer-relative four-player table with public scores, rivers, melds, riichi state, dora indicators, round metadata, and the Human hand.

Legal discard / tsumogiri choices are selected from the displayed hand. The **操作** panel contains non-discard choices such as calls, win, riichi-stage actions, and Pass. Round results are shown before the next-round confirmation and the final ranking is shown after the hanchan.

The engine runs on a worker thread; Tk widgets are touched only by the main thread. Closing the window releases pending Human-decision or round-confirmation waits.

Current Human Play intentionally does not provide seat selection, rule selection, save/resume, multiplayer, or AI takeover.

## Replay Viewer

A completed `lisjong-arena` durable local game record can be replayed without re-running the engine:

```powershell
python -m lisjong_play.replay_gui
python -m lisjong_play.replay_gui path\to\record-bundle
lisjong-play-replay path\to\record-bundle
```

Without a path, the viewer opens a directory chooser. Records are loaded only through Arena's supported strict loader. Unsupported schema versions and corrupt / truncated / tampered bundles fail closed; opening a record never modifies it.

Navigation is over **recorded seat decisions** and includes first/previous/next, previous/next round, play/pause, and 0.5x/1x/2x/4x pacing. Speed changes presentation delay only.

Live Human Play, Replay Viewer, and Spectator GUI share the same `GuiBoardRenderer`, board layout, and tile-image infrastructure.

### Replay data boundary

The viewer displays only facts present in the durable record:

- the current decision seat's player-safe concealed hand; it does not synthesize four-seat omniscient hand history
- typed per-round facts from durable-record schema v2: round identity, scores, riichi-stick settlement, dora indicators, riichi seats, winner / win method / deal-in seat, point deltas, and draw type/reason
- backend-computed scoring details only when the record actually contains them
- final scores / ranks and record provenance

With RiichiEnv 0.4.8, backend scoring detail is available only where Arena could authoritatively capture it; missing han / fu / yaku / payment detail is shown as unavailable rather than inferred from deltas.

The record does not currently provide authoritative winning tile / winning hand / exhaustive-draw tenpai seats or a riichi-declaration river marker. The viewer does not reconstruct those values.

The Replay Viewer never recomputes legality, scoring, yaku / fu, round progression, hidden hands, shanten, or ukeire, and it never starts an engine game or Policy while replaying a record.

### Static HTML Replay

The same record can be exported as one self-contained HTML file that opens in any browser without Python, a desktop display, or network access:

```powershell
python -m lisjong_play.replay_html path\to\record-bundle -o replay.html
lisjong-play-replay-html path\to\record-bundle
```

Without `-o`, the file is written to `<record directory name>.html` in the current directory. An existing file is not replaced unless `--overwrite` is given. The page has the same navigation as the Tk viewer (plus a position slider and ←/→, PageUp/PageDown, Home, Space shortcuts) and shows exactly the facts the Tk viewer shows; a record the strict loader rejects produces no file.

## Spectator GUI

A live AI x4 hanchan can be watched without any human seat:

```powershell
python -m lisjong_play.spectator_gui
python -m lisjong_play.spectator_gui --policy combined --seed 12345
lisjong-play-spectator --policy yakuhai-call --seed 12345
```

This is a separate entry point from Human Play and Replay; the existing `lisjong-play`, `lisjong-play-gui`, and `lisjong-play-replay` commands are unchanged.

All four seats use the same selected first-party Policy through the existing `lisjong-arena` bridge, and each seat receives its own fresh Policy instance. There is no human selector and no human action panel: the shared board renderer is constructed without a selection callback, so tiles are displayed rather than clickable.

### Presentation boundary

One spectator presentation boundary is **one selector decision presentation boundary**: one decision request that the engine driver hands to one seat's selector. It is not "one action" and not "one engine revision" — in a reaction window three seats receive their own selector request against the same engine revision, and each of those is its own boundary.

Playback controls act on that boundary only:

- **一時停止 / 再開** stops and restarts progress to the next boundary. Engine state is never mutated from the Tk thread.
- **ステップ** is enabled only while paused and releases exactly one boundary.
- **速度** (0.5x / 1x / 2x / 4x) changes the delay between boundaries and nothing else. It is never passed to a Policy, and a given seed with the same deterministic Policy composition produces the same match result at any speed.

The engine and Policies run on a worker thread; Tk widgets are touched only by the main thread. Closing the window releases a worker that is paused or waiting at a boundary.

At the faster speeds a boundary can arrive sooner than a full board redraw. Each polling pass therefore draws only the newest board it has received, so the display never drifts further and further behind the match. The board is cumulative state, not a delta, so the drawn board is still correct; progress, round-result, and match-result text is kept in full and in order. While paused, a pass carries at most one new board, so Step still shows exactly the boundary it released.

### Spectator data boundary

Spectator presentation is built only from the public engine contracts already used by Human Play: each decision's player-safe `SeatObservation` plus the delivered `RoundProgressFact` / `RoundCompletionFact` / `MatchCompletionFact` values. It never reads `MatchState`, `RoundState`, physical tile identity, or wall / dead-wall state, and it never recomputes legality, scoring, progression, shanten, or ukeire.

**Concealed-hand scope: public board only.** Under the currently pinned `lisjong-engine` contract there is no engine-owned four-seat spectator projection, so the viewer shows the public board plus the concealed hand of the seat that is currently deciding — the same player-safe hand that seat's own observation carries. Opponents' concealed hands are not guessed, and several seats' observations are never merged into a synthetic omniscient state.

Round results and the final scores / ranking reuse the existing `render_round_completion` / `render_match_completion` presentation. There is no human confirmation prompt: results stay in the progress panel, and pause holds the board for as long as the spectator wants. A worker failure is reported as an error and never presented as a finished match.

The table is oriented with the fixed EAST seat at the bottom so it does not rotate every decision; the deciding seat is named in the board's decision label. Human Play and Replay orientation is unchanged.

## RiichiLab live viewer

A live RiichiLab **ranked** hanchan can be watched on the same board while `lisjong-arena` plays it.

Before an important smoke or any network run, verify that the installed internal
VCS revisions match this checkout's exact pins:

```powershell
python -m lisjong_arena.environment_verify --project pyproject.toml
```

Then start the live viewer:

```powershell
python -m lisjong_play.riichilab_gui --profile lisjong-dev
lisjong-play-riichilab --profile lisjong-dev
```

With Arena's durable ranked record acquired at the same time:

```powershell
lisjong-play-riichilab `
  --profile lisjong-dev `
  --record-dir C:\Dev\lisjong-artifacts\riichilab
```

This is a separate entry point again; `lisjong-play`, `lisjong-play-gui`, `lisjong-play-replay`, and `lisjong-play-spectator` are unchanged. The initial scope is exactly one ranked hanchan — no requeue, reconnect, multiple games, or attaching to an already-running Arena process.

The bot token is resolved by Arena from the profile's own environment variable, is handed straight to Arena, and is never displayed, logged, or persisted by this viewer.

### Live presentation boundary

The viewer consumes Arena's supported live presentation seam and never parses raw RiichiLab `request_action` JSON or base64 `Observation`. Each frame is projected from the very same `PolicyInput` that Arena gave the Policy for that decision, so the board shows lisjong's own player-visible state with the bound bot seat at the bottom.

**Pause stops the display only.** RiichiLab ranked has a server response deadline, so the viewer never gates execution:

- **表示のみ一時停止** freezes the display cursor. The ranked game, the Policy, and the WebSocket keep running, and frames keep being received while paused.
- **ステップ** is enabled only while paused and advances exactly one already-received frame. Nothing is sent to the worker.
- **最新へ追従** jumps back to the newest received frame and resumes following.

This is deliberately different from the Spectator GUI, whose pause *is* a local engine gate.

The pending-frame history has an explicit finite capacity. During a long pause the oldest undisplayed snapshots are dropped — the board is cumulative state, so the newest frame is still correct — and the number of frames received but never displayed is shown in the status line, including the frames Arena itself coalesced. Completion and failure are held outside that capacity and are never dropped.

Final scores are shown as Arena reported them. Final rank, yaku, han, fu, and the winning hand are not provided by the seam and are not inferred. A failed run is reported by exception type and never shown as a completed match.

**Closing the window does not abort the game.** Close detaches the presentation only; the ranked worker is not a daemon thread and is joined after the Tk mainloop exits, so the hanchan finishes under Arena's lifecycle instead of being disconnected mid-game.

### RiichiLab live viewer in a browser

The same live ranked hanchan can be watched in a web browser instead of a Tk window:

```powershell
python -m lisjong_play.riichilab_html --profile lisjong-dev --open-browser
lisjong-play-riichilab-html --profile lisjong-dev --port 8765
lisjong-play-riichilab-html `
  --profile lisjong-dev `
  --record-dir C:\Dev\lisjong-artifacts\riichilab
```

The command starts exactly one ranked hanchan and serves the viewer at `http://127.0.0.1:<port>/` (default port 8765; `--port 0` picks a free port). The page has no control that starts a game: the ranked run is started only by this command. The server binds to `127.0.0.1` only and has no option to listen on another interface; requests with a foreign `Host`, and control requests that are not same-origin JSON, are rejected.

The page reuses the Tk viewer's live source and the static HTML Replay's board drawing, so it shows the same facts with the same controls: **表示のみ一時停止**, **ステップ**, and **最新へ追従** move the display cursor only and never reach the ranked worker. The page polls the local server; while no browser is open, Arena's bounded buffer coalesces older snapshots and the viewer reports them as not displayed, without affecting the ranked run.

**Closing the tab or pressing Ctrl+C does not abort the game.** Ctrl+C stops the local server and detaches the presentation; the process then waits until the ranked hanchan finishes under Arena's lifecycle. After the game ends the server keeps serving the final result until Ctrl+C.

#### Continuous mode

`--continuous` runs Arena's continuous ranked runner (`run_continuous_ranked_cli`) in the same process and shows each hanchan as it starts:

```powershell
lisjong-play-riichilab-html `
  --profile lisjong-dev `
  --continuous `
  --duration-seconds 3600 `
  --record-dir C:\Dev\lisjong-artifacts\riichilab `
  --port 8765
```

- Profile / credential resolution, retries, durable records, the stdout summary, and the exit code all come from Arena. The summary lines keep the format Arena's AWS verifier parses, and the viewer prints no line that collides with them.
- Arena opens a fresh presentation buffer for every game attempt. The viewer switches to the newest game only while following. A paused display stays on its game until **最新へ追従**. The previous game's final scores or failure type stay listed. Games that were never displayed are counted.
- `--duration-seconds` and `--games` are accepted only with `--continuous`.
- When the run ends, the server closes and the command exits with Arena's exit code.
- **Ctrl+C** detaches the presentation and asks Arena to stop gracefully: the running hanchan finishes, and no new hanchan starts.

On AWS, Arena's RiichiLab launcher can run this mode on an EC2 instance that allows no inbound connections, and you can watch it from your PC through SSM port forwarding (`lisbun/lisjong-arena#381`). Use the same local and remote port number, because the viewer accepts only its own `Host`.

## Tile images

GUI tile images are vendored PNGs from [FluffyStuff/riichi-mahjong-tiles](https://github.com/FluffyStuff/riichi-mahjong-tiles) under public-domain / CC0 1.0 terms. They are bundled under `src/lisjong_play/assets/tiles/` and resolved by canonical tile label through `lisjong_play.tile_images`; no runtime network access is required.

See `src/lisjong_play/assets/tiles/THIRD_PARTY_NOTICE.md` for the exact source revision and license provenance. That provenance applies to the image assets only; this repository remains MIT-licensed.

The shared renderer uses a `BoardTileImages` bundle with separate registries for the Human hand, board-side meld / dora tiles, river tiles, and desaturated tsumogiri river tiles. The Human hand is largest because it is the click target; melds and dora indicators are smaller; river tiles are smallest so six tiles per row remain compact. Tsumogiri is shown by graying the tile face rather than by a text marker, while the CLI keeps its `*` marker.

The four seats are positioned around the measured center block rather than in stretched grid cells. Each seat keeps its name, score, and exposed melds on the outer side and faces its river toward the center; rivers grow away from the center so they do not cover round information or the Human hand. Live Human Play, Replay Viewer, Spectator GUI, and the RiichiLab live viewer share this layout and tile-image infrastructure. The static HTML Replay and the browser RiichiLab live viewer share one HTML board drawing (`lisjong_play.html_board`) that follows the same layout.

The table composition is informed by [MJX's observation visualizer](https://github.com/mjx-project/mjx/tree/master/mjx/visualizer), but no MJX code, font, or artwork is copied or bundled.

See [`docs/architecture.md`](docs/architecture.md) for responsibility and dependency boundaries.
