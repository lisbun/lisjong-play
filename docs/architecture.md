# Architecture

`lisjong-play` is the Human Play consumer for the lisjong ecosystem.

## Responsibility

`lisjong-play` owns human-facing presentation, input, action-selection UX, confirmation / interaction, CLI / GUI presentation, human seat assignment, and the minimum session orchestration needed to play through `lisjong-engine`.

Game / round / turn state, legal actions, reaction priority, scoring / settlement, round / match progression, and terminal conditions remain owned by `lisjong-engine`.

CLI and GUI Human decisions use the engine public boundary directly:

```text
SeatObservation
+
tuple[ActionDescriptor, ...]
        |
        v
Human UI selector
        |
        v
selected ActionDescriptor
```

The Tkinter prototype preserves the synchronous engine contract with one narrow thread bridge:

```text
Tk main thread                         engine worker
---------------                        -------------
render GuiBoardView  <--- request ---  SeatObservation + options
button selection      --- reply ---->  original ActionDescriptor
round result          <--- delivery -- RoundCompletionFact
next-round button     --- confirm ---> callback returns
```

Only the main thread touches Tk widgets. The worker blocks at the existing Human selector and round-completion boundaries, and window close releases either wait. The GUI view model is derived only from `SeatObservation`; progress and results reuse the same player-safe delivery facts and pure renderers as the CLI. The bridge is a concrete Tk prototype boundary, not a generic asynchronous frontend protocol.

Human choice does not pass through `PolicyInput`, `DecisionContext`, `InternalAction`, or `execute_policy()`.

Round completion presentation consumes `lisjong-engine.round_completion.RoundCompletionFact` as the player-safe authority. Win result rendering may display the projected winner hand, winning tile, yaku, han / fu or yakuman units, dora counts, revealed indicator tiles, base hand payments, settlement transfers, and riichi-stick awards. `lisjong-play` does not read `CompletedRound` / `RoundState`, recalculate scoring or dora, infer ura disclosure, or choose an arbitrary representative from equal maximum-score interpretations.

Live Human presentation and post-session history use separate engine-owned player-safe contracts:

```text
live Human presentation
    <- RoundProgressFact / completion delivery

same-process Human round history
    <- Human EAST RoundEvidence
```

The opt-in session history narrows each `RoundEvidenceCompletion` to the Human EAST projection as soon as it is delivered, retains engine-provided round identity and evidence order, and becomes available only after a successful hanchan return. It is an in-memory read-oriented boundary, not a decision trace, AI analysis, persisted replay, or generic replay system.

AI seats use real `lisjong.Policy` implementations. The CLI explicitly supports `minimal`, `combined`, and `yakuhai-call`; all three AI seats use the selected type with an independent Policy instance per seat. The implementation reuses the existing first-party Policy bridge from `lisjong-arena`; bridge conversion / mapping semantics are not copied into this repository.

## Presentation surfaces

```text
live Human Play       CLI / Tkinter GUI            (implemented)
live AI Spectator     lisjong-play #25             (not implemented yet)
persisted Replay      lisjong-play #26             (this Issue)
raw durable record    lisjong-arena #155 / COMPLETE
                      lisjong-arena #207 / schema v2 round-result facts
```

The Replay Viewer is a **consumer** of the `lisjong-arena` durable local game record schema v2. `lisjong-play` is not the owner of that schema, does not fork its raw format, and does not parse the bundle files itself. The only record entry point is Arena's supported strict loader `lisjong_arena.durable_local_game_record.load_local_game_record()`:

```text
durable record bundle
        v
Arena strict loader (schema / digest / identity / completion)
        v
ReplayTimeline        pure, Tk-free presentation source
        v
ReplayController      navigation source of truth
        v
GuiBoardRenderer      shared with live Human Play
        v
Tk board / tile image renderer
```

A loader rejection (unsupported schema version, corrupt / truncated / tampered payload, digest mismatch) fails closed as `ReplayLoadError`. A rejected record is never shown as a partial replay, and opening a record never writes to it.

### Replay boundary semantics

The presentation step is **one recorded seat decision**. Both the board and the round identity come from that decision's recorded `PolicyInput`, so no navigation unit is invented on top of the record.

Round boundaries come from the recorded round identity (`round_wind` / `hand_number` / `honba` / `dealer_seat`), and round results come from the record's typed `LocalGameInspection.round_results`. RiichiEnv can emit a previous round's `hora` / `ryukyoku` and the next round's `start_kyoku` inside a single environment step, so neither boundary is derived from step ordinals or step/event intervals. Arena captures those round facts at execution time and binds them to the objective `GameTrace` inside its own strict loader; `lisjong-play` no longer re-parses MJAI events to rebuild round results. The decision-derived round sequence and the recorded round-result sequence are still cross-checked here, and fail closed when they disagree.

Backward navigation moves the recorded frame index; the engine is never run in reverse, and no engine or Policy execution is started at any point during replay. Tk widget state is never the source of truth: `ReplayController` holds the cursor, playback flag, and speed, and the auto-play timer is a Tk main-thread `after` job that is cancelled on pause, on manual navigation, and on window close.

### What the Replay Viewer does not recompute

Legality, call priority, scoring, yaku / fu, riichi settlement, round progression, hidden-hand inference, and shanten / ukeire are never recomputed in the viewer. Presentation is built only from recorded values.

Concealed hands are shown **only for the seat that owns the decision**, taken from that decision's player-safe recorded `PolicyInput.own_hand`. The durable record guarantees a player-safe own hand per decision seat and no authoritative four-seat concealed-hand projection over time, so multiple seats' `PolicyInput` values are never merged into a synthetic omniscient state and other seats' hands are not displayed. Recorded discards carry no riichi-declaration marker, so the river shows none rather than guessing one.

Round result presentation is a bounded projection of the record's typed per-round facts: round identity, start and end scores, riichi-stick settlement, dora indicators, riichi seats, winner, win method, deal-in seat, point deltas, ura indicators, and the draw reason with its exhaustive-vs-abortive distinction.

Backend-computed scoring (`han` / `fu` / `yaku` / payments / pao) is presented verbatim when the record carries it and reported as unavailable when it does not. Arena #207 established that RiichiEnv 0.4.8 replaces `env.win_results` before an `env.step()` returns for every non-final round, so that scoring is capturable only for a game's final round. The viewer never derives the missing values from point deltas or from other rounds. Ura indicators are gated on the recorded riichi seats, since the backend emits ura markers on every win regardless of riichi.

The winning tile, the winning hand, and exhaustive-draw tenpai seats remain absent from the record. Displaying them would require reimplementing scoring and tenpai evaluation in the viewer, which this repository does not do; that remains an open RiichiEnv-side prerequisite tracked on `lisjong-arena#207`.

The final ranking comes from the recorded `LocalGameResult` scores and ranks.

## Initial dependency direction

```text
lisjong-play
    |---> lisjong-engine
    |---> lisjong
    `---> lisjong-arena   # first-party Policy bridge reuse
```

`lisjong-engine` must not depend on `lisjong` or `lisjong-play`.

The direct `lisjong-arena` dependency now carries two concrete reuse decisions: the first-party Policy bridge for live AI seats, and the durable local game record strict loader for the Replay Viewer. It is still not a generic runtime architecture commitment. Re-evaluate extraction only when another concrete non-Arena consumer needs the same bridge, the dependency footprint becomes an actual maintenance/deployment problem, or the bridge needs an independent release lifecycle.

## Human Play vertical slices

```text
Human EAST
+
MinimalPolicy x 3 (default)
or GenbutsuDefenseFiniteHorizonValueAwarePolicy x 3
or YakuhaiCallGenbutsuDefenseFiniteHorizonHandValueAwarePolicy x 3
        |
        v
lisjong-engine
        |
        v
one hanchan completion
```

The original CLI remains the stable minimum slice. A dedicated Tkinter Issue adds an optional desktop GUI prototype over the same Human EAST / selected Policy x3 composition, using a viewer-relative table; MJX's observation visualizer informed that presentation approach, but no MJX code, font, artwork, proto, or state model is included. A later Issue replaced the GUI's text-based tile display with vendored FluffyStuff/riichi-mahjong-tiles (public domain / CC0 1.0) PNG images, resolved by canonical tile label through a small `lisjong_play.tile_images` registry; see `src/lisjong_play/assets/tiles/THIRD_PARTY_NOTICE.md` for the pinned source revision and license provenance, which is separate from this repository's own MIT license. Hand, drawn-tile, river, meld, and dora-indicator presentation semantics (concealed/drawn separation, legal-discard click-to-select, tsumogiri / riichi-declaration / called-discard river markers) are unchanged by the switch to images.

Both live slices intentionally exclude seat selection, per-seat or arbitrary Policy selection, rule selection, TUI/Web UI, save/resume, multiplayer, timeout recovery, and AI takeover. The dedicated same-process Human EAST history boundary above does not add replay persistence or reconstruction, and the live GUI does not require a canonical record schema.

The Replay Viewer added by Issue #26 is a separate read-only surface over an already-persisted Arena record. It deliberately does not add a generic replay engine, a project-wide canonical `GameRecord`, a viewer-side mahjong rule engine, a record editor or repair path, a record library / database, arbitrary timeline scrubbing, or AI-reasoning visualization. Recorded step ordinal and decision seat are preserved on every frame so a later Issue can link an analysis result to the matching recorded decision without changing this boundary.
