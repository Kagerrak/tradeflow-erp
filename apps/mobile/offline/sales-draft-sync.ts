import {
  createSalesOrderDraft,
  loadSalesOrderDraft,
  updateSalesOrderDraft,
  type SaveDraftState,
} from "@tradeflow/sales-order-draft";

import type { SalesDraftStore } from "./sales-draft-store";

export type SyncSalesDraftsOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId: () => string;
  fetch?: (request: Request) => Promise<Response>;
  now?: () => string;
  store: SalesDraftStore;
};

export type SalesDraftSyncResult =
  | { kind: "empty" }
  | { kind: "paused"; reason: SaveDraftState["kind"] }
  | { count: number; kind: "synced" };

export async function syncSalesDrafts(
  options: SyncSalesDraftsOptions,
): Promise<SalesDraftSyncResult> {
  const now = options.now ?? (() => new Date().toISOString());
  const pending = await options.store.listPending();
  if (pending.length === 0) return { kind: "empty" };
  let count = 0;
  for (const item of pending) {
    await options.store.markAttempted(item.sequence, now());
    const common = {
      accessToken: options.accessToken,
      baseUrl: options.baseUrl,
      command: item.command,
      correlationId: options.createCorrelationId(),
      ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
      idempotencyKey: item.idempotencyKey,
    };
    const result =
      item.expectedVersion === null
        ? await createSalesOrderDraft(common)
        : await updateSalesOrderDraft({
            ...common,
            expectedVersion: item.expectedVersion,
            salesOrderId: item.orderId,
          });
    if (result.kind === "saved") {
      await options.store.markSynced(item.sequence, result.draft, now());
      count += 1;
      continue;
    }
    if (result.kind === "conflict") {
      const authoritative = await loadSalesOrderDraft({
        accessToken: options.accessToken,
        baseUrl: options.baseUrl,
        correlationId: options.createCorrelationId(),
        ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
        salesOrderId: item.orderId,
      });
      await options.store.markConflict(
        item.sequence,
        result.correlationId,
        authoritative.kind === "loaded" ? authoritative.draft : null,
        now(),
      );
    }
    return { kind: "paused", reason: result.kind };
  }
  return { count, kind: "synced" };
}
