# Architecture

`lisjong-play` is the Human Play consumer for the lisjong ecosystem.

## Responsibility

`lisjong-play` owns human-facing presentation, input, action-selection UX, confirmation / interaction, CLI / future GUI presentation, human seat assignment, and the minimum session orchestration needed to play through `lisjong-engine`.

Game / round / turn state, legal actions, reaction priority, scoring / settlement, round / match progression, and terminal conditions remain owned by `lisjong-engine`.

Human decisions use the engine public boundary directly:

```text
SeatObservation
+
tuple[ActionDescriptor, ...]
        |
        v
Human selector
        |
        v
selected ActionDescriptor
```

Human choice does not pass through `PolicyInput`, `DecisionContext`, `InternalAction`, or `execute_policy()`.

Round completion presentation consumes `lisjong-engine.round_completion.RoundCompletionFact` as the player-safe authority. Win result rendering may display the projected winner hand, winning tile, yaku, han / fu or yakuman units, dora counts, revealed indicator tiles, base hand payments, settlement transfers, and riichi-stick awards. `lisjong-play` does not read `CompletedRound` / `RoundState`, recalculate scoring or dora, infer ura disclosure, or choose an arbitrary representative from equal maximum-score interpretations.

AI seats use real `lisjong.Policy` implementations. The CLI explicitly supports `minimal` and `combined`; all three AI seats use the selected type with an independent Policy instance per seat. The implementation reuses the existing first-party Policy bridge from `lisjong-arena`; bridge conversion / mapping semantics are not copied into this repository.

## Initial dependency direction

```text
lisjong-play
    |---> lisjong-engine
    |---> lisjong
    `---> lisjong-arena   # first-party Policy bridge reuse
```

`lisjong-engine` must not depend on `lisjong` or `lisjong-play`.

The direct `lisjong-arena` dependency is an initial reuse decision, not a generic runtime architecture commitment. Re-evaluate extraction only when another concrete non-Arena consumer needs the same bridge, the dependency footprint becomes an actual maintenance/deployment problem, or the bridge needs an independent release lifecycle.

## Initial vertical slice

```text
Human EAST
+
MinimalPolicy x 3 (default)
or GenbutsuDefenseFiniteHorizonValueAwarePolicy x 3
        |
        v
lisjong-engine
        |
        v
one hanchan completion
```

The slice is CLI-only and intentionally excludes seat selection, per-seat or arbitrary Policy selection, rule selection, GUI/TUI/Web UI, replay, save/resume, multiplayer, timeout recovery, and AI takeover.
