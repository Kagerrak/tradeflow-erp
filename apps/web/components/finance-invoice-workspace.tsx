"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type InvoiceList = components["schemas"]["DraftInvoiceListResponse"];
type Invoice = components["schemas"]["DraftInvoiceResponse"];
type ListState =
  | { kind: "loading" }
  | { kind: "ready"; invoices: InvoiceList }
  | { kind: "unavailable"; correlationId: string };

async function fetchInvoices(): Promise<ListState> {
  try {
    const response = await fetch("/api/finance/invoices", {
      cache: "no-store",
    });
    const data = (await response.json()) as InvoiceList | { kind?: string };
    if (response.ok && "items" in data) {
      return { kind: "ready", invoices: data };
    }
    return { kind: "unavailable", correlationId: crypto.randomUUID() };
  } catch {
    return { kind: "unavailable", correlationId: crypto.randomUUID() };
  }
}

export function FinanceInvoiceWorkspace() {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchInvoices().then((next) => {
      if (active) {
        setState(next);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setState(await fetchInvoices());
  }, []);

  const post = async (invoice: Invoice) => {
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/finance/invoices/${invoice.draft_invoice_id}/post`,
        {
          body: JSON.stringify({ idempotencyKey: crypto.randomUUID() }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as { status?: string };
      if (response.ok) {
        setMessage(`Invoice posted · ${data.status ?? "posted"}`);
        await refresh();
      } else {
        setMessage(
          "Invoice could not be posted. Check scope and ledger state.",
        );
      }
    } catch {
      setMessage("Posting service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="finance-app">
      <header className="finance-header">
        <Link href="/">TradeFlow</Link>
        <span>Finance / Invoice posting</span>
        <span>Server-authoritative ledger</span>
      </header>
      <main className="finance-main">
        <section className="finance-title">
          <div>
            <p className="eyebrow">Draft invoice → ledger</p>
            <h1>Post invoices to the customer ledger.</h1>
          </div>
          <p>
            Convert fulfilled delivery confirmations into immutable customer
            ledger entries and update open balances.
          </p>
        </section>

        <section className="finance-panel">
          <div className="finance-section-head">
            <div>
              <span>Maker / finance desk</span>
              <h2>Draft invoices</h2>
            </div>
            <strong>
              {state.kind === "ready" ? state.invoices.total : "—"}
            </strong>
          </div>

          {message !== null && (
            <p className="finance-message" role="status">
              {message}
            </p>
          )}

          {state.kind === "loading" ? (
            <p className="finance-empty" role="status">
              Loading scoped invoices…
            </p>
          ) : state.kind === "unavailable" ? (
            <p className="finance-empty" role="alert">
              Invoice list unavailable. Reference {state.correlationId}
            </p>
          ) : state.invoices.items.length === 0 ? (
            <p className="finance-empty" role="status">
              No invoices are visible for this Finance scope.
            </p>
          ) : (
            <table className="finance-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Customer</th>
                  <th>Total</th>
                  <th>Created</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {state.invoices.items.map((invoice) => (
                  <tr key={invoice.draft_invoice_id}>
                    <td>
                      <StatusLabel status={invoice.status} />
                    </td>
                    <td>{invoice.customer_id}</td>
                    <td>
                      {invoice.currency} {invoice.grand_total}
                    </td>
                    <td>{new Date(invoice.created_at).toLocaleDateString()}</td>
                    <td>
                      {invoice.status === "draft" ? (
                        <button
                          disabled={busy}
                          onClick={() => void post(invoice)}
                          type="button"
                        >
                          {busy ? "Posting…" : "Post to ledger"}
                        </button>
                      ) : (
                        <span className="finance-muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </main>
    </div>
  );
}

function StatusLabel({ status }: { status: string }) {
  const tone =
    status === "posted"
      ? "positive"
      : status === "voided"
        ? "critical"
        : "attention";
  return (
    <span className={`finance-status ${tone}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}
