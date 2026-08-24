"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "./ui/page-header";

type TransferList = components["schemas"]["TransferListResponse"];
type TransferItem =
  | components["schemas"]["TransferReceivedItem"]
  | components["schemas"]["TransferReleasedItem"];
type ErrorBody = { correlationId?: string; kind?: string };
type ListState =
  | { kind: "loading" }
  | { kind: "ready"; transfers: TransferList }
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
  return crypto.randomUUID();
}

async function fetchTransfers(): Promise<ListState> {
  try {
    const response = await fetch("/api/inventory/transfers", {
      cache: "no-store",
    });
    const data = (await response.json()) as TransferList | ErrorBody;
    if (response.ok && "items" in data) {
      return { kind: "ready", transfers: data };
    }
    return { kind: "unavailable", correlationId: readCorrelationId(data) };
  } catch {
    return { kind: "unavailable", correlationId: crypto.randomUUID() };
  }
}

export function InventoryTransferWorkspace() {
  const pathname = usePathname();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [skuId, setSkuId] = useState("");
  const [fromWarehouseId, setFromWarehouseId] = useState("");
  const [toWarehouseId, setToWarehouseId] = useState("");
  const [fromLocationId, setFromLocationId] = useState("");
  const [toLocationId, setToLocationId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [unitCode, setUnitCode] = useState("EA");
  const [reason, setReason] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [lotCode, setLotCode] = useState("");
  const requestIdentity = useRef<CommandIdentity | null>(null);
  const receiveIdentities = useRef(new Map<string, string>());

  const refresh = useCallback(async () => {
    setState(await fetchTransfers());
  }, []);

  useEffect(() => {
    let active = true;
    fetchTransfers().then((next) => {
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
      !fromWarehouseId ||
      !toWarehouseId ||
      !fromLocationId ||
      !toLocationId ||
      !quantity ||
      !reason ||
      !sourceReference
    ) {
      setMessage("All fields except Lot Code are required.");
      return;
    }
    const command = {
      fromLocationId,
      fromWarehouseId,
      lotCode: lotCode || undefined,
      quantity,
      reason,
      skuId,
      sourceReference,
      toLocationId,
      toWarehouseId,
      unitCode,
    };
    const fingerprint = JSON.stringify(command);
    if (requestIdentity.current?.fingerprint !== fingerprint) {
      requestIdentity.current = { fingerprint, key: crypto.randomUUID() };
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch("/api/inventory/transfers", {
        body: JSON.stringify({
          ...command,
          idempotencyKey: requestIdentity.current.key,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const data = (await response.json()) as {
        transfer?: { status?: string };
      };
      if (response.ok) {
        requestIdentity.current = null;
        setMessage(
          `Transfer requested · ${data.transfer?.status ?? "released"}`,
        );
        setSkuId("");
        setFromWarehouseId("");
        setToWarehouseId("");
        setFromLocationId("");
        setToLocationId("");
        setQuantity("");
        setReason("");
        setSourceReference("");
        setLotCode("");
        await refresh();
      } else {
        setMessage("Transfer request was rejected. Check stock and scope.");
      }
    } catch {
      setMessage("Transfer service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  const receive = async (transfer: TransferItem) => {
    let idempotencyKey = receiveIdentities.current.get(transfer.transfer_id);
    if (idempotencyKey === undefined) {
      idempotencyKey = crypto.randomUUID();
      receiveIdentities.current.set(transfer.transfer_id, idempotencyKey);
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/inventory/transfers/${transfer.transfer_id}/receive`,
        {
          body: JSON.stringify({
            expectedVersion: transfer.version,
            idempotencyKey,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as {
        transfer?: { status?: string };
      };
      if (response.ok) {
        receiveIdentities.current.delete(transfer.transfer_id);
        setMessage(
          `Transfer received · ${data.transfer?.status ?? "received"}`,
        );
        await refresh();
      } else {
        setMessage("Receive was rejected. Check warehouse scope and state.");
      }
    } catch {
      setMessage("Transfer service unavailable. Retry unchanged work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        description="Move stock between warehouses at source cost. Transfers release available stock into in-transit custody and complete when the destination warehouse receives the goods."
        eyebrow="Inventory"
        tabs={tabs}
        title="Transfers"
      />

      <section className="inventory-intro card">
        <div>
          <p className="eyebrow">Stock movement / 005</p>
          <h2>Move stock between warehouses at source cost.</h2>
        </div>
        <p>
          Transfers release available stock into in-transit custody and complete
          when the destination warehouse receives the goods.
        </p>
      </section>

      <section
        className="inventory-directory card"
        aria-labelledby="request-title"
      >
        <div className="inventory-section-head">
          <div>
            <span className="section-number">Request</span>
            <h2 id="request-title">Request a transfer</h2>
          </div>
        </div>

        {message !== null && (
          <p
            className="inventory-message"
            role="status"
            data-testid="transfer-message"
          >
            {message}
          </p>
        )}

        <form
          className="inventory-search"
          onSubmit={(event) => {
            event.preventDefault();
            void request();
          }}
        >
          <label htmlFor="transfer-sku-id">SKU ID</label>
          <input
            data-testid="transfer-sku-id"
            id="transfer-sku-id"
            onChange={(event) => setSkuId(event.target.value)}
            value={skuId}
          />
          <label htmlFor="transfer-from-warehouse">From warehouse ID</label>
          <input
            data-testid="transfer-from-warehouse"
            id="transfer-from-warehouse"
            onChange={(event) => setFromWarehouseId(event.target.value)}
            value={fromWarehouseId}
          />
          <label htmlFor="transfer-to-warehouse">To warehouse ID</label>
          <input
            data-testid="transfer-to-warehouse"
            id="transfer-to-warehouse"
            onChange={(event) => setToWarehouseId(event.target.value)}
            value={toWarehouseId}
          />
          <label htmlFor="transfer-from-location">From location ID</label>
          <input
            data-testid="transfer-from-location"
            id="transfer-from-location"
            onChange={(event) => setFromLocationId(event.target.value)}
            value={fromLocationId}
          />
          <label htmlFor="transfer-to-location">To location ID</label>
          <input
            data-testid="transfer-to-location"
            id="transfer-to-location"
            onChange={(event) => setToLocationId(event.target.value)}
            value={toLocationId}
          />
          <label htmlFor="transfer-quantity">Quantity</label>
          <input
            data-testid="transfer-quantity"
            id="transfer-quantity"
            onChange={(event) => setQuantity(event.target.value)}
            value={quantity}
          />
          <label htmlFor="transfer-unit">Unit code</label>
          <input
            data-testid="transfer-unit"
            id="transfer-unit"
            onChange={(event) => setUnitCode(event.target.value)}
            value={unitCode}
          />
          <label htmlFor="transfer-reason">Reason</label>
          <input
            data-testid="transfer-reason"
            id="transfer-reason"
            onChange={(event) => setReason(event.target.value)}
            value={reason}
          />
          <label htmlFor="transfer-source-reference">Source reference</label>
          <input
            data-testid="transfer-source-reference"
            id="transfer-source-reference"
            onChange={(event) => setSourceReference(event.target.value)}
            value={sourceReference}
          />
          <label htmlFor="transfer-lot-code">Lot code (optional)</label>
          <input
            data-testid="transfer-lot-code"
            id="transfer-lot-code"
            onChange={(event) => setLotCode(event.target.value)}
            value={lotCode}
          />
          <button data-testid="transfer-request" disabled={busy} type="submit">
            Request transfer
          </button>
        </form>
      </section>

      <section
        className="inventory-directory card"
        aria-labelledby="list-title"
      >
        <div className="inventory-section-head">
          <div>
            <span className="section-number">Transfers</span>
            <h2 id="list-title">Transfers in scope</h2>
          </div>
        </div>
        <TransferListState retry={refresh} state={state} onReceive={receive} />
      </section>
    </>
  );
}

function TransferListState({
  onReceive,
  retry,
  state,
}: {
  onReceive: (transfer: TransferItem) => void;
  retry: () => void;
  state: ListState;
}) {
  if (state.kind === "loading") {
    return (
      <div className="inventory-message" role="status">
        <span className="inventory-loader" aria-hidden="true" />
        <h3>Loading transfers…</h3>
      </div>
    );
  }
  if (state.kind !== "ready") {
    return (
      <div className="inventory-message" role="alert">
        <span className="inventory-alert" aria-hidden="true">
          !
        </span>
        <h3>Transfers are unavailable</h3>
        <p>Confirm the service connection and try again.</p>
        <p className="support-reference">
          Support reference <code>{state.correlationId}</code>
        </p>
        <button onClick={() => void retry()} type="button">
          Retry transfers
        </button>
      </div>
    );
  }
  if (state.transfers.items.length === 0) {
    return (
      <div className="inventory-message inventory-empty">
        <span aria-hidden="true">∅</span>
        <h3>No transfers in your warehouse scope</h3>
      </div>
    );
  }
  return (
    <div className="inventory-grid" aria-live="polite">
      {state.transfers.items.map((transfer) => (
        <article className="inventory-card" key={transfer.transfer_id}>
          <div className="inventory-card-head">
            <div>
              <span>{transfer.status}</span>
              <h3>{transfer.transfer_id}</h3>
            </div>
            <strong>{transfer.quantity_base}</strong>
          </div>
          <dl className="inventory-trace">
            <div>
              <dt>Source</dt>
              <dd>
                {transfer.from_warehouse_id} / {transfer.from_location_id}
              </dd>
            </div>
            <div>
              <dt>Destination</dt>
              <dd>
                {transfer.to_warehouse_id} / {transfer.to_location_id}
              </dd>
            </div>
            <div>
              <dt>Reason</dt>
              <dd>{transfer.reason}</dd>
            </div>
            <div>
              <dt>Reference</dt>
              <dd>{transfer.source_reference}</dd>
            </div>
          </dl>
          {transfer.status === "released" && (
            <button
              data-testid={`transfer-receive-${transfer.transfer_id}`}
              onClick={() => onReceive(transfer)}
              type="button"
            >
              Receive
            </button>
          )}
        </article>
      ))}
    </div>
  );
}
