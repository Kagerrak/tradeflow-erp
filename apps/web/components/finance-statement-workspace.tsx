"use client";

import type { components } from "@tradeflow/api-client";
import { useState } from "react";
import { PageHeader } from "./ui/page-header";

type Statement = components["schemas"]["StatementResponse"];
type Line = components["schemas"]["StatementLine"];
type Document = components["schemas"]["StatementDocument"];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function thirtyDaysAgo(): string {
  const date = new Date();
  date.setDate(date.getDate() - 30);
  return date.toISOString().slice(0, 10);
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString();
}

function formatCurrency(value: string): string {
  return Number(value).toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

export function FinanceStatementWorkspace() {
  const [customerId, setCustomerId] = useState("");
  const [fromDate, setFromDate] = useState(thirtyDaysAgo);
  const [toDate, setToDate] = useState(today);
  const [asOf, setAsOf] = useState(today);
  const [statement, setStatement] = useState<Statement | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = async () => {
    if (customerId.trim() === "") {
      setMessage("Customer ID is required.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const params = new URLSearchParams({
        from_date: fromDate,
        to_date: toDate,
      });
      if (asOf) params.set("as_of", asOf);
      const response = await fetch(
        `/api/finance/customers/${customerId}/statement?${params.toString()}`,
        { cache: "no-store" },
      );
      const data = (await response.json()) as Statement | { kind?: string };
      if (response.ok && "lines" in data) {
        setStatement(data);
      } else {
        setMessage("Statement unavailable for this customer and range.");
        setStatement(null);
      }
    } catch {
      setMessage("Statement service unavailable. Retry unchanged work.");
      setStatement(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        description="View a branch-scoped, time-bounded customer statement derived from the immutable customer ledger."
        eyebrow="Finance"
        title="Statement of account"
      />

      <section className="finance-title card">
        <div>
          <p className="eyebrow">Customer account ledger</p>
          <h1>Statement of account.</h1>
        </div>
        <p>
          View a branch-scoped, time-bounded customer statement derived from the
          immutable customer ledger.
        </p>
      </section>

      <section className="finance-panel">
        <div className="finance-section-head">
          <div>
            <span>Finance / read-only</span>
            <h2>Statement query</h2>
          </div>
        </div>

        {message !== null && (
          <p className="finance-message" role="status">
            {message}
          </p>
        )}

        <div className="finance-fields">
          <label className="finance-wide">
            Customer ID
            <input
              onChange={(event) => setCustomerId(event.target.value)}
              value={customerId}
            />
          </label>
          <label>
            From
            <input
              onChange={(event) => setFromDate(event.target.value)}
              type="date"
              value={fromDate}
            />
          </label>
          <label>
            To
            <input
              onChange={(event) => setToDate(event.target.value)}
              type="date"
              value={toDate}
            />
          </label>
          <label>
            As of
            <input
              onChange={(event) => setAsOf(event.target.value)}
              type="date"
              value={asOf}
            />
          </label>
          <button
            className="finance-primary"
            disabled={busy}
            onClick={() => void load()}
            type="button"
          >
            {busy ? "Loading…" : "Run statement"}
          </button>
        </div>
      </section>

      {statement !== null && (
        <section className="finance-panel">
          <div className="finance-section-head">
            <div>
              <span>{statement.currency}</span>
              <h2>Closing balance</h2>
            </div>
            <strong>{formatCurrency(statement.closing_balance)}</strong>
          </div>

          <div className="finance-section-head">
            <div>
              <span>Customer funds not applied to invoices</span>
              <h3>Unapplied Payment</h3>
            </div>
            <strong>{formatCurrency(statement.unapplied_payment_total)}</strong>
          </div>

          {statement.unapplied_payments.length > 0 && (
            <div className="finance-queue">
              {statement.unapplied_payments.map((payment) => (
                <article key={payment.payment_receipt_id}>
                  <div>
                    <span className="finance-status attention">
                      {payment.application_state.replaceAll("_", " ")}
                    </span>
                    <h3>{formatCurrency(payment.unapplied_amount)}</h3>
                    <p>
                      Receipt {formatCurrency(payment.amount)} · allocated{" "}
                      {formatCurrency(payment.allocated_amount)}
                    </p>
                    <small>{payment.payment_receipt_id}</small>
                  </div>
                </article>
              ))}
            </div>
          )}

          <div className="finance-section-head">
            <div>
              <span>Opening</span>
              <h3>{formatCurrency(statement.opening_balance)}</h3>
            </div>
            <div>
              <span>Range</span>
              <h3>
                {formatDate(statement.from_date)} —{" "}
                {formatDate(statement.to_date)}
              </h3>
            </div>
          </div>

          <h3>Documents</h3>
          {statement.documents.length === 0 ? (
            <p className="finance-empty">No open or paid documents.</p>
          ) : (
            <div className="finance-queue">
              {statement.documents.map((document: Document) => (
                <article key={document.invoice_id}>
                  <div>
                    <span className={`finance-status ${document.state}`}>
                      {document.state.replaceAll("_", " ")}
                    </span>
                    <h3>
                      {formatCurrency(document.original_amount)} open:{" "}
                      {formatCurrency(document.open_amount)}
                    </h3>
                    <p>
                      Paid {formatCurrency(document.paid_amount)} · Aging{" "}
                      {document.aging_bucket}
                    </p>
                    <small>{document.invoice_id}</small>
                  </div>
                </article>
              ))}
            </div>
          )}

          <h3>Ledger lines</h3>
          {statement.lines.length === 0 ? (
            <p className="finance-empty">No ledger activity in range.</p>
          ) : (
            <div className="finance-queue">
              {statement.lines.map((line: Line) => (
                <article key={line.entry_id}>
                  <div>
                    <span className="finance-status neutral">
                      {line.entry_type}
                    </span>
                    <h3>{formatCurrency(line.amount)}</h3>
                    <p>
                      {line.source_type} · running{" "}
                      {formatCurrency(line.running_balance)}
                    </p>
                    <small>{line.entry_id}</small>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      )}
    </>
  );
}
