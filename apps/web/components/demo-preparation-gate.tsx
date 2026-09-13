"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Evaluation lifecycle gate for the console.
 *
 * The demo rebuilds its data when it is used again after the refresh interval,
 * so a visitor can arrive while the environment is being prepared. This shows
 * that honestly — preparation, readiness, or a genuine failure — instead of
 * leaving a half-populated console on screen.
 */

type DemoLifecycle = "ready" | "refreshing" | "failed" | "unknown";

type DemoStatusPayload = {
  status?: string;
  nextResetAt?: string | null;
  lastError?: string | null;
};

const POLL_MS = 10_000;

function isBlocked(status: DemoLifecycle): boolean {
  return status === "refreshing" || status === "failed";
}

export function DemoPreparationGate() {
  const [status, setStatus] = useState<DemoLifecycle>("ready");
  const [nextResetAt, setNextResetAt] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const poll = useCallback(async () => {
    try {
      const response = await fetch("/api/demo/status", { cache: "no-store" });
      const payload = (await response.json()) as DemoStatusPayload;
      const next = payload.status;
      setStatus(
        next === "ready" || next === "refreshing" || next === "failed"
          ? next
          : "unknown",
      );
      setNextResetAt(payload.nextResetAt ?? null);
    } catch {
      // A failed probe must not fake a refresh; the API still gates traffic.
    }
  }, []);

  useEffect(() => {
    const run = () => {
      void poll();
    };
    const initial = setTimeout(run, 0);
    const handle = setInterval(run, POLL_MS);
    return () => {
      clearTimeout(initial);
      clearInterval(handle);
    };
  }, [poll]);

  if (!isBlocked(status)) return null;

  const failed = status === "failed";

  return (
    <div
      aria-live="polite"
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--background)]/95 p-6 backdrop-blur-sm"
      role="status"
    >
      <div className="max-w-lg rounded-lg border border-[var(--border)] bg-[var(--card)] p-8 shadow-lg">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--muted-foreground)]">
          {failed ? "HOLD / unavailable" : "PREPARING / evaluation"}
        </p>
        <h2 className="mt-3 text-2xl font-semibold text-[var(--card-foreground)]">
          {failed ? "The demo could not be prepared" : "Preparing the demo"}
        </h2>
        <p className="mt-3 text-sm text-[var(--muted-foreground)]">
          {failed
            ? "The evaluation data set failed to rebuild. Nothing was changed in the ERP; try again in a few minutes."
            : "The evaluation data set is being rebuilt with a clean, consistent set of orders, deliveries, invoices and payments. This usually takes under a minute."}
        </p>
        {nextResetAt && !failed ? (
          <p className="mt-2 text-xs text-[var(--muted-foreground)]">
            Scheduled refresh due {new Date(nextResetAt).toLocaleString()}.
          </p>
        ) : null}
        <button
          className="mt-6 inline-flex items-center rounded-md border border-[var(--border)] px-4 py-2 text-sm font-medium disabled:opacity-60"
          disabled={checking}
          onClick={() => {
            setChecking(true);
            void poll().finally(() => setChecking(false));
          }}
          type="button"
        >
          {checking ? "Checking…" : "Check again"}
        </button>
      </div>
    </div>
  );
}
