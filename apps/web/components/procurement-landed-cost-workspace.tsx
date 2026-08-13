"use client";

import type { components } from "@tradeflow/api-client";
import Link from "next/link";
import { useEffect, useState } from "react";

type LandedCostResponse = components["schemas"]["LandedCostResponse"];
type ChargeCommand = components["schemas"]["ChargeCommand"];

interface Props {
  goodsReceiptId: string;
}

const CHARGE_TYPES = [
  "freight",
  "insurance",
  "customs",
  "brokerage",
  "handling",
];

export function ProcurementLandedCostWorkspace({ goodsReceiptId }: Props) {
  const [data, setData] = useState<LandedCostResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [charges, setCharges] = useState<ChargeCommand[]>([
    { amount_base: "", charge_type: "freight" },
  ]);

  useEffect(
    function fetchOnMount() {
      async function fetchCosts() {
        try {
          const response = await fetch(
            `/api/procurement/goods-receipts/${goodsReceiptId}/landed-costs`,
            { cache: "no-store", headers: { Accept: "application/json" } },
          );
          const body = (await response.json()) as
            LandedCostResponse | { kind?: string };
          if (response.ok && "goods_receipt_id" in body) {
            setData(body);
          } else {
            setMessage("Landed costs could not be loaded.");
          }
        } catch {
          setMessage("Landed cost service unavailable.");
        } finally {
          setLoading(false);
        }
      }
      void fetchCosts();
    },
    [goodsReceiptId],
  );

  function updateCharge(
    index: number,
    field: keyof ChargeCommand,
    value: string,
  ) {
    setCharges(function update(current) {
      return current.map(function mapCharge(charge, i) {
        return i === index ? { ...charge, [field]: value } : charge;
      });
    });
  }

  function addCharge() {
    setCharges(function append(current) {
      return [
        ...current,
        { amount_base: "", charge_type: CHARGE_TYPES[0] ?? "freight" },
      ];
    });
  }

  async function allocate() {
    setMessage(null);
    const body = {
      charges: charges
        .filter(function hasAmount(charge) {
          return String(charge.amount_base).trim().length > 0;
        })
        .map(function toCommand(charge) {
          return {
            amount_base: String(charge.amount_base),
            charge_type: charge.charge_type,
          };
        }),
    };

    try {
      const response = await fetch(
        `/api/procurement/goods-receipts/${goodsReceiptId}/landed-costs`,
        {
          body: JSON.stringify(body),
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const result = (await response.json()) as
        LandedCostResponse | { kind?: string; message?: string };
      if (response.ok && "goods_receipt_id" in result) {
        setData(result);
        setMessage("Landed costs allocated.");
      } else {
        setMessage(
          "message" in result && result.message
            ? result.message
            : "Landed costs could not be allocated.",
        );
      }
    } catch {
      setMessage("Landed cost service unavailable.");
    }
  }

  const totalLandedCost =
    data?.charges.reduce(function sumCharges(sum, charge) {
      return sum + Number.parseFloat(String(charge.amount_base));
    }, 0) ?? 0;

  return (
    <div className="procurement-app">
      <header className="procurement-header">
        <Link href="/">TradeFlow</Link>
        <span>Procurement / Goods receipts</span>
        <span>Landed cost</span>
      </header>
      <main className="procurement-main">
        <section className="procurement-title">
          <div>
            <p className="eyebrow">Inbound acquisition cost</p>
            <h1>Allocate landed cost.</h1>
          </div>
          <p>
            Distribute freight, insurance, customs, brokerage, and handling
            costs across receipt lines to update moving-average inventory
            valuation.
          </p>
        </section>

        {loading ? (
          <p className="procurement-message">Loading landed costs…</p>
        ) : data === null ? (
          <p className="procurement-message">
            {message ?? "Goods receipt not found."}
          </p>
        ) : (
          <>
            <section className="procurement-panel">
              <div className="procurement-section-head">
                <div>
                  <span>Procurement / detail</span>
                  <h2>Receipt {data.goods_receipt_id}</h2>
                </div>
              </div>

              {message !== null && (
                <p className="procurement-message" role="status">
                  {message}
                </p>
              )}

              <table className="procurement-table">
                <thead>
                  <tr>
                    <th>Line</th>
                    <th>SKU ID</th>
                    <th>Received</th>
                    <th>Unit cost</th>
                    <th>Original value</th>
                    <th>Allocated landed cost</th>
                    <th>Total cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map(function renderLine(line, index) {
                    const original = Number.parseFloat(
                      line.original_line_value,
                    );
                    const landed = Number.parseFloat(
                      line.total_allocated_landed_cost,
                    );
                    return (
                      <tr key={line.goods_receipt_line_id}>
                        <td>{index + 1}</td>
                        <td>{line.sku_id}</td>
                        <td>{line.received_quantity_base}</td>
                        <td>{line.unit_cost}</td>
                        <td>{original.toFixed(2)}</td>
                        <td>{landed.toFixed(2)}</td>
                        <td>{(original + landed).toFixed(2)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="procurement-total">
                Total landed cost: {totalLandedCost.toFixed(2)}{" "}
                {data.base_currency}
              </p>
            </section>

            <section className="procurement-panel">
              <div className="procurement-section-head">
                <div>
                  <span>Inventory / allocate</span>
                  <h2>Add charges</h2>
                </div>
              </div>

              {charges.map(function renderCharge(charge, index) {
                return (
                  <div className="procurement-fields" key={index}>
                    <label>
                      Charge type
                      <select
                        onChange={function handleTypeChange(event) {
                          updateCharge(
                            index,
                            "charge_type",
                            event.target.value,
                          );
                        }}
                        value={charge.charge_type}
                      >
                        {CHARGE_TYPES.map(function renderType(type) {
                          return (
                            <option key={type} value={type}>
                              {type}
                            </option>
                          );
                        })}
                      </select>
                    </label>
                    <label>
                      Amount (base currency)
                      <input
                        onChange={function handleAmountChange(event) {
                          updateCharge(
                            index,
                            "amount_base",
                            event.target.value,
                          );
                        }}
                        value={charge.amount_base}
                      />
                    </label>
                  </div>
                );
              })}
              <button onClick={addCharge} type="button">
                Add charge
              </button>
              <button
                onClick={function handleAllocateClick() {
                  void allocate();
                }}
                type="button"
              >
                Allocate landed costs
              </button>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
