"use client";

import type { CustomerDirectoryState } from "@tradeflow/customer-directory";
import {
  type CreateSalesOrderDraftInput,
  type ReferenceState,
  type SalesOrderDraft,
  type SaveDraftState,
  type UpdateSalesOrderDraftInput,
} from "@tradeflow/sales-order-draft";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

type Branch = {
  branch_id: string;
  code: string;
  is_active: boolean;
  name: string;
};

type Scope =
  | {
      branches: Branch[];
      capabilities: string[];
      user: { display_name: string; subject: string };
    }
  | {
      correlationId: string;
      kind: "forbidden" | "unauthenticated" | "unavailable";
    };

type WorkspaceState =
  | { kind: "loading" }
  | {
      correlationId: string;
      kind: "denied";
      reason: "forbidden" | "unauthenticated" | "unavailable";
    }
  | {
      customers: Extract<CustomerDirectoryState, { kind: "ready" }>["items"];
      kind: "ready";
      scope: Extract<Scope, { branches: Branch[] }>;
    };

async function loadScope(): Promise<Scope> {
  const response = await fetch("/api/customer-scope", { cache: "no-store" });
  return (await response.json()) as Scope;
}

async function loadCustomers(): Promise<CustomerDirectoryState> {
  const response = await fetch("/api/customers?query=", { cache: "no-store" });
  return (await response.json()) as CustomerDirectoryState;
}

async function loadReference(
  branchId: string,
  customerId: string,
): Promise<ReferenceState> {
  const response = await fetch(
    `/api/sales-orders/reference?branchId=${encodeURIComponent(branchId)}&customerId=${encodeURIComponent(customerId)}`,
    { cache: "no-store" },
  );
  return (await response.json()) as ReferenceState;
}

export function SalesOrderEditor() {
  const [workspace, setWorkspace] = useState<WorkspaceState>({
    kind: "loading",
  });
  const [customerId, setCustomerId] = useState("");
  const [reference, setReference] = useState<
    ReferenceState | { kind: "idle" } | { kind: "loading" }
  >({ kind: "idle" });
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [discount, setDiscount] = useState("0.00");
  const [paymentPolicy, setPaymentPolicy] = useState<
    "prepaid" | "cash_on_delivery" | "on_account" | ""
  >("");
  const [overrideReason, setOverrideReason] = useState("");
  const [save, setSave] = useState<SaveDraftState | null>(null);
  const [lastSaved, setLastSaved] = useState<SalesOrderDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const orderId = useRef(crypto.randomUUID());
  const idempotencyKey = useRef<string | null>(null);
  const lineIds = useRef(new Map<string, string>());

  const markChanged = useCallback(() => {
    idempotencyKey.current = null;
    setSave(null);
  }, []);

  useEffect(() => {
    let active = true;
    void Promise.all([loadScope(), loadCustomers()])
      .then(([scope, customers]) => {
        if (!active) return;
        if ("kind" in scope) {
          setWorkspace({
            correlationId: scope.correlationId,
            kind: "denied",
            reason: scope.kind,
          });
          return;
        }
        if (customers.kind !== "ready") {
          setWorkspace({
            correlationId: customers.correlationId,
            kind: "denied",
            reason:
              customers.kind === "validation" ? "unavailable" : customers.kind,
          });
          return;
        }
        setWorkspace({ customers: customers.items, kind: "ready", scope });
      })
      .catch(() => {
        if (active) {
          setWorkspace({
            correlationId: crypto.randomUUID(),
            kind: "denied",
            reason: "unavailable",
          });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const selectCustomer = useCallback(
    async (nextCustomerId: string) => {
      setCustomerId(nextCustomerId);
      setReference({ kind: nextCustomerId.length === 0 ? "idle" : "loading" });
      setQuantities({});
      setSave(null);
      setLastSaved(null);
      markChanged();
      if (workspace.kind !== "ready" || nextCustomerId.length === 0) return;
      const customer = workspace.customers.find(
        (item) => item.customerId === nextCustomerId,
      );
      if (customer === undefined) return;
      try {
        const next = await loadReference(customer.branchId, nextCustomerId);
        setReference(next);
        if (next.kind === "ready") {
          setPaymentPolicy(next.reference.paymentTimingDefault);
        }
      } catch {
        setReference({
          correlationId: crypto.randomUUID(),
          kind: "unavailable",
        });
      }
    },
    [markChanged, workspace],
  );

  if (workspace.kind === "loading") {
    return (
      <main className="sales-message" role="status">
        <h1>Loading order-entry scope…</h1>
      </main>
    );
  }
  if (workspace.kind === "denied") {
    return (
      <main className="sales-message" role="alert">
        <Link className="sales-wordmark" href="/">
          TradeFlow / Sales
        </Link>
        <h1>
          {workspace.reason === "unauthenticated"
            ? "Sign in to draft an order"
            : workspace.reason === "forbidden"
              ? "Sales access is not assigned"
              : "Sales order entry is unavailable"}
        </h1>
        <p>
          Support reference <code>{workspace.correlationId}</code>
        </p>
      </main>
    );
  }

  const readyReference =
    reference.kind === "ready" ? reference.reference : undefined;
  const referenceFailure =
    reference.kind !== "idle" &&
    reference.kind !== "loading" &&
    reference.kind !== "ready"
      ? reference
      : undefined;
  const saved = lastSaved ?? undefined;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (readyReference === undefined) return;
    const lines = readyReference.items.flatMap((item) => {
      const quantity = quantities[item.priceListLineId]?.trim() ?? "";
      if (quantity.length === 0 || Number(quantity) <= 0) return [];
      let lineId = lineIds.current.get(item.priceListLineId);
      if (lineId === undefined) {
        lineId = crypto.randomUUID();
        lineIds.current.set(item.priceListLineId, lineId);
      }
      return [
        {
          expected_price_list_line_id: item.priceListLineId,
          expected_unit_conversion_id: item.unitConversionId,
          expected_unit_conversion_version: item.unitConversionVersion,
          line_id: lineId,
          manual_override_unit_price: null,
          price_override_reason: null,
          quantity,
          sku_id: item.skuId,
          unit_code: item.unitCode,
        },
      ];
    });
    if (lines.length === 0) {
      setSave({ correlationId: crypto.randomUUID(), kind: "validation" });
      return;
    }
    const key = idempotencyKey.current ?? crypto.randomUUID();
    idempotencyKey.current = key;
    const fields: UpdateSalesOrderDraftInput = {
      branch_id: readyReference.branchId,
      customer_id: readyReference.customerId,
      expected_customer_version: readyReference.customerVersion,
      expected_price_list_version_id: readyReference.priceListVersionId,
      expected_pricing_date: readyReference.pricingDate,
      delivery_address_version_id:
        readyReference.addresses[0]?.addressVersionId ?? "",
      lines,
      order_discount_amount: discount,
      payment_timing_override_reason:
        paymentPolicy === readyReference.paymentTimingDefault
          ? null
          : overrideReason,
      payment_timing_policy: paymentPolicy === "" ? null : paymentPolicy,
    };
    setSaving(true);
    try {
      const response =
        saved === undefined
          ? await fetch("/api/sales-orders", {
              body: JSON.stringify({
                command: {
                  ...fields,
                  sales_order_id: orderId.current,
                } satisfies CreateSalesOrderDraftInput,
                idempotencyKey: key,
              }),
              headers: { "Content-Type": "application/json" },
              method: "POST",
            })
          : await fetch(`/api/sales-orders/${saved.salesOrderId}`, {
              body: JSON.stringify({
                command: fields,
                expectedVersion: saved.version,
                idempotencyKey: key,
              }),
              headers: { "Content-Type": "application/json" },
              method: "PUT",
            });
      const next = (await response.json()) as SaveDraftState;
      setSave(next);
      if (next.kind === "saved") setLastSaved(next.draft);
    } catch {
      setSave({ correlationId: crypto.randomUUID(), kind: "unavailable" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="sales-app">
      <header className="sales-header">
        <Link className="sales-wordmark" href="/">
          TradeFlow
        </Link>
        <span>Sales order drafts</span>
        <span>{workspace.scope.user.display_name}</span>
      </header>
      <main className="sales-main">
        <section className="sales-title">
          <div>
            <p className="eyebrow">Commercial capture / 005</p>
            <h1>Price the promise before committing it.</h1>
          </div>
          <p>
            Drafts snapshot customer pricing, unit conversion, tax, discount,
            address, and payment timing. Credit and inventory remain untouched
            until Commercial Approval.
          </p>
        </section>
        <section className="sales-panel" aria-labelledby="sales-order-title">
          <div className="sales-panel-head">
            <h2 id="sales-order-title">New Sales Order Draft</h2>
            <span>Server-authoritative pricing</span>
          </div>
          <form className="sales-form" onSubmit={submit}>
            <div className="sales-form-section sales-form-grid">
              <label>
                Customer Account
                <select
                  aria-label="Customer Account"
                  onChange={(event) => void selectCustomer(event.target.value)}
                  value={customerId}
                >
                  <option value="">Choose an active account</option>
                  {workspace.customers
                    .filter((customer) => customer.status === "active")
                    .map((customer) => (
                      <option
                        key={customer.customerId}
                        value={customer.customerId}
                      >
                        {customer.accountNumber} / {customer.legalName}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Payment Timing Policy
                <select
                  aria-label="Payment Timing Policy"
                  disabled={readyReference === undefined}
                  onChange={(event) => {
                    setPaymentPolicy(
                      event.target.value as typeof paymentPolicy,
                    );
                    markChanged();
                  }}
                  value={paymentPolicy}
                >
                  <option value="prepaid">Prepaid</option>
                  <option value="cash_on_delivery">Cash on delivery</option>
                  <option value="on_account">On account</option>
                </select>
              </label>
              {readyReference !== undefined &&
                paymentPolicy !== readyReference.paymentTimingDefault && (
                  <label>
                    Override reason
                    <input
                      aria-label="Payment Timing Override reason"
                      onChange={(event) => {
                        setOverrideReason(event.target.value);
                        markChanged();
                      }}
                      required
                      value={overrideReason}
                    />
                  </label>
                )}
              <label>
                Order discount ({readyReference?.currency ?? "Base Currency"})
                <input
                  aria-label="Order discount"
                  inputMode="decimal"
                  min="0"
                  onChange={(event) => {
                    setDiscount(event.target.value);
                    markChanged();
                  }}
                  step="0.01"
                  value={discount}
                />
              </label>
            </div>
            {reference.kind === "loading" && (
              <div className="sales-message" role="status">
                Loading effective price and tax references…
              </div>
            )}
            {referenceFailure !== undefined && (
              <SalesFailure state={referenceFailure} />
            )}
            {readyReference !== undefined && (
              <div className="sales-form-section">
                <p className="section-number">
                  Price List {readyReference.priceListCode} / version{" "}
                  {readyReference.priceListVersion} /{" "}
                  {readyReference.priceInclusionMode}
                </p>
                {readyReference.items.map((item) => (
                  <div className="sales-line" key={item.priceListLineId}>
                    <div>
                      <strong>{item.skuCode}</strong>
                      <small>
                        {item.skuName} · {item.taxCode} · list{" "}
                        {readyReference.currency} {item.listUnitPrice}
                      </small>
                    </div>
                    <label>
                      Quantity
                      <input
                        aria-label={`${item.skuCode} quantity`}
                        inputMode="decimal"
                        min="0"
                        onChange={(event) => {
                          setQuantities((current) => ({
                            ...current,
                            [item.priceListLineId]: event.target.value,
                          }));
                          markChanged();
                        }}
                        step="0.000001"
                        value={quantities[item.priceListLineId] ?? ""}
                      />
                    </label>
                    <div>
                      <small>Entered unit</small>
                      <strong>{item.unitCode}</strong>
                    </div>
                    <div>
                      <small>Base conversion</small>
                      <strong>
                        {item.baseQuantityPerUnit} {item.baseStockingUnit}
                      </strong>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {saved !== undefined && <SavedDraft draft={saved} />}
            {save !== null && save.kind !== "saved" && (
              <SalesFailure state={save} />
            )}
            <div className="sales-actions">
              <span>
                {saved === undefined
                  ? "No stock or credit commitment"
                  : `Draft version ${saved.version}`}
              </span>
              <button
                disabled={readyReference === undefined || saving}
                type="submit"
              >
                {saving
                  ? "Saving authoritative draft…"
                  : saved === undefined
                    ? "Save Sales Order Draft"
                    : "Save new draft revision"}
              </button>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}

function SavedDraft({ draft }: { draft: SalesOrderDraft }) {
  return (
    <>
      <div className="sales-result" role="status">
        <h3>Draft acknowledged by TradeFlow</h3>
        <p>
          {draft.priceListCode} · {draft.priceInclusionMode} ·{" "}
          {draft.paymentTimingPolicy.replaceAll("_", " ")}
        </p>
      </div>
      <div className="sales-total-strip">
        <div>
          <span>Subtotal</span>
          <strong>
            {draft.currency} {draft.subtotal}
          </strong>
        </div>
        <div>
          <span>Allocated discount</span>
          <strong>{draft.discountTotal}</strong>
        </div>
        <div>
          <span>Tax</span>
          <strong>{draft.taxTotal}</strong>
        </div>
        <div>
          <span>Draft total</span>
          <strong>
            {draft.currency} {draft.grandTotal}
          </strong>
        </div>
      </div>
    </>
  );
}

function SalesFailure({
  state,
}: {
  state: Exclude<ReferenceState | SaveDraftState, { kind: "ready" | "saved" }>;
}) {
  const title =
    state.kind === "conflict"
      ? "Server state changed — review required"
      : state.kind === "validation"
        ? "The draft needs correction"
        : state.kind === "forbidden"
          ? "Sales draft access is not assigned"
          : state.kind === "unauthenticated"
            ? "Sign in to continue"
            : "Draft synchronization is unavailable";
  return (
    <div className="sales-message" role="alert">
      <h3>{title}</h3>
      <p>
        {state.kind === "conflict"
          ? "TradeFlow did not merge the local edits. Reload the authoritative draft and compare it explicitly."
          : "Your entered work remains on screen. Correct it or retry with the same command identity."}
      </p>
      <p>
        Support reference <code>{state.correlationId}</code>
      </p>
    </div>
  );
}
