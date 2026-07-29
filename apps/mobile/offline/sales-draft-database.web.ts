import type { SalesDraftStore } from "./sales-draft-store";

export async function createSalesDraftStore(
  _databaseName?: string,
): Promise<SalesDraftStore> {
  throw new Error(
    "Durable offline Sales Order capture requires iOS or Android.",
  );
}
