import { describe, expect, it } from "vitest";

import { searchInventoryDirectory } from "./index";

describe("inventory directory", () => {
  it("maps the scoped server projection without deriving balances", async () => {
    const state = await searchInventoryDirectory({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "inventory-correlation",
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              items: [
                {
                  available: "30.000000",
                  base_currency: "PHP",
                  base_stocking_unit: "EA",
                  custody: "available",
                  expiration_control: true,
                  expiration_date: "2027-12-31",
                  warehouse_inventory_value: "360.000000",
                  location_code: "AVAILABLE",
                  lot_code: "LOT-A",
                  moving_average_unit_cost: "12.000000",
                  on_hand: "30.000000",
                  reserved: "0.000000",
                  serial_numbers: [],
                  sku_code: "COLA-330",
                  sku_id: "d6a72680-6334-434d-8969-d2fc87da6397",
                  sku_name: "Cola 330 mL",
                  tracking_policy: "lot",
                  warehouse_code: "MNL-01",
                  warehouse_id: "6cadf528-a2ff-4d05-b25c-940c79b112ad",
                },
              ],
              total: 1,
            }),
            {
              headers: { "content-type": "application/json" },
              status: 200,
            },
          ),
        ),
      query: "COLA",
    });

    expect(state).toEqual({
      correlationId: "inventory-correlation",
      items: [
        {
          available: "30.000000",
          baseCurrency: "PHP",
          baseStockingUnit: "EA",
          custody: "available",
          expirationControl: true,
          expirationDate: "2027-12-31",
          warehouseInventoryValue: "360.000000",
          locationCode: "AVAILABLE",
          lotCode: "LOT-A",
          movingAverageUnitCost: "12.000000",
          onHand: "30.000000",
          reserved: "0.000000",
          serialNumbers: [],
          skuCode: "COLA-330",
          skuId: "d6a72680-6334-434d-8969-d2fc87da6397",
          skuName: "Cola 330 mL",
          trackingPolicy: "lot",
          warehouseCode: "MNL-01",
          warehouseId: "6cadf528-a2ff-4d05-b25c-940c79b112ad",
        },
      ],
      kind: "ready",
      total: 1,
    });
  });

  it.each([
    [401, "unauthenticated"],
    [403, "forbidden"],
    [422, "validation"],
    [503, "unavailable"],
  ] as const)("maps HTTP %s to %s", async (status, kind) => {
    const state = await searchInventoryDirectory({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "inventory-error",
      fetch: () =>
        Promise.resolve(
          new Response("{}", {
            headers: { "content-type": "application/json" },
            status,
          }),
        ),
      query: "",
    });
    expect(state).toEqual({ correlationId: "inventory-error", kind });
  });

  it("does not call the API without a token", async () => {
    const state = await searchInventoryDirectory({
      accessToken: undefined,
      baseUrl: "https://api.tradeflow.test",
      correlationId: "inventory-auth",
      query: "",
    });
    expect(state).toEqual({
      correlationId: "inventory-auth",
      kind: "unauthenticated",
    });
  });
});
