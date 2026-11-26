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

## Test execution policy

- For source code or test changes, run focused tests that directly cover the changed behavior and its immediate regression boundary during implementation and before opening or updating a Pull Request.
- Keep local format / lint checks required by the repository configuration.
- Treat `python -m unittest discover -s tests -v` as the full regression suite and GitHub Actions as its pre-merge source of truth. Do not require the same full suite to be run locally for every change.
- Run the local full suite when there is a concrete reason, such as reproducing or investigating a CI failure, changing shared test infrastructure or cross-cutting behavior, working without CI, or when an Issue or the user explicitly requires it.
- After review fixes, rerun the focused tests affected by the change and rely on GitHub Actions for full regression by default.
- Confirm the GitHub Actions full suite passes before merge.
- For documentation-only changes, run at least `git diff --check` and confirm that no source or test code changed.

## 外部公開資料の調査に関する継続承認

2026-11-27のユーザー承認（lisbun/lisjong-project#135）に基づき、技術調査・設計・レビューに限り、以下は追加承認不要とする。

- 公開論文、技術文書、仕様、ライセンス、公開ソースコードの閲覧。
- 既存の承認済み依存関係でversionが固定されているpackage（wheel等）を、公式配布元から取得し、隔離された一時領域で安全に展開・静的調査すること。

後者は取得元・version・調査目的を記録し、外部コードの実行・installを行わず、既存依存関係・実行環境・repositoryを変更せず、課金・credential使用・内部データの外部送信を伴わないことを条件とする。展開時のpath traversal等に注意し、取得物はrepositoryへcommitしない。

この範囲外の外部source取得（clone等）、新規依存関係の導入、外部コードの実行、外部model・外部学習データの取得、外部serviceへの内部データ送信は個別承認を必要とする。通常の承認済み開発・CIの実行を禁止するものではない。

既使用developmentデータの分析に関する#118の外部取得禁止、実験・課金・正式評価・Champion変更・稼働bot切替・credential/settings変更・情報境界等の既存承認規則を緩和しない。過去の操作を遡及承認しない。

## Historical reference

The legacy `lisbun/python-study` Human CLI / `HumanPlayer` implementation is a behavior / UX / regression-knowledge reference only. Do not mechanically copy its runtime API, class hierarchy, action IDs, controller/state model, or implementation structure.
