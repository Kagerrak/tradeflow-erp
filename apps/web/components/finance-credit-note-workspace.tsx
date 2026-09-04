"use client";

import type { components } from "@tradeflow/api-client";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "./ui/page-header";
import { randomId } from "@/lib/random-id";

type CreditNoteList = components["schemas"]["CreditNoteListResponse"];
type CreditNote = components["schemas"]["CreditNoteResponse"];
type ListState =
  | { kind: "loading" }
  | { kind: "ready"; notes: CreditNoteList }
  | { kind: "unavailable"; correlationId: string };

async function fetchCreditNotes(): Promise<ListState> {
  try {
    const response = await fetch("/api/finance/credit-notes", {
      cache: "no-store",
    });
    const data = (await response.json()) as CreditNoteList | { kind?: string };
    if (response.ok && "items" in data) {
      return { kind: "ready", notes: data };
    }
    return { kind: "unavailable", correlationId: randomId() };
  } catch {
    return { kind: "unavailable", correlationId: randomId() };
  }
}

export function FinanceCreditNoteWorkspace() {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [invoiceId, setInvoiceId] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("PHP");
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState<CreditNote | null>(null);

  const refresh = useCallback(async () => {
    setState(await fetchCreditNotes());
  }, []);

  useEffect(() => {
    let active = true;
    fetchCreditNotes().then((next) => {
      if (active) {
        setState(next);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  const request = async () => {
    if (!invoiceId || !amount || !reason) {
      setMessage("Invoice, amount, and reason are required.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/finance/invoices/${invoiceId}/credit-notes`,
        {
          body: JSON.stringify({
            amount,
            currency,
            idempotencyKey: randomId(),
            reason,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as { status?: string };
      if (response.ok) {
        setMessage(
          `Credit note requested · ${data.status ?? "pending_authorization"}`,
        );
        setInvoiceId("");
        setAmount("");
        setReason("");
        await refresh();
      } else {
        setMessage(
          "Credit note request was rejected. Check invoice state and scope.",
        );
      }
    } catch {
      setMessage("Credit note service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  const authorize = async (note: CreditNote) => {
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/finance/credit-notes/${note.credit_note_id}/post`,
        {
          body: JSON.stringify({ idempotencyKey: randomId() }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as {
        number?: string;
        status?: string;
      };
      if (response.ok) {
        setMessage(`Credit note authorized · ${data.number ?? data.status}`);
        setSelected(null);
        await refresh();
      } else {
        setMessage(
          "Authorization was rejected. Check maker-checker scope and limits.",
        );
      }
    } catch {
      setMessage("Credit note service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  const reverse = async (note: CreditNote) => {
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/finance/credit-notes/${note.credit_note_id}/reverse`,
        {
          body: JSON.stringify({
            idempotencyKey: randomId(),
            reason: "Reversed from workspace.",
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as { status?: string };
      if (response.ok) {
        setMessage(`Credit note reversed · ${data.status}`);
        setSelected(null);
        await refresh();
      } else {
        setMessage("Reversal was rejected. Check maker-checker scope.");
      }
    } catch {
      setMessage("Credit note service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  const statusClass = (status: string) => {
    if (status === "posted") return "positive";
    if (status === "reversed") return "critical";
    return "attention";
  };

  return (
    <>
      <PageHeader
        description="Request credits against posted invoices, authorize them with a different eligible user, and reverse them without erasing history."
        eyebrow="Finance"
        title="Credit notes"
      />

      <section className="finance-title card">
        <div>
          <p className="eyebrow">Request → authorize → reverse</p>
          <h1>Issue immutable credit notes.</h1>
        </div>
        <p>
          Request credits against posted invoices, authorize them with a
          different eligible user, and reverse them without erasing history.
        </p>
      </section>

      <section className="finance-panel">
        <div className="finance-section-head">
          <div>
            <span>Maker / finance desk</span>
            <h2>Request a credit note</h2>
          </div>
        </div>

        {message !== null && (
          <p
            className="finance-message"
            role="status"
            data-testid="credit-note-message"
          >
            {message}
          </p>
        )}

        <div className="finance-fields">
          <article>
            <div>
              <label htmlFor="invoice-id">Posted invoice ID</label>
              <input
                data-testid="credit-note-invoice-id"
                id="invoice-id"
                onChange={(e) => setInvoiceId(e.target.value)}
                type="text"
                value={invoiceId}
              />
            </div>
          </article>
          <article>
            <div>
              <label htmlFor="amount">Amount</label>
              <input
                data-testid="credit-note-amount"
                id="amount"
                onChange={(e) => setAmount(e.target.value)}
                type="text"
                value={amount}
              />
            </div>
            <div>
              <label htmlFor="currency">Currency</label>
              <input
                data-testid="credit-note-currency"
                id="currency"
                onChange={(e) => setCurrency(e.target.value)}
                type="text"
                value={currency}
              />
            </div>
          </article>
          <article>
            <div>
              <label htmlFor="reason">Reason</label>
              <input
                data-testid="credit-note-reason"
                id="reason"
                onChange={(e) => setReason(e.target.value)}
                type="text"
                value={reason}
              />
            </div>
          </article>
          <button
            className="finance-primary"
            data-testid="credit-note-request"
            disabled={busy}
            onClick={request}
            type="button"
          >
            Request credit note
          </button>
        </div>
      </section>

      <section className="finance-panel" style={{ marginTop: "1.5rem" }}>
        <div className="finance-section-head">
          <div>
            <span>Checker / finance desk</span>
            <h2>Credit notes</h2>
          </div>
          <strong>{state.kind === "ready" ? state.notes.total : "—"}</strong>
        </div>

        {state.kind === "loading" ? (
          <p className="finance-empty" role="status">
            Loading scoped credit notes…
          </p>
        ) : state.kind === "unavailable" ? (
          <p className="finance-empty" role="alert">
            Credit note list unavailable. Reference {state.correlationId}
          </p>
        ) : state.notes.items.length === 0 ? (
          <p className="finance-empty">
            No credit notes for your branch scope.
          </p>
        ) : (
          <table className="finance-table">
            <thead>
              <tr>
                <th>Number</th>
                <th>Invoice</th>
                <th>Amount</th>
                <th>Status</th>
                <th>Requested by</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {state.notes.items.map((note) => (
                <tr
                  className={
                    selected?.credit_note_id === note.credit_note_id
                      ? "finance-selected"
                      : undefined
                  }
                  data-testid={`credit-note-row-${note.credit_note_id}`}
                  key={note.credit_note_id}
                  onClick={() => setSelected(note)}
                >
                  <td data-label="Number">{note.number ?? "—"}</td>
                  <td data-label="Invoice">{note.draft_invoice_id}</td>
                  <td data-label="Amount">
                    {note.amount} {note.currency}
                  </td>
                  <td data-label="Status">
                    <span
                      className={`finance-status ${statusClass(note.status)}`}
                    >
                      {note.status}
                    </span>
                  </td>
                  <td data-label="Requested by">{note.requested_by}</td>
                  <td data-label="Actions">
                    {note.status === "pending_authorization" && (
                      <button
                        data-testid={`credit-note-authorize-${note.credit_note_id}`}
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          void authorize(note);
                        }}
                        type="button"
                      >
                        Authorize
                      </button>
                    )}
                    {note.status === "posted" && (
                      <button
                        data-testid={`credit-note-reverse-${note.credit_note_id}`}
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          void reverse(note);
                        }}
                        type="button"
                      >
                        Reverse
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
