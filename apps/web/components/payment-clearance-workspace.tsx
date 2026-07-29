"use client";

import {
  paymentStateContent,
  type PaymentOperationalState,
  type PaymentReceipt,
  type PaymentReceiptCommandState,
  type PaymentReceiptListState,
} from "@tradeflow/payment-clearance";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

const methods = ["cash", "bank_transfer", "check", "electronic"] as const;

export function PaymentClearanceWorkspace() {
  const [queue, setQueue] = useState<PaymentReceiptListState | null>(null);
  const [result, setResult] = useState<PaymentReceiptCommandState | null>(null);
  const [busy, setBusy] = useState(false);
  const [method, setMethod] = useState<(typeof methods)[number]>("cash");
  const [branchId, setBranchId] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [salesOrderId, setSalesOrderId] = useState("");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [provider, setProvider] = useState("");
  const [documentUrl, setDocumentUrl] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const identity = useRef<{
    fingerprint: string;
    key: string;
    receiptId: string;
  } | null>(null);

  const loadQueue = useCallback(async () => {
    try {
      const response = await fetch(
        "/api/payments?status=pending_verification",
        { cache: "no-store" },
      );
      setQueue((await response.json()) as PaymentReceiptListState);
    } catch {
      setQueue({ correlationId: crypto.randomUUID(), kind: "unavailable" });
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetch("/api/payments?status=pending_verification", {
      cache: "no-store",
    })
      .then(async (response) => {
        const next = (await response.json()) as PaymentReceiptListState;
        if (active) setQueue(next);
      })
      .catch(() => {
        if (active) {
          setQueue({
            correlationId: crypto.randomUUID(),
            kind: "unavailable",
          });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const record = async () => {
    if (
      branchId.trim() === "" ||
      customerId.trim() === "" ||
      amount.trim() === ""
    ) {
      setMessage("Branch, Customer Account, and received amount are required.");
      return;
    }
    if (
      method !== "cash" &&
      (reference.trim() === "" ||
        provider.trim() === "" ||
        documentUrl.trim() === "")
    ) {
      setMessage(
        "Non-cash receipts require the external reference, account/provider, and evidence document.",
      );
      return;
    }
    const fingerprint = JSON.stringify({
      amount,
      branchId,
      customerId,
      documentUrl,
      method,
      provider,
      reference,
      salesOrderId,
    });
    if (identity.current?.fingerprint !== fingerprint) {
      identity.current = {
        fingerprint,
        key: crypto.randomUUID(),
        receiptId: crypto.randomUUID(),
      };
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch("/api/payments", {
        body: JSON.stringify({
          command: {
            amount,
            branch_id: branchId.trim(),
            currency: "PHP",
            customer_id: customerId.trim(),
            evidence:
              method === "cash"
                ? null
                : {
                    account_or_provider: provider.trim(),
                    document_url: documentUrl.trim(),
                    value_date: new Date().toISOString().slice(0, 10),
                  },
            external_reference: method === "cash" ? null : reference.trim(),
            payment_method: method,
            payment_receipt_id: identity.current.receiptId,
            received_at: new Date().toISOString(),
            sales_order_id:
              salesOrderId.trim() === "" ? null : salesOrderId.trim(),
          },
          idempotencyKey: identity.current.key,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const next = (await response.json()) as PaymentReceiptCommandState;
      setResult(next);
      if (next.kind === "recorded") {
        identity.current = null;
        await loadQueue();
      }
    } catch {
      setResult({ correlationId: crypto.randomUUID(), kind: "unavailable" });
    } finally {
      setBusy(false);
    }
  };

  const verify = async (receipt: PaymentReceipt) => {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/payments/${receipt.paymentReceiptId}/verification`,
        {
          body: JSON.stringify({
            command: {
              decision:
                receipt.paymentMethod === "check"
                  ? "evidence_verified"
                  : "cleared",
              reason: "Evidence and value date reviewed",
              verified_at: new Date().toISOString(),
            },
            idempotencyKey: crypto.randomUUID(),
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      setResult((await response.json()) as PaymentReceiptCommandState);
      await loadQueue();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="payment-app">
      <header className="payment-header">
        <Link href="/">TradeFlow</Link>
        <span>Finance / Payment clearance</span>
        <span>Server-authoritative ledger</span>
      </header>
      <main className="payment-main">
        <section className="payment-title">
          <div>
            <p className="eyebrow">Receipt → clear → cover → pick</p>
            <h1>Make cleared money visible.</h1>
          </div>
          <p>
            Record what arrived, separate evidence from cleared funds, and show
            Warehouse exactly when reserved value is safe to pick.
          </p>
        </section>

        <section className="payment-workbench">
          <div className="payment-maker">
            <div className="payment-section-head">
              <div>
                <span>Maker / receipt desk</span>
                <h2>Record customer payment</h2>
              </div>
              <strong>PHP</strong>
            </div>
            <div className="payment-methods" aria-label="Payment method">
              {methods.map((value) => (
                <button
                  aria-pressed={method === value}
                  key={value}
                  onClick={() => setMethod(value)}
                  type="button"
                >
                  {value.replaceAll("_", " ")}
                </button>
              ))}
            </div>
            <div className="payment-fields">
              <label>
                Branch ID
                <input
                  onChange={(event) => setBranchId(event.target.value)}
                  value={branchId}
                />
              </label>
              <label>
                Customer Account ID
                <input
                  onChange={(event) => setCustomerId(event.target.value)}
                  value={customerId}
                />
              </label>
              <label>
                Sales Order ID <small>optional</small>
                <input
                  onChange={(event) => setSalesOrderId(event.target.value)}
                  value={salesOrderId}
                />
              </label>
              <label>
                Received amount
                <input
                  inputMode="decimal"
                  onChange={(event) => setAmount(event.target.value)}
                  value={amount}
                />
              </label>
              {method !== "cash" && (
                <>
                  <label>
                    External reference
                    <input
                      onChange={(event) => setReference(event.target.value)}
                      value={reference}
                    />
                  </label>
                  <label>
                    Account or provider
                    <input
                      onChange={(event) => setProvider(event.target.value)}
                      value={provider}
                    />
                  </label>
                  <label className="payment-wide">
                    Evidence document URL
                    <input
                      onChange={(event) => setDocumentUrl(event.target.value)}
                      value={documentUrl}
                    />
                  </label>
                </>
              )}
            </div>
            {message !== null && (
              <p className="payment-message" role="alert">
                {message}
              </p>
            )}
            <button
              className="payment-primary"
              disabled={busy}
              onClick={() => void record()}
              type="button"
            >
              {busy ? "Posting…" : "Record immutable receipt"}
            </button>
          </div>

          <div className="payment-checker">
            <div className="payment-section-head">
              <div>
                <span>Checker / evidence queue</span>
                <h2>Pending verification</h2>
              </div>
              <strong>{queue?.kind === "ready" ? queue.total : "—"}</strong>
            </div>
            {queue === null ? (
              <p className="payment-empty" role="status">
                Loading scoped receipts…
              </p>
            ) : queue.kind !== "ready" ? (
              <p className="payment-empty" role="alert">
                Verification queue unavailable. Reference {queue.correlationId}
              </p>
            ) : queue.items.length === 0 ? (
              <p className="payment-empty" role="status">
                No non-cash evidence is waiting for this Finance scope.
              </p>
            ) : (
              <div className="payment-queue">
                {queue.items.map((receipt) => (
                  <article key={receipt.paymentReceiptId}>
                    <div>
                      <StatusLabel state={receipt.status} />
                      <h3>
                        {receipt.currency} {receipt.amount}
                      </h3>
                      <p>
                        {receipt.paymentMethod.replaceAll("_", " ")} ·{" "}
                        {receipt.externalReferenceNormalized}
                      </p>
                      <small>Recorded by {receipt.recordedBy}</small>
                    </div>
                    <button
                      disabled={busy}
                      onClick={() => void verify(receipt)}
                      type="button"
                    >
                      {receipt.paymentMethod === "check"
                        ? "Verify evidence"
                        : "Clear payment"}
                    </button>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>

        {result !== null && (
          <section className="payment-result" aria-live="polite">
            {result.kind === "recorded" || result.kind === "updated" ? (
              <>
                <StatusLabel state={result.receipt.status} />
                <h2>
                  {
                    paymentStateContent[
                      result.receipt.status as PaymentOperationalState
                    ].title
                  }
                </h2>
                <p>
                  {
                    paymentStateContent[
                      result.receipt.status as PaymentOperationalState
                    ].nextAction
                  }
                </p>
              </>
            ) : (
              <>
                <span className="payment-status critical">Command stopped</span>
                <h2>TradeFlow did not change the ledger</h2>
                <p>
                  Resolve the {result.kind.replaceAll("_", " ")} boundary and
                  retry with the same command identity.
                </p>
              </>
            )}
          </section>
        )}

        <section className="payment-state-guide" aria-labelledby="state-guide">
          <div>
            <p className="eyebrow">Shared operational language</p>
            <h2 id="state-guide">Every state says what happens next.</h2>
          </div>
          <div className="payment-state-grid">
            {(
              Object.entries(paymentStateContent) as Array<
                [
                  PaymentOperationalState,
                  (typeof paymentStateContent)[PaymentOperationalState],
                ]
              >
            ).map(([state, content]) => (
              <article key={state}>
                <StatusLabel state={state} />
                <h3>{content.title}</h3>
                <p>{content.description}</p>
                <strong>Next / {content.nextAction}</strong>
              </article>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

function StatusLabel({ state }: { state: PaymentOperationalState }) {
  const content = paymentStateContent[state];
  return (
    <span className={`payment-status ${content.tone}`}>
      {state.replaceAll("_", " ")}
    </span>
  );
}
