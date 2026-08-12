"use client";

import type { InventoryDirectoryState } from "@tradeflow/inventory-directory";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

type ScreenState = InventoryDirectoryState | { kind: "loading" };

async function loadInventory(query: string): Promise<InventoryDirectoryState> {
  const response = await fetch(
    `/api/inventory?query=${encodeURIComponent(query)}`,
    {
      cache: "no-store",
      headers: { Accept: "application/json" },
    },
  );
  return (await response.json()) as InventoryDirectoryState;
}

export function InventoryWorkspace() {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<ScreenState>({ kind: "loading" });
  const search = useCallback(async (nextQuery: string) => {
    setState({ kind: "loading" });
    try {
      setState(await loadInventory(nextQuery));
    } catch {
      setState({ correlationId: crypto.randomUUID(), kind: "unavailable" });
    }
  }, []);

  useEffect(() => {
    let active = true;
    void loadInventory("")
      .then((next) => {
        if (active) setState(next);
      })
      .catch(() => {
        if (active) {
          setState({
            correlationId: crypto.randomUUID(),
            kind: "unavailable",
          });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void search(query);
  };

  return (
    <div className="inventory-app">
      <header className="inventory-header">
        <Link href="/" className="inventory-wordmark">
          TradeFlow
        </Link>
        <span>Inventory control</span>
        <span>Server-authoritative</span>
      </header>
      <main className="inventory-main">
        <section className="inventory-intro">
          <div>
            <p className="eyebrow">Stock ledger / 004</p>
            <h1>Promise only what is actually available.</h1>
          </div>
          <p>
            On-hand and value are rebuilt from posted movements. Results are
            limited to your assigned Warehouses.
          </p>
        </section>
        <section
          className="inventory-directory"
          aria-labelledby="inventory-title"
        >
          <div className="inventory-section-head">
            <div>
              <p className="section-number">01 / Availability</p>
              <h2 id="inventory-title">Warehouse stock</h2>
            </div>
            <span className="projection-badge">Immutable movement basis</span>
          </div>
          <form className="inventory-search" onSubmit={submit}>
            <label htmlFor="inventory-query">
              Search SKU code or product name
            </label>
            <div>
              <input
                id="inventory-query"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="e.g. COLA-330"
                value={query}
              />
              <button type="submit">Search stock</button>
            </div>
          </form>
          <InventoryState retry={() => void search(query)} state={state} />
        </section>
      </main>
    </div>
  );
}

function InventoryState({
  retry,
  state,
}: {
  retry: () => void;
  state: ScreenState;
}) {
  if (state.kind === "loading") {
    return (
      <div className="inventory-message" role="status">
        <span className="inventory-loader" aria-hidden="true" />
        <h3>Loading scoped availability…</h3>
        <p>Reading the latest committed stock projection.</p>
      </div>
    );
  }
  if (state.kind !== "ready") {
    const title =
      state.kind === "forbidden"
        ? "Inventory access is not assigned"
        : state.kind === "unauthenticated"
          ? "Sign in to view stock"
          : state.kind === "validation"
            ? "Revise the inventory search"
            : "Inventory is temporarily unavailable";
    return (
      <div className="inventory-message" role="alert">
        <span className="inventory-alert" aria-hidden="true">
          !
        </span>
        <h3>{title}</h3>
        <p>
          {state.kind === "forbidden"
            ? "Ask an operations administrator for inventory read access and Warehouse scope."
            : state.kind === "unauthenticated"
              ? "Sign in through your identity provider, then return here."
              : state.kind === "validation"
                ? "Use a valid SKU code or product name."
                : "Confirm the service connection and try again."}
        </p>
        <p className="support-reference">
          Support reference <code>{state.correlationId}</code>
        </p>
        {state.kind === "unavailable" && (
          <button type="button" onClick={retry}>
            Retry availability
          </button>
        )}
      </div>
    );
  }
  if (state.total === 0) {
    return (
      <div className="inventory-message inventory-empty">
        <span aria-hidden="true">∅</span>
        <h3>No stock in your Warehouse scope</h3>
        <p>Revise the search or post authorized opening stock.</p>
      </div>
    );
  }
  return (
    <div className="inventory-grid" aria-live="polite">
      {state.items.map((item) => (
        <article
          className="inventory-card"
          key={`${item.skuId}:${item.warehouseId}:${item.locationCode}:${item.lotCode ?? item.serialNumbers.join(",")}`}
        >
          <div className="inventory-card-head">
            <div>
              <span>{item.skuCode}</span>
              <h3>{item.skuName}</h3>
            </div>
            <strong>{item.custody.toUpperCase()}</strong>
          </div>
          <dl className="inventory-quantities">
            <div>
              <dt>On hand</dt>
              <dd>
                {item.warehouseOnHand} <small>{item.baseStockingUnit}</small>
              </dd>
            </div>
            <div>
              <dt>Reserved</dt>
              <dd>{item.commercialReserved}</dd>
            </div>
            <div className="inventory-available">
              <dt>Available</dt>
              <dd>{item.warehouseAvailable}</dd>
            </div>
          </dl>
          <dl className="inventory-trace">
            <div>
              <dt>Warehouse / location</dt>
              <dd>
                {item.warehouseCode} / {item.locationCode}
              </dd>
            </div>
            <div>
              <dt>Tracking</dt>
              <dd>{item.trackingPolicy}</dd>
            </div>
            <div>
              <dt>Identity / expiration</dt>
              <dd>
                {item.lotCode ??
                  (item.serialNumbers.length > 0
                    ? item.serialNumbers.join(", ")
                    : "—")}{" "}
                / {item.expirationDate ?? "—"}
              </dd>
            </div>
            <div>
              <dt>Moving average</dt>
              <dd>
                {item.baseCurrency} {item.movingAverageUnitCost}
              </dd>
            </div>
            <div>
              <dt>Warehouse inventory value</dt>
              <dd>
                {item.baseCurrency} {item.warehouseInventoryValue}
              </dd>
            </div>
          </dl>
        </article>
      ))}
    </div>
  );
}
