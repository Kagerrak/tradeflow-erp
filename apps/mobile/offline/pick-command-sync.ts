import { createTradeFlowClient } from "@tradeflow/api-client";

import type {
  LocalPickStatus,
  PickCommandStore,
  PickResponse,
} from "./pick-command-store";

export type SyncPickCommandsOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId: () => string;
  fetch?: (request: Request) => Promise<Response>;
  now?: () => string;
  store: PickCommandStore;
};

export type PickPauseReason = Extract<
  LocalPickStatus,
  "conflict" | "forbidden" | "reversed" | "scan_denied"
>;

export type PickCommandSyncResult =
  | { kind: "empty" }
  | { kind: "paused"; reason: PickPauseReason | "unavailable" }
  | { count: number; kind: "synced"; response: PickResponse };

export type ReversePickResult =
  | { correlationId: string; kind: "reversed" }
  | { kind: "paused"; reason: PickPauseReason | "unavailable" };

type ErrorEnvelope = {
  error?: {
    code?: unknown;
    correlation_id?: unknown;
  };
};

const scanDeniedCodes = new Set([
  "barcode_mapping_ambiguous",
  "barcode_mapping_not_found",
  "duplicate_serial_selection",
  "expired_stock_not_pickable",
  "fefo_override_required",
  "identity_assignment_incomplete",
  "lot_selection_quantity_mismatch",
  "serial_already_picked",
  "serial_sku_mismatch",
  "tracked_stock_insufficient",
]);

function classifyError(
  status: number,
  code: string,
): PickPauseReason | "unavailable" {
  if (code === "pick_reversed" || code === "pick_already_reversed") {
    return "reversed";
  }
  if (scanDeniedCodes.has(code) || status === 422) return "scan_denied";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  return "unavailable";
}

function readError(
  payload: unknown,
  response: Response,
  fallbackCorrelationId: string,
): { code: string; correlationId: string } {
  const body = (payload ?? {}) as ErrorEnvelope;
  return {
    code: typeof body.error?.code === "string" ? body.error.code : "",
    correlationId:
      typeof body.error?.correlation_id === "string"
        ? body.error.correlation_id
        : (response.headers.get("X-Correlation-ID") ?? fallbackCorrelationId),
  };
}

export async function syncPickCommands(
  options: SyncPickCommandsOptions,
): Promise<PickCommandSyncResult> {
  const now = options.now ?? (() => new Date().toISOString());
  const pending = await options.store.listPending();
  if (pending.length === 0) return { kind: "empty" };
  let count = 0;
  let latest: PickResponse | null = null;

  for (const item of pending) {
    const correlationId = options.createCorrelationId();
    await options.store.markAttempted(item.sequence, now());
    try {
      const client = createTradeFlowClient({
        accessToken: options.accessToken ?? "",
        baseUrl: options.baseUrl,
        correlationId,
        ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
      });
      const { data, error, response } = await client.POST(
        "/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        {
          body: item.command,
          headers: { "Idempotency-Key": item.idempotencyKey },
          params: {
            path: { fulfillment_order_id: item.fulfillmentOrderId },
          },
        },
      );
      if (data !== undefined) {
        await options.store.markSynced(item.sequence, data, now());
        count += 1;
        latest = data;
        continue;
      }
      const failure = readError(error, response, correlationId);
      const reason = classifyError(response.status, failure.code);
      if (reason !== "unavailable") {
        await options.store.markState(
          item.sequence,
          reason,
          failure.correlationId,
          now(),
        );
      }
      return { kind: "paused", reason };
    } catch {
      return { kind: "paused", reason: "unavailable" };
    }
  }
  return latest === null
    ? { kind: "empty" }
    : { count, kind: "synced", response: latest };
}

export async function reverseSyncedPick(
  options: SyncPickCommandsOptions & {
    expectedVersion: number;
    idempotencyKey: string;
    pickId: string;
    reason: string;
    reversalPickId: string;
  },
): Promise<ReversePickResult> {
  const correlationId = options.createCorrelationId();
  try {
    const client = createTradeFlowClient({
      accessToken: options.accessToken ?? "",
      baseUrl: options.baseUrl,
      correlationId,
      ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
    });
    const { data, error, response } = await client.POST(
      "/v1/fulfillment/picks/{pick_id}/reversal",
      {
        body: {
          expected_fulfillment_version: options.expectedVersion,
          reason: options.reason,
          reversal_pick_id: options.reversalPickId,
        },
        headers: { "Idempotency-Key": options.idempotencyKey },
        params: { path: { pick_id: options.pickId } },
      },
    );
    if (data !== undefined) {
      const acknowledgedCorrelationId =
        response.headers.get("X-Correlation-ID") ?? correlationId;
      await options.store.markReversed(
        options.pickId,
        acknowledgedCorrelationId,
        (options.now ?? (() => new Date().toISOString()))(),
      );
      return {
        correlationId: acknowledgedCorrelationId,
        kind: "reversed",
      };
    }
    const failure = readError(error, response, correlationId);
    return {
      kind: "paused",
      reason: classifyError(response.status, failure.code),
    };
  } catch {
    return { kind: "paused", reason: "unavailable" };
  }
}
