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
```

## Current presentation surfaces

```text
Human Play CLI       implemented
Human Play GUI       implemented
Replay Viewer        implemented
AI Spectator #25     planned
```

Human Play and Replay Viewer share presentation code where the required facts are equivalent. The existence of multiple consumers does **not** imply a project-wide canonical viewer state or generic frontend framework.

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

`GuiBoardRenderer` is the shared board-rendering boundary for live Human Play and Replay Viewer.

```text
Human live source ───┐
                     ├─> GuiBoardRenderer -> Tk board / tile images
Replay source ───────┘

future Spectator source (#25)
        └─────────────> reuse the same presentation where semantics match
```

Do not add a generic `ViewerState` abstraction merely because a third source is planned. Extract only concrete common presentation semantics demonstrated by real consumers.

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

Current renderer behavior uses separate normal-size and river-size image registries. This is a presentation implementation detail, not a Mahjong-domain contract. Future layout work such as Issue #37 may change display sizing / composition without changing the underlying engine or replay semantics.

## Planned Spectator boundary

Issue #25 adds a live AI x4 spectator source. It is **not** implemented yet.

The intended dependency shape is:

```text
first-party engine execution
        v
explicit live spectator / objective projection
        v
shared lisjong-play presentation
```

Spectator presentation must not read private mutable engine state as a shortcut. If four-seat concealed-hand viewing is desired, the required truth must come through an explicit engine-owned spectator-safe projection and must never flow back into Policy-visible input.

Arena durable-record persistence is not a prerequisite for live spectator execution.

## Non-goals of the current architecture

- project-wide canonical `GameRecord`
- generic frontend/viewer framework
- viewer-side Mahjong rule engine
- record editor / repair path
- database / record library
- arbitrary omniscient replay reconstruction
- AI reasoning / logits / Q-value visualization
- rule selection, multiplayer, save/resume, or AI takeover as implied current features

Concrete future needs should be handled by bounded Issues and promoted into this document only when they become current architecture.
