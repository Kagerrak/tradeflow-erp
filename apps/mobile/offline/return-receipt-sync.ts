import { createTradeFlowClient, type components } from "@tradeflow/api-client";

import type {
  LocalReturnEvidence,
  ReturnReceiptStore,
} from "./return-receipt-store";

export type SyncReturnReceiptsOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId: () => string;
  fetch?: (request: Request) => Promise<Response>;
  now?: () => string;
  onSynced?: (requestId: string) => Promise<void>;
  readEvidence?: (localUri: string) => Promise<ArrayBuffer>;
  store: ReturnReceiptStore;
  uploadEvidence?: (evidence: LocalReturnEvidence) => Promise<void>;
};

export type ReturnReceiptSyncResult =
  | { kind: "empty" }
  | {
      kind: "paused";
      reason:
        | "conflict"
        | "forbidden"
        | "unauthenticated"
        | "unavailable"
        | "upload_failed";
    }
  | { count: number; kind: "synced" };

type ErrorEnvelope = {
  error?: { code?: unknown; correlation_id?: unknown; message?: unknown };
};

class SyncHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly correlationId: string,
    message: string,
  ) {
    super(message);
  }
}

function readError(payload: unknown): { code: string; message: string } {
  const envelope = (payload ?? {}) as ErrorEnvelope;
  return {
    code:
      typeof envelope.error?.code === "string"
        ? envelope.error.code
        : "return_receipt_rejected",
    message:
      typeof envelope.error?.message === "string"
        ? envelope.error.message
        : "The server rejected this Return Receipt.",
  };
}

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

export async function syncReturnReceipts(
  options: SyncReturnReceiptsOptions,
): Promise<ReturnReceiptSyncResult> {
  const pending = await options.store.listPending();
  if (pending.length === 0) return { kind: "empty" };
  const now = options.now ?? (() => new Date().toISOString());
  let count = 0;
  for (const item of pending) {
    await options.store.markAttempted(item.sequence, now());
    for (const evidence of item.evidence) {
      if (evidence.status === "uploaded") continue;
      try {
        if (options.uploadEvidence === undefined) {
          await uploadEvidenceToServer(
            options,
            item.requestId,
            evidence,
            item.command.received_at,
          );
        } else {
          await options.uploadEvidence(evidence);
        }
        await options.store.markEvidenceUploaded(
          item.sequence,
          evidence.evidenceId,
          now(),
        );
      } catch (error) {
        if (
          error instanceof SyncHttpError &&
          (error.status === 401 || error.status === 403)
        ) {
          if (error.status === 401) {
            await options.store.markRetryableAuth(
              item.sequence,
              error.correlationId,
              now(),
              error.code,
              error.message,
            );
          } else {
            await options.store.markState(
              item.sequence,
              "forbidden",
              error.correlationId,
              now(),
              error.code,
              error.message,
            );
          }
          return {
            kind: "paused",
            reason: error.status === 401 ? "unauthenticated" : "forbidden",
          };
        }
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
      const response = await (options.fetch ?? fetch)(
        new Request(
          `${options.baseUrl}/v1/return-requests/${item.requestId}/receipts`,
          {
            body: JSON.stringify(item.command),
            headers: {
              Authorization: `Bearer ${options.accessToken ?? ""}`,
              "Content-Type": "application/json",
              "Idempotency-Key": item.idempotencyKey,
              "X-Correlation-ID": correlationId,
            },
            method: "POST",
          },
        ),
      );
      const payload = (await response.json()) as unknown;
      const data = response.ok
        ? (payload as components["schemas"]["ReturnReceiptResponse"])
        : undefined;
      const error = response.ok ? undefined : payload;
      if (data !== undefined) {
        await options.store.markSynced(item.sequence, data, now());
        await options.onSynced?.(item.requestId);
        count += 1;
        continue;
      }
      const reason =
        response.status === 401
          ? "unauthenticated"
          : response.status === 403
            ? "forbidden"
            : response.status === 400 ||
                response.status === 409 ||
                response.status === 422
              ? "conflict"
              : "unavailable";
      if (reason !== "unavailable") {
        const detail = readError(error);
        const responseCorrelation = readCorrelation(
          error,
          response,
          correlationId,
        );
        if (reason === "unauthenticated") {
          await options.store.markRetryableAuth(
            item.sequence,
            responseCorrelation,
            now(),
            detail.code,
            detail.message,
          );
        } else {
          await options.store.markState(
            item.sequence,
            reason,
            responseCorrelation,
            now(),
            detail.code,
            detail.message,
          );
        }
      }
      return { kind: "paused", reason };
    } catch {
      return { kind: "paused", reason: "unavailable" };
    }
  }
  return { count, kind: "synced" };
}

async function uploadEvidenceToServer(
  options: SyncReturnReceiptsOptions,
  requestId: string,
  evidence: LocalReturnEvidence,
  deviceCapturedAt: string,
): Promise<void> {
  const correlationId = options.createCorrelationId();
  const client = createTradeFlowClient({
    accessToken: options.accessToken ?? "",
    baseUrl: options.baseUrl,
    correlationId,
    ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
  });
  const intent = await client.POST(
    "/v1/return-requests/{return_request_id}/evidence/uploads",
    {
      body: {
        content_type: evidence.contentType,
        device_captured_at: deviceCapturedAt,
        evidence_id: evidence.evidenceId,
        kind: evidence.kind,
        sha256: evidence.sha256,
        size_bytes: evidence.sizeBytes,
      },
      params: { path: { return_request_id: requestId } },
    },
  );
  if (intent.data === undefined) {
    const detail = readError(intent.error);
    throw new SyncHttpError(
      intent.response.status,
      detail.code,
      readCorrelation(intent.error, intent.response, correlationId),
      detail.message,
    );
  }
  if (intent.data.status !== "verified") {
    const body = await (
      options.readEvidence ??
      (async (localUri: string) => (await fetch(localUri)).arrayBuffer())
    )(evidence.localUri);
    for (const part of intent.data.parts) {
      const request = new Request(part.upload_url, {
        body: body.slice(part.start_byte, part.end_byte),
        headers: part.upload_headers,
        method: "PUT",
      });
      const uploaded = await (options.fetch ?? fetch)(request);
      if (!uploaded.ok) throw new Error("Evidence upload part failed.");
    }
    const completed = await client.POST(
      "/v1/return-requests/{return_request_id}/evidence/{evidence_id}/complete",
      {
        params: {
          path: {
            evidence_id: evidence.evidenceId,
            return_request_id: requestId,
          },
        },
      },
    );
    if (completed.data === undefined) {
      const detail = readError(completed.error);
      throw new SyncHttpError(
        completed.response.status,
        detail.code,
        readCorrelation(completed.error, completed.response, correlationId),
        detail.message,
      );
    }
    if (completed.data.status !== "verified") {
      throw new Error("Evidence verification failed.");
    }
  }
}
