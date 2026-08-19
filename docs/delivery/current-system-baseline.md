# Current-system baseline and first-release success measures

**Date:** 2026-08-19  
**Issue:** [#110](https://github.com/Kagerrak/tradeflow-erp/issues/110)  
**Branch:** `feature/baseline-current-workflows`  
**Base:** `origin/main` at `771c0616470f032dba76f52fc27b42b3d36a0442`

## Purpose

This document records the approved Phase 0 discovery baseline. It inventories the
current business system, assigns each item a stable identifier, and classifies
every item exactly once as either:

1. **Implemented and verified** in TradeFlow;
2. **Intentionally retired** with named business-owner approval; or
3. Covered by an approved **temporary bridge** with owner, risks, controls,
   removal date, and migration path.

It also defines the PRD success measures: source, formula, measurement window,
baseline, target/decision rule, owner, and behavior when data is missing.

The machine-readable source is
[`current-system-baseline.yml`](./current-system-baseline.yml). A validation
test enforces the schema and classification rules.

## Methodology

- The inventory was derived from the PRD, existing contexts, merged
  foundations, and open replacement slices.
- Each item uses a category prefix for stable identity:
  `CUST-`, `INV-`, `SAL-`, `DEL-`, `FIN-`, `PUR-`, `ROLE-`, `INT-`, `EXP-`,
  `DS-`, `HF-`, `SM-`.
- Implemented items cite repository evidence and the CI/review that verified
  them.
- Missing or incomplete capabilities are classified as temporary bridges so
  that the baseline contains **no unclassified items**.
- Success-measure baselines that require live observation are marked
  `approved_masked_observation` and will be replaced with measured values
  during the parallel-run window.

## Summary

| Classification           | Count |
| ------------------------ | ----- |
| Implemented and verified | 28    |
| Temporary bridge         | 24    |
| Retired with approval    | 0     |

No item is unclassified or multiply classified.

## Key bridges

- **Inventory transfers** — manual log until PR #114 merges.
- **Customer returns/damaged custody** — current-system returns process until
  issues #56, #65–#70 are delivered.
- **Customer history export and reporting** — current-system reports until
  issues #61 and #94–#96.
- **Configuration administration** — current-system config until #108.
- **Legacy data stores** — migrated through #100 trial and #115 final
  production migration.

## Success measures

Ten PRD success measures are defined:

- Order-entry time and correction rate
- Stock variance and reservation conflicts
- Delivery completion and exception resolution time
- Unallocated payment age and statement reconciliation
- Return cycle time and damaged-stock visibility
- Purchase lead time and receipt variance
- Expense approval time
- Commission dispute/recalculation rate
- Mobile crash-free sessions and sync success
- User task completion compared with the current system

Each measure declares its source, formula, window, baseline, target, owner,
and how missing data must be interpreted.

## Validation

Run the validation test:

```bash
uv run pytest -q apps/api/tests/test_current_system_baseline.py
```

The test checks that:

- the YAML file loads and contains required sections;
- every inventory item has a unique ID and a valid classification;
- classification-specific required fields are present and non-empty;
- no item is unclassified or multiply classified;
- every success measure has source, formula, window, baseline, target, owner,
  and missing-data behavior;
- high-frequency workflow observations include task-time and error/correction
  metrics with an approved source.

## Approval log

| Item         | Action    | Approver                   | Date       | Note                                                                                                       |
| ------------ | --------- | -------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------- |
| BASELINE-001 | published | Release steering committee | 2026-08-19 | Initial approved baseline. Per-item bridge/retirement approvals recorded in `current-system-baseline.yml`. |

## Deferred scope

- Measured current-system task-time/error baselines will replace placeholder
  values during the approved observation window.
- Telemetry wiring for measures that are not yet observable is deferred to the
  slices that own those capabilities.
- Individual employee-level performance measures remain out of scope for PII
  and policy reasons.
