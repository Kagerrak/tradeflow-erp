"use client";

import { type components } from "@tradeflow/api-client";
import { type FormEvent, useEffect, useRef, useState } from "react";

type ReturnRequest = components["schemas"]["ReturnRequestResponse"];
type Classifications = components["schemas"]["ReturnClassificationsResponse"];
type Eligibility = components["schemas"]["ReturnEligibilityResponse"];
type EvidenceItem = components["schemas"]["ReturnEvidenceItem"];
type EvidenceList = components["schemas"]["ReturnEvidenceList"];
type EvidenceSyncState = components["schemas"]["ReturnEvidenceSyncState"];
type Mutation = { body: string };

export function ReturnAuthorizationWorkspace() {
  const [items, setItems] = useState<ReturnRequest[]>([]);
  const [selected, setSelected] = useState<ReturnRequest | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receiptId, setReceiptId] = useState("");
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  const [lineQuantities, setLineQuantities] = useState<Record<string, string>>(
    {},
  );
  const [classifications, setClassifications] =
    useState<Classifications | null>(null);
  const [reasonCode, setReasonCode] = useState("");
  const [partyCode, setPartyCode] = useState("");
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [syncState, setSyncState] = useState<EvidenceSyncState | null>(null);
  const [noteText, setNoteText] = useState("");
  const [evidenceBusy, setEvidenceBusy] = useState(false);
  const createMutation = useRef<Mutation | null>(null);
  const authorizationMutation = useRef<Mutation | null>(null);
  const noteMutation = useRef<Mutation | null>(null);

  useEffect(() => {
    void fetch("/api/return-requests?status=pending_authorization", {
      cache: "no-store",
    })
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

  async function loadEligibility() {
    setBusy(true);
    setError(null);
    setEligibility(null);
    setLineQuantities({});
    createMutation.current = null;
    try {
      const response = await fetch(
        `/api/delivery-receipts/${receiptId}/return-eligibility`,
        { cache: "no-store" },
      );
      const payload = (await response.json()) as Eligibility & {
        message?: string;
      };
      if (!response.ok)
        throw new Error(
          payload.message ?? "Return eligibility could not be loaded.",
        );
      setEligibility(payload);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Return eligibility could not be loaded.",
      );
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void fetch("/api/return-classifications", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok)
          throw new Error("Return classifications could not be loaded.");
        return (await response.json()) as Classifications;
      })
      .then((payload) => {
        setClassifications(payload);
        setReasonCode(payload.reasons[0]?.code ?? "");
        setPartyCode(payload.responsible_parties[0]?.code ?? "");
      })
      .catch((reason: unknown) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "Return classifications could not be loaded.",
        ),
      );
  }, []);

  useEffect(() => {
    if (selected === null) return;
    const returnRequestId = selected.return_request_id;
    const controller = new AbortController();
    async function loadEvidence() {
      setEvidenceBusy(true);
      try {
        const [evidenceResponse, syncResponse] = await Promise.all([
          fetch(`/api/return-requests/${returnRequestId}/evidence`, {
            cache: "no-store",
            signal: controller.signal,
          }),
          fetch(`/api/return-requests/${returnRequestId}/sync-state`, {
            cache: "no-store",
            signal: controller.signal,
          }),
        ]);
        if (!evidenceResponse.ok || !syncResponse.ok) {
          throw new Error("Return evidence could not be loaded.");
        }
        const evidencePayload = (await evidenceResponse.json()) as EvidenceList;
        const syncPayload = (await syncResponse.json()) as EvidenceSyncState;
        setEvidence(evidencePayload.items);
        setSyncState(syncPayload);
      } catch (reason: unknown) {
        if (reason instanceof DOMException && reason.name === "AbortError")
          return;
        setError(
          reason instanceof Error
            ? reason.message
            : "Return evidence could not be loaded.",
        );
      } finally {
        setEvidenceBusy(false);
      }
    }
    void loadEvidence();
    return () => controller.abort();
  }, [selected]);

  async function addNote(event: FormEvent) {
    event.preventDefault();
    if (selected === null || noteText.trim() === "") return;
    setEvidenceBusy(true);
    setError(null);
    try {
      const evidenceId = crypto.randomUUID();
      const mutation = noteMutation.current ?? {
        body: JSON.stringify({
          command: {
            evidence_id: evidenceId,
            device_captured_at: new Date().toISOString(),
            note_text: noteText.trim(),
          },
          idempotencyKey: `return-evidence-note:${evidenceId}`,
        }),
      };
      noteMutation.current = mutation;
      const response = await fetch(
        `/api/return-requests/${selected.return_request_id}/evidence/notes`,
        {
          body: mutation.body,
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const payload = (await response.json()) as EvidenceItem & {
        message?: string;
      };
      if (!response.ok) {
        if (response.status < 500) noteMutation.current = null;
        throw new Error(payload.message ?? "Note could not be added.");
      }
      noteMutation.current = null;
      setNoteText("");
      setEvidence((current) => [...current, payload]);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Note could not be added.",
      );
    } finally {
      setEvidenceBusy(false);
    }
  }

  async function authorize() {
    if (selected === null) return;
    setBusy(true);
    setError(null);
    try {
      const mutation = authorizationMutation.current ?? {
        body: JSON.stringify({
          command: { expected_request_version: selected.version },
          idempotencyKey: `return-authorization:${crypto.randomUUID()}`,
        }),
      };
      authorizationMutation.current = mutation;
      const response = await fetch(
        `/api/return-requests/${selected.return_request_id}/authorization`,
        {
          body: mutation.body,
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const payload = (await response.json()) as ReturnRequest & {
        message?: string;
      };
      if (!response.ok) {
        if (response.status < 500) authorizationMutation.current = null;
        throw new Error(payload.message ?? "Return Authorization failed.");
      }
      authorizationMutation.current = null;
      setSelected(payload);
      setItems((current) =>
        current.filter(
          (item) => item.return_request_id !== payload.return_request_id,
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Return Authorization failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function createRequest(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const reason = classifications?.reasons.find(
        (item) => item.code === reasonCode,
      );
      const party = classifications?.responsible_parties.find(
        (item) => item.code === partyCode,
      );
      if (reason === undefined || party === undefined)
        throw new Error("Select valid return classifications.");
      const lines = Object.entries(lineQuantities)
        .filter(([, value]) => Number(value) > 0)
        .map(([deliveryLineId, quantityBase]) => ({
          delivery_line_id: deliveryLineId,
          quantity_base: quantityBase,
        }));
      if (eligibility === null || lines.length === 0)
        throw new Error(
          "Load a receipt and select at least one eligible line.",
        );
      const requestId = crypto.randomUUID();
      const mutation = createMutation.current ?? {
        body: JSON.stringify({
          command: {
            lines,
            reason_code: reason.code,
            reason_label: reason.label,
            responsible_party_code: party.code,
            responsible_party_label: party.label,
            return_request_id: requestId,
          },
          idempotencyKey: `return-request:${requestId}`,
          receiptId,
        }),
      };
      createMutation.current = mutation;
      const response = await fetch("/api/return-requests", {
        body: mutation.body,
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const payload = (await response.json()) as ReturnRequest & {
        message?: string;
      };
      if (!response.ok) {
        if (response.status < 500) createMutation.current = null;
        throw new Error(payload.message ?? "Return Request failed.");
      }
      createMutation.current = null;
      setItems((current) => [payload, ...current]);
      setSelected(payload);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Return Request failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="correction-app">
      <section className="correction-main">
        <header className="correction-intro">
          <p className="eyebrow">Returns · maker-checker</p>
          <h1>Return authorizations</h1>
          <p>
            Approve only quantities still eligible against the current Delivery
            Receipt.
          </p>
        </header>
        {error !== null && <p role="alert">{error}</p>}
        <form
          className="correction-tabs"
          aria-label="Create Return Request"
          onSubmit={(event) => void createRequest(event)}
        >
          <label>
            Delivery Receipt ID (paste or scan)
            <input
              required
              value={receiptId}
              onChange={(event) => {
                setReceiptId(event.target.value);
                setEligibility(null);
                setLineQuantities({});
                createMutation.current = null;
              }}
            />
          </label>
          <button
            type="button"
            disabled={busy || receiptId.trim() === ""}
            onClick={() => void loadEligibility()}
          >
            Load delivered lines
          </button>
          {eligibility !== null && (
            <fieldset>
              <legend>
                Receipt {eligibility.number}: select eligible delivered lines
              </legend>
              {eligibility.lines.map((line) => (
                <label key={line.delivery_line_id}>
                  <input
                    type="checkbox"
                    disabled={Number(line.eligible_quantity_base) <= 0}
                    checked={line.delivery_line_id in lineQuantities}
                    onChange={(event) => {
                      setLineQuantities((current) => {
                        const next = { ...current };
                        if (event.target.checked)
                          next[line.delivery_line_id] =
                            line.eligible_quantity_base;
                        else delete next[line.delivery_line_id];
                        return next;
                      });
                      createMutation.current = null;
                    }}
                  />
                  SKU {line.sku_id}: {line.eligible_quantity_base} eligible of{" "}
                  {line.delivered_quantity_base} delivered
                  {line.delivery_line_id in lineQuantities && (
                    <input
                      aria-label={`Return quantity for ${line.sku_id}`}
                      inputMode="decimal"
                      max={line.eligible_quantity_base}
                      min="0.000001"
                      step="0.000001"
                      value={lineQuantities[line.delivery_line_id]}
                      onChange={(event) => {
                        setLineQuantities((current) => ({
                          ...current,
                          [line.delivery_line_id]: event.target.value,
                        }));
                        createMutation.current = null;
                      }}
                    />
                  )}
                </label>
              ))}
            </fieldset>
          )}
          <label>
            Return reason
            <select
              required
              value={reasonCode}
              onChange={(event) => {
                setReasonCode(event.target.value);
                createMutation.current = null;
              }}
            >
              {classifications?.reasons.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Responsible party
            <select
              required
              value={partyCode}
              onChange={(event) => {
                setPartyCode(event.target.value);
                createMutation.current = null;
              }}
            >
              {classifications?.responsible_parties.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={
              busy ||
              classifications === null ||
              eligibility === null ||
              Object.keys(lineQuantities).length === 0
            }
          >
            Create return request
          </button>
        </form>
        <div className="correction-workspace">
          <section
            className="correction-ledger"
            aria-label="Pending Return Requests"
          >
            {items.length === 0 && <p>No pending Return Requests.</p>}
            {items.map((item) => (
              <button
                key={item.return_request_id}
                onClick={() => {
                  setSelected(item);
                  setReviewed(false);
                  authorizationMutation.current = null;
                }}
              >
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
              <p>Select a Return Request to review.</p>
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
                </dl>
                <div aria-label="Return eligibility">
                  {selected.lines.map((line) => (
                    <p key={line.delivery_line_id}>
                      <strong>{line.sku_id}</strong>: {line.quantity_base} of{" "}
                      {line.eligible_quantity_base} eligible;{" "}
                      {line.delivered_quantity_base} delivered
                    </p>
                  ))}
                </div>
                {selected.status === "authorized" ? (
                  <p>Authorized by {selected.authorized_by}</p>
                ) : (
                  <>
                    <label>
                      <input
                        type="checkbox"
                        checked={reviewed}
                        onChange={(event) => setReviewed(event.target.checked)}
                      />{" "}
                      I reviewed delivery eligibility and responsibility
                    </label>
                    <button
                      disabled={!reviewed || busy}
                      onClick={() => void authorize()}
                    >
                      Authorize return
                    </button>
                  </>
                )}
                <section
                  aria-label="Return evidence"
                  className="evidence-panel"
                >
                  <h3>Evidence</h3>
                  {evidenceBusy ? (
                    <p>Loading evidence…</p>
                  ) : (
                    <>
                      {syncState !== null && (
                        <p
                          className={`sync-status sync-status-${syncState.status}`}
                        >
                          Sync: {syncState.status}
                          {syncState.status === "conflict" &&
                          syncState.conflict_reason
                            ? ` — ${syncState.conflict_reason}`
                            : null}
                        </p>
                      )}
                      {evidence.length === 0 ? (
                        <p>No evidence captured yet.</p>
                      ) : (
                        <ul className="evidence-list">
                          {evidence.map((item) => (
                            <li key={item.evidence_id}>
                              <strong>
                                {item.kind === "photo" ? "Photo" : "Note"}
                              </strong>{" "}
                              <span
                                className={`evidence-status evidence-status-${item.status}`}
                              >
                                {item.status}
                              </span>
                              <br />
                              <small>
                                {item.captured_by} ·{" "}
                                {new Date(
                                  item.device_captured_at,
                                ).toLocaleString()}
                              </small>
                              {item.kind === "note" && item.note_text ? (
                                <p>{item.note_text}</p>
                              ) : null}
                              {item.kind === "photo" ? (
                                <small>
                                  {item.content_type} · {item.size_bytes} bytes
                                </small>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      )}
                      {selected.status !== "authorized" && (
                        <form onSubmit={(event) => void addNote(event)}>
                          <label>
                            Add note
                            <textarea
                              disabled={evidenceBusy}
                              maxLength={2000}
                              onChange={(event) =>
                                setNoteText(event.target.value)
                              }
                              value={noteText}
                            />
                          </label>
                          <button
                            disabled={evidenceBusy || noteText.trim() === ""}
                            type="submit"
                          >
                            Save note
                          </button>
                        </form>
                      )}
                    </>
                  )}
                </section>
              </>
            )}
          </section>
        </div>
      </section>
    </main>
  );
}
