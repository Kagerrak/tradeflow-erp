"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useEffect, useState } from "react";

type PurchaseOrderResponse = components["schemas"]["PurchaseOrderResponse"];
type PurchaseOrderLineResponse =
  components["schemas"]["PurchaseOrderLineResponse"];
type GoodsReceiptResponse = components["schemas"]["GoodsReceiptResponse"];

interface Props {
  purchaseOrderId: string;
}

interface ReceiptLineForm {
  purchase_order_line_id: string;
  received_quantity_base: string;
  lot_code: string;
  serial_numbers: string;
}

export function ProcurementGoodsReceiptWorkspace({ purchaseOrderId }: Props) {
  const [order, setOrder] = useState<PurchaseOrderResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [warehouseId, setWarehouseId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [receiptNumber, setReceiptNumber] = useState("");
  const [lines, setLines] = useState<ReceiptLineForm[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [posted, setPosted] = useState<GoodsReceiptResponse | null>(null);

  useEffect(() => {
    const fetchOrder = async () => {
      try {
        const response = await fetch(
          `/api/procurement/purchase-orders/${purchaseOrderId}`,
          { cache: "no-store", headers: { Accept: "application/json" } },
        );
        const data = (await response.json()) as
          PurchaseOrderResponse | { kind?: string };
        if (response.ok && "purchase_order_id" in data) {
          setOrder(data);
          setLines(
            data.lines.map((line: PurchaseOrderLineResponse) => ({
              lot_code: "",
              purchase_order_line_id: line.purchase_order_line_id,
              received_quantity_base: "",
              serial_numbers: "",
            })),
          );
        } else {
          setMessage("Purchase order could not be loaded.");
        }
      } catch {
        setMessage("Purchase order service unavailable.");
      } finally {
        setLoading(false);
      }
    };
    void fetchOrder();
  }, [purchaseOrderId]);

  const updateLine = (
    lineId: string,
    field: keyof ReceiptLineForm,
    value: string,
  ) => {
    setLines((current) =>
      current.map((line) =>
        line.purchase_order_line_id === lineId
          ? { ...line, [field]: value }
          : line,
      ),
    );
  };

  const postReceipt = async () => {
    setMessage(null);
    const body = {
      location_id: locationId,
      receipt_number: receiptNumber,
      warehouse_id: warehouseId,
      lines: lines
        .filter((line) => line.received_quantity_base.trim().length > 0)
        .map((line) => ({
          lot_code: line.lot_code || undefined,
          purchase_order_line_id: line.purchase_order_line_id,
          received_quantity_base: line.received_quantity_base,
          serial_numbers:
            line.serial_numbers.trim().length > 0
              ? line.serial_numbers.split(",").map((s) => s.trim())
              : undefined,
        })),
    };

    try {
      const response = await fetch(
        `/api/procurement/purchase-orders/${purchaseOrderId}/receipts`,
        {
          body: JSON.stringify(body),
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as
        GoodsReceiptResponse | { kind?: string; message?: string };
      if (response.ok && "goods_receipt_id" in data) {
        setPosted(data);
        setMessage(`Posted receipt ${data.receipt_number}.`);
      } else {
        setMessage(
          "message" in data && data.message
            ? data.message
            : "Goods receipt could not be posted.",
        );
      }
    } catch {
      setMessage("Goods receipt service unavailable.");
    }
  };

  const canReceive =
    order?.status === "approved" || order?.status === "partially_received";

  return (
    <div className="procurement-app">
      <header className="procurement-header">
        <Link href="/">TradeFlow</Link>
        <span>Procurement / Purchase orders</span>
        <span>Goods receipt</span>
      </header>
      <main className="procurement-main">
        <section className="procurement-title">
          <div>
            <p className="eyebrow">Supplier commitment receipt</p>
            <h1>Post goods receipt.</h1>
          </div>
          <p>
            Receive inventory against an approved purchase order. Receipts
            update stock movements, availability, and moving-average valuation.
          </p>
        </section>

        {loading ? (
          <p className="procurement-message">Loading purchase order…</p>
        ) : order === null ? (
          <p className="procurement-message">
            {message ?? "Purchase order not found."}
          </p>
        ) : (
          <>
            <section className="procurement-panel">
              <div className="procurement-section-head">
                <div>
                  <span>Procurement / detail</span>
                  <h2>{order.code}</h2>
                </div>
              </div>
              <div className="procurement-fields">
                <label>
                  Status
                  <input readOnly value={order.status} />
                </label>
                <label>
                  Currency
                  <input readOnly value={order.currency} />
                </label>
                <label>
                  Version
                  <input readOnly value={order.version} />
                </label>
              </div>
            </section>

            {canReceive ? (
              <section className="procurement-panel">
                <div className="procurement-section-head">
                  <div>
                    <span>Inventory / post</span>
                    <h2>Receipt details</h2>
                  </div>
                </div>

                {message !== null && (
                  <p className="procurement-message" role="status">
                    {message}
                  </p>
                )}

                <div className="procurement-fields">
                  <label>
                    Warehouse ID
                    <input
                      onChange={(event) => setWarehouseId(event.target.value)}
                      value={warehouseId}
                    />
                  </label>
                  <label>
                    Location ID
                    <input
                      onChange={(event) => setLocationId(event.target.value)}
                      value={locationId}
                    />
                  </label>
                  <label>
                    Receipt number
                    <input
                      onChange={(event) => setReceiptNumber(event.target.value)}
                      value={receiptNumber}
                    />
                  </label>
                </div>

                <table className="procurement-table">
                  <thead>
                    <tr>
                      <th>Line</th>
                      <th>SKU ID</th>
                      <th>Ordered (base)</th>
                      <th>Received (base)</th>
                      <th>Quantity</th>
                      <th>Lot code</th>
                      <th>Serial numbers</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line, index) => (
                      <tr key={line.purchase_order_line_id}>
                        <td>{index + 1}</td>
                        <td>{order.lines[index]?.sku_id}</td>
                        <td>{order.lines[index]?.base_quantity}</td>
                        <td>{order.lines[index]?.unit_cost}</td>
                        <td>
                          <input
                            onChange={(event) =>
                              updateLine(
                                line.purchase_order_line_id,
                                "received_quantity_base",
                                event.target.value,
                              )
                            }
                            value={line.received_quantity_base}
                          />
                        </td>
                        <td>
                          <input
                            onChange={(event) =>
                              updateLine(
                                line.purchase_order_line_id,
                                "lot_code",
                                event.target.value,
                              )
                            }
                            value={line.lot_code}
                          />
                        </td>
                        <td>
                          <input
                            onChange={(event) =>
                              updateLine(
                                line.purchase_order_line_id,
                                "serial_numbers",
                                event.target.value,
                              )
                            }
                            value={line.serial_numbers}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <button onClick={() => void postReceipt()} type="button">
                  Post goods receipt
                </button>
              </section>
            ) : (
              <p className="procurement-message">
                This purchase order cannot receive goods while its status is
                {order.status}.
              </p>
            )}

            {posted !== null && (
              <section className="procurement-panel">
                <div className="procurement-section-head">
                  <div>
                    <span>Inventory / result</span>
                    <h2>Posted receipt</h2>
                  </div>
                </div>
                <pre className="procurement-empty">
                  {JSON.stringify(posted, null, 2)}
                </pre>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
