"use client";

import {
  platformStateContent,
  type PlatformSessionState,
} from "@tradeflow/platform-session";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type ShellState = PlatformSessionState | { kind: "loading" };

const workflowSteps = [
  ["01", "Customer", "Account and terms"],
  ["02", "Order", "Price and approve"],
  ["03", "Reserve", "Commit warehouse stock"],
  ["04", "Pick", "Assign tracked goods"],
  ["05", "Deliver", "Proof and acceptance"],
] as const;

const navigation = [
  ["Control desk", "01", "/"],
  ["Orders", "02", "/sales-orders/new"],
  ["Fulfillment", "03", "/picking"],
  ["Inventory", "04", "/inventory"],
  ["Customers", "05", "/customers"],
  ["Finance", "06", "/finance"],
  ["Allocations", "07", "/finance/allocations"],
  ["Statement", "08", "/finance/statement"],
] as const;

async function fetchPlatformSession(): Promise<PlatformSessionState> {
  const response = await fetch("/api/platform-session", {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  return (await response.json()) as PlatformSessionState;
}

export function TradeFlowShell() {
  const [state, setState] = useState<ShellState>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      setState(await fetchPlatformSession());
    } catch {
      setState({
        correlationId: crypto.randomUUID(),
        kind: "unavailable",
      });
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetchPlatformSession()
      .then((nextState) => {
        if (active) {
          setState(nextState);
        }
      })
      .catch(() => {
        if (active) {
          setState({
            correlationId: crypto.randomUUID(),
            kind: "unavailable",
          });
        }
      });

    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="app-frame">
      <a className="skip-link" href="#main-content">
        Skip to operational status
      </a>

      <aside className="rail" aria-label="Primary navigation">
        <Brand />
        <nav className="rail-nav">
          <p className="rail-label">Workspace</p>
          {navigation.map(([label, number, href], index) => (
            <Link
              className={index === 0 ? "nav-item nav-item-active" : "nav-item"}
              href={href}
              key={label}
            >
              <span aria-hidden="true">{number}</span>
              {label}
            </Link>
          ))}
        </nav>
        <div className="rail-foot">
          <span className="signal signal-ready" aria-hidden="true" />
          <span>
            <b>Secure boundary</b>
            Server-authoritative
          </span>
        </div>
      </aside>

      <main className="main" id="main-content">
        <header className="topbar">
          <Brand compact />
          <div className="topbar-context">
            <span className="eyebrow">Distribution operations</span>
            <span className="topbar-divider" aria-hidden="true" />
            <span>Platform foundation</span>
          </div>
          <span className="environment">Development</span>
        </header>

        <section className="intro" aria-labelledby="page-title">
          <div>
            <p className="eyebrow">Operational readiness / 001</p>
            <h1 id="page-title">Start with a clean handoff.</h1>
          </div>
          <p className="intro-copy">
            Identity, services, and data must agree before business work begins.
            This control desk makes that boundary visible.
          </p>
        </section>

        <div className="workspace">
          <section className="state-region" aria-live="polite">
            <StatePanel state={state} retry={load} />
          </section>
          <WorkflowRail />
        </div>

        <footer className="main-footer">
          <span>TradeFlow ERP</span>
          <span>Auditable by design</span>
          <span className="footer-rule" aria-hidden="true" />
          <span>Web console / v0.1</span>
        </footer>
      </main>
    </div>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "brand brand-compact" : "brand"}>
      <span className="brand-mark" aria-hidden="true">
        <i />
        TF
      </span>
      <span className="brand-name">
        <b>TradeFlow</b>
        {!compact && <small>Distribution ERP</small>}
      </span>
    </div>
  );
}

function StatePanel({
  retry,
  state,
}: {
  retry: () => Promise<void>;
  state: ShellState;
}) {
  if (state.kind === "loading") {
    return (
      <div
        className="state-panel state-loading"
        role="status"
        aria-label="Checking identity, API, and database"
      >
        <div className="state-index">CHECK / 03</div>
        <div className="loader-lines" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <p className="state-kicker">Establishing authority</p>
        <h2>Checking identity, API, and database…</h2>
        <p>
          TradeFlow will only open operational work after the server confirms
          this session.
        </p>
      </div>
    );
  }

  if (state.kind === "ready") {
    return (
      <div className="state-panel state-ready">
        <div className="state-index">READY / 03</div>
        <div className="ready-heading">
          <span className="status-seal status-seal-ready" aria-hidden="true">
            ✓
          </span>
          <div>
            <p className="state-kicker">Server acknowledged</p>
            <h2>Platform handoff is ready</h2>
          </div>
        </div>
        <p className="state-summary">
          The identity boundary, API, and primary database agree. Business
          capabilities can now be added without changing the trust model.
        </p>

        <dl className="checklist">
          <div>
            <dt>
              <span className="signal signal-ready" aria-hidden="true" />
              Identity
            </dt>
            <dd>{state.user.displayName}</dd>
          </div>
          <div>
            <dt>
              <span className="signal signal-ready" aria-hidden="true" />
              API service
            </dt>
            <dd>{state.service}</dd>
          </div>
          <div>
            <dt>
              <span className="signal signal-ready" aria-hidden="true" />
              Primary data
            </dt>
            <dd>{state.database}</dd>
          </div>
        </dl>

        <div className="authority-strip">
          <div>
            <span>Capability</span>
            <strong>{state.user.capabilities.join(", ")}</strong>
          </div>
          <div>
            <span>Correlation</span>
            <code>{state.correlationId}</code>
          </div>
        </div>

        <div className="next-action">
          <span className="next-number">NEXT / 01</span>
          <span>
            <b>Configure organization scope</b>
            Branch, warehouse, and user assignments begin in issue #3.
          </span>
          <span className="arrow" aria-hidden="true">
            →
          </span>
        </div>
      </div>
    );
  }

  const content = platformStateContent[state.kind];

  return (
    <div className={`state-panel state-${state.kind}`}>
      <div className="state-index">{content.index}</div>
      <div className="ready-heading">
        <span className="status-seal" aria-hidden="true">
          !
        </span>
        <div>
          <p className="state-kicker">{content.kicker}</p>
          <h2>{content.heading}</h2>
        </div>
      </div>
      <p className="state-summary">{content.detail}</p>
      <div className="recovery">
        <span>Recovery</span>
        <strong>{content.action}</strong>
      </div>
      {state.kind === "unavailable" && (
        <button
          className="retry-button"
          onClick={() => void retry()}
          type="button"
        >
          Retry connection
          <span aria-hidden="true">↗</span>
        </button>
      )}
      <p className="correlation-note">
        Support reference <code>{state.correlationId}</code>
      </p>
    </div>
  );
}

function WorkflowRail() {
  return (
    <aside className="workflow" aria-labelledby="workflow-title">
      <div className="workflow-heading">
        <div>
          <p className="eyebrow">Order to delivery</p>
          <h2 id="workflow-title">One accountable flow</h2>
        </div>
        <span>5 stages</span>
      </div>
      <ol>
        {workflowSteps.map(([number, name, description], index) => (
          <li key={number}>
            <span className="workflow-number">{number}</span>
            <span>
              <b>{name}</b>
              <small>{description}</small>
            </span>
            <span
              className={
                index === 0 ? "workflow-gate gate-active" : "workflow-gate"
              }
            >
              {index === 0 ? "Next" : "Locked"}
            </span>
          </li>
        ))}
      </ol>
      <p className="workflow-note">
        Each stage opens only after the server accepts the previous business
        decision.
      </p>
    </aside>
  );
}
