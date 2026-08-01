import { createTradeFlowClient } from "@tradeflow/api-client";

import type {
  DeliveryConfirmationStore,
  LocalDeliveryEvidence,
} from "./delivery-confirmation-store";

export type SyncDeliveryConfirmationsOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId: () => string;
  fetch?: (request: Request) => Promise<Response>;
  now?: () => string;
  store: DeliveryConfirmationStore;
  uploadEvidence: (evidence: LocalDeliveryEvidence) => Promise<void>;
};

export type DeliveryConfirmationSyncResult =
  | { kind: "empty" }
  | {
      kind: "paused";
      reason: "conflict" | "forbidden" | "unavailable" | "upload_failed";
    }
  | { count: number; kind: "synced" };

type ErrorEnvelope = {
  error?: { code?: unknown; correlation_id?: unknown };
};

function readCorrelation(
  payload: unknown,
  response: Response,
  fallback: string,
): string {
  const envelope = (payload ?? {}) as ErrorEnvelope;
  return typeof envelope.error?.correlation_id === "string"
    ? envelope.error.correlation_id
    : (response.headers.get("X-Correlation-ID") ?? fallback);
}

export async function syncDeliveryConfirmations(
  options: SyncDeliveryConfirmationsOptions,
): Promise<DeliveryConfirmationSyncResult> {
  const pending = await options.store.listPending();
  if (pending.length === 0) return { kind: "empty" };
  const now = options.now ?? (() => new Date().toISOString());
  let count = 0;
  for (const item of pending) {
    await options.store.markAttempted(item.sequence, now());
    for (const evidence of item.evidence) {
      if (evidence.status === "uploaded") continue;
      try {
        await options.uploadEvidence(evidence);
        await options.store.markEvidenceUploaded(
          item.sequence,
          evidence.evidenceId,
          now(),
        );
      } catch {
        await options.store.markState(
          item.sequence,
          "upload_failed",
          "",
          now(),
        );
        return { kind: "paused", reason: "upload_failed" };
      }
    }
    const correlationId = options.createCorrelationId();
    try {
      const client = createTradeFlowClient({
        accessToken: options.accessToken ?? "",
        baseUrl: options.baseUrl,
        correlationId,
        ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
      });
      const { data, error, response } = await client.POST(
        "/v1/deliveries/{delivery_id}/confirmations",
        {
          body: item.command,
          headers: { "Idempotency-Key": item.idempotencyKey },
          params: { path: { delivery_id: item.deliveryId } },
        },
      );
      if (data !== undefined) {
        await options.store.markSynced(item.sequence, data, now());
        count += 1;
        continue;
      }
      const reason =
        response.status === 403
          ? "forbidden"
          : response.status === 409
            ? "conflict"
            : "unavailable";
      if (reason !== "unavailable") {
        await options.store.markState(
          item.sequence,
          reason,
          readCorrelation(error, response, correlationId),
          now(),
        );
      }
      return { kind: "paused", reason };
    } catch {
      return { kind: "paused", reason: "unavailable" };
    }
  }
  return { count, kind: "synced" };
}
