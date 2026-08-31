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

Human decisions use the engine's player-safe `SeatObservation` and original legal `ActionDescriptor` values directly.

For an ordinary turn where every legal action is a discard, the hand itself becomes the compact selection UI. Legal hand-discard choices are numbered under the corresponding tiles. When an explicit `DiscardActionDescriptor(is_tsumogiri=True)` is present, the drawn tile is separated to the right and `Enter` selects tsumogiri rather than exposing it as another numbered alias.

Reaction decisions use a compact board. When `PassActionDescriptor` is legal, pass is the `Enter` default and only non-pass actions receive menu numbers. A pass-only reaction still waits for Human input. Likewise, an explicit tsumogiri-only decision still waits for `Enter`; it is never auto-selected.

Non-numeric, zero, negative, and out-of-range input is retried. The selected value returned to the engine is always one of the original `ActionDescriptor` instances supplied by the engine; the CLI does not reconstruct legality or synthesize actions.

After each non-terminal round, the round result and updated scores are shown before the CLI waits for `Enter` to proceed. The terminal round proceeds directly to the player-safe final score/rank display. `Ctrl+C` is handled only at the top-level CLI boundary.

See `docs/architecture.md` for the responsibility and dependency boundary.
