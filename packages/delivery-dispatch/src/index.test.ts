import { describe, expect, it } from "vitest";

import { listAssignedDeliveries } from "./index";

describe("Delivery Dispatch client", () => {
  it("maps the minimum cacheable assigned Delivery task", async () => {
    const state = await listAssignedDeliveries({
      accessToken: "token",
      baseUrl: "https://api.test",
      correlationId: "delivery-list",
      fetch: () =>
        Promise.resolve(
          Response.json(
            {
              items: [
                {
                  assigned_to: "delivery-mnl",
                  collection_required: true,
                  delivery_address: {
                    city: "Manila",
                    line_1: "100 Payment Street",
                  },
                  delivery_id: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
                  evidence_requirements: ["recipient_name", "signature"],
                  fulfillment_order_id: "765b5ab6-7f39-4671-8561-747755641016",
                  lines: [
                    {
                      line_id: "4af0c99a-b55d-4f68-bf34-6f0805630032",
                      lot_selections: [],
                      quantity_base: "2.000000",
                      serial_numbers: ["SN-001"],
                      sku_code: "JUICE-1L",
                      sku_id: "37989314-b1b7-4bea-9c68-b6390ddae80f",
                      sku_name: "Mango Juice 1L",
                    },
                  ],
                  payment_timing_policy: "cash_on_delivery",
                  recipient_name: "Retail Customer",
                  status: "dispatched",
                  version: 1,
                },
              ],
              total: 1,
            },
            {
              headers: {
                ETag: '"delivery-v1"',
                "X-Correlation-ID": "delivery-list",
              },
            },
          ),
        ),
    });

    expect(state).toEqual({
      cacheTag: '"delivery-v1"',
      correlationId: "delivery-list",
      items: [
        expect.objectContaining({
          assignedTo: "delivery-mnl",
          collectionRequired: true,
          evidenceRequirements: ["recipient_name", "signature"],
          lines: [
            expect.objectContaining({
              serialNumbers: ["SN-001"],
              skuCode: "JUICE-1L",
            }),
          ],
          paymentTimingPolicy: "cash_on_delivery",
        }),
      ],
      kind: "ready",
      total: 1,
    });
  });
});
