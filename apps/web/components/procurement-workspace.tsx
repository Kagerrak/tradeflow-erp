"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useEffect, useState } from "react";

type PurchaseOrderSummary = components["schemas"]["PurchaseOrderSummary"];
type GoodsReceiptSummary = components["schemas"]["GoodsReceiptSummary"];

type PurchaseOrderList = { items: PurchaseOrderSummary[]; total: number };
type GoodsReceiptList = { items: GoodsReceiptSummary[]; total: number };

export function ProcurementWorkspace() {
  const [openOrders, setOpenOrders] = useState<PurchaseOrderSummary[]>([]);
  const [recentReceipts, setRecentReceipts] = useState<GoodsReceiptSummary[]>(
    [],
  );
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(function fetchOnMount() {
    async function fetchSummary() {
      try {
        const [ordersResponse, receiptsResponse] = await Promise.all([
          fetch("/api/procurement/purchase-orders?status=approved", {
            cache: "no-store",
            headers: { Accept: "application/json" },
          }),
          fetch("/api/procurement/goods-receipts", {
            cache: "no-store",
            headers: { Accept: "application/json" },
          }),
        ]);
        const ordersBody = (await ordersResponse.json()) as
          PurchaseOrderList | { kind?: string };
        const receiptsBody = (await receiptsResponse.json()) as
          GoodsReceiptList | { kind?: string };
        if ("items" in ordersBody) {
          setOpenOrders(ordersBody.items);
        }
        if ("items" in receiptsBody) {
          setRecentReceipts(receiptsBody.items.slice(0, 5));
        }
      } catch {
        setMessage("Procurement summary unavailable.");
      } finally {
        setLoading(false);
      }
    }
    void fetchSummary();
  }, []);

  return (
    <div className="procurement-app">
      <header className="procurement-header">
        <Link href="/">TradeFlow</Link>
        <span>Procurement</span>
      </header>
      <main className="procurement-main">
        <section className="procurement-title">
          <div>
            <p className="eyebrow">Inbound supply</p>
            <h1>Procurement workspace.</h1>
          </div>
          <p>
            Manage suppliers, purchase orders, goods receipts, and landed costs
            from one place.
          </p>
        </section>

        <section className="procurement-panel">
          <div className="procurement-section-head">
            <div>
              <span>Procurement / navigate</span>
              <h2>Workspaces</h2>
            </div>
          </div>
          <nav className="procurement-fields">
            <Link className="button" href="/procurement/suppliers">
              Suppliers
            </Link>
            <Link className="button" href="/procurement/purchase-orders">
              Purchase orders
            </Link>
            <Link className="button" href="/procurement/purchase-requests">
              Purchase requests
            </Link>
            <Link className="button" href="/procurement/goods-receipts">
              Goods receipts
            </Link>
          </nav>
        </section>

        {loading ? (
          <p className="procurement-message">Loading summary…</p>
        ) : message !== null ? (
          <p className="procurement-message">{message}</p>
        ) : (
          <>
            <section className="procurement-panel">
              <div className="procurement-section-head">
                <div>
                  <span>Procurement / read</span>
                  <h2>Open purchase orders</h2>
                </div>
              </div>
              {openOrders.length === 0 ? (
                <p className="procurement-empty">No open purchase orders.</p>
              ) : (
                <ul className="procurement-list">
                  {openOrders.map(function renderOrder(order) {
                    return (
                      <li key={order.purchase_order_id}>
                        <Link href={`/procurement/purchase-orders`}>
                          {order.code} — {order.currency} ({order.status})
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section className="procurement-panel">
              <div className="procurement-section-head">
                <div>
                  <span>Inventory / read</span>
                  <h2>Recent goods receipts</h2>
                </div>
              </div>
              {recentReceipts.length === 0 ? (
                <p className="procurement-empty">No recent goods receipts.</p>
              ) : (
                <ul className="procurement-list">
                  {recentReceipts.map(function renderReceipt(receipt) {
                    return (
                      <li key={receipt.goods_receipt_id}>
                        <Link
                          href={`/procurement/goods-receipts/${receipt.goods_receipt_id}/landed-costs`}
                        >
                          {receipt.receipt_number} — {receipt.status}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}
