import { describe, expect, it } from "vitest";

import {
  getPickingContext,
  listPicks,
  postPick,
  resolveBarcode,
  reversePick,
} from "./index";

const orderId = "765b5ab6-7f39-4671-8561-747755641016";
const lineId = "4af0c99a-b55d-4f68-bf34-6f0805630032";
const pickId = "6dfdb618-3d36-4597-aa35-a3ff46fa8aa0";
const warehouseId = "dd2cabf2-3f01-4a5c-94a8-b1a580b1d0f4";

const contextResponse = {
  fulfillment_order_id: orderId,
  lines: [
    {
      base_stocking_unit: "EA",
      expiration_control: true,
      fefo_candidates: [
        {
          available_quantity_base: "18.000000",
          expiration_date: "2026-08-20",
          lot_code: "LOT-EARLY",
          recommended: true,
        },
      ],
      line_id: lineId,
      picked_quantity_base: "6.000000",
      released_quantity_base: "24.000000",
      remaining_quantity_base: "18.000000",
      reversed_quantity_base: "0.000000",
      sku_code: "JUICE-1L",
      sku_id: "37989314-b1b7-4bea-9c68-b6390ddae80f",
      sku_name: "Mango Juice 1L",
      tracking_policy: "lot",
    },
  ],
  status: "partially_picked",
  version: 4,
  warehouse_id: warehouseId,
};

describe("Warehouse Picking client", () => {
  it("maps exact released, picked, and remaining quantities from picking context", async () => {
    const state = await getPickingContext({
      accessToken: "token",
      baseUrl: "https://api.test",
      correlationId: "pick-context",
      fetch: () =>
        Promise.resolve(
          Response.json(contextResponse, {
            headers: { "X-Correlation-ID": "pick-context" },
          }),
        ),
      fulfillmentOrderId: orderId,
    });

    expect(state).toEqual({
      context: expect.objectContaining({
        fulfillmentOrderId: orderId,
        status: "partially_picked",
        warehouseId,
      }),
      correlationId: "pick-context",
      kind: "ready",
    });
    if (state.kind === "ready") {
      expect(state.context.lines[0]).toEqual(
        expect.objectContaining({
          fefoCandidates: [
            expect.objectContaining({
              lotCode: "LOT-EARLY",
              recommended: true,
            }),
          ],
          trackingPolicy: "lot",
        }),
      );
    }
  });

  it("turns a denied barcode into an actionable non-mutating scan state", async () => {
    const state = await resolveBarcode({
      accessToken: "token",
      barcode: "INACTIVE-LOT",
      baseUrl: "https://api.test",
      correlationId: "scan-denied",
      fetch: () =>
        Promise.resolve(
          Response.json(
            {
              error: {
                code: "barcode_mapping_inactive",
                correlation_id: "scan-denied",
                message: "The barcode mapping is inactive.",
              },
            },
            { status: 422 },
          ),
        ),
      fulfillmentOrderId: orderId,
      lineId,
      warehouseId,
    });

    expect(state).toEqual({
      code: "barcode_mapping_inactive",
      correlationId: "scan-denied",
      kind: "scan_denied",
      message: "The barcode mapping is inactive.",
    });
  });

  it("maps resolved barcode identity and Unit Conversion evidence", async () => {
    const state = await resolveBarcode({
      accessToken: "token",
      barcode: "LOT-EARLY-SCAN",
      baseUrl: "https://api.test",
      correlationId: "scan-resolved",
      fetch: () =>
        Promise.resolve(
          Response.json({
            barcode: "LOT-EARLY-SCAN",
            barcode_mapping_id: "barcode-map",
            base_quantity_per_unit: "12.000000",
            expiration_date: "2026-08-20",
            lot_code: "LOT-EARLY",
            mapping_type: "lot",
            serial_number: null,
            sku_code: "JUICE-1L",
            sku_id: contextResponse.lines[0]?.sku_id,
            unit_code: "CASE",
          }),
        ),
      fulfillmentOrderId: orderId,
      lineId,
      warehouseId,
    });

    expect(state).toEqual({
      correlationId: "scan-resolved",
      kind: "resolved",
      resolution: expect.objectContaining({
        baseQuantityPerUnit: "12.000000",
        lotCode: "LOT-EARLY",
        unitCode: "CASE",
      }),
    });
  });

  it("posts a partial pick with a stable command identity and classifies stale work as conflict", async () => {
    let captured: Request | undefined;
    const state = await postPick({
      accessToken: "token",
      baseUrl: "https://api.test",
      command: {
        expected_fulfillment_version: 4,
        lines: [
          {
            line_id: lineId,
            quantity: "2",
            selections: [
              {
                lot_code: "LOT-EARLY",
                quantity: "2",
              },
            ],
            unit_code: "CASE",
          },
        ],
        pick_id: pickId,
      },
      correlationId: "pick-conflict",
      fetch: (request) => {
        captured = request;
        return Promise.resolve(
          Response.json(
            {
              error: {
                code: "fulfillment_version_conflict",
                correlation_id: "pick-conflict",
                message: "Refresh before posting the Pick.",
              },
            },
            { status: 409 },
          ),
        );
      },
      fulfillmentOrderId: orderId,
      idempotencyKey: "stable-pick-command",
    });

    expect(captured?.headers.get("Idempotency-Key")).toBe(
      "stable-pick-command",
    );
    expect(await captured?.json()).toEqual(
      expect.objectContaining({ pick_id: pickId }),
    );
    expect(state).toEqual({
      code: "fulfillment_version_conflict",
      correlationId: "pick-conflict",
      kind: "conflict",
      message: "Refresh before posting the Pick.",
    });
  });

  it("lists immutable picks and posts a reasoned reversal", async () => {
    const requests: Request[] = [];
    const fetch = (request: Request) => {
      requests.push(request);
      if (request.method === "GET") {
        return Promise.resolve(
          Response.json({
            items: [
              {
                actor_subject: "warehouse-clerk",
                correlation_id: "pick-post",
                event_type: "posted",
                lines: [],
                pick_id: pickId,
                posted_at: "2026-07-29T12:00:00Z",
                quantity_base: "6.000000",
                reason: null,
                reversal_of_pick_id: null,
              },
            ],
            total: 1,
          }),
        );
      }
      return Promise.resolve(
        Response.json({
          fulfillment_order_id: orderId,
          original_pick_id: pickId,
          reversal_pick_id: "ebcdef1f-4712-478e-896b-67dc51058c0c",
          reversed_quantity_base: "6.000000",
          source_movement_ids: ["available-in"],
          staging_movement_ids: ["staging-out"],
          status: "reversed",
          version: 5,
        }),
      );
    };

    const list = await listPicks({
      accessToken: "token",
      baseUrl: "https://api.test",
      correlationId: "pick-list",
      fetch,
      fulfillmentOrderId: orderId,
    });
    const reversal = await reversePick({
      accessToken: "token",
      baseUrl: "https://api.test",
      command: {
        expected_fulfillment_version: 4,
        reason: "Staging tote was damaged before dispatch.",
        reversal_pick_id: "ebcdef1f-4712-478e-896b-67dc51058c0c",
      },
      correlationId: "pick-reversal",
      fetch,
      idempotencyKey: "stable-reversal",
      pickId,
    });

    expect(list).toEqual(expect.objectContaining({ kind: "ready", total: 1 }));
    expect(requests[1]?.headers.get("Idempotency-Key")).toBe("stable-reversal");
    expect(reversal).toEqual(
      expect.objectContaining({
        kind: "reversed",
        reversal: expect.objectContaining({
          originalPickId: pickId,
          status: "reversed",
        }),
      }),
    );
  });
});
