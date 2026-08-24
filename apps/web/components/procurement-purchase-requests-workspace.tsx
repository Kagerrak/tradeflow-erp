"use client";

import type { components } from "@tradeflow/api-client";
import { useEffect, useState } from "react";
import { PageHeader } from "./ui/page-header";

type PurchaseRequestSummary = components["schemas"]["PurchaseRequestSummary"];
type PurchaseRequestResponse = components["schemas"]["PurchaseRequestResponse"];
type CreatePurchaseRequestCommand =
  components["schemas"]["CreatePurchaseRequestCommand"];
type RevisePurchaseRequestCommand =
  components["schemas"]["RevisePurchaseRequestCommand"];
type ConversionResponse =
  components["schemas"]["tradeflow_api__purchase_requests__ConversionResponse"];

type ListState =
  | { kind: "loading" }
  | { items: PurchaseRequestSummary[]; kind: "ready"; total: number }
  | { kind: "unavailable" };

export function ProcurementPurchaseRequestsWorkspace() {
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [selected, setSelected] = useState<PurchaseRequestResponse | null>(
    null,
  );
  const [form, setForm] = useState<CreatePurchaseRequestCommand>({
    supplier_id: "",
    branch_id: "",
    code: "",
    currency: "PHP",
    exchange_rate: "1",
    lines: [
      { sku_id: "", requested_quantity: "1", unit_code: "", unit_cost: "" },
    ],
  });
  const [convertCode, setConvertCode] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const fetchPurchaseRequests = async (): Promise<
    { items: PurchaseRequestSummary[]; total: number } | { kind?: string }
  > => {
    const response = await fetch("/api/procurement/purchase-requests", {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    return (await response.json()) as
      { items: PurchaseRequestSummary[]; total: number } | { kind?: string };
  };

  const refresh = async () => {
    setList({ kind: "loading" });
    try {
      const data = await fetchPurchaseRequests();
      if ("items" in data) {
        setList({ kind: "ready", items: data.items, total: data.total });
      } else {
        setList({ kind: "unavailable" });
      }
    } catch {
      setList({ kind: "unavailable" });
    }
  };

  const loadDetail = async (purchaseRequestId: string) => {
    try {
      const response = await fetch(
        `/api/procurement/purchase-requests/${purchaseRequestId}`,
        { cache: "no-store", headers: { Accept: "application/json" } },
      );
      const data = (await response.json()) as
        PurchaseRequestResponse | { kind?: string };
      if (response.ok && "purchase_request_id" in data) {
        setSelected(data);
        setConvertCode("");
      } else {
        setMessage("Purchase request could not be loaded.");
      }
    } catch {
      setMessage("Purchase request service unavailable.");
    }
  };

  useEffect(() => {
    void fetchPurchaseRequests()
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
      const response = await fetch("/api/procurement/purchase-requests", {
        body: JSON.stringify(form),
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const data = (await response.json()) as
        PurchaseRequestResponse | { kind?: string };
      if (response.ok && "purchase_request_id" in data) {
        setSelected(data);
        setMessage(`Created ${data.code}.`);
        void refresh();
      } else {
        setMessage("Purchase request could not be created.");
      }
    } catch {
      setMessage("Purchase request service unavailable.");
    }
  };

  const revise = async () => {
    if (selected === null) return;
    setMessage(null);
    const command: RevisePurchaseRequestCommand = {
      branch_id: selected.branch_id,
      currency: selected.currency,
      exchange_rate: selected.exchange_rate,
      expected_version: selected.version,
      lines: selected.lines.map((line) => ({
        requested_quantity: line.requested_quantity,
        sku_id: line.sku_id,
        unit_code: line.unit_code,
        unit_cost: line.unit_cost,
      })),
      supplier_id: selected.supplier_id,
    };
    try {
      const response = await fetch(
        `/api/procurement/purchase-requests/${selected.purchase_request_id}`,
        {
          body: JSON.stringify(command),
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          method: "PUT",
        },
      );
      const data = (await response.json()) as
        PurchaseRequestResponse | { kind?: string };
      if (response.ok && "purchase_request_id" in data) {
        setSelected(data);
        setMessage(`Revised ${data.code}.`);
        void refresh();
      } else {
        setMessage("Purchase request could not be revised.");
      }
    } catch {
      setMessage("Purchase request service unavailable.");
    }
  };

  const act = async (action: "approve" | "reject") => {
    if (selected === null) return;
    setMessage(null);
    try {
      const response = await fetch(
        `/api/procurement/purchase-requests/${selected.purchase_request_id}/${action}`,
        {
          body: JSON.stringify({ expected_version: selected.version }),
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as
        PurchaseRequestResponse | { kind?: string };
      if (response.ok && "purchase_request_id" in data) {
        setSelected(data);
        setMessage(
          `${action === "approve" ? "Approved" : "Rejected"} ${data.code}.`,
        );
        void refresh();
      } else {
        setMessage(`Purchase request could not be ${action}d.`);
      }
    } catch {
      setMessage("Purchase request service unavailable.");
    }
  };

  const convert = async () => {
    if (selected === null || convertCode.length === 0) return;
    setMessage(null);
    const lines = selected.lines
      .filter((line) => Number(line.open_quantity) > 0)
      .map((line) => ({
        purchase_request_line_id: line.purchase_request_line_id,
        requested_quantity: line.open_quantity,
      }));
    if (lines.length === 0) {
      setMessage("No open quantity to convert.");
      return;
    }
    try {
      const response = await fetch(
        `/api/procurement/purchase-requests/${selected.purchase_request_id}/conversions`,
        {
          body: JSON.stringify({
            expected_version: selected.version,
            lines,
            purchase_order_code: convertCode,
          }),
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const data = (await response.json()) as
        ConversionResponse | { kind?: string };
      if (response.ok && "purchase_order_id" in data) {
        setMessage(`Converted to ${data.purchase_order_code}.`);
        void loadDetail(selected.purchase_request_id);
        void refresh();
      } else {
        setMessage("Purchase request could not be converted.");
      }
    } catch {
      setMessage("Purchase request service unavailable.");
    }
  };

  const updateLine = (
    index: number,
    patch: Partial<CreatePurchaseRequestCommand["lines"][number]>,
  ) => {
    setForm((current) => {
      const lines = current.lines.map((line, i) =>
        i === index ? { ...line, ...patch } : line,
      );
      return { ...current, lines };
    });
  };

  return (
    <>
      <PageHeader
        description="Create, approve, and partially convert purchase requests to linked purchase order drafts."
        eyebrow="Procurement"
        title="Purchase requests"
      />

      <section className="procurement-title card">
        <div>
          <p className="eyebrow">Supplier requisitions</p>
          <h1>Purchase requests.</h1>
        </div>
        <p>
          Create, approve, and partially convert purchase requests to linked
          purchase order drafts.
        </p>
      </section>

      <section className="procurement-panel">
        <div className="procurement-section-head">
          <div>
            <span>Procurement / write</span>
            <h2>Create purchase request</h2>
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
                setForm((current) => ({
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
                setForm((current) => ({
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
                setForm((current) => ({
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
                setForm((current) => ({
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
                setForm((current) => ({
                  ...current,
                  exchange_rate: event.target.value,
                }))
              }
              value={form.exchange_rate}
            />
          </label>
          {form.lines.map((line, index) => (
            <div className="procurement-wide" key={index}>
              <label>
                Line {index + 1} — SKU ID
                <input
                  onChange={(event) =>
                    updateLine(index, { sku_id: event.target.value })
                  }
                  value={line.sku_id}
                />
              </label>
              <label>
                Quantity
                <input
                  onChange={(event) =>
                    updateLine(index, {
                      requested_quantity: event.target.value,
                    })
                  }
                  value={line.requested_quantity}
                />
              </label>
              <label>
                Unit
                <input
                  onChange={(event) =>
                    updateLine(index, { unit_code: event.target.value })
                  }
                  value={line.unit_code}
                />
              </label>
              <label>
                Unit cost
                <input
                  onChange={(event) =>
                    updateLine(index, { unit_cost: event.target.value })
                  }
                  value={line.unit_cost}
                />
              </label>
            </div>
          ))}
          <button onClick={create} type="button">
            Create purchase request
          </button>
        </div>
      </section>

      <section className="procurement-panel">
        <div className="procurement-section-head">
          <div>
            <span>Procurement / read</span>
            <h2>Purchase requests</h2>
          </div>
        </div>

        {list.kind === "loading" && (
          <p className="procurement-message">Loading purchase requests…</p>
        )}
        {list.kind === "unavailable" && (
          <p className="procurement-message">Purchase requests unavailable.</p>
        )}

        {list.kind === "ready" && (
          <>
            {list.items.length === 0 ? (
              <p className="procurement-empty">No purchase requests found.</p>
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
                  {list.items.map((request) => (
                    <tr key={request.purchase_request_id}>
                      <td>{request.code}</td>
                      <td>{request.status}</td>
                      <td>{request.currency}</td>
                      <td>{request.version}</td>
                      <td>
                        <button
                          onClick={() =>
                            void loadDetail(request.purchase_request_id)
                          }
                          type="button"
                        >
                          Open
                        </button>
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
          <div className="procurement-fields">
            {(selected.status === "draft" ||
              selected.status === "submitted") && (
              <>
                <button onClick={() => void act("approve")} type="button">
                  Approve
                </button>
                <button onClick={() => void act("reject")} type="button">
                  Reject
                </button>
                <button onClick={() => void revise()} type="button">
                  Revise
                </button>
              </>
            )}
            {(selected.status === "approved" ||
              selected.status === "partially_converted") && (
              <>
                <label>
                  Purchase order code
                  <input
                    onChange={(event) => setConvertCode(event.target.value)}
                    value={convertCode}
                  />
                </label>
                <button onClick={() => void convert()} type="button">
                  Convert to purchase order
                </button>
              </>
            )}
          </div>
        </section>
      )}
    </>
  );
}
