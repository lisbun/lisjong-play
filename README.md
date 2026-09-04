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

The launch screen lets you change the deterministic seed and select any opponent supported by the CLI. It presents Human EAST at the bottom of a viewer-relative four-player table, with public scores, rivers, melds, riichi state, dora indicators, round metadata, and the Human hand. Discards and other legal actions are selected with buttons. Round completion pauses at a **次局へ** button, while the terminal round proceeds to the final ranking.

This is deliberately a functional prototype: it uses simple text tiles and does not yet include tile artwork, animation, sound, replay, save/resume, or seat/rule selection. Tkinter must be available in the Python 3.14 installation; the standard Windows installer normally includes it. The engine runs on a worker thread and all Tk operations remain on the GUI main thread, so closing the window also releases a pending Human decision or round confirmation.

The table composition is informed by [MJX's observation visualizer](https://github.com/mjx-project/mjx/tree/master/mjx/visualizer), but no MJX code, font, or artwork is copied or bundled.

Human decisions use the engine's player-safe `SeatObservation` and original legal `ActionDescriptor` values directly.

For an ordinary turn where every legal action is a discard, the hand itself becomes the compact selection UI. Legal hand-discard choices are numbered under the corresponding tiles. When an explicit `DiscardActionDescriptor(is_tsumogiri=True)` is present, the drawn tile is separated to the right and `Enter` selects tsumogiri rather than exposing it as another numbered alias.

Reaction decisions use a compact board. When `PassActionDescriptor` is legal, pass is the `Enter` default and only non-pass actions receive menu numbers. A pass-only reaction still waits for Human input. Likewise, an explicit tsumogiri-only decision still waits for `Enter`; it is never auto-selected.

Non-numeric, zero, negative, and out-of-range input is retried. The selected value returned to the engine is always one of the original `ActionDescriptor` instances supplied by the engine; the CLI does not reconstruct legality or synthesize actions.

After each non-terminal round, the round result and updated scores are shown before the CLI waits for `Enter` to proceed. The terminal round proceeds directly to the player-safe final score/rank display. `Ctrl+C` is handled only at the top-level CLI boundary.

See `docs/architecture.md` for the responsibility and dependency boundary.
