"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "./ui/field";
import { Input } from "./ui/input";
import { PageHeader } from "./ui/page-header";
import { randomId } from "@/lib/random-id";

type AdjustmentList = components["schemas"]["AdjustmentListResponseWrapper"];
type AdjustmentItem = components["schemas"]["AdjustmentResponse"];
type ErrorBody = { correlationId?: string; kind?: string };
type ListState =
  | { kind: "loading" }
  | { kind: "ready"; adjustments: AdjustmentList }
  | { kind: "unavailable"; correlationId: string };
type CommandIdentity = { fingerprint: string; key: string };

const inventoryTabs = [
  { href: "/inventory", label: "Stock ledger" },
  { href: "/inventory/transfers", label: "Transfers" },
  { href: "/inventory/adjustments", label: "Adjustments" },
];

function readCorrelationId(body: unknown): string {
  if (
    typeof body === "object" &&
    body !== null &&
    "correlationId" in body &&
    typeof body.correlationId === "string" &&
    body.correlationId.length > 0
  ) {
    return body.correlationId;
  }
  return randomId();
}

async function fetchAdjustments(): Promise<ListState> {
  try {
    const response = await fetch("/api/inventory/adjustments", {
      cache: "no-store",
    });
    const data = (await response.json()) as AdjustmentList | ErrorBody;
    if (response.ok && "items" in data) {
      return { kind: "ready", adjustments: data };
    }
    return { kind: "unavailable", correlationId: readCorrelationId(data) };
  } catch {
    return { kind: "unavailable", correlationId: randomId() };
  }
}

export function InventoryAdjustmentWorkspace() {
  const pathname = usePathname();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [skuId, setSkuId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [kind, setKind] = useState<"surplus" | "shortage">("surplus");
  const [quantity, setQuantity] = useState("");
  const [unitCode, setUnitCode] = useState("EA");
  const [reason, setReason] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [lotCode, setLotCode] = useState("");
  const requestIdentity = useRef<CommandIdentity | null>(null);
  const postIdentities = useRef(new Map<string, string>());
  const reverseIdentities = useRef(new Map<string, string>());

  const refresh = useCallback(async () => {
    setState(await fetchAdjustments());
  }, []);

  useEffect(() => {
    let active = true;
    fetchAdjustments().then((next) => {
      if (active) {
        setState(next);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  const tabs = (
    <>
      {inventoryTabs.map((tab) => (
        <Link
          key={tab.href}
          href={tab.href}
          aria-current={pathname === tab.href ? "page" : undefined}
        >
          {tab.label}
        </Link>
      ))}
    </>
  );

  const request = async () => {
    if (
      !skuId ||
      !warehouseId ||
      !locationId ||
      !quantity ||
      !reason ||
      !sourceReference
    ) {
      setMessage("All fields except Lot Code are required.");
      return;
    }
    const command = {
      skuId,
      warehouseId,
      locationId,
      kind,
      quantity,
      unitCode,
      reason,
      sourceReference,
      lotCode: lotCode || undefined,
    };
    const fingerprint = JSON.stringify(command);
    if (requestIdentity.current?.fingerprint !== fingerprint) {
      requestIdentity.current = { fingerprint, key: randomId() };
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch("/api/inventory/adjustments", {
        body: JSON.stringify({
          ...command,
          idempotencyKey: requestIdentity.current.key,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const data = (await response.json()) as {
        adjustment?: { status?: string };
      };
      if (response.ok) {
        requestIdentity.current = null;
        setMessage(
          `Adjustment requested · ${data.adjustment?.status ?? "pending_authorization"}`,
        );
        setSkuId("");
        setWarehouseId("");
        setLocationId("");
        setQuantity("");
        setReason("");
        setSourceReference("");
        setLotCode("");
        await refresh();
      } else {
        setMessage("Adjustment request was rejected. Check stock and scope.");
      }
    } catch {
      setMessage("Adjustment service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  const post = async (adjustment: AdjustmentItem) => {
    let idempotencyKey = postIdentities.current.get(adjustment.adjustment_id);
    if (idempotencyKey === undefined) {
      idempotencyKey = randomId();
      postIdentities.current.set(adjustment.adjustment_id, idempotencyKey);
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/inventory/adjustments/${adjustment.adjustment_id}/post`,
        {
          body: JSON.stringify({
            expectedVersion: adjustment.version,
            idempotencyKey,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as {
        adjustment?: { status?: string };
      };
      if (response.ok) {
        postIdentities.current.delete(adjustment.adjustment_id);
        setMessage(
          `Adjustment posted · ${data.adjustment?.status ?? "posted"}`,
        );
        await refresh();
      } else {
        setMessage("Post was rejected. Check authorization and state.");
      }
    } catch {
      setMessage("Adjustment service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  const reverse = async (adjustment: AdjustmentItem) => {
    const reversalReason = window.prompt("Reason for reversal");
    if (reversalReason === null || reversalReason.length === 0) {
      return;
    }
    let idempotencyKey = reverseIdentities.current.get(
      adjustment.adjustment_id,
    );
    if (idempotencyKey === undefined) {
      idempotencyKey = randomId();
      reverseIdentities.current.set(adjustment.adjustment_id, idempotencyKey);
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/inventory/adjustments/${adjustment.adjustment_id}/reverse`,
        {
          body: JSON.stringify({
            expectedVersion: adjustment.version,
            reason: reversalReason,
            idempotencyKey,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as {
        adjustment?: { status?: string };
      };
      if (response.ok) {
        reverseIdentities.current.delete(adjustment.adjustment_id);
        setMessage(
          `Adjustment reversed · ${data.adjustment?.status ?? "reversed"}`,
        );
        await refresh();
      } else {
        setMessage("Reverse was rejected. Check authorization and state.");
      }
    } catch {
      setMessage("Adjustment service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        description="Correct inventory counts with authorized adjustments. Adjustments record surplus or shortage variances against available stock. Each adjustment is authorized, posted, and immutable."
        eyebrow="Inventory"
        tabs={tabs}
        title="Adjustments"
      />

      <section className="inventory-intro card">
        <div>
          <p className="eyebrow">Stock correction / 006</p>
          <h2>Correct inventory counts with authorized adjustments.</h2>
        </div>
        <p>
          Adjustments record surplus or shortage variances against available
          stock. Each adjustment is authorized, posted, and immutable.
        </p>
      </section>

      <section
        className="inventory-directory card"
        aria-labelledby="request-title"
      >
        <div className="inventory-section-head">
          <div>
            <span className="section-number">Request</span>
            <h2 id="request-title">Request an adjustment</h2>
          </div>
        </div>

        {message !== null && (
          <p
            className="inventory-message"
            role="status"
            data-testid="adjustment-message"
          >
            {message}
          </p>
        )}

        <form
          className="operational-form"
          onSubmit={(event) => {
            event.preventDefault();
            void request();
          }}
        >
          <FieldSet>
            <FieldLegend>Stock identity</FieldLegend>
            <FieldDescription>
              Locate the exact warehouse stock record being corrected.
            </FieldDescription>
            <FieldGroup className="operational-form-grid">
              <Field>
                <FieldLabel htmlFor="adjustment-sku-id">SKU ID</FieldLabel>
                <Input
                  data-testid="adjustment-sku-id"
                  id="adjustment-sku-id"
                  onChange={(event) => setSkuId(event.target.value)}
                  value={skuId}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="adjustment-warehouse">
                  Warehouse ID
                </FieldLabel>
                <Input
                  data-testid="adjustment-warehouse"
                  id="adjustment-warehouse"
                  onChange={(event) => setWarehouseId(event.target.value)}
                  value={warehouseId}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="adjustment-location">
                  Location ID
                </FieldLabel>
                <Input
                  data-testid="adjustment-location"
                  id="adjustment-location"
                  onChange={(event) => setLocationId(event.target.value)}
                  value={locationId}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="adjustment-lot-code">
                  Lot code (optional)
                </FieldLabel>
                <Input
                  data-testid="adjustment-lot-code"
                  id="adjustment-lot-code"
                  onChange={(event) => setLotCode(event.target.value)}
                  value={lotCode}
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          <FieldSet>
            <FieldLegend>Counted variance</FieldLegend>
            <FieldDescription>
              Record whether the physical count increases or reduces available
              stock.
            </FieldDescription>
            <FieldGroup className="operational-form-grid operational-form-grid-3">
              <Field>
                <FieldLabel htmlFor="adjustment-kind">Kind</FieldLabel>
                <select
                  className="operational-select"
                  data-testid="adjustment-kind"
                  id="adjustment-kind"
                  onChange={(event) =>
                    setKind(event.target.value as "surplus" | "shortage")
                  }
                  value={kind}
                >
                  <option value="surplus">Surplus</option>
                  <option value="shortage">Shortage</option>
                </select>
              </Field>
              <Field>
                <FieldLabel htmlFor="adjustment-quantity">Quantity</FieldLabel>
                <Input
                  data-testid="adjustment-quantity"
                  id="adjustment-quantity"
                  inputMode="decimal"
                  onChange={(event) => setQuantity(event.target.value)}
                  value={quantity}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="adjustment-unit">Unit code</FieldLabel>
                <Input
                  data-testid="adjustment-unit"
                  id="adjustment-unit"
                  onChange={(event) => setUnitCode(event.target.value)}
                  value={unitCode}
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          <FieldSet>
            <FieldLegend>Audit evidence</FieldLegend>
            <FieldDescription>
              Explain the variance and identify the count sheet or source
              record.
            </FieldDescription>
            <FieldGroup className="operational-form-grid">
              <Field>
                <FieldLabel htmlFor="adjustment-reason">Reason</FieldLabel>
                <Input
                  data-testid="adjustment-reason"
                  id="adjustment-reason"
                  onChange={(event) => setReason(event.target.value)}
                  value={reason}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="adjustment-source-reference">
                  Source reference
                </FieldLabel>
                <Input
                  data-testid="adjustment-source-reference"
                  id="adjustment-source-reference"
                  onChange={(event) => setSourceReference(event.target.value)}
                  value={sourceReference}
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          <div className="operational-form-action">
            <p>
              Posting requires authorization and creates an immutable stock
              movement.
            </p>
            <Button
              data-testid="adjustment-request"
              disabled={busy}
              type="submit"
            >
              {busy ? "Requesting…" : "Request adjustment"}
            </Button>
          </div>
        </form>
      </section>

      <section
        className="inventory-directory card"
        aria-labelledby="list-title"
      >
        <div className="inventory-section-head">
          <div>
            <span className="section-number">Adjustments</span>
            <h2 id="list-title">Adjustments in scope</h2>
          </div>
        </div>
        <AdjustmentListState
          retry={refresh}
          state={state}
          onPost={post}
          onReverse={reverse}
        />
      </section>
    </>
  );
}

function AdjustmentListState({
  onPost,
  onReverse,
  retry,
  state,
}: {
  onPost: (adjustment: AdjustmentItem) => void;
  onReverse: (adjustment: AdjustmentItem) => void;
  retry: () => void;
  state: ListState;
}) {
  if (state.kind === "loading") {
    return (
      <div className="inventory-message" role="status">
        <span className="inventory-loader" aria-hidden="true" />
        <h3>Loading adjustments…</h3>
      </div>
    );
  }
  if (state.kind !== "ready") {
    return (
      <div className="inventory-message" role="alert">
        <span className="inventory-alert" aria-hidden="true">
          !
        </span>
        <h3>Adjustments are unavailable</h3>
        <p>Confirm the service connection and try again.</p>
        <p className="support-reference">
          Support reference <code>{state.correlationId}</code>
        </p>
        <Button onClick={() => void retry()} type="button" variant="outline">
          Retry adjustments
        </Button>
      </div>
    );
  }
  if (state.adjustments.items.length === 0) {
    return (
      <div className="inventory-message inventory-empty">
        <span aria-hidden="true">∅</span>
        <h3>No adjustments in your warehouse scope</h3>
      </div>
    );
  }
  return (
    <div className="inventory-grid" aria-live="polite">
      {state.adjustments.items.map((adjustment) => (
        <article className="inventory-card" key={adjustment.adjustment_id}>
          <div className="inventory-card-head">
            <div>
              <span>{adjustment.status}</span>
              <h3>{adjustment.adjustment_id}</h3>
            </div>
            <strong>{adjustment.quantity_base}</strong>
          </div>
          <dl className="inventory-trace">
            <div>
              <dt>Kind</dt>
              <dd>{adjustment.kind}</dd>
            </div>
            <div>
              <dt>Warehouse / Location</dt>
              <dd>
                {adjustment.warehouse_id} / {adjustment.location_id}
              </dd>
            </div>
            <div>
              <dt>Reason</dt>
              <dd>{adjustment.reason}</dd>
            </div>
            <div>
              <dt>Reference</dt>
              <dd>{adjustment.source_reference}</dd>
            </div>
          </dl>
          {adjustment.status === "pending_authorization" && (
            <Button
              data-testid={`adjustment-post-${adjustment.adjustment_id}`}
              onClick={() => onPost(adjustment)}
              size="sm"
              type="button"
            >
              Post
            </Button>
          )}
          {adjustment.status === "posted" && (
            <Button
              data-testid={`adjustment-reverse-${adjustment.adjustment_id}`}
              onClick={() => onReverse(adjustment)}
              size="sm"
              type="button"
              variant="outline"
            >
              Reverse
            </Button>
          )}
        </article>
      ))}
    </div>
  );
}
