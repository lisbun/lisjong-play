# AGENTS.md

## Repository purpose

`lisjong-play` owns Human Play consumer capabilities for the lisjong ecosystem.

It is responsible for human-facing presentation, human input, action-selection UX, confirmation / interaction, CLI / future GUI presentation, human seat assignment, and the minimum session orchestration required to play through `lisjong-engine`.

## Responsibility boundaries

- Use `lisjong-engine` as the authority for game / round / turn state, legal actions, reaction priority, scoring / settlement, progression, and terminal conditions.
- Human choice uses the engine public `SeatObservation` + `ActionDescriptor` boundary directly. Do not route human decisions through `PolicyInput`, `DecisionContext`, `InternalAction`, or `execute_policy()`.
- Do not read or reconstruct privileged engine state such as `MatchState`, `RoundState`, physical tile identity, wall / dead-wall contents, or opponents' concealed tiles.
- Do not infer ordered past actions by diffing snapshots. Use player-safe ordered progress / completion delivery from `lisjong-engine`.
- For AI seats, reuse the existing first-party Policy bridge from `lisjong-arena`; do not duplicate its conversion / decision-local mapping semantics in this repository.
- Do not add game rules, Policy logic, evaluation protocols, generic backend abstractions, or a generic Player hierarchy here.

## Initial implementation scope

The first vertical slice is intentionally narrow:

- Human EAST
- `MinimalPolicy` x 3 AI seats
- first-party `lisjong-engine`
- fixed/default `RuleSet`
- one hanchan through match completion
- CLI only

Do not expand the first slice with seat selection, Policy selection, rule selection, GUI/TUI/Web UI, replay, save/resume, multiplayer, timeout recovery, or AI takeover unless a dedicated Issue explicitly adds them.

## Quality expectations

- Unknown `ActionDescriptor` variants must fail closed; never silently choose Pass, the first option, or a discard fallback.
- Preserve engine decision granularity, including two-stage riichi.
- Keep `KeyboardInterrupt` propagation intact below the top-level CLI boundary.
- Interactive behavior must be testable with injected input/output; CI tests must not depend on real stdin.
- Prefer focused unit tests for all public action variants plus a scripted-input integration test for the hanchan flow.
- Follow the repository's pinned Python / Ruff configuration once `pyproject.toml` is introduced.

## Historical reference

The legacy `lisbun/python-study` Human CLI / `HumanPlayer` implementation is a behavior / UX / regression-knowledge reference only. Do not mechanically copy its runtime API, class hierarchy, action IDs, controller/state model, or implementation structure.
