# lisjong-play

Human Play consumer for the lisjong ecosystem.

The initial vertical slice is deliberately small: **Human EAST vs `MinimalPolicy` x3**, one hanchan, using the first-party `lisjong-engine` and its default `RuleSet`.

## Run

Python 3.14 is required.

```powershell
python -m pip install -e ".[dev]"
python -m lisjong_play
```

The default match seed is `0`. To replay another deterministic match:

```powershell
python -m lisjong_play --seed 12345
```

At every Human decision the CLI shows the current player-safe board and all legal `ActionDescriptor` options. Choose a 1-based menu number. When a reaction includes pass, `Enter` means pass; when an explicit tsumogiri discard is legal, `Enter` means tsumogiri. These are input shortcuts only—the selected value returned to the engine is always the original `ActionDescriptor`.

After each non-terminal round, the round result and updated scores are shown before the CLI waits for `Enter` to proceed. The terminal round proceeds directly to the player-safe final score/rank display. `Ctrl+C` is handled only at the top-level CLI boundary.

See `docs/architecture.md` for the responsibility and dependency boundary.
