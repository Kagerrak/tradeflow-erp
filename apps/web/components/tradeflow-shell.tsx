"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  platformStateContent,
  type PlatformSessionState,
} from "@tradeflow/platform-session";
import { ErrorState } from "./ui/error-state";
import { DemoScenario } from "./demo-scenario";

const quickActions = [
  {
    description: "Create a priced sales order",
    href: "/sales-orders/new",
    title: "New sales order",
  },
  {
    description: "Record a customer payment",
    href: "/payments",
    title: "Receive payment",
  },
  {
    description: "Assign and confirm picks",
    href: "/picking",
    title: "Pick orders",
  },
  {
    description: "Review posted stock",
    href: "/inventory",
    title: "Stock ledger",
  },
];

type ShellState = PlatformSessionState | { kind: "loading" };

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
        if (active) setState(nextState);
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
    <div className="dashboard">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">Control desk</span>
          <h1>Welcome to TradeFlow</h1>
          <p>Choose a workspace to start working.</p>
        </div>
      </header>

      <section className="dashboard-grid" aria-label="Quick actions">
        {quickActions.map((action) => (
          <Link className="dashboard-tile" href={action.href} key={action.href}>
            <span className="dashboard-tile-title">{action.title}</span>
            <span className="dashboard-tile-desc">{action.description}</span>
          </Link>
        ))}
      </section>

      <DemoScenario />

      <section className="dashboard-status" aria-label="Platform status">
        <StatusCard retry={load} state={state} />
      </section>
    </div>
  );
}

function StatusCard({
  retry,
  state,
}: {
  retry: () => Promise<void>;
  state: ShellState;
}) {
  if (state.kind === "loading") {
    return (
      <div
        className="card dashboard-status-card"
        role="status"
        aria-label="Checking identity, API, and database"
      >
        <h2>Platform status</h2>
        <p>Checking identity, API, and database…</p>
      </div>
    );
  }

  if (state.kind === "ready") {
    return (
      <div className="card dashboard-status-card">
        <div className="dashboard-status-heading">
          <span className="dashboard-status-dot" aria-hidden="true" />
          <div>
            <h2>Platform handoff is ready</h2>
            <p>
              {state.user.displayName} · {state.service} ·{" "}
              <code>{state.correlationId}</code>
            </p>
          </div>
        </div>
      </div>
    );
  }

  const content = platformStateContent[state.kind];
  return (
    <ErrorState
      action={
        state.kind === "unavailable" ? (
          <button
            className="btn-primary"
            onClick={() => void retry()}
            type="button"
          >
            Retry connection
          </button>
        ) : undefined
      }
      correlationId={state.correlationId}
      title={content.heading}
    >
      <p>{content.detail}</p>
    </ErrorState>
  );
}
