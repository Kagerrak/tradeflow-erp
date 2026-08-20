# TradeFlow workflow checkpoint

- Date: August 19, 2026
- Phase: Issue #110 — Baseline current workflows and measure first-release success outcomes
- Branch: `feature/baseline-current-workflows`
- Base: `origin/main` at `771c0616470f032dba76f52fc27b42b3d36a0442`

## Implementation card

- Outcome: Publish an approved, stable current-system inventory and PRD success-measure baseline that feeds the #103 go/no-go decision.
- Persona: release steering committee, auditors, and the delivery team.
- Start: first-release reconciliation is a coarse planning matrix with unverified scope assumptions. End: every discovered current-system workflow, screen, report, role, integration, export, and source data store has a stable identifier and exactly one approved day-one classification, and every PRD success measure has a defined source, formula, window, baseline, target/decision rule, and owner.
- Invariants: each inventory item receives exactly one classification; no item is unclassified or multiply classified; implemented items cite repository evidence; retired items cite a named approving owner and reason; temporary bridges cite owner, risks, controls, removal date, and migration path; success-measure definitions distinguish missing data from zero.
- Authorization/scope: read-only discovery from approved masked sources; approvals recorded by named business owners.
- Financial/stock effects: none — this slice produces immutable evidence only.
- Reliability: machine-readable file under version control; repeatable validation test; rerun of one snapshot produces identical output.
- Dependencies: #72 merged, `origin/main` stable, existing PRD and reconciliation docs.
- Replacement requirement: closes the discovery/scope gap blocking an honest replacement-readiness assessment.
- Non-goals: production cutover, employee performance scoring, PII export, policy changes, telemetry wiring for measures that are already observable.

## Planned changes

- `docs/delivery/current-system-baseline.yml` — machine-readable inventory and measure definitions.
- `docs/delivery/current-system-baseline.md` — methodology, summary, and owner approval log.
- `apps/api/tests/test_current_system_baseline.py` — validation of schema, classification rules, and required evidence.
- Update `docs/delivery/first-release-reconciliation.md` to reference the new baseline and reflect current `origin/main`.

## Verification

- `uv run pytest -q apps/api/tests/test_current_system_baseline.py` passes.
- `pnpm format`, `pnpm lint`, `pnpm typecheck` pass.
- Full CI gate green before requesting merge approval.
