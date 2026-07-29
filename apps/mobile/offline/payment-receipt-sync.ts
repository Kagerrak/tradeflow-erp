import {
  recordPaymentReceipt,
  type PaymentReceiptCommandState,
} from "@tradeflow/payment-clearance";

import type { PaymentReceiptStore } from "./payment-receipt-store";

type PaymentReceiptCommandSuccess = Extract<
  PaymentReceiptCommandState,
  { receipt: unknown }
>;

export type SyncPaymentReceiptsOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId: () => string;
  fetch?: (request: Request) => Promise<Response>;
  now?: () => string;
  store: PaymentReceiptStore;
};

export type PaymentReceiptSyncResult =
  | { kind: "empty" }
  | { kind: "paused"; state: PaymentReceiptCommandState }
  | {
      count: number;
      kind: "synced";
      state: PaymentReceiptCommandSuccess;
    };

export async function syncPaymentReceipts(
  options: SyncPaymentReceiptsOptions,
): Promise<PaymentReceiptSyncResult> {
  const now = options.now ?? (() => new Date().toISOString());
  const pending = await options.store.listPending();
  if (pending.length === 0) return { kind: "empty" };
  let count = 0;
  let latest: PaymentReceiptCommandSuccess | null = null;
  for (const item of pending) {
    await options.store.markAttempted(item.sequence, now());
    const state = await recordPaymentReceipt({
      accessToken: options.accessToken,
      baseUrl: options.baseUrl,
      command: item.command,
      correlationId: options.createCorrelationId(),
      ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
      idempotencyKey: item.idempotencyKey,
    });
    if (state.kind === "recorded") {
      await options.store.markSynced(item.sequence, state.receipt, now());
      latest = state;
      count += 1;
      continue;
    }
    await options.store.markPaused(item.sequence, state.correlationId, now());
    return { kind: "paused", state };
  }
  if (latest === null) return { kind: "empty" };
  return { count, kind: "synced", state: latest };
}
