import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type ConfigureSkuInput = components["schemas"]["ConfigureSkuCommand"];
export type OpeningStockInput = components["schemas"]["OpeningStockCommand"];

export type InventoryDirectoryItem = {
  available: string;
  baseCurrency: string;
  baseStockingUnit: string;
  custody: "available" | "quarantine";
  expirationControl: boolean;
  expirationDate: string | null;
  warehouseInventoryValue: string;
  locationCode: string;
  lotCode: string | null;
  movingAverageUnitCost: string;
  onHand: string;
  reserved: string;
  commercialReserved: string;
  serialNumbers: string[];
  skuCode: string;
  skuId: string;
  skuName: string;
  trackingPolicy: "untracked" | "lot" | "serial";
  warehouseCode: string;
  warehouseId: string;
  warehouseOnHand: string;
  warehouseAvailable: string;
};

export type InventoryDirectoryState =
  | {
      correlationId: string;
      kind: "unauthenticated" | "forbidden" | "validation" | "unavailable";
    }
  | {
      correlationId: string;
      items: InventoryDirectoryItem[];
      kind: "ready";
      total: number;
    };

export type SearchInventoryDirectoryOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
  query: string;
};

export async function searchInventoryDirectory({
  accessToken,
  baseUrl,
  correlationId,
  fetch,
  query,
}: SearchInventoryDirectoryOptions): Promise<InventoryDirectoryState> {
  if (accessToken === undefined || accessToken.length === 0) {
    return { correlationId, kind: "unauthenticated" };
  }
  try {
    const client = createTradeFlowClient({
      accessToken,
      baseUrl,
      correlationId,
      ...(fetch === undefined ? {} : { fetch }),
    });
    const { data, response } = await client.GET("/v1/inventory/availability", {
      params: { query: { query } },
    });
    if (response.status === 401)
      return { correlationId, kind: "unauthenticated" };
    if (response.status === 403) return { correlationId, kind: "forbidden" };
    if (response.status === 422) return { correlationId, kind: "validation" };
    if (response.status >= 500) return { correlationId, kind: "unavailable" };
    if (data === undefined) {
      throw new Error(
        "TradeFlow did not return scoped Inventory Availability.",
      );
    }
    return {
      correlationId,
      items: data.items.map((item) => ({
        available: item.available,
        baseCurrency: item.base_currency,
        baseStockingUnit: item.base_stocking_unit,
        custody: item.custody,
        expirationControl: item.expiration_control,
        expirationDate: item.expiration_date,
        warehouseInventoryValue: item.warehouse_inventory_value,
        locationCode: item.location_code,
        lotCode: item.lot_code,
        movingAverageUnitCost: item.moving_average_unit_cost,
        onHand: item.on_hand,
        reserved: item.reserved,
        commercialReserved: item.commercial_reserved,
        serialNumbers: item.serial_numbers,
        skuCode: item.sku_code,
        skuId: item.sku_id,
        skuName: item.sku_name,
        trackingPolicy: item.tracking_policy,
        warehouseCode: item.warehouse_code,
        warehouseId: item.warehouse_id,
        warehouseOnHand: item.warehouse_on_hand,
        warehouseAvailable: item.warehouse_available,
      })),
      kind: "ready",
      total: data.total,
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId, kind: "unavailable" };
    }
    throw error;
  }
}
