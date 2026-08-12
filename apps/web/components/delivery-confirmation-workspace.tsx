"use client";

import type {
  AssignedDelivery,
  AssignedDeliveryListState,
  FailureState,
} from "@tradeflow/delivery-dispatch";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

type ConfirmationResponse = {
  collection?: {
    amount_collected: string;
    cash_reconciliation_status: "pending" | null;
    payment_method: string;
    status: string;
  } | null;
  confirmation_id: string;
  delivery_receipt: {
    delivery_receipt_id: string;
    number: string;
    status: "pending_document" | "ready" | "unavailable";
  };
};
type OperationState =
  | { kind: "empty" }
  | { kind: "pending" }
  | { kind: "upload_failed" }
  | FailureState
  | { kind: "confirmed"; response: ConfirmationResponse };
type PendingWebConfirmation = {
  capturedAt: string;
  confirmationId: string;
  evidence: Array<{
    evidenceId: string;
    file: File;
    kind: "photo" | "signature";
  }>;
  idempotencyKey: string;
  paymentReceiptId: string | null;
};

export function DeliveryConfirmationWorkspace() {
  const [list, setList] = useState<AssignedDeliveryListState | null>(null);
  const [selected, setSelected] = useState<AssignedDelivery | null>(null);
  const [recipient, setRecipient] = useState("");
  const [notes, setNotes] = useState("");
  const [cashCollected, setCashCollected] = useState("");
  const [settlementMode, setSettlementMode] = useState<
    "cash" | "noncash" | "on_account"
  >("cash");
  const [noncashMethod, setNoncashMethod] = useState<
    "bank_transfer" | "check" | "electronic"
  >("bank_transfer");
  const [paymentReceiptId, setPaymentReceiptId] = useState("");
  const [conversionId, setConversionId] = useState("");
  const [signature, setSignature] = useState<File | null>(null);
  const [photos, setPhotos] = useState<File[]>([]);
  const [operation, setOperation] = useState<OperationState>({ kind: "empty" });
  const [receiptUrl, setReceiptUrl] = useState<string | null>(null);
  const pending = useRef<PendingWebConfirmation | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch("/api/deliveries", { cache: "no-store" });
        setList((await response.json()) as AssignedDeliveryListState);
      } catch {
        setList({
          code: "delivery_service_unavailable",
          correlationId: crypto.randomUUID(),
          kind: "unavailable",
          message: "Assigned Deliveries could not be reached.",
        });
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const choose = (delivery: AssignedDelivery) => {
    setSelected(delivery);
    setRecipient(delivery.recipientName);
    setCashCollected(delivery.collectionAmountDue ?? "");
    setSettlementMode("cash");
    setPaymentReceiptId("");
    setConversionId("");
    setOperation({ kind: "empty" });
    setReceiptUrl(null);
    pending.current = null;
  };

  const edit = (change: () => void) => {
    pending.current = null;
    change();
  };

  const confirm = async () => {
    if (
      selected === null ||
      signature === null ||
      recipient.trim().length === 0
    )
      return;
    if (selected.collectionRequired && selected.collectionAmountDue === null)
      return;
    if (
      selected.collectionRequired &&
      settlementMode !== "on_account" &&
      (!isCanonicalPositiveDecimal(cashCollected) ||
        Number(cashCollected) < Number(selected.collectionAmountDue))
    )
      return;
    if (
      selected.collectionRequired &&
      settlementMode === "noncash" &&
      paymentReceiptId.trim() === ""
    )
      return;
    if (
      selected.collectionRequired &&
      settlementMode === "on_account" &&
      conversionId.trim() === ""
    )
      return;
    setOperation({ kind: "pending" });
    let work = pending.current;
    if (work === null) {
      const confirmationId = crypto.randomUUID();
      work = {
        capturedAt: new Date().toISOString(),
        confirmationId,
        evidence: [
          {
            evidenceId: crypto.randomUUID(),
            file: signature,
            kind: "signature",
          },
          ...photos.map((file) => ({
            evidenceId: crypto.randomUUID(),
            file,
            kind: "photo" as const,
          })),
        ],
        idempotencyKey: `delivery-confirmation:${confirmationId}`,
        paymentReceiptId:
          selected.collectionRequired && settlementMode === "cash"
            ? crypto.randomUUID()
            : null,
      };
      pending.current = work;
    }
    try {
      const evidenceIds: string[] = [];
      for (const evidence of work.evidence) {
        evidenceIds.push(
          await uploadEvidence(
            selected.deliveryId,
            evidence.kind,
            evidence.file,
            evidence.evidenceId,
            work.capturedAt,
          ),
        );
      }
      const response = await fetch(
        `/api/deliveries/${selected.deliveryId}/confirmation`,
        {
          body: JSON.stringify({
            action: "confirm",
            command: {
              confirmation_id: work.confirmationId,
              device_captured_at: work.capturedAt,
              evidence_ids: evidenceIds,
              expected_delivery_version: selected.version,
              lines: selected.lines.map((line) => ({
                accepted_quantity_base: line.quantityBase,
                damaged_quantity_base: "0",
                delivery_line_id: line.deliveryLineId,
                exception_details: {},
                identity_partitions: line.identityPositions.map((position) => ({
                  accepted_quantity_base: position.quantityBase,
                  damaged_quantity_base: "0",
                  delivery_line_identity_allocation_id:
                    position.deliveryLineIdentityAllocationId,
                  refused_quantity_base: "0",
                  short_missing_quantity_base: "0",
                  still_undelivered_quantity_base: "0",
                })),
                refused_quantity_base: "0",
                short_missing_quantity_base: "0",
                still_undelivered_quantity_base: "0",
              })),
              notes: notes.trim() || null,
              recipient_name: recipient.trim(),
              ...(selected.collectionRequired && settlementMode !== "on_account"
                ? {
                    collection: {
                      amount: cashCollected,
                      currency: "PHP",
                      evidence: null,
                      external_reference: null,
                      payment_method:
                        settlementMode === "cash" ? "cash" : noncashMethod,
                      payment_receipt_id:
                        settlementMode === "cash"
                          ? work.paymentReceiptId
                          : paymentReceiptId.trim(),
                      received_at: work.capturedAt,
                    },
                  }
                : {}),
              ...(selected.collectionRequired && settlementMode === "on_account"
                ? { on_account_conversion_id: conversionId.trim() }
                : {}),
            },
            idempotencyKey: work.idempotencyKey,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const payload = (await response.json()) as
        ConfirmationResponse | FailureState;
      if (!response.ok) {
        setOperation(payload as FailureState);
        return;
      }
      setOperation({
        kind: "confirmed",
        response: payload as ConfirmationResponse,
      });
    } catch {
      setOperation({ kind: "upload_failed" });
    }
  };

  const refreshReceipt = async () => {
    if (operation.kind !== "confirmed") return;
    const receiptId = operation.response.delivery_receipt.delivery_receipt_id;
    const detailResponse = await fetch(`/api/delivery-receipts/${receiptId}`, {
      cache: "no-store",
    });
    if (!detailResponse.ok) return;
    const detail = (await detailResponse.json()) as {
      number: string;
      status: "pending_document" | "ready" | "unavailable";
    };
    const response = {
      ...operation.response,
      delivery_receipt: {
        ...operation.response.delivery_receipt,
        number: detail.number,
        status: detail.status,
      },
    };
    setOperation({ kind: "confirmed", response });
    if (detail.status === "ready") {
      const accessResponse = await fetch(
        `/api/delivery-receipts/${receiptId}`,
        {
          method: "POST",
        },
      );
      if (accessResponse.ok) {
        const access = (await accessResponse.json()) as { access_url: string };
        setReceiptUrl(access.access_url);
      }
    }
  };

  return (
    <div className="delivery-app">
      <header className="delivery-header">
        <Link href="/">TradeFlow</Link>
        <nav aria-label="Delivery navigation">
          <Link href="/dispatch">Dispatch</Link>
          <strong>Deliver</strong>
          <Link href="/delivery-corrections">Corrections</Link>
        </nav>
        <span>Proof of delivery / live</span>
      </header>
      <main className="delivery-main">
        <section className="delivery-intro">
          <p className="eyebrow">Accepted delivery / 006</p>
          <h1>Prove the handoff. Preserve the truth.</h1>
          <p>Capture recipient evidence and commit accepted quantity once.</p>
        </section>
        {list === null && <State title="Loading assigned Deliveries" />}
        {list !== null && list.kind !== "ready" && (
          <State title={stateTitle(list.kind)} detail={list.message} />
        )}
        {list?.kind === "ready" && list.items.length === 0 && (
          <State
            title="No assigned Deliveries"
            detail="The authorized delivery queue is empty."
          />
        )}
        {list?.kind === "ready" && list.items.length > 0 && (
          <div className="delivery-grid">
            <section
              aria-label="Assigned Deliveries"
              className="delivery-queue"
            >
              {list.items.map((delivery) => (
                <button
                  key={delivery.deliveryId}
                  onClick={() => choose(delivery)}
                >
                  <strong>{delivery.recipientName}</strong>
                  <span>{delivery.deliveryId}</span>
                  <small>{delivery.status.toUpperCase()}</small>
                </button>
              ))}
            </section>
            {selected !== null && (
              <section className="delivery-capture">
                <p className="eyebrow">PROOF OF DELIVERY</p>
                <h2>{selected.recipientName}</h2>
                <>
                  {selected.collectionRequired && (
                    <div className="delivery-cod">
                      <p>
                        COD due: PHP{" "}
                        {selected.collectionAmountDue ?? "Unavailable"}
                      </p>
                      <div aria-label="COD settlement method">
                        {(["cash", "noncash", "on_account"] as const).map(
                          (value) => (
                            <button
                              aria-pressed={settlementMode === value}
                              key={value}
                              onClick={() =>
                                edit(() => setSettlementMode(value))
                              }
                              type="button"
                            >
                              {value.replaceAll("_", " ")}
                            </button>
                          ),
                        )}
                      </div>
                      {settlementMode !== "on_account" && (
                        <label>
                          COD amount collected
                          <input
                            aria-label="COD amount collected"
                            inputMode="decimal"
                            value={cashCollected}
                            onChange={(event) =>
                              edit(() => setCashCollected(event.target.value))
                            }
                          />
                        </label>
                      )}
                      {settlementMode === "noncash" && (
                        <>
                          <label>
                            Non-cash method
                            <select
                              aria-label="Non-cash method"
                              value={noncashMethod}
                              onChange={(event) =>
                                edit(() =>
                                  setNoncashMethod(
                                    event.target.value as typeof noncashMethod,
                                  ),
                                )
                              }
                            >
                              <option value="bank_transfer">
                                Bank transfer
                              </option>
                              <option value="check">Check</option>
                              <option value="electronic">Electronic</option>
                            </select>
                          </label>
                          <label>
                            Cleared Payment Receipt ID
                            <input
                              aria-label="Cleared Payment Receipt ID"
                              value={paymentReceiptId}
                              onChange={(event) =>
                                edit(() =>
                                  setPaymentReceiptId(event.target.value),
                                )
                              }
                            />
                          </label>
                        </>
                      )}
                      {settlementMode === "on_account" && (
                        <label>
                          Approved On Account conversion ID
                          <input
                            aria-label="Approved On Account conversion ID"
                            value={conversionId}
                            onChange={(event) =>
                              edit(() => setConversionId(event.target.value))
                            }
                          />
                        </label>
                      )}
                      <small>
                        Settlement and proof post together only after server
                        acknowledgement.
                      </small>
                    </div>
                  )}
                  <label>
                    Recipient name
                    <input
                      aria-label="Recipient name"
                      value={recipient}
                      onChange={(event) =>
                        edit(() => setRecipient(event.target.value))
                      }
                    />
                  </label>
                  <label>
                    Signature evidence
                    <input
                      aria-label="Signature evidence"
                      accept="image/jpeg,image/png,image/webp"
                      type="file"
                      onChange={(event) =>
                        edit(() =>
                          setSignature(event.target.files?.[0] ?? null),
                        )
                      }
                    />
                  </label>
                  <label>
                    Delivery photos
                    <input
                      aria-label="Delivery photos"
                      accept="image/jpeg,image/png,image/webp"
                      multiple
                      type="file"
                      onChange={(event) =>
                        edit(() =>
                          setPhotos(Array.from(event.target.files ?? [])),
                        )
                      }
                    />
                  </label>
                  <label>
                    Notes
                    <textarea
                      aria-label="Delivery notes"
                      value={notes}
                      onChange={(event) =>
                        edit(() => setNotes(event.target.value))
                      }
                    />
                  </label>
                  <button
                    disabled={
                      signature === null ||
                      operation.kind === "pending" ||
                      (selected.collectionRequired &&
                        (selected.collectionAmountDue === null ||
                          (settlementMode !== "on_account" &&
                            (!isCanonicalPositiveDecimal(cashCollected) ||
                              Number(cashCollected) <
                                Number(selected.collectionAmountDue))) ||
                          (settlementMode === "noncash" &&
                            paymentReceiptId.trim() === "") ||
                          (settlementMode === "on_account" &&
                            conversionId.trim() === "")))
                    }
                    onClick={() => void confirm()}
                  >
                    {selected.collectionRequired
                      ? "Confirm COD collection and delivery"
                      : "Confirm accepted quantity"}
                  </button>
                </>
              </section>
            )}
          </div>
        )}
        <OperationStateView
          onRefreshReceipt={() => void refreshReceipt()}
          receiptUrl={receiptUrl}
          state={operation}
        />
      </main>
    </div>
  );
}

function isCanonicalPositiveDecimal(value: string): boolean {
  return /^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/.test(value) && Number(value) > 0;
}

async function uploadEvidence(
  deliveryId: string,
  kind: "photo" | "signature",
  file: File,
  evidenceId: string,
  capturedAt: string,
): Promise<string> {
  if (
    !["image/jpeg", "image/png", "image/webp"].includes(file.type) ||
    file.size > 10 * 1024 * 1024
  )
    throw new Error("invalid evidence");
  const hash = [
    ...new Uint8Array(
      await crypto.subtle.digest("SHA-256", await file.arrayBuffer()),
    ),
  ]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  const intentResponse = await postAction(deliveryId, {
    action: "intent",
    command: {
      content_type: file.type,
      device_captured_at: capturedAt,
      evidence_id: evidenceId,
      kind,
      sha256: hash,
      size_bytes: file.size,
    },
  });
  const intent = (await intentResponse.json()) as {
    parts?: Array<{
      end_byte: number;
      start_byte: number;
      upload_headers: Record<string, string>;
      upload_url: string;
    }>;
    status?: string;
  };
  if (!intentResponse.ok) throw new Error("intent failed");
  if (intent.status !== "verified") {
    if (intent.parts === undefined)
      throw new Error("missing signed upload parts");
    const bytes = await file.arrayBuffer();
    for (const part of intent.parts) {
      const uploaded = await fetch(part.upload_url, {
        body: bytes.slice(part.start_byte, part.end_byte),
        headers: part.upload_headers,
        method: "PUT",
      });
      if (!uploaded.ok) throw new Error("upload failed");
    }
    const completed = await postAction(deliveryId, {
      action: "complete",
      evidenceId,
    });
    if (!completed.ok) throw new Error("verification failed");
  }
  return evidenceId;
}

function postAction(deliveryId: string, body: object): Promise<Response> {
  return fetch(`/api/deliveries/${deliveryId}/confirmation`, {
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
    method: "POST",
  });
}

function OperationStateView({
  onRefreshReceipt,
  receiptUrl,
  state,
}: {
  onRefreshReceipt: () => void;
  receiptUrl: string | null;
  state: OperationState;
}) {
  if (state.kind === "empty")
    return <State title="No captured confirmations" />;
  if (state.kind === "pending")
    return (
      <State
        title="Pending Sync"
        detail="Uploading evidence and waiting for server acknowledgement."
      />
    );
  if (state.kind === "upload_failed")
    return (
      <State
        title="Upload failed — evidence retained"
        detail="Retry the same proof without changing its identity."
      />
    );
  if (state.kind === "confirmed")
    return (
      <section className="delivery-state" aria-live="polite">
        <h2>Delivery confirmed</h2>
        <p>
          {state.response.collection?.status === "cleared"
            ? `COD ${state.response.collection.payment_method} collection of PHP ${state.response.collection.amount_collected} cleared; cash reconciliation is pending. `
            : ""}
          {state.response.delivery_receipt.status === "ready"
            ? `Receipt ${state.response.delivery_receipt.number} is ready.`
            : state.response.delivery_receipt.status === "unavailable"
              ? "Receipt rendering unavailable — background retry scheduled."
              : "Receipt unavailable — rendering in progress."}
        </p>
        <button onClick={onRefreshReceipt}>Refresh receipt</button>
        {receiptUrl !== null && (
          <a href={receiptUrl} rel="noreferrer">
            Open signed receipt
          </a>
        )}
      </section>
    );
  return (
    <State
      title={stateTitle(state.kind)}
      detail={`${state.message} · ${state.correlationId}`}
    />
  );
}

function stateTitle(kind: string): string {
  if (kind === "forbidden") return "Confirmation forbidden";
  if (kind === "conflict") return "Confirmation conflict — review required";
  if (kind === "unavailable") return "Delivery service unavailable";
  if (kind === "unauthenticated") return "Sign in required";
  return "Confirmation could not be accepted";
}

function State({ title, detail }: { title: string; detail?: string }) {
  return (
    <section aria-live="polite" className="delivery-state">
      <h2>{title}</h2>
      {detail !== undefined && <p>{detail}</p>}
    </section>
  );
}
