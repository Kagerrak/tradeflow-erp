# TradeFlow workflow checkpoint

- Date: July 28, 2026
- Phase: Planning complete; platform foundation implementation starting
- Branch: `feat/platform-shell`
- Baseline: `8dd085f`

## Established

- The documentation-only repository is initialized on `main` and published as
  the private GitHub repository `Kagerrak/tradeflow-erp`.
- The Matt Pocock engineering workflow is configured in `AGENTS.md` and
  `docs/agents/`.
- The multi-context domain model includes Organization & Access, Customers,
  Catalog & Inventory, Sales, Fulfillment, Returns, Procurement, Finance, and
  Commissions.
- Sixteen accepted ADRs record the material first-slice business and
  architecture policies.
- PRD #1 defines the platform foundation and first customer-to-delivery slice.
- Issues #2 through #14 split the PRD into dependency-aware tracer bullets.
- Issue #2 is the current dependency-unblocked implementation item.

## Verification evidence

- GitHub remote uses HTTPS and `main` tracks `origin/main`.
- PRD #1 and issues #2 through #14 are open with `enhancement` and
  `ready-for-agent` labels.
- Issue bodies contain parent, behavior, acceptance criteria, and blocker
  sections.
- Baseline documentation passed `git diff --check`; no secrets were found by
  the repository scan.

## Changes since last checkpoint

This is the first checkpoint. Baseline commit:

- `8dd085f docs: establish TradeFlow product and delivery plan`

The working tree was clean before creating `feat/platform-shell`.

## Deferred human policy decisions

- Commission basis and earning trigger, deferred to the commission slice.
- International landed-cost allocation, deferred to the procurement slice.
- Organization-specific deployment values such as Base Currency, tax seeds,
  approval limits, payment deadlines, and document-series formats.
- Production OIDC provider selection; the application boundary remains
  standards-based and deployment-configurable.
