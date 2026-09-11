# lisjong-play

Human Play consumer for the lisjong ecosystem.

The playable slice is deliberately small: **Human EAST vs one selected first-party Policy x3**, one hanchan, using the first-party `lisjong-engine` and its default `RuleSet`.

## Run

Python 3.14 is required.

```powershell
python -m pip install -e ".[dev]"
python -m lisjong_play
python -m lisjong_play --opponent minimal
python -m lisjong_play --opponent combined
python -m lisjong_play --opponent yakuhai-call
python -m lisjong_play --opponent yakuhai-call --seed 12345
```

The opponent defaults to `minimal`, preserving the original Human EAST vs `MinimalPolicy` x3 behavior. `combined` selects `GenbutsuDefenseFiniteHorizonValueAwarePolicy`, while `yakuhai-call` selects `YakuhaiCallGenbutsuDefenseFiniteHorizonHandValueAwarePolicy`, for all three AI seats. Each AI seat receives an independent Policy instance and runs through the existing first-party bridge from `lisjong-arena`.

The default match seed is `0`. To replay another deterministic match with the default opponent:

```powershell
python -m lisjong_play --seed 12345
```

## GUI prototype

An optional Tkinter desktop prototype exposes the same Human EAST match without changing the CLI:

```powershell
python -m lisjong_play.gui
python -m lisjong_play.gui --opponent combined --seed 12345
lisjong-play-gui --opponent yakuhai-call --seed 12345
```

The launch screen lets you change the deterministic seed and select any opponent supported by the CLI. It presents Human EAST at the bottom of a viewer-relative four-player table, with public scores, rivers, melds, riichi state, dora indicators, round metadata, and the Human hand, all rendered with tile images (see [Tile images](#tile-images) below). Legal discard / tsumogiri choices are selected directly by clicking the displayed hand tile images rather than duplicated as separate buttons; the **操作** panel holds only non-discard controls (reactions / calls / win / riichi-stage actions / Pass), and shows a short instruction when a decision has no non-discard controls. Remaining action panel controls still wrap into bounded rows instead of overflowing. A sole Pass is selected automatically because there is no Human choice to make; Pass remains explicit whenever another legal action is available. Each round result is retained in the log and shown in a **局結果** dialog before the next-round confirmation; the final ranking is retained and shown separately in a **半荘結果** dialog.

This is deliberately a functional prototype: it does not yet include animation, sound, replay, save/resume, or seat/rule selection. Tkinter must be available in the Python 3.14 installation; the standard Windows installer normally includes it. The engine runs on a worker thread and all Tk operations remain on the GUI main thread, so closing the window also releases a pending Human decision or round confirmation.

## 牌譜Replay Viewer

A third surface replays a **completed `lisjong-arena` durable local game record** in the same Tkinter board, without re-running the engine:

```powershell
python -m lisjong_play.replay_gui
python -m lisjong_play.replay_gui path\to\record-bundle
lisjong-play-replay path\to\record-bundle
```

Without a path the viewer opens a directory chooser. The record is read only through `lisjong-arena`'s supported strict loader, so an unsupported schema version or a corrupt / truncated / tampered bundle is reported as a human-readable error and never shown as a partial replay. Opening a record never modifies it.

Navigation moves over **recorded seat decisions**: `|< 先頭`, `< 前へ`, `次へ >`, plus `<< 前局` / `次局 >>` for recorded round boundaries (`<< 前局` returns to the current round's start first, then steps back a round). `▶ 再生` / `⏸ 一時停止` drive auto-play and the 速度 selector switches between 0.5x, 1x, 2x and 4x. Speed only changes the wait between frames; it never changes which frames are visited. Playback stops at the last recorded frame, never moves past either boundary, and the timer is released on pause, on manual navigation, and on window close.

The board, seat labels, scores, rivers, melds, riichi state, dora indicators, and round metadata are rendered by the same board renderer and the same tile images as live Human Play. Round results, the final ranking, and the record's own identity / seed / game mode / Policy assignment are shown in the panel below the controls.

The replay is deliberately limited to what the record actually contains:

- Only the seat that owns the current decision has its concealed hand displayed. The durable record guarantees a player-safe own hand per decision seat and no authoritative four-seat concealed-hand history, so other seats' hands are not shown and are never reconstructed.
- Round results come from the record's typed per-round facts (schema v2): round identity, starting and ending scores, riichi-stick settlement, dora indicators, riichi seats, winner / tsumo-ron / deal-in seat, point deltas, and the draw reason with its exhaustive-vs-abortive distinction.
- Backend-computed scoring (han, fu, yaku, payments, pao) is shown **only for rounds where the record actually carries it**. RiichiEnv 0.4.8 exposes it for the final round of a game only, so other rounds show `得点内訳: 記録なし` instead of a value derived from the point deltas.
- Ura indicators are displayed only when the winning seat is one of the record's riichi seats, because the record carries the backend's ura markers on every win regardless of whether riichi was declared.
- The winning tile, the winning hand, and exhaustive-draw tenpai seats are **not** in the record, so they are not displayed rather than recomputed.
- Recorded discards carry no riichi-declaration marker, so the river does not mark one.

The viewer never recomputes legality, call priority, scoring, yaku / fu, round progression, hidden hands, or shanten / ukeire, and it never starts an engine game or a Policy during replay.

### Tile images

The GUI's tile images are vendored PNGs from [FluffyStuff/riichi-mahjong-tiles](https://github.com/FluffyStuff/riichi-mahjong-tiles) (public domain / CC0 1.0), bundled as package resources under `src/lisjong_play/assets/tiles/` and looked up by canonical tile label through `lisjong_play.tile_images`. Lookup does not depend on the process working directory, and no network access is required at runtime. See `src/lisjong_play/assets/tiles/THIRD_PARTY_NOTICE.md` for the exact source revision and license provenance; that CC0 provenance applies only to the vendored image assets and is separate from this repository's own MIT license.

The same assets are displayed at two sizes, largest where the player acts: hand and drawn tiles are the biggest because they are the click targets, while river tiles, melds, and dora indicators share one smaller table size, so a full river stays on the table without pushing the hand area off screen. Tsumogiri discards are drawn from a fourth registry holding river-size images whose white tile face is replaced with gray, the glyph colors left untouched, so a tsumogiri stands out in the river without a text marker and without becoming unreadable. Each size has its own `TileImageRegistry`, so a tile label resolves to one cached image object per size and the Tk `PhotoImage` references stay alive for the application lifetime. Live Human Play and the Replay Viewer share this behavior through the same board renderer.

The four seats are placed around the center of the felt rather than in stretched grid cells: each seat keeps its name / score text and its exposed melds on its outer side and turns only its river toward the middle, so the four rivers frame the round information the way an online mahjong table does. A river grows away from the center as it lengthens, so it never overruns the center block or the hand area below the table. Multiple melds on one seat stack vertically beside the river instead of widening the seat, each labelled only with its call type and the seat it was called from, because the called tile is already visible among the exposed tiles. River tiles are spaced a pixel apart and the river is set apart from the melds, while the tiles inside one call stay flush. Seats are positioned from the center block's measured size on every render, so a longer round line or a different font never pushes the round information under a river. The river legend sits in an unused corner of the felt rather than taking its own row.

The table composition is informed by [MJX's observation visualizer](https://github.com/mjx-project/mjx/tree/master/mjx/visualizer), but no MJX code, font, or artwork is copied or bundled.

Human decisions use the engine's player-safe `SeatObservation` and original legal `ActionDescriptor` values directly.

For an ordinary turn where every legal action is a discard, the hand itself becomes the compact selection UI. Legal hand-discard choices are numbered under the corresponding tiles. When an explicit `DiscardActionDescriptor(is_tsumogiri=True)` is present, the drawn tile is separated to the right and `Enter` selects tsumogiri rather than exposing it as another numbered alias.

Reaction decisions use a compact board. When `PassActionDescriptor` is legal, pass is the `Enter` default and only non-pass actions receive menu numbers. A pass-only reaction still waits for Human input. Likewise, an explicit tsumogiri-only decision still waits for `Enter`; it is never auto-selected.

Non-numeric, zero, negative, and out-of-range input is retried. The selected value returned to the engine is always one of the original `ActionDescriptor` instances supplied by the engine; the CLI does not reconstruct legality or synthesize actions.

After each non-terminal round, the round result and updated scores are shown before the CLI waits for `Enter` to proceed. The terminal round proceeds directly to the player-safe final score/rank display. `Ctrl+C` is handled only at the top-level CLI boundary.

See `docs/architecture.md` for the responsibility and dependency boundary.
