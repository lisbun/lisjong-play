# Architecture

`lisjong-play` is the human-facing play and presentation consumer for the lisjong ecosystem.

This document describes **current responsibility and stable presentation boundaries**. Historical implementation sequence belongs in GitHub Issues / PRs rather than being repeated here.

## Responsibility

`lisjong-play` owns:

- Human-facing CLI / GUI presentation
- Human input and action-selection UX
- presentation-only confirmation / navigation
- the minimum session orchestration required by its concrete consumers
- Replay Viewer presentation over supported Arena records
- live AI-only Spectator presentation over public engine contracts

It does not own:

- Mahjong rules, legality, scoring, settlement, or match progression
- AI Policy semantics
- Arena record schemas
- training / evaluation semantics

Owner relationships:

```text
lisjong-engine
    game / round / turn truth
    legal ActionDescriptor values
    scoring / settlement / progression

lisjong
    AI Policy semantics

lisjong-arena
    first-party Policy bridge
    durable local game record + strict loader

lisjong-play
    Human interaction / presentation
    replay presentation
    live spectator presentation
```

## Current presentation surfaces

```text
Human Play CLI       implemented
Human Play GUI       implemented
Replay Viewer        implemented
Spectator GUI        implemented
```

The three GUI surfaces are distinct presentation **sources** over one shared board presentation:

```text
Human Play GUI
    human seat + AI seats

Replay Viewer
    durable record -> playback

Spectator GUI
    live AI x4 -> presentation
```

They share presentation code where the required facts are equivalent. The existence of multiple consumers does **not** imply a project-wide canonical viewer state, event bus, or generic frontend framework.

## Human decision boundary

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
original selected ActionDescriptor
```

Human choice does not pass through `PolicyInput`, `DecisionContext`, `InternalAction`, or `execute_policy()`.

The UI does not recalculate legal actions or construct replacement actions. The selected result is one of the engine-provided `ActionDescriptor` instances.

### Tk thread bridge

The Human GUI preserves the synchronous engine contract with a narrow thread bridge:

```text
Tk main thread                         engine worker
---------------                        -------------
render GuiBoardView  <--- request ---  SeatObservation + options
button selection      --- reply ---->  original ActionDescriptor
round result          <--- delivery -- RoundCompletionFact
next-round button     --- confirm ---> callback returns
```

Only the main thread touches Tk widgets. Window close releases a pending Human decision or round confirmation. This is a concrete Tk boundary, not a generic asynchronous frontend protocol.

### Human result presentation

Round completion presentation consumes `lisjong-engine.round_completion.RoundCompletionFact` as the player-safe authority. `lisjong-play` does not inspect private `CompletedRound` / `RoundState`, recalculate scoring or dora, infer hidden information, or choose among scoring interpretations.

Same-process Human round history remains a separate player-safe engine-owned evidence boundary; it is not a persisted replay format or AI decision trace.

## AI seat boundary

AI seats use real `lisjong.Policy` implementations. Each AI seat receives an independent Policy instance. The current implementation reuses the first-party Policy bridge from `lisjong-arena`; conversion / mapping semantics are not copied into `lisjong-play`.

Current Human Play exposes `minimal`, `combined`, and `yakuhai-call` opponent selections. Policy strength or promotion status remains owned outside this repository.

## Shared board presentation

`GuiBoardRenderer` is the shared board-rendering boundary for all three sources.

```text
Human live source ───┐
Replay source ───────┼─> GuiBoardRenderer -> Tk board / tile images
Spectator source ────┘
```

Every source produces the same `GuiBoardView`; the renderer does not know which source produced it. Rivers, melds, riichi state, scores, round metadata, and dora indicators are drawn once, in one place.

`build_gui_board_view()` takes an optional `orientation_seat`, which chooses only which seat sits at the table's `bottom` position. Human Play and Replay leave it unset and keep their existing viewer-relative orientation. It does not change which concealed hand the view carries.

Do not add a generic `ViewerState` abstraction merely because three sources exist. Extract only concrete common presentation semantics demonstrated by real consumers.

## Replay Viewer boundary

Replay Viewer is a read-only consumer of the `lisjong-arena` durable local game record schema v2. `lisjong-play` does not own or fork the raw record format.

The only record entry point is Arena's supported strict loader:

```text
durable record bundle
        v
lisjong_arena.durable_local_game_record.load_local_game_record()
        v
ReplayTimeline        pure / Tk-free presentation source
        v
ReplayController      navigation source of truth
        v
GuiBoardRenderer
        v
Tk presentation
```

Unsupported schema versions and corrupt / truncated / tampered bundles fail closed as replay-load errors. A rejected record is never presented as a partial success, and opening a record is read-only.

### Replay navigation semantics

One replay presentation step is **one recorded seat decision**. Board state and round identity come from that decision's recorded `PolicyInput`.

Round boundaries use recorded round identity, while round results use the record's typed per-round result facts. They are not inferred from one-environment-step assumptions, event spacing, or point-delta patterns.

`ReplayController` owns cursor, playback state, and speed. Tk widget state is not the source of truth. Playback pacing changes presentation delay only; it never changes the visited decision sequence.

### Replay information boundary

The viewer presents recorded facts only.

Allowed examples:

- current decision seat's recorded player-safe `own_hand`
- public scores / rivers / melds / riichi / dora / round metadata
- typed recorded round-result facts
- final result and provenance

Not reconstructed:

- other seats' concealed hands
- missing winning hand / winning tile
- missing exhaustive-draw tenpai seats
- missing riichi-declaration river marker
- missing scoring detail for rounds where the backend did not expose it in time

Multiple seats' `PolicyInput` values must not be merged into a synthetic omniscient state.

Replay presentation never recomputes legality, call priority, yaku / fu, scoring, settlement, round progression, shanten, ukeire, or hidden information.

RiichiEnv 0.4.8 can replace backend result state before a non-final round's enclosing `env.step()` returns. Arena schema v2 therefore stores the strongest authoritative per-round facts that can be captured without replay-side reconstruction. Missing backend facts remain explicitly unavailable; Arena #207 is historical completion evidence for that contract, not an open prerequisite.

## Dependency direction

```text
lisjong-play
    |---> lisjong-engine
    |---> lisjong
    `---> lisjong-arena
```

`lisjong-engine` must not depend on `lisjong` or `lisjong-play`.

The direct `lisjong-arena` dependency currently has two concrete reasons:

1. first-party Policy bridge reuse for live AI seats
2. durable local game record strict loader for Replay Viewer

This is not a commitment to a generic Arena-owned runtime. Reconsider extraction only if another concrete non-Arena consumer needs the same boundary, dependency footprint becomes a demonstrated maintenance problem, or an independent release lifecycle is required.

## Tile presentation

Tile artwork is provided by the vendored FluffyStuff/riichi-mahjong-tiles assets; exact provenance is recorded in `src/lisjong_play/assets/tiles/THIRD_PARTY_NOTICE.md`.

The shared renderer receives a `BoardTileImages` bundle with separate registries for Human-hand tiles, board-side meld / dora tiles, river tiles, and desaturated tsumogiri river tiles. Hand tiles are the largest because they are interactive; board-side tiles are smaller; river tiles are smallest to preserve the six-tiles-per-row layout. Tsumogiri is a presentation-only grayscale treatment in the GUI, while the CLI retains its `*` text marker.

Seats are positioned around the measured center block. Name / score text and exposed melds stay on the outer side of each seat, while the river faces the center and grows outward. The renderer derives the center clearance from the current center block dimensions and keeps extra horizontal clearance for the left and right seats, so variable text width does not crowd the side rivers. This layout is shared by live Human Play and Replay Viewer and does not change engine or replay semantics.

## Spectator boundary

Spectator GUI is a live AI-only presentation source. All four seats are real `lisjong.Policy` seats built through the existing `lisjong-arena` bridge, each with its own fresh Policy instance; there is no human selector and no human action-selection UI.

```text
AI Policy x4 (PolicySeatSelector, one fresh instance per seat)
        v
first-party lisjong-engine execution (run_hanchan)
        v
SpectatorSeatSelector      presentation boundary + pacing gate
        v
SpectatorSessionBridge     Tk-free event queue
        v
GuiBoardRenderer
        v
Tk presentation
```

Arena durable-record persistence is not a prerequisite; Spectator writes no record.

### Spectator presentation boundary

One spectator presentation boundary is **one selector decision presentation boundary**: one decision request that the engine driver hands to one seat's selector.

It is deliberately not described as "one action" and not equated with one engine revision. In a reaction window the driver asks three seats for a choice against a single shared revision, so that window produces three boundaries, not one. Round and match completion facts are presented as they are delivered and are not step units.

`SpectatorControl` owns pause / step / speed for that boundary:

```text
present board  ->  gate (pause / step / pacing)  ->  Policy decides
```

- pause stops progress to the next boundary; it never mutates engine state
- step is valid only while paused and releases exactly one boundary
- speed scales the between-boundary delay only, and is never passed into Policy input
- close releases a worker that is paused or waiting, so the window can always be closed

The gate is a single `threading.Condition` touched by the worker (wait) and the Tk main thread (pause / resume / step / speed / close), so there is no lock ordering that can deadlock. Recorded replay navigation stays in `ReplayController`; live execution and recorded playback are not forced into one controller.

Because a board redraw can cost more than a boundary interval at the faster speeds, each Tk polling pass renders only the newest `SpectatorDecisionPresented` it drained. That bounds display lag without changing execution: the board is cumulative state rather than a delta, ordered progress / round / match text is never dropped, and a paused pass carries at most one new board so Step semantics are unaffected.

### Spectator information boundary

Spectator reads only the public engine contracts Human Play already uses: the per-decision player-safe `SeatObservation`, and the delivered `RoundProgressFact` / `RoundCompletionFact` / `MatchCompletionFact`. It does not read `MatchState`, `RoundState`, physical tile identity, or wall / dead-wall state, and it does not recompute legality, call priority, scoring, settlement, progression, shanten, or ukeire.

Concealed-hand scope is **public board only**: the board plus the currently deciding seat's own player-safe hand. The currently pinned `lisjong-engine` exposes no engine-owned four-seat spectator projection, and this repository does not substitute one. Opponents' concealed hands are not inferred, and multiple seats' observations are never merged into a synthetic omniscient state. Widening this scope requires an explicit engine-owned spectator-safe projection, not a consumer-side reconstruction.

Information flow stays one-way: nothing presented to the spectator is fed back into Policy-visible input, and no AI reasoning, HandBelief ground truth, or evaluation artifact is produced here.

### Spectator result presentation

Round results and the final scores / ranking reuse `render_round_completion()` and `render_match_completion()`. Spectator does not imitate Human confirmation semantics: results are appended to the progress panel and the spectator uses pause or the boundary pacing to dwell on them. Human Play's next-round confirmation is unchanged. A worker exception is surfaced as an explicit failure and never presented as a completed match.

## Non-goals of the current architecture

- project-wide canonical `GameRecord`
- generic frontend/viewer framework
- viewer-side Mahjong rule engine
- record editor / repair path
- database / record library
- arbitrary omniscient replay reconstruction
- AI reasoning / logits / Q-value visualization
- rule selection, multiplayer, save/resume, or AI takeover as implied current features
- per-seat arbitrary Policy league editor
- durable persistence or Arena evaluation from the live Spectator source

Concrete future needs should be handled by bounded Issues and promoted into this document only when they become current architecture.
