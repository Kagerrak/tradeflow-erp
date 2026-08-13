"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useEffect, useState } from "react";

type PurchaseOrderSummary = components["schemas"]["PurchaseOrderSummary"];
type PurchaseOrderResponse = components["schemas"]["PurchaseOrderResponse"];
type CreatePurchaseOrderCommand =
  components["schemas"]["CreatePurchaseOrderCommand"];

type ListState =
  | { kind: "loading" }
  | {
      items: PurchaseOrderSummary[];
      kind: "ready";
      total: number;
    }
  | { kind: "unavailable" };

export function ProcurementPurchaseOrdersWorkspace() {
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [selected, setSelected] = useState<PurchaseOrderResponse | null>(null);
  const [form, setForm] = useState<CreatePurchaseOrderCommand>({
    supplier_id: "",
    branch_id: "",
    code: "",
    currency: "PHP",
    exchange_rate: "1",
    lines: [
      { sku_id: "", requested_quantity: "1", unit_code: "", unit_cost: "" },
    ],
  });
  const [message, setMessage] = useState<string | null>(null);

  const fetchPurchaseOrders = async (): Promise<
    { items: PurchaseOrderSummary[]; total: number } | { kind?: string }
  > => {
    const response = await fetch("/api/procurement/purchase-orders", {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    return (await response.json()) as
      { items: PurchaseOrderSummary[]; total: number } | { kind?: string };
  };

  const refresh = async () => {
    setList({ kind: "loading" });
    try {
      const data = await fetchPurchaseOrders();
      if ("items" in data) {
        setList({ kind: "ready", items: data.items, total: data.total });
      } else {
        setList({ kind: "unavailable" });
      }
    } catch {
      setList({ kind: "unavailable" });
    }
  };

  useEffect(() => {
    void fetchPurchaseOrders()
      .then((data) => {
        if ("items" in data) {
          setList({ kind: "ready", items: data.items, total: data.total });
        } else {
          setList({ kind: "unavailable" });
        }
      })
      .catch(() => {
        setList({ kind: "unavailable" });
      });
  }, []);

  const create = async () => {
    setMessage(null);
    try {
      const response = await fetch("/api/procurement/purchase-orders", {
        body: JSON.stringify(form),
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const data = (await response.json()) as
        PurchaseOrderResponse | { kind?: string };
      if (response.ok && "purchase_order_id" in data) {
        setSelected(data);
        setMessage(`Created ${data.code}.`);
        void refresh();
      } else {
        setMessage("Purchase order could not be created.");
      }
    } catch {
      setMessage("Purchase order service unavailable.");
    }
  };

  const approve = async (purchaseOrderId: string) => {
    setMessage(null);
    try {
      const response = await fetch(
        `/api/procurement/purchase-orders/${purchaseOrderId}/approve`,
        {
          cache: "no-store",
          method: "POST",
        },
      );
      const data = (await response.json()) as
        PurchaseOrderResponse | { kind?: string };
      if (response.ok && "purchase_order_id" in data) {
        setSelected(data);
        setMessage(`Approved ${data.code}.`);
        void refresh();
      } else {
        setMessage("Purchase order could not be approved.");
      }
    } catch {
      setMessage("Purchase order service unavailable.");
    }
  };

  return (
    <div className="procurement-app">
      <header className="procurement-header">
        <Link href="/">TradeFlow</Link>
        <span>Procurement / Purchase orders</span>
        <span>Purchase order workspace</span>
      </header>
      <main className="procurement-main">
        <section className="procurement-title">
          <div>
            <p className="eyebrow">Supplier commitments</p>
            <h1>Purchase orders.</h1>
          </div>
          <p>
            Raise and approve purchase orders against suppliers. Approved orders
            unlock goods receipts and landed cost allocation.
          </p>
        </section>

        <section className="procurement-panel">
          <div className="procurement-section-head">
            <div>
              <span>Procurement / write</span>
              <h2>Create purchase order</h2>
            </div>
          </div>

          {message !== null && (
            <p className="procurement-message" role="status">
              {message}
            </p>
          )}

          <div className="procurement-fields">
            <label>
              Supplier ID
              <input
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => ({
                    ...current,
                    supplier_id: event.target.value,
                  }))
                }
                value={form.supplier_id}
              />
            </label>
            <label>
              Branch ID
              <input
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => ({
                    ...current,
                    branch_id: event.target.value,
                  }))
                }
                value={form.branch_id}
              />
            </label>
            <label>
              Code
              <input
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => ({
                    ...current,
                    code: event.target.value,
                  }))
                }
                value={form.code}
              />
            </label>
            <label>
              Currency
              <input
                maxLength={3}
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => ({
                    ...current,
                    currency: event.target.value.toUpperCase(),
                  }))
                }
                value={form.currency}
              />
            </label>
            <label>
              Exchange rate
              <input
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => ({
                    ...current,
                    exchange_rate: event.target.value,
                  }))
                }
                value={form.exchange_rate}
              />
            </label>
            <label className="procurement-wide">
              Line 1 — SKU ID
              <input
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => {
                    const line = current.lines[0]!;
                    return {
                      ...current,
                      lines: [
                        {
                          requested_quantity: line.requested_quantity,
                          sku_id: event.target.value,
                          unit_code: line.unit_code,
                          unit_cost: line.unit_cost,
                        },
                      ],
                    };
                  })
                }
                value={form.lines[0]!.sku_id}
              />
            </label>
            <label>
              Quantity
              <input
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => {
                    const line = current.lines[0]!;
                    return {
                      ...current,
                      lines: [
                        {
                          requested_quantity: event.target.value,
                          sku_id: line.sku_id,
                          unit_code: line.unit_code,
                          unit_cost: line.unit_cost,
                        },
                      ],
                    };
                  })
                }
                value={form.lines[0]!.requested_quantity}
              />
            </label>
            <label>
              Unit
              <input
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => {
                    const line = current.lines[0]!;
                    return {
                      ...current,
                      lines: [
                        {
                          requested_quantity: line.requested_quantity,
                          sku_id: line.sku_id,
                          unit_code: event.target.value,
                          unit_cost: line.unit_cost,
                        },
                      ],
                    };
                  })
                }
                value={form.lines[0]!.unit_code}
              />
            </label>
            <label>
              Unit cost
              <input
                onChange={(event) =>
                  setForm((current: CreatePurchaseOrderCommand) => {
                    const line = current.lines[0]!;
                    return {
                      ...current,
                      lines: [
                        {
                          requested_quantity: line.requested_quantity,
                          sku_id: line.sku_id,
                          unit_code: line.unit_code,
                          unit_cost: event.target.value,
                        },
                      ],
                    };
                  })
                }
                value={form.lines[0]!.unit_cost}
              />
            </label>
            <button onClick={create} type="button">
              Create purchase order
            </button>
          </div>
        </section>

        <section className="procurement-panel">
          <div className="procurement-section-head">
            <div>
              <span>Procurement / read</span>
              <h2>Purchase orders</h2>
            </div>
          </div>

          {list.kind === "loading" && (
            <p className="procurement-message">Loading purchase orders…</p>
          )}
          {list.kind === "unavailable" && (
            <p className="procurement-message">Purchase orders unavailable.</p>
          )}

          {list.kind === "ready" && (
            <>
              {list.items.length === 0 ? (
                <p className="procurement-empty">No purchase orders found.</p>
              ) : (
                <table className="procurement-table">
                  <thead>
                    <tr>
                      <th>Code</th>
                      <th>Status</th>
                      <th>Currency</th>
                      <th>Version</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.items.map((po) => (
                      <tr key={po.purchase_order_id}>
                        <td>{po.code}</td>
                        <td>{po.status}</td>
                        <td>{po.currency}</td>
                        <td>{po.version}</td>
                        <td>
                          {po.status === "draft" ? (
                            <button
                              onClick={() => void approve(po.purchase_order_id)}
                              type="button"
                            >
                              Approve
                            </button>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="procurement-total">Total: {list.total}</p>
            </>
          )}
        </section>

        {selected !== null && (
          <section className="procurement-panel">
            <div className="procurement-section-head">
              <div>
                <span>Procurement / detail</span>
                <h2>{selected.code}</h2>
              </div>
            </div>
            <pre className="procurement-empty">
              {JSON.stringify(selected, null, 2)}
            </pre>
          </section>
        )}
      </main>
    </div>
  );
}
