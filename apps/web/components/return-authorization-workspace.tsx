"use client";

import { type components } from "@tradeflow/api-client";
import { type FormEvent, useEffect, useRef, useState } from "react";

type ReturnRequest = components["schemas"]["ReturnRequestResponse"];
type Classifications = components["schemas"]["ReturnClassificationsResponse"];
type Mutation = { body: string };

export function ReturnAuthorizationWorkspace() {
  const [items, setItems] = useState<ReturnRequest[]>([]);
  const [selected, setSelected] = useState<ReturnRequest | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receiptId, setReceiptId] = useState("");
  const [deliveryLineId, setDeliveryLineId] = useState("");
  const [quantity, setQuantity] = useState("1.000000");
  const [classifications, setClassifications] =
    useState<Classifications | null>(null);
  const [reasonCode, setReasonCode] = useState("");
  const [partyCode, setPartyCode] = useState("");
  const createMutation = useRef<Mutation | null>(null);
  const authorizationMutation = useRef<Mutation | null>(null);

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
      const requestId = crypto.randomUUID();
      const mutation = createMutation.current ?? {
        body: JSON.stringify({
          command: {
            lines: [
              { delivery_line_id: deliveryLineId, quantity_base: quantity },
            ],
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
            Delivery Receipt ID
            <input
              required
              value={receiptId}
              onChange={(event) => {
                setReceiptId(event.target.value);
                createMutation.current = null;
              }}
            />
          </label>
          <label>
            Delivery Line ID
            <input
              required
              value={deliveryLineId}
              onChange={(event) => {
                setDeliveryLineId(event.target.value);
                createMutation.current = null;
              }}
            />
          </label>
          <label>
            Quantity
            <input
              required
              inputMode="decimal"
              value={quantity}
              onChange={(event) => {
                setQuantity(event.target.value);
                createMutation.current = null;
              }}
            />
          </label>
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
          <button disabled={busy || classifications === null}>
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
              </>
            )}
          </section>
        </div>
      </section>
    </main>
  );
}
