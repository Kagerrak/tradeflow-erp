"use client";

import {
  type CommercialApprovalState,
  type CommercialReviewState,
  type LoadSalesDraftState,
  type SalesOrderSearchState,
} from "@tradeflow/sales-order-draft";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { PageHeader } from "./ui/page-header";
import { randomId } from "@/lib/random-id";

type Scope = {
  capabilities: string[];
  user: { display_name: string };
  warehouses: Array<{
    branch_id: string;
    code: string;
    is_active: boolean;
    name: string;
    warehouse_id: string;
  }>;
};

type CommercialApprovalFailure = Exclude<
  CommercialApprovalState,
  { kind: "approved" }
>;

const failureCopy: Record<
  CommercialApprovalFailure["kind"],
  { guidance: string; title: string }
> = {
  conflict: {
    guidance:
      "Reload the authoritative order and review its latest revision before approving.",
    title: "The priced revision changed",
  },
  exception_required: {
    guidance:
      "Enter the required evidence or route this revision to a different checker with sufficient authority.",
    title: "A different or higher-authority checker is required",
  },
  forbidden: {
    guidance:
      "Ask an administrator to assign Commercial Approval access for this Branch and Warehouse.",
    title: "Commercial Approval access is not assigned",
  },
  held: {
    guidance:
      "Resolve the Customer Credit Hold, then rerun approval for this exact revision.",
    title: "Customer Credit Hold blocks approval",
  },
  unauthenticated: {
    guidance: "Sign in again, then reopen the pending revision.",
    title: "Sign in to approve",
  },
  unavailable: {
    guidance:
      "Keep this revision unchanged and retry. The same approval command identity will be reused.",
    title: "Commercial controls are unavailable",
  },
  validation: {
    guidance:
      "Review the Warehouse and required exception reasons, then retry this exact revision.",
    title: "Approval evidence needs correction",
  },
};

export function CommercialApprovalQueue({
  initialOrderId,
}: {
  initialOrderId: string | undefined;
}) {
  const [scope, setScope] = useState<Scope | null>(null);
  const [orders, setOrders] = useState<SalesOrderSearchState | null>(null);
  const [selected, setSelected] = useState<LoadSalesDraftState | null>(null);
  const [warehouseId, setWarehouseId] = useState("");
  const [review, setReview] = useState<CommercialReviewState | null>(null);
  const [exceptionReason, setExceptionReason] = useState("");
  const [creditReason, setCreditReason] = useState("");
  const [approval, setApproval] = useState<CommercialApprovalState | null>(
    null,
  );
  const [approving, setApproving] = useState(false);
  const commandIdentity = useRef<{ fingerprint: string; key: string } | null>(
    null,
  );
  const openedInitialOrder = useRef(false);

  const refresh = async () => {
    const [scopeResponse, orderResponse] = await Promise.all([
      fetch("/api/customer-scope", { cache: "no-store" }),
      fetch("/api/sales-orders?query=", { cache: "no-store" }),
    ]);
    setScope((await scopeResponse.json()) as Scope);
    setOrders((await orderResponse.json()) as SalesOrderSearchState);
  };

  useEffect(() => {
    let active = true;
    void Promise.all([
      fetch("/api/customer-scope", { cache: "no-store" }),
      fetch("/api/sales-orders?query=", { cache: "no-store" }),
    ]).then(async ([scopeResponse, orderResponse]) => {
      if (!active) return;
      setScope((await scopeResponse.json()) as Scope);
      setOrders((await orderResponse.json()) as SalesOrderSearchState);
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (selected?.kind !== "loaded" || warehouseId.length === 0) {
      return;
    }
    let active = true;
    void fetch(
      `/api/sales-orders/${selected.draft.salesOrderId}/commercial-review?warehouse_id=${encodeURIComponent(warehouseId)}`,
      { cache: "no-store" },
    )
      .then(async (response) => {
        const next = (await response.json()) as CommercialReviewState;
        if (active) setReview(next);
      })
      .catch(() => {
        if (active) {
          setReview({
            correlationId: randomId(),
            kind: "unavailable",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [selected, warehouseId]);

  const openOrder = useCallback(
    async (salesOrderId: string) => {
      setApproval(null);
      setSelected(null);
      setWarehouseId("");
      setReview(null);
      commandIdentity.current = null;
      const response = await fetch(`/api/sales-orders/${salesOrderId}`, {
        cache: "no-store",
      });
      const next = (await response.json()) as LoadSalesDraftState;
      setSelected(next);
      if (next.kind === "loaded" && scope !== null) {
        setWarehouseId(
          scope.warehouses.find(
            (warehouse) =>
              warehouse.is_active &&
              warehouse.branch_id === next.draft.branchId,
          )?.warehouse_id ?? "",
        );
      }
    },
    [scope],
  );

  useEffect(() => {
    if (!initialOrderId || scope === null || openedInitialOrder.current) return;
    openedInitialOrder.current = true;
    void openOrder(initialOrderId);
  }, [initialOrderId, openOrder, scope]);

  const approve = async () => {
    if (selected?.kind !== "loaded" || warehouseId.length === 0) return;
    const fingerprint = JSON.stringify({
      creditReason: creditReason.trim(),
      exceptionReason: exceptionReason.trim(),
      orderId: selected.draft.salesOrderId,
      version: selected.draft.version,
      warehouseId,
    });
    if (commandIdentity.current?.fingerprint !== fingerprint) {
      commandIdentity.current = { fingerprint, key: randomId() };
    }
    setApproving(true);
    try {
      const response = await fetch(
        `/api/sales-orders/${selected.draft.salesOrderId}/commercial-approval`,
        {
          body: JSON.stringify({
            command: {
              credit_override_reason:
                creditReason.trim().length === 0 ? null : creditReason,
              exception_reason:
                exceptionReason.trim().length === 0 ? null : exceptionReason,
              warehouse_id: warehouseId,
            },
            expectedVersion: selected.draft.version,
            idempotencyKey: commandIdentity.current.key,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const next = (await response.json()) as CommercialApprovalState;
      setApproval(next);
      if (next.kind === "approved") await refresh();
    } catch {
      setApproval({
        correlationId: randomId(),
        kind: "unavailable",
      });
    } finally {
      setApproving(false);
    }
  };

  const pending =
    orders?.kind === "ready"
      ? orders.items.filter((order) => order.status === "awaiting_approval")
      : [];

  return (
    <>
      <PageHeader
        description="Review another user’s exact priced revision, Customer exposure, and Warehouse stock before making a durable commitment."
        eyebrow="Sales"
        title="Commercial approvals"
      />

      <section className="sales-title card">
        <div>
          <p className="eyebrow">Maker-checker / 006</p>
          <h1>Commit only what survives the controls.</h1>
        </div>
        <p>
          Review another user’s exact priced revision, Customer exposure, and
          Warehouse stock before making a durable commitment.
        </p>
      </section>
      <section className="sales-panel">
        <div className="sales-panel-head">
          <h2>Pending approvals</h2>
          <span>{pending.length} orders awaiting approval</span>
        </div>
        {orders === null ? (
          <div className="sales-message" role="status">
            Loading scoped Sales Orders…
          </div>
        ) : orders.kind !== "ready" ? (
          <div className="sales-message" role="alert">
            Approval queue unavailable. Support reference{" "}
            <code>{orders.correlationId}</code>
          </div>
        ) : pending.length === 0 ? (
          <div className="sales-message" role="status">
            <h3>No priced drafts await approval</h3>
          </div>
        ) : (
          <div className="sales-approval-list">
            {pending.map((order) => (
              <Button
                key={order.salesOrderId}
                onClick={() => void openOrder(order.salesOrderId)}
                type="button"
                variant="ghost"
              >
                <span>{order.customerName}</span>{" "}
                <strong>
                  {order.currency} {order.grandTotal}
                </strong>{" "}
                <small>
                  v{order.version} ·{" "}
                  {order.paymentTimingPolicy.replaceAll("_", " ")}
                </small>
              </Button>
            ))}
          </div>
        )}
        {selected?.kind === "loaded" && (
          <section className="sales-approval" aria-labelledby="checker-title">
            <div>
              <p className="section-number">Authoritative revision</p>
              <h3 id="checker-title">
                {selected.draft.currency} {selected.draft.grandTotal}
              </h3>
              <p>
                Maker-priced version {selected.draft.version} ·{" "}
                {selected.draft.paymentTimingPolicy.replaceAll("_", " ")}
              </p>
            </div>
            <div className="sales-approval-fields">
              <label>
                Fulfillment warehouse
                <select
                  aria-label="Fulfillment warehouse"
                  className="operational-select"
                  onChange={(event) => {
                    setReview(null);
                    setWarehouseId(event.target.value);
                  }}
                  value={warehouseId}
                >
                  <option value="" disabled>
                    Choose a warehouse
                  </option>
                  {scope?.warehouses
                    .filter(
                      (warehouse) =>
                        warehouse.is_active &&
                        warehouse.branch_id === selected.draft.branchId,
                    )
                    .map((warehouse) => (
                      <option
                        key={warehouse.warehouse_id}
                        value={warehouse.warehouse_id}
                      >
                        {warehouse.code} / {warehouse.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Discount / floor exception reason
                <Input
                  aria-label="Commercial exception reason"
                  onChange={(event) => setExceptionReason(event.target.value)}
                  value={exceptionReason}
                />
              </label>
              {selected.draft.paymentTimingPolicy === "on_account" && (
                <label>
                  Credit Override reason
                  <Input
                    aria-label="Credit Override reason"
                    onChange={(event) => setCreditReason(event.target.value)}
                    value={creditReason}
                  />
                </label>
              )}
              <Button
                disabled={
                  approving ||
                  warehouseId.length === 0 ||
                  review?.kind !== "ready" ||
                  review.review.warehouseId !== warehouseId ||
                  review.review.creditHold ||
                  !review.review.customerSnapshotCurrent ||
                  !scope?.capabilities.includes("sales:commercial-approve")
                }
                onClick={() => void approve()}
                type="button"
              >
                {approving ? "Checking controls…" : "Approve exact revision"}
              </Button>
            </div>
          </section>
        )}
        {selected?.kind === "loaded" &&
          warehouseId.length > 0 &&
          (review === null ? (
            <div className="sales-message" role="status">
              Loading customer, credit, pricing, and Warehouse evidence…
            </div>
          ) : review.kind !== "ready" ? (
            <div className="sales-message" role="alert">
              Commercial Review evidence is unavailable. Support reference{" "}
              <code>{review.correlationId}</code>
            </div>
          ) : (
            <section
              aria-labelledby="commercial-evidence-title"
              className="sales-evidence"
            >
              <div className="sales-evidence-heading">
                <div>
                  <p className="section-number">Control evidence</p>
                  <h3 id="commercial-evidence-title">
                    {review.review.customerName}
                  </h3>
                  <p>
                    {review.review.customerAccountNumber} ·{" "}
                    {review.review.customerStatus} ·{" "}
                    {review.review.paymentTerms} ·{" "}
                    {review.review.paymentTimingPolicy.replaceAll("_", " ")}
                  </p>
                  <p>Maker {review.review.makerSubject}</p>
                </div>
                <div className="sales-evidence-flags">
                  <strong>
                    {review.review.creditHold
                      ? "Credit hold"
                      : "No credit hold"}
                  </strong>
                  <span>
                    Customer snapshot{" "}
                    {review.review.customerSnapshotCurrent
                      ? "current"
                      : "changed"}
                  </span>
                </div>
              </div>
              <dl
                aria-label="Credit exposure"
                className="sales-evidence-metrics"
              >
                <div>
                  <dt>Open balance</dt>
                  <dd>
                    {review.review.currency} {review.review.openBalance}
                  </dd>
                </div>
                <div>
                  <dt>Approved uninvoiced</dt>
                  <dd>
                    {review.review.currency} {review.review.approvedUninvoiced}
                  </dd>
                </div>
                <div>
                  <dt>Projected exposure</dt>
                  <dd>
                    {review.review.currency} {review.review.projectedExposure}
                  </dd>
                </div>
                <div>
                  <dt>Credit limit</dt>
                  <dd>
                    {review.review.creditLimit === null
                      ? "Not assigned"
                      : `${review.review.currency} ${review.review.creditLimit}`}
                  </dd>
                </div>
              </dl>
              <div className="sales-exceptions">
                <h4>Required exceptions</h4>
                {review.review.requiredExceptions.length === 0 ? (
                  <p>No exception approval required.</p>
                ) : (
                  <ul>
                    {review.review.requiredExceptions.map((exception) => (
                      <li key={exception.type}>
                        <strong>{exception.type.replaceAll("_", " ")}</strong>
                        <span>
                          {review.review.currency} {exception.amount}
                          {exception.percentage === null
                            ? ""
                            : ` · ${exception.percentage}%`}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="sales-evidence-lines">
                {review.review.lines.map((line) => (
                  <article key={line.lineId}>
                    <div className="sales-evidence-line-title">
                      <div>
                        <strong>{line.skuCode}</strong>
                        <span>{line.skuName}</span>
                      </div>
                      <span>
                        {line.belowFloor ? "Below floor" : "Floor respected"}
                      </span>
                    </div>
                    <dl>
                      <div>
                        <dt>Conversion</dt>
                        <dd>
                          {line.enteredQuantity} {line.enteredUnit} ={" "}
                          {line.quantityBase}{" "}
                          {line.conversionSnapshot["base_stocking_unit"] ??
                            "base units"}
                        </dd>
                      </div>
                      <div>
                        <dt>Pricing</dt>
                        <dd>
                          List {line.listUnitPrice} · Effective{" "}
                          {line.effectiveUnitPrice} · Floor{" "}
                          {line.floorUnitPrice ?? "none"}
                        </dd>
                      </div>
                      <div>
                        <dt>Discount</dt>
                        <dd>{line.allocatedDiscount}</dd>
                      </div>
                      <div>
                        <dt>Tax</dt>
                        <dd>
                          {line.taxSnapshot["tax_code"] ?? "Uncoded"} ·{" "}
                          {line.taxSnapshot["tax_rate"] ?? "0"} ·{" "}
                          {line.taxSnapshot["inclusion_mode"] ?? "unspecified"}
                        </dd>
                      </div>
                    </dl>
                    <dl
                      aria-label={`${line.skuCode} warehouse availability`}
                      className="sales-stock-evidence"
                    >
                      <div>
                        <dt>On hand</dt>
                        <dd>{line.warehouseOnHandBase}</dd>
                      </div>
                      <div>
                        <dt>Reserved</dt>
                        <dd>{line.warehouseReservedBase}</dd>
                      </div>
                      <div>
                        <dt>Reservable</dt>
                        <dd>{line.reservableQuantityBase}</dd>
                      </div>
                      <div>
                        <dt>Backorder</dt>
                        <dd>{line.backorderQuantityBase}</dd>
                      </div>
                    </dl>
                  </article>
                ))}
              </div>
            </section>
          ))}
        {approval !== null && (
          <div
            className={
              approval.kind === "approved"
                ? "sales-approval-result"
                : "sales-message"
            }
            role={approval.kind === "approved" ? "status" : "alert"}
          >
            <div>
              <h3>
                {approval.kind === "approved"
                  ? Number(approval.approval.backorderQuantityBase) > 0
                    ? "Approved with backorder"
                    : "Approved and fully reserved"
                  : failureCopy[approval.kind].title}
              </h3>
              {approval.kind === "approved" ? (
                <p>
                  {approval.approval.reservedQuantityBase} reserved ·{" "}
                  {approval.approval.backorderQuantityBase} backordered
                </p>
              ) : (
                <>
                  <p>{failureCopy[approval.kind].guidance}</p>
                  {approval.message !== undefined && <p>{approval.message}</p>}
                  <p>
                    Support reference <code>{approval.correlationId}</code>
                    {approval.errorCode !== undefined && (
                      <>
                        {" "}
                        · <code>{approval.errorCode}</code>
                      </>
                    )}
                  </p>
                </>
              )}
            </div>
          </div>
        )}
      </section>
    </>
  );
}
