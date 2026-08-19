"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

type ReceiptList = components["schemas"]["PaymentReceiptListResponse"];
type Receipt = components["schemas"]["PaymentReceiptResponse"];
type AllocationDetail =
  components["schemas"]["PaymentReceiptAllocationListResponse"];
type InvoiceList = components["schemas"]["DraftInvoiceListResponse"];
type ReceiptListState =
  | { kind: "loading" }
  | { kind: "ready"; receipts: ReceiptList }
  | { kind: "unavailable"; correlationId: string };

export function FinanceAllocationWorkspace() {
  const [receipts, setReceipts] = useState<ReceiptListState>({
    kind: "loading",
  });
  const [selectedReceipt, setSelectedReceipt] = useState<Receipt | null>(null);
  const [allocationDetail, setAllocationDetail] =
    useState<AllocationDetail | null>(null);
  const [openInvoices, setOpenInvoices] = useState<InvoiceList["items"]>([]);
  const [invoiceId, setInvoiceId] = useState("");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const allocationIdentity = useRef<{
    fingerprint: string;
    idempotencyKey: string;
  } | null>(null);

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

  const selectReceipt = async (receipt: Receipt) => {
    setSelectedReceipt(receipt);
    setAllocationDetail(null);
    setOpenInvoices([]);
    setInvoiceId("");
    setAmount("");
    setMessage(null);
    const invoiceParams = new URLSearchParams({
      customer_id: receipt.customer_id,
      open_only: "true",
      status: "posted",
    });
    try {
      const [detailResponse, invoiceResponse] = await Promise.all([
        fetch(
          `/api/finance/payment-receipts/${receipt.payment_receipt_id}/allocations`,
          { cache: "no-store" },
        ),
        fetch(`/api/finance/invoices?${invoiceParams.toString()}`, {
          cache: "no-store",
        }),
      ]);
      const detail = (await detailResponse.json()) as AllocationDetail;
      const invoices = (await invoiceResponse.json()) as InvoiceList;
      if (!detailResponse.ok || !invoiceResponse.ok) {
        setMessage("Receipt or open-invoice inquiry is unavailable.");
        return;
      }
      setAllocationDetail(detail);
      setOpenInvoices(invoices.items);
    } catch {
      setMessage("Receipt or open-invoice inquiry is unavailable.");
    }
  };

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
      allocationDetail === null ||
      invoiceId.trim() === "" ||
      amount.trim() === ""
    ) {
      setMessage("Receipt, invoice, and amount are required.");
      return;
    }
    const fingerprint = JSON.stringify({
      amount: amount.trim(),
      expectedVersion: allocationDetail.version,
      invoiceId: invoiceId.trim(),
      paymentReceiptId: selectedReceipt.payment_receipt_id,
    });
    if (allocationIdentity.current?.fingerprint !== fingerprint) {
      allocationIdentity.current = {
        fingerprint,
        idempotencyKey: crypto.randomUUID(),
      };
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/finance/payment-receipts/${selectedReceipt.payment_receipt_id}/allocations`,
        {
          body: JSON.stringify({
            amount: amount.trim(),
            expectedVersion: allocationDetail.version,
            idempotencyKey: allocationIdentity.current.idempotencyKey,
            invoiceId: invoiceId.trim(),
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as { amount?: string }[];
      if (response.ok) {
        const successMessage = `Allocated PHP ${data[0]?.amount ?? amount} to invoice ${invoiceId}.`;
        setInvoiceId("");
        setAmount("");
        allocationIdentity.current = null;
        await loadReceipts();
        await selectReceipt(selectedReceipt);
        setMessage(successMessage);
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
                    <p>
                      {receipt.application_state.replaceAll("_", " ")} ·{" "}
                      {receipt.currency} {receipt.unapplied_amount} unapplied
                    </p>
                    <small>{receipt.payment_receipt_id}</small>
                  </div>
                  <button
                    disabled={busy}
                    onClick={() => void selectReceipt(receipt)}
                    type="button"
                  >
                    Select
                  </button>
                </article>
              ))}
            </div>
          )}

          {selectedReceipt !== null && allocationDetail !== null && (
            <div className="finance-fields">
              <label className="finance-wide">
                Selected receipt
                <input readOnly value={selectedReceipt.payment_receipt_id} />
              </label>
              <div>
                <span>Application state</span>
                <strong>
                  {allocationDetail.application_state.replaceAll("_", " ")}
                </strong>
              </div>
              <div>
                <span>Cleared / allocated / unapplied</span>
                <strong>
                  {allocationDetail.cleared_amount} /{" "}
                  {allocationDetail.allocated_amount} /{" "}
                  {allocationDetail.available_amount}
                </strong>
              </div>
              <label>
                Open invoice
                <select
                  aria-label="Open invoice"
                  onChange={(event) => setInvoiceId(event.target.value)}
                  value={invoiceId}
                >
                  <option value="">Select an open invoice</option>
                  {openInvoices.map((invoice) => (
                    <option
                      key={invoice.draft_invoice_id}
                      value={invoice.draft_invoice_id}
                    >
                      {invoice.draft_invoice_id} · {invoice.currency}{" "}
                      {invoice.open_balance} open
                    </option>
                  ))}
                </select>
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
              {allocationDetail.application_state === "partially_applied" &&
                Number(allocationDetail.available_amount) > 0 && (
                  <p className="finance-message" role="status">
                    Excess customer funds remain Unapplied Payment and have not
                    reduced another invoice.
                  </p>
                )}
              {allocationDetail.allocations.length > 0 && (
                <div className="finance-wide">
                  <strong>Allocation history</strong>
                  {allocationDetail.allocations.map((allocation) => (
                    <p key={allocation.allocation_id}>
                      {allocation.amount} → {allocation.invoice_id}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
