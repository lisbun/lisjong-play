# lisjong-play

Human-facing play and presentation consumer for the lisjong ecosystem.

Current surfaces:

```text
Human Play CLI      implemented
Human Play GUI      implemented
Replay Viewer       implemented
AI Spectator        planned (#25)
```

Game rules, legal actions, scoring, settlement, and match progression are owned by `lisjong-engine`. `lisjong-play` presents existing owner contracts; it does not reimplement Mahjong rules.

## Requirements

Python 3.14 is required.

```powershell
python -m pip install -e ".[dev]"
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

Live Human Play and Replay Viewer share the same `GuiBoardRenderer`, board layout, and tile-image infrastructure.

### Replay data boundary

The viewer displays only facts present in the durable record:

- the current decision seat's player-safe concealed hand; it does not synthesize four-seat omniscient hand history
- typed per-round facts from durable-record schema v2: round identity, scores, riichi-stick settlement, dora indicators, riichi seats, winner / win method / deal-in seat, point deltas, and draw type/reason
- backend-computed scoring details only when the record actually contains them
- final scores / ranks and record provenance

With RiichiEnv 0.4.8, backend scoring detail is available only where Arena could authoritatively capture it; missing han / fu / yaku / payment detail is shown as unavailable rather than inferred from deltas.

The record does not currently provide authoritative winning tile / winning hand / exhaustive-draw tenpai seats or a riichi-declaration river marker. The viewer does not reconstruct those values.

The Replay Viewer never recomputes legality, scoring, yaku / fu, round progression, hidden hands, shanten, or ukeire, and it never starts an engine game or Policy while replaying a record.

## Tile images

GUI tile images are vendored PNGs from [FluffyStuff/riichi-mahjong-tiles](https://github.com/FluffyStuff/riichi-mahjong-tiles) under public-domain / CC0 1.0 terms. They are bundled under `src/lisjong_play/assets/tiles/` and resolved by canonical tile label through `lisjong_play.tile_images`; no runtime network access is required.

See `src/lisjong_play/assets/tiles/THIRD_PARTY_NOTICE.md` for the exact source revision and license provenance. That provenance applies to the image assets only; this repository remains MIT-licensed.

The shared renderer uses a `BoardTileImages` bundle with separate registries for the Human hand, board-side meld / dora tiles, river tiles, and desaturated tsumogiri river tiles. The Human hand is largest because it is the click target; melds and dora indicators are smaller; river tiles are smallest so six tiles per row remain compact. Tsumogiri is shown by graying the tile face rather than by a text marker, while the CLI keeps its `*` marker.

The four seats are positioned around the measured center block rather than in stretched grid cells. Each seat keeps its name, score, and exposed melds on the outer side and faces its river toward the center; rivers grow away from the center so they do not cover round information or the Human hand. Live Human Play and Replay Viewer share this layout and tile-image infrastructure.

The table composition is informed by [MJX's observation visualizer](https://github.com/mjx-project/mjx/tree/master/mjx/visualizer), but no MJX code, font, or artwork is copied or bundled.

## Planned presentation work

- [Issue #25](https://github.com/lisbun/lisjong-play/issues/25): AI x4 live Spectator mode using the existing shared presentation boundary

These Issues own future work. This README describes implemented behavior only.

See [`docs/architecture.md`](docs/architecture.md) for responsibility and dependency boundaries.
