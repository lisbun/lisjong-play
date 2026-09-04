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

## Initial dependency direction

```text
lisjong-play
    |---> lisjong-engine
    |---> lisjong
    `---> lisjong-arena   # first-party Policy bridge reuse
```

`lisjong-engine` must not depend on `lisjong` or `lisjong-play`.

The direct `lisjong-arena` dependency is an initial reuse decision, not a generic runtime architecture commitment. Re-evaluate extraction only when another concrete non-Arena consumer needs the same bridge, the dependency footprint becomes an actual maintenance/deployment problem, or the bridge needs an independent release lifecycle.

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

The original CLI remains the stable minimum slice. A dedicated Tkinter Issue adds an optional desktop GUI prototype over the same Human EAST / selected Policy x3 composition. The prototype uses simple text tiles and a viewer-relative table; MJX's observation visualizer informed that presentation approach, but no MJX code, font, artwork, proto, or state model is included.

Both slices intentionally exclude seat selection, per-seat or arbitrary Policy selection, rule selection, TUI/Web UI, persisted replay, save/resume, multiplayer, timeout recovery, and AI takeover. The dedicated same-process Human EAST history boundary above does not add replay persistence or reconstruction, and the live GUI does not require a canonical record schema.
