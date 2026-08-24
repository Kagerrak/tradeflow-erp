"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { EmptyState } from "./ui/empty-state";
import { ErrorState } from "./ui/error-state";
import { PageHeader } from "./ui/page-header";

type PurchaseOrderSummary = components["schemas"]["PurchaseOrderSummary"];
type GoodsReceiptSummary = components["schemas"]["GoodsReceiptSummary"];

type PurchaseOrderList = { items: PurchaseOrderSummary[]; total: number };
type GoodsReceiptList = { items: GoodsReceiptSummary[]; total: number };

const procurementModules = [
  {
    description: "Manage vendor accounts.",
    href: "/procurement/suppliers",
    title: "Suppliers",
  },
  {
    description: "Create and approve purchase orders.",
    href: "/procurement/purchase-orders",
    title: "Purchase orders",
  },
  {
    description: "Raise and convert purchase requests.",
    href: "/procurement/purchase-requests",
    title: "Purchase requests",
  },
  {
    description: "Record incoming goods and landed costs.",
    href: "/procurement/goods-receipts",
    title: "Goods receipts",
  },
];

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
    <>
      <PageHeader
        description="Manage suppliers, purchase orders, goods receipts, and landed costs from one place."
        eyebrow="Operations"
        title="Procurement"
      />

      <section className="dashboard-grid" aria-label="Procurement modules">
        {procurementModules.map((module) => (
          <Link className="dashboard-tile" href={module.href} key={module.href}>
            <span className="dashboard-tile-title">{module.title}</span>
            <span className="dashboard-tile-desc">{module.description}</span>
          </Link>
        ))}
      </section>

      {loading ? (
        <div className="workspace-loading" role="status">
          <span className="workspace-loader" aria-hidden="true" />
          <p>Loading summary…</p>
        </div>
      ) : message !== null ? (
        <ErrorState title="Procurement summary unavailable">
          <p>{message}</p>
        </ErrorState>
      ) : (
        <>
          <section className="card" aria-labelledby="open-orders-title">
            <h2 id="open-orders-title">Open purchase orders</h2>
            {openOrders.length === 0 ? (
              <EmptyState
                description="Approved purchase orders will appear here."
                title="No open purchase orders"
              />
            ) : (
              <ul className="procurement-list">
                {openOrders.map((order) => (
                  <li key={order.purchase_order_id}>
                    <Link href="/procurement/purchase-orders">
                      {order.code} — {order.currency} ({order.status})
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card" aria-labelledby="recent-receipts-title">
            <h2 id="recent-receipts-title">Recent goods receipts</h2>
            {recentReceipts.length === 0 ? (
              <EmptyState
                description="Recent receipts will appear here."
                title="No recent goods receipts"
              />
            ) : (
              <ul className="procurement-list">
                {recentReceipts.map((receipt) => (
                  <li key={receipt.goods_receipt_id}>
                    <Link
                      href={`/procurement/goods-receipts/${receipt.goods_receipt_id}/landed-costs`}
                    >
                      {receipt.receipt_number} — {receipt.status}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </>
  );
}
