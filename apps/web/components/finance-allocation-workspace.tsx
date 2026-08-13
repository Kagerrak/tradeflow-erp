"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type ReceiptList = components["schemas"]["PaymentReceiptListResponse"];
type Receipt = components["schemas"]["PaymentReceiptResponse"];
type ReceiptListState =
  | { kind: "loading" }
  | { kind: "ready"; receipts: ReceiptList }
  | { kind: "unavailable"; correlationId: string };

export function FinanceAllocationWorkspace() {
  const [receipts, setReceipts] = useState<ReceiptListState>({
    kind: "loading",
  });
  const [selectedReceipt, setSelectedReceipt] = useState<Receipt | null>(null);
  const [invoiceId, setInvoiceId] = useState("");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const loadReceipts = useCallback(async () => {
    setReceipts({ kind: "loading" });
    try {
      const response = await fetch("/api/finance/payment-receipts", {
        cache: "no-store",
      });
      const data = (await response.json()) as ReceiptList | { kind?: string };
      if (response.ok && "items" in data) {
        setReceipts({ kind: "ready", receipts: data });
      } else {
        setReceipts({
          kind: "unavailable",
          correlationId: crypto.randomUUID(),
        });
      }
    } catch {
      setReceipts({ kind: "unavailable", correlationId: crypto.randomUUID() });
    }
  }, []);

  useEffect(() => {
    let active = true;
    fetch("/api/finance/payment-receipts", { cache: "no-store" })
      .then(async (response) => {
        const data = (await response.json()) as ReceiptList | { kind?: string };
        if (!active) return;
        if (response.ok && "items" in data) {
          setReceipts({ kind: "ready", receipts: data });
        } else {
          setReceipts({
            kind: "unavailable",
            correlationId: crypto.randomUUID(),
          });
        }
      })
      .catch(() => {
        if (active) {
          setReceipts({
            kind: "unavailable",
            correlationId: crypto.randomUUID(),
          });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const allocate = async () => {
    if (
      selectedReceipt === null ||
      invoiceId.trim() === "" ||
      amount.trim() === ""
    ) {
      setMessage("Receipt, invoice, and amount are required.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/finance/payment-receipts/${selectedReceipt.payment_receipt_id}/allocations`,
        {
          body: JSON.stringify({
            amount: amount.trim(),
            idempotencyKey: crypto.randomUUID(),
            invoiceId: invoiceId.trim(),
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as { amount?: string }[];
      if (response.ok) {
        setMessage(
          `Allocated PHP ${data[0]?.amount ?? amount} to invoice ${invoiceId}.`,
        );
        setInvoiceId("");
        setAmount("");
        await loadReceipts();
      } else {
        setMessage(
          "Allocation was not accepted. Check receipt and invoice state.",
        );
      }
    } catch {
      setMessage("Allocation service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="finance-app">
      <header className="finance-header">
        <Link href="/">TradeFlow</Link>
        <span>Finance / Payment allocation</span>
        <span>Server-authoritative ledger</span>
      </header>
      <main className="finance-main">
        <section className="finance-title">
          <div>
            <p className="eyebrow">Cleared receipt → invoice</p>
            <h1>Apply payments to open invoices.</h1>
          </div>
          <p>
            Match cleared customer funds against posted invoices and reduce the
            customer&apos;s open balance.
          </p>
        </section>

        <section className="finance-panel">
          <div className="finance-section-head">
            <div>
              <span>Maker / finance desk</span>
              <h2>Cleared receipts</h2>
            </div>
            <strong>
              {receipts.kind === "ready" ? receipts.receipts.total : "—"}
            </strong>
          </div>

          {message !== null && (
            <p className="finance-message" role="status">
              {message}
            </p>
          )}

          {receipts.kind === "loading" ? (
            <p className="finance-empty" role="status">
              Loading cleared receipts…
            </p>
          ) : receipts.kind === "unavailable" ? (
            <p className="finance-empty" role="alert">
              Receipt list unavailable. Reference {receipts.correlationId}
            </p>
          ) : receipts.receipts.items.length === 0 ? (
            <p className="finance-empty" role="status">
              No cleared receipts are visible for this Finance scope.
            </p>
          ) : (
            <div className="finance-queue">
              {receipts.receipts.items.map((receipt) => (
                <article
                  className={
                    selectedReceipt?.payment_receipt_id ===
                    receipt.payment_receipt_id
                      ? "finance-selected"
                      : ""
                  }
                  key={receipt.payment_receipt_id}
                >
                  <div>
                    <span className="finance-status neutral">
                      {receipt.status}
                    </span>
                    <h3>
                      {receipt.currency} {receipt.amount}
                    </h3>
                    <p>{receipt.payment_method.replaceAll("_", " ")}</p>
                    <small>{receipt.payment_receipt_id}</small>
                  </div>
                  <button
                    disabled={busy}
                    onClick={() => setSelectedReceipt(receipt)}
                    type="button"
                  >
                    Select
                  </button>
                </article>
              ))}
            </div>
          )}

          {selectedReceipt !== null && (
            <div className="finance-fields">
              <label className="finance-wide">
                Selected receipt
                <input readOnly value={selectedReceipt.payment_receipt_id} />
              </label>
              <label>
                Invoice ID
                <input
                  onChange={(event) => setInvoiceId(event.target.value)}
                  value={invoiceId}
                />
              </label>
              <label>
                Amount to allocate
                <input
                  inputMode="decimal"
                  onChange={(event) => setAmount(event.target.value)}
                  value={amount}
                />
              </label>
              <button
                className="finance-primary"
                disabled={busy}
                onClick={() => void allocate()}
                type="button"
              >
                {busy ? "Allocating…" : "Allocate to invoice"}
              </button>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
