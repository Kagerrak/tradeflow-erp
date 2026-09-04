"use client";

import { type components } from "@tradeflow/api-client";
import { useEffect, useRef, useState } from "react";
import { randomId } from "@/lib/random-id";

type ReturnRequest = components["schemas"]["ReturnRequestResponse"];
type LineResponse = components["schemas"]["ReturnRequestLineResponse"];
type ReceiptResponse = components["schemas"]["ReturnReceiptResponse"];
type Outcome = components["schemas"]["ReturnReceiptLineCommand"]["outcome"];

type LineConfig = {
  outcome: Outcome;
  quantity: string;
  notes: string;
};

type MutationBody = {
  body: string;
  key: string;
  receiptId: string;
};

const OUTCOMES: Outcome[] = ["restock", "quarantine", "damaged", "rejected"];

export function ReturnReceiptWorkspace() {
  const [items, setItems] = useState<ReturnRequest[]>([]);
  const [selected, setSelected] = useState<ReturnRequest | null>(null);
  const [lineConfigs, setLineConfigs] = useState<Record<string, LineConfig>>(
    {},
  );
  const [notes, setNotes] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [posted, setPosted] = useState<ReceiptResponse | null>(null);
  const receiptMutation = useRef<MutationBody | null>(null);

  useEffect(() => {
    void fetch("/api/return-requests?status=authorized", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok)
          throw new Error("Return Requests could not be loaded.");
        return (await response.json()) as { items: ReturnRequest[] };
      })
      .then((payload) => setItems(payload.items))
      .catch((reason: unknown) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "Return Requests could not be loaded.",
        ),
      );
  }, []);

  const select = (item: ReturnRequest) => {
    setSelected(item);
    setNotes(item.notes ?? "");
    setPhotos([]);
    setPosted(null);
    setError(null);
    receiptMutation.current = null;
    const configs: Record<string, LineConfig> = {};
    for (const line of item.lines) {
      configs[line.return_request_line_id] = {
        notes: "",
        outcome: "restock",
        quantity: line.quantity_base,
      };
    }
    setLineConfigs(configs);
  };

  const updateLine = (lineId: string, patch: Partial<LineConfig>) => {
    setLineConfigs((current) => ({
      ...current,
      [lineId]: { ...current[lineId], ...patch } as LineConfig,
    }));
    receiptMutation.current = null;
  };

  const postReceipt = async () => {
    if (selected === null) return;
    const capturedAt = new Date().toISOString();
    setBusy(true);
    setError(null);
    try {
      const evidenceIds: string[] = [];
      for (const photo of photos) {
        const evidenceId = randomId();
        evidenceIds.push(
          await uploadEvidence(
            selected.return_request_id,
            photo,
            evidenceId,
            capturedAt,
          ),
        );
      }
      let mutation = receiptMutation.current;
      if (mutation === null) {
        const receiptId = randomId();
        const lines = buildLines(selected.lines, lineConfigs);
        const command = {
          evidence_ids: evidenceIds,
          expected_request_version: selected.version,
          lines,
          notes: notes.trim() || null,
          received_at: capturedAt,
          return_receipt_id: receiptId,
        };
        mutation = {
          body: JSON.stringify({
            command,
            idempotencyKey: `return-receipt:${receiptId}`,
          }),
          key: receiptId,
          receiptId,
        };
        receiptMutation.current = mutation;
      }
      const response = await fetch(
        `/api/return-requests/${selected.return_request_id}/receipts`,
        {
          body: mutation.body,
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const payload = (await response.json()) as ReceiptResponse & {
        message?: string;
      };
      if (!response.ok) {
        if (response.status < 500) receiptMutation.current = null;
        throw new Error(payload.message ?? "Return Receipt failed.");
      }
      receiptMutation.current = null;
      setPosted(payload);
      setItems((current) =>
        current.filter(
          (item) => item.return_request_id !== selected.return_request_id,
        ),
      );
      setSelected(null);
      setLineConfigs({});
      setPhotos([]);
      setNotes("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Return Receipt failed.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="correction-app">
      <section className="correction-main">
        <header className="correction-intro">
          <p className="eyebrow">Returns · receive and inspect</p>
          <h1>Return receipts</h1>
          <p>
            Receive authorized returns, inspect each line, and move stock into
            controlled custody.
          </p>
        </header>
        {error !== null && <p role="alert">{error}</p>}
        {posted !== null && (
          <section aria-live="polite">
            <h2>Receipt posted</h2>
            <p>
              Return Request {posted.return_request_id} is now {posted.status}.
            </p>
          </section>
        )}
        <div className="correction-workspace">
          <section
            className="correction-ledger"
            aria-label="Authorized Return Requests"
          >
            {items.length === 0 && <p>No authorized Return Requests.</p>}
            {items.map((item) => (
              <button key={item.return_request_id} onClick={() => select(item)}>
                <span>{item.reason_label}</span>
                <strong>{item.return_request_id}</strong>
                <small>
                  {item.affected_value_base_currency} {item.base_currency}
                </small>
              </button>
            ))}
          </section>
          <section className="correction-detail">
            {selected === null ? (
              <p>Select an authorized Return Request to receive.</p>
            ) : (
              <>
                <h2>{selected.return_request_id}</h2>
                <dl aria-label="Return classification">
                  <dt>Reason</dt>
                  <dd>
                    {selected.reason_label} ({selected.reason_code})
                  </dd>
                  <dt>Responsible party</dt>
                  <dd>{selected.responsible_party_label}</dd>
                  <dt>Requested by</dt>
                  <dd>{selected.requested_by}</dd>
                  <dt>Authorized by</dt>
                  <dd>{selected.authorized_by}</dd>
                </dl>
                <fieldset>
                  <legend>Inspection lines</legend>
                  {selected.lines.map((line) => (
                    <ReturnLineEditor
                      key={line.return_request_line_id}
                      config={lineConfigs[line.return_request_line_id]}
                      line={line}
                      onChange={(patch) =>
                        updateLine(line.return_request_line_id, patch)
                      }
                    />
                  ))}
                </fieldset>
                <label>
                  Receipt notes
                  <textarea
                    value={notes}
                    onChange={(event) => {
                      setNotes(event.target.value);
                      receiptMutation.current = null;
                    }}
                  />
                </label>
                <label>
                  Inspection photos
                  <input
                    accept="image/jpeg,image/png,image/webp"
                    multiple
                    type="file"
                    onChange={(event) => {
                      setPhotos(Array.from(event.target.files ?? []));
                      receiptMutation.current = null;
                    }}
                  />
                </label>
                <button
                  disabled={
                    busy ||
                    photos.length === 0 ||
                    !hasValidLines(selected.lines, lineConfigs)
                  }
                  onClick={() => void postReceipt()}
                >
                  Post return receipt
                </button>
              </>
            )}
          </section>
        </div>
      </section>
    </main>
  );
}

function ReturnLineEditor({
  config,
  line,
  onChange,
}: {
  config: LineConfig | undefined;
  line: LineResponse;
  onChange: (patch: Partial<LineConfig>) => void;
}) {
  if (config === undefined) return null;
  const quantityDisabled = config.outcome === "rejected";
  const quantity = quantityDisabled ? "0" : config.quantity;
  return (
    <div>
      <p>
        <strong>{line.sku_id}</strong>: {line.quantity_base} authorized
      </p>
      <label>
        Outcome
        <select
          value={config.outcome}
          onChange={(event) => {
            const outcome = event.target.value as Outcome;
            onChange({
              outcome,
              quantity: outcome === "rejected" ? "0" : line.quantity_base,
            });
          }}
        >
          {OUTCOMES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label>
        Received quantity
        <input
          aria-label={`Received quantity for ${line.sku_id}`}
          disabled={quantityDisabled}
          inputMode="decimal"
          max={line.quantity_base}
          min="0"
          step="0.000001"
          value={quantity}
          onChange={(event) => onChange({ quantity: event.target.value })}
        />
      </label>
      <label>
        Line notes
        <input
          value={config.notes}
          onChange={(event) => onChange({ notes: event.target.value })}
        />
      </label>
    </div>
  );
}

function buildLines(
  lines: LineResponse[],
  configs: Record<string, LineConfig>,
): components["schemas"]["ReturnReceiptLineCommand"][] {
  return lines.map((line) => {
    const config = configs[line.return_request_line_id];
    const outcome = config?.outcome ?? "restock";
    return {
      notes: config?.notes.trim() || null,
      outcome,
      received_quantity_base:
        outcome === "rejected" ? "0" : (config?.quantity ?? line.quantity_base),
      return_request_line_id: line.return_request_line_id,
    };
  });
}

function hasValidLines(
  lines: LineResponse[],
  configs: Record<string, LineConfig>,
): boolean {
  return lines.every((line) => {
    const config = configs[line.return_request_line_id];
    if (config === undefined) return false;
    if (config.outcome === "rejected") return true;
    const quantity = Number(config.quantity);
    const authorized = Number(line.quantity_base);
    return (
      isCanonicalPositiveDecimal(config.quantity) &&
      quantity > 0 &&
      quantity <= authorized
    );
  });
}

function isCanonicalPositiveDecimal(value: string): boolean {
  return /^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/.test(value) && Number(value) >= 0;
}

async function uploadEvidence(
  requestId: string,
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
  const intentResponse = await fetch(
    `/api/return-requests/${requestId}/evidence/uploads`,
    {
      body: JSON.stringify({
        command: {
          content_type: file.type,
          device_captured_at: capturedAt,
          evidence_id: evidenceId,
          kind: "photo",
          sha256: hash,
          size_bytes: file.size,
        },
      }),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    },
  );
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
    const completed = await fetch(
      `/api/return-requests/${requestId}/evidence/${evidenceId}/complete`,
      { method: "POST" },
    );
    if (!completed.ok) throw new Error("verification failed");
  }
  return evidenceId;
}
