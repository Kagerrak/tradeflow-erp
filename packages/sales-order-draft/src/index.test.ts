import { describe, expect, it } from "vitest";

import {
  createSalesOrderDraft,
  loadOrderEntryReference,
  previewSalesOrderDraft,
} from "./index";

const reference = {
  addresses: [
    {
      address_key: "DELIVERY",
      address_version_id: "4d8ad09a-f96f-41b3-b30a-0af843353943",
      city: "Manila",
      country_code: "PH",
      line_1: "100 Draft Street",
      line_2: null,
      postal_code: "1000",
      region: "NCR",
      version: 1,
    },
  ],
  branch_id: "efad4205-5060-49fb-b752-3faca649ca6e",
  currency: "PHP",
  customer_id: "98481a1c-e493-41a6-851b-93142553ceab",
  customer_name: "Draft Order Retail",
  customer_version: 1,
  items: [
    {
      base_quantity_per_unit: "1.000000",
      base_stocking_unit: "EA",
      floor_unit_price: "7.500000",
      list_unit_price: "9.500000",
      price_list_line_id: "d60c173e-efec-4b3a-b1c6-1e893e4cdfff",
      sku_code: "COLA-330",
      sku_id: "4d209f00-0c57-49fc-9f0b-fc5cf082cb02",
      sku_name: "Cola 330 SKU",
      tax_code: "VAT12",
      tax_code_version_id: "348389fa-38c7-4dc1-b6e6-31b7bbc2a66e",
      tax_rate: "0.120000",
      unit_code: "EA",
      unit_conversion_id: null,
      unit_conversion_version: null,
    },
  ],
  payment_timing_default: "prepaid",
  price_inclusion_mode: "exclusive",
  price_list_code: "MNL-CUSTOMER",
  price_list_version: 1,
  price_list_version_id: "2903b3b0-608f-4caf-907a-0dd0886bb8f7",
  pricing_date: "2026-07-29",
};

describe("Sales Order Draft client", () => {
  it("matches the exclusive-tax API golden vector exactly", () => {
    expect(
      previewSalesOrderDraft({
        currency: "PHP",
        discountAmount: "0.03",
        inclusionMode: "exclusive",
        lines: [
          {
            linePosition: 1,
            quantity: "3.000000",
            taxRate: "0.120000",
            unitPrice: "9.500000",
          },
          {
            linePosition: 2,
            quantity: "1.000000",
            taxRate: "0.120000",
            unitPrice: "5.000000",
          },
        ],
      }),
    ).toMatchObject({
      discountTotal: "0.03",
      grandTotal: "37.49",
      subtotal: "33.50",
      taxTotal: "4.02",
      taxableTotal: "33.47",
    });
  });

  it("uses stable line order for an inclusive-tax residual tie", () => {
    const preview = previewSalesOrderDraft({
      currency: "PHP",
      discountAmount: "0.01",
      inclusionMode: "inclusive",
      lines: [
        {
          linePosition: 1,
          quantity: "1.000000",
          taxRate: "0.120000",
          unitPrice: "1.000000",
        },
        {
          linePosition: 2,
          quantity: "1.000000",
          taxRate: "0.120000",
          unitPrice: "1.000000",
        },
      ],
    });
    expect(preview).toMatchObject({
      discountTotal: "0.01",
      grandTotal: "1.99",
      subtotal: "2.00",
      taxTotal: "0.22",
      taxableTotal: "1.77",
    });
    expect(preview.lines.map((line) => line.allocatedDiscount)).toEqual([
      "0.01",
      "0.00",
    ]);
  });

  it("rounds six-place discounts half up for every supported currency scale", () => {
    expect(
      previewSalesOrderDraft({
        currency: "PHP",
        discountAmount: "0.006000",
        inclusionMode: "exclusive",
        lines: [
          {
            linePosition: 1,
            quantity: "1.000000",
            taxRate: "0.000000",
            unitPrice: "1.000000",
          },
        ],
      }).discountTotal,
    ).toBe("0.01");
    expect(
      previewSalesOrderDraft({
        currency: "XOF",
        discountAmount: "0.500000",
        inclusionMode: "exclusive",
        lines: [
          {
            linePosition: 1,
            quantity: "1.000000",
            taxRate: "0.000000",
            unitPrice: "2.000000",
          },
        ],
      }).discountTotal,
    ).toBe("1");
  });

  it("maps server-issued effective references for offline caching", async () => {
    const state = await loadOrderEntryReference({
      accessToken: "token",
      baseUrl: "https://api.test",
      branchId: reference.branch_id,
      correlationId: "sales-reference",
      customerId: reference.customer_id,
      fetch: () =>
        Promise.resolve(
          new Response(JSON.stringify(reference), {
            headers: { "content-type": "application/json" },
            status: 200,
          }),
        ),
    });
    expect(state.kind).toBe("ready");
    if (state.kind !== "ready") throw new Error("Expected ready reference.");
    expect(state.reference.priceListCode).toBe("MNL-CUSTOMER");
    expect(state.reference.items[0]?.listUnitPrice).toBe("9.500000");
    expect(state.reference.addresses[0]?.line1).toBe("100 Draft Street");
  });

  it("classifies a server version conflict for explicit review", async () => {
    const state = await createSalesOrderDraft({
      accessToken: "token",
      baseUrl: "https://api.test",
      command: {
        branch_id: reference.branch_id,
        customer_id: reference.customer_id,
        expected_customer_version: reference.customer_version,
        expected_price_list_version_id: reference.price_list_version_id,
        expected_pricing_date: reference.pricing_date,
        delivery_address_version_id: reference.addresses[0]!.address_version_id,
        lines: [
          {
            expected_price_list_line_id: reference.items[0]!.price_list_line_id,
            expected_unit_conversion_id: null,
            expected_unit_conversion_version: null,
            line_id: "a5551b35-34ff-4cb8-8062-d6386f7e4e25",
            manual_override_unit_price: null,
            price_override_reason: null,
            quantity: "1.000000",
            sku_id: reference.items[0]!.sku_id,
            unit_code: "EA",
          },
        ],
        order_discount_amount: "0.000000",
        payment_timing_override_reason: null,
        payment_timing_policy: null,
        sales_order_id: "323484f7-f3b5-4070-846f-83b9aad4fadb",
      },
      correlationId: "sales-conflict",
      fetch: () => Promise.resolve(new Response("{}", { status: 409 })),
      idempotencyKey: "sales-create",
    });
    expect(state).toEqual({
      correlationId: "sales-conflict",
      kind: "conflict",
    });
  });
});
