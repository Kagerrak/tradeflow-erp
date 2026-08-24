"use client";

import {
  paymentStateContent,
  type PaymentOperationalState,
  type PaymentReceipt,
  type PaymentReceiptCommandState,
  type PaymentReceiptListState,
} from "@tradeflow/payment-clearance";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "./ui/page-header";

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
  const [conversionDeliveryId, setConversionDeliveryId] = useState("");
  const [conversionVersion, setConversionVersion] = useState("1");
  const [conversionReason, setConversionReason] = useState("");
  const [conversionMessage, setConversionMessage] = useState<string | null>(
    null,
  );
  const [cashReceiptId, setCashReceiptId] = useState("");
  const [cashCounted, setCashCounted] = useState("");
  const [cashReason, setCashReason] = useState("");
  const [cashMessage, setCashMessage] = useState<string | null>(null);
  const cashIdentity = useRef<{
    fingerprint: string;
    idempotencyKey: string;
    reconciliationId: string;
  } | null>(null);
  const conversionIdentity = useRef<{
    fingerprint: string;
    idempotencyKey: string;
    conversionId: string;
  } | null>(null);
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

  const convertCOD = async () => {
    if (
      conversionDeliveryId.trim() === "" ||
      conversionReason.trim() === "" ||
      Number(conversionVersion) < 1
    ) {
      setConversionMessage(
        "Delivery, current version, and approval reason are required.",
      );
      return;
    }
    const fingerprint = JSON.stringify({
      conversionDeliveryId,
      conversionReason,
      conversionVersion,
    });
    if (conversionIdentity.current?.fingerprint !== fingerprint) {
      conversionIdentity.current = {
        conversionId: crypto.randomUUID(),
        fingerprint,
        idempotencyKey: crypto.randomUUID(),
      };
    }
    setBusy(true);
    try {
      const response = await fetch(
        `/api/deliveries/${conversionDeliveryId.trim()}/cod-conversion`,
        {
          body: JSON.stringify({
            command: {
              conversion_id: conversionIdentity.current.conversionId,
              expected_delivery_version: Number(conversionVersion),
              reason: conversionReason.trim(),
            },
            idempotencyKey: conversionIdentity.current.idempotencyKey,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const payload = (await response.json()) as {
        amount?: string;
        correlationId?: string;
        message?: string;
        status?: string;
      };
      if (response.ok) {
        setConversionMessage(
          `Converted PHP ${payload.amount ?? "0.00"} to On Account; the assigned driver may now confirm.`,
        );
        conversionIdentity.current = null;
      } else {
        setConversionMessage(
          `${payload.message ?? "Conversion stopped."} · ${payload.correlationId ?? "no reference"}`,
        );
      }
    } catch {
      setConversionMessage(
        "COD conversion service unavailable. Retry unchanged work; the command identity is retained.",
      );
    } finally {
      setBusy(false);
    }
  };

  const reconcileCash = async () => {
    if (
      cashReceiptId.trim() === "" ||
      cashCounted.trim() === "" ||
      cashReason.trim() === ""
    ) {
      setCashMessage(
        "Cash receipt, counted amount, and discrepancy reason are required.",
      );
      return;
    }
    const fingerprint = JSON.stringify({
      cashCounted,
      cashReason,
      cashReceiptId,
    });
    if (cashIdentity.current?.fingerprint !== fingerprint) {
      cashIdentity.current = {
        fingerprint,
        idempotencyKey: crypto.randomUUID(),
        reconciliationId: crypto.randomUUID(),
      };
    }
    setBusy(true);
    try {
      const response = await fetch(
        `/api/payments/${cashReceiptId.trim()}/cash-reconciliation`,
        {
          body: JSON.stringify({
            command: {
              cash_reconciliation_id: cashIdentity.current.reconciliationId,
              counted_amount: cashCounted,
              reason: cashReason.trim(),
              reconciled_at: new Date().toISOString(),
            },
            idempotencyKey: cashIdentity.current.idempotencyKey,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const payload = (await response.json()) as {
        correlationId?: string;
        message?: string;
        variance_amount?: string;
      };
      if (response.ok) {
        setCashMessage(
          `Cash reconciled; recorded variance PHP ${payload.variance_amount ?? "0.00"}.`,
        );
        cashIdentity.current = null;
      } else {
        setCashMessage(
          `${payload.message ?? "Reconciliation stopped."} · ${payload.correlationId ?? "no reference"}`,
        );
      }
    } catch {
      setCashMessage(
        "Cash reconciliation service unavailable. Retry unchanged work; the command identity is retained.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        description="Record what arrived, separate evidence from cleared funds, and show Warehouse exactly when reserved value is safe to pick."
        eyebrow="Finance"
        title="Payment clearance"
      />

      <section className="payment-title card">
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
          <div className="payment-section-head">
            <div>
              <span>Checker / COD exception</span>
              <h2>Convert unpaid COD to On Account</h2>
            </div>
          </div>
          <div className="payment-fields">
            <label>
              Delivery ID
              <input
                onChange={(event) =>
                  setConversionDeliveryId(event.target.value)
                }
                value={conversionDeliveryId}
              />
            </label>
            <label>
              Current Delivery version
              <input
                inputMode="numeric"
                onChange={(event) => setConversionVersion(event.target.value)}
                value={conversionVersion}
              />
            </label>
            <label className="payment-wide">
              Credit Override reason
              <input
                onChange={(event) => setConversionReason(event.target.value)}
                value={conversionReason}
              />
            </label>
          </div>
          <button
            className="payment-primary"
            disabled={busy}
            onClick={() => void convertCOD()}
            type="button"
          >
            Approve COD conversion
          </button>
          {conversionMessage !== null && (
            <p className="payment-message" role="status">
              {conversionMessage}
            </p>
          )}
          <div className="payment-section-head">
            <div>
              <span>Finance / cash custody</span>
              <h2>Reconcile physical COD cash</h2>
            </div>
          </div>
          <div className="payment-fields">
            <label>
              Cash Payment Receipt ID
              <input
                onChange={(event) => setCashReceiptId(event.target.value)}
                value={cashReceiptId}
              />
            </label>
            <label>
              Counted cash
              <input
                inputMode="decimal"
                onChange={(event) => setCashCounted(event.target.value)}
                value={cashCounted}
              />
            </label>
            <label className="payment-wide">
              Reconciliation or discrepancy reason
              <input
                onChange={(event) => setCashReason(event.target.value)}
                value={cashReason}
              />
            </label>
          </div>
          <button
            className="payment-primary"
            disabled={busy}
            onClick={() => void reconcileCash()}
            type="button"
          >
            Reconcile COD cash
          </button>
          {cashMessage !== null && (
            <p className="payment-message" role="status">
              {cashMessage}
            </p>
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
              {result.receipt.status === "cleared" && (
                <p>
                  {result.receipt.currency} {result.receipt.unappliedAmount}{" "}
                  remains {result.receipt.applicationState.replaceAll("_", " ")}
                  ; no unrelated invoice balance changed.
                </p>
              )}
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
    </>
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
