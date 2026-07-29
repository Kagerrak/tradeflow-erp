import { describe, expect, it } from "vitest";

import {
  commerciallyApproveSalesOrder,
  createSalesOrderDraft,
  loadCommercialReview,
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

  it("maps a partial Commercial Approval without losing base quantities", async () => {
    const state = await commerciallyApproveSalesOrder({
      accessToken: "token",
      baseUrl: "https://api.test",
      command: {
        credit_override_reason: null,
        exception_reason: "Discount reviewed",
        warehouse_id: "02efc423-72ca-48dc-82a8-700566ffbd90",
      },
      correlationId: "approval-correlation",
      expectedVersion: 1,
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              approved_by: "commercial-mnl",
              backorder_quantity_base: "1.000000",
              commercial_approval_id: "9ee0c3c0-d673-452f-bdef-eeec91a4773f",
              credit: {
                approved_excess: "0.00",
                approved_uninvoiced_before: "0.00",
                credit_limit: null,
                open_balance: "0.00",
                order_value: "0.00",
                override_required: false,
                projected_exposure: "0.00",
              },
              maker_subject: "sales-mnl",
              payment_timing_policy: "prepaid",
              required_exceptions: ["discount"],
              reservations: [
                {
                  backorder_quantity_base: "1.000000",
                  line_id: "a5551b35-34ff-4cb8-8062-d6386f7e4e25",
                  ordered_quantity_base: "3.000000",
                  reserved_quantity_base: "2.000000",
                  sku_id: "4d209f00-0c57-49fc-9f0b-fc5cf082cb02",
                },
              ],
              reserved_quantity_base: "2.000000",
              sales_order_id: "323484f7-f3b5-4070-846f-83b9aad4fadb",
              sales_order_revision_id: "be85cc1b-699f-4567-b833-a66944b2d8a6",
              status: "approved",
              warehouse_id: "02efc423-72ca-48dc-82a8-700566ffbd90",
            }),
            { headers: { "content-type": "application/json" }, status: 201 },
          ),
        ),
      idempotencyKey: "approve-order",
      salesOrderId: "323484f7-f3b5-4070-846f-83b9aad4fadb",
    });
    expect(state.kind).toBe("approved");
    if (state.kind !== "approved") throw new Error("Expected approval.");
    expect(state.approval.reservedQuantityBase).toBe("2.000000");
    expect(state.approval.backorderQuantityBase).toBe("1.000000");
    expect(state.approval.requiredExceptions).toEqual(["discount"]);
  });

  it("maps the exact Commercial Review evidence for the selected warehouse", async () => {
    let requestedUrl = "";
    const state = await loadCommercialReview({
      accessToken: "token",
      baseUrl: "https://api.test",
      correlationId: "commercial-review",
      fetch: (request) => {
        requestedUrl = request.url;
        return Promise.resolve(
          new Response(
            JSON.stringify({
              approved_uninvoiced: "250.00",
              credit_hold: false,
              credit_limit: "1000.00",
              currency: "PHP",
              customer_account_number: "MNL-SALES-001",
              customer_id: reference.customer_id,
              customer_name: "Draft Order Retail",
              customer_snapshot_current: true,
              customer_status: "active",
              discount_total: "25.00",
              grand_total: "1120.00",
              lines: [
                {
                  allocated_discount: "25.00",
                  backorder_quantity_base: "1.000000",
                  below_floor: true,
                  calculation_snapshot: {
                    line_total: "1120.00",
                    subtotal: "1000.00",
                  },
                  conversion_snapshot: {
                    base_quantity_per_unit: "12.000000",
                    base_stocking_unit: "EA",
                    entered_unit: "CASE",
                  },
                  effective_unit_price: "100.000000",
                  entered_quantity: "10.000000",
                  entered_unit: "CASE",
                  floor_unit_price: "105.000000",
                  line_id: "a5551b35-34ff-4cb8-8062-d6386f7e4e25",
                  list_unit_price: "110.000000",
                  manual_override_unit_price: "100.000000",
                  quantity_base: "120.000000",
                  reservable_quantity_base: "119.000000",
                  sku_code: "COLA-CASE",
                  sku_id: reference.items[0]!.sku_id,
                  sku_name: "Cola case",
                  tax_snapshot: {
                    inclusion_mode: "exclusive",
                    tax_code: "VAT12",
                    tax_rate: "0.120000",
                  },
                  warehouse_on_hand_base: "150.000000",
                  warehouse_reserved_base: "31.000000",
                },
              ],
              maker_subject: "sales-mnl",
              open_balance: "100.00",
              payment_terms: "Net 30",
              payment_timing_policy: "on_account",
              projected_exposure: "1470.00",
              required_exceptions: [
                {
                  amount: "20.00",
                  exception_type: "discount",
                  percentage: "2.500000",
                },
                {
                  amount: "70.00",
                  exception_type: "credit_override",
                  percentage: null,
                },
              ],
              sales_order_id: "323484f7-f3b5-4070-846f-83b9aad4fadb",
              sales_order_revision_id: "be85cc1b-699f-4567-b833-a66944b2d8a6",
              status: "draft",
              subtotal: "1000.00",
              tax_total: "145.00",
              version: 1,
              warehouse_id: "02efc423-72ca-48dc-82a8-700566ffbd90",
            }),
            { headers: { "content-type": "application/json" }, status: 200 },
          ),
        );
      },
      salesOrderId: "323484f7-f3b5-4070-846f-83b9aad4fadb",
      warehouseId: "02efc423-72ca-48dc-82a8-700566ffbd90",
    });

    expect(requestedUrl).toBe(
      "https://api.test/v1/sales/orders/323484f7-f3b5-4070-846f-83b9aad4fadb/commercial-review?warehouse_id=02efc423-72ca-48dc-82a8-700566ffbd90",
    );
    expect(state.kind).toBe("ready");
    if (state.kind !== "ready") throw new Error("Expected review evidence.");
    expect(state.review.requiredExceptions).toEqual([
      { amount: "20.00", percentage: "2.500000", type: "discount" },
      { amount: "70.00", percentage: null, type: "credit_override" },
    ]);
    expect(state.review.lines[0]).toMatchObject({
      backorderQuantityBase: "1.000000",
      conversionSnapshot: {
        base_quantity_per_unit: "12.000000",
        base_stocking_unit: "EA",
        entered_unit: "CASE",
      },
      reservableQuantityBase: "119.000000",
      taxSnapshot: {
        inclusion_mode: "exclusive",
        tax_code: "VAT12",
        tax_rate: "0.120000",
      },
      warehouseOnHandBase: "150.000000",
      warehouseReservedBase: "31.000000",
    });
  });

  it("preserves safe Commercial Approval error details for recovery guidance", async () => {
    const state = await commerciallyApproveSalesOrder({
      accessToken: "token",
      baseUrl: "https://api.test",
      command: {
        credit_override_reason: null,
        exception_reason: "Discount reviewed",
        warehouse_id: "02efc423-72ca-48dc-82a8-700566ffbd90",
      },
      correlationId: "approval-denied",
      expectedVersion: 1,
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "approval_limit_exceeded",
                message: "This exception exceeds the checker's approval limit.",
              },
            }),
            { headers: { "content-type": "application/json" }, status: 403 },
          ),
        ),
      idempotencyKey: "approve-order",
      salesOrderId: "323484f7-f3b5-4070-846f-83b9aad4fadb",
    });

    expect(state).toEqual({
      correlationId: "approval-denied",
      errorCode: "approval_limit_exceeded",
      kind: "exception_required",
      message: "This exception exceeds the checker's approval limit.",
    });
  });

  it("does not coerce malformed Commercial Approval error details", async () => {
    const state = await commerciallyApproveSalesOrder({
      accessToken: "token",
      baseUrl: "https://api.test",
      command: {
        credit_override_reason: null,
        exception_reason: null,
        warehouse_id: "02efc423-72ca-48dc-82a8-700566ffbd90",
      },
      correlationId: "approval-conflict",
      expectedVersion: 1,
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: 409,
                message: { reason: "not safe display text" },
              },
            }),
            { headers: { "content-type": "application/json" }, status: 409 },
          ),
        ),
      idempotencyKey: "approve-order",
      salesOrderId: "323484f7-f3b5-4070-846f-83b9aad4fadb",
    });

    expect(state).toEqual({
      correlationId: "approval-conflict",
      kind: "conflict",
    });
  });
});
