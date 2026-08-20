"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useState } from "react";

type Timeline = components["schemas"]["CustomerTimelineResponse"];
type Event = components["schemas"]["CustomerTimelineEvent"];

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

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function formatCurrency(value: string): string {
  return Number(value).toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

export function FinanceTimelineWorkspace() {
  const [customerId, setCustomerId] = useState("");
  const [fromDate, setFromDate] = useState(thirtyDaysAgo);
  const [toDate, setToDate] = useState(today);
  const [asOf, setAsOf] = useState(today);
  const [eventType, setEventType] = useState("");
  const [timeline, setTimeline] = useState<Timeline | null>(null);
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
        as_of: asOf,
      });
      if (eventType.trim()) params.set("event_type", eventType.trim());
      const response = await fetch(
        `/api/finance/customers/${customerId}/timeline?${params.toString()}`,
        { cache: "no-store" },
      );
      const data = (await response.json()) as Timeline | { kind?: string };
      if (response.ok && "items" in data) {
        setTimeline(data);
      } else {
        setMessage("Timeline unavailable for this customer and range.");
        setTimeline(null);
      }
    } catch {
      setMessage("Timeline service unavailable. Retry unchanged work.");
      setTimeline(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="finance-app">
      <header className="finance-header">
        <Link href="/">TradeFlow</Link>
        <span>Finance / Timeline</span>
        <span>Server-authoritative timeline</span>
      </header>
      <main className="finance-main">
        <section className="finance-title">
          <div>
            <p className="eyebrow">Customer transaction timeline</p>
            <h1>Consolidated activity.</h1>
          </div>
          <p>
            View a branch-scoped, chronological timeline of orders, deliveries,
            invoices, payments, credits and returns.
          </p>
        </section>

        <section className="finance-panel">
          <div className="finance-section-head">
            <div>
              <span>Finance / read-only</span>
              <h2>Timeline query</h2>
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
            <label>
              Event type
              <select
                onChange={(event) => setEventType(event.target.value)}
                value={eventType}
              >
                <option value="">All</option>
                <option value="order">Order</option>
                <option value="delivery">Delivery</option>
                <option value="return">Return</option>
                <option value="invoice">Invoice</option>
                <option value="invoice_void">Invoice void</option>
                <option value="payment">Payment</option>
                <option value="credit">Credit</option>
                <option value="credit_reversal">Credit reversal</option>
              </select>
            </label>
            <button
              className="finance-primary"
              disabled={busy}
              onClick={() => void load()}
              type="button"
            >
              {busy ? "Loading…" : "Run timeline"}
            </button>
          </div>
        </section>

        {timeline !== null && (
          <section className="finance-panel">
            <div className="finance-section-head">
              <div>
                <span>{timeline.currency}</span>
                <h2>As-of balance</h2>
              </div>
              <strong>{formatCurrency(timeline.closing_balance)}</strong>
            </div>

            <div className="finance-section-head">
              <div>
                <span>Opening</span>
                <h3>{formatCurrency(timeline.opening_balance)}</h3>
              </div>
              <div>
                <span>Range</span>
                <h3>
                  {formatDate(timeline.from_date)} —{" "}
                  {formatDate(timeline.to_date)}
                </h3>
              </div>
              <div>
                <span>Total events</span>
                <h3>{timeline.total}</h3>
              </div>
            </div>

            <h3>Events</h3>
            {timeline.items.length === 0 ? (
              <p className="finance-empty">No activity in range.</p>
            ) : (
              <div className="finance-queue">
                {timeline.items.map((event: Event) => (
                  <article key={event.event_id}>
                    <div>
                      <span className={`finance-status ${event.event_type}`}>
                        {event.event_type.replaceAll("_", " ")}
                      </span>
                      <h3>
                        {event.amount !== "0.000000"
                          ? formatCurrency(event.amount)
                          : formatCurrency(event.document_value)}
                      </h3>
                      <p>
                        {event.source_type} · {formatDateTime(event.event_at)}
                      </p>
                      {event.reference_number && (
                        <p>Ref: {event.reference_number}</p>
                      )}
                      <small>{event.event_id}</small>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
