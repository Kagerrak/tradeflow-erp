import { createTradeFlowClient } from "@tradeflow/api-client";

import type {
  LocalReturnEvidence,
  ReturnEvidenceCapture,
  ReturnEvidencePhoto,
  ReturnEvidenceStore,
} from "./return-evidence-store";

export type SyncReturnEvidenceOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId: () => string;
  fetch?: (request: Request) => Promise<Response>;
  now?: () => string;
  onSynced?: (requestId: string) => Promise<void>;
  readEvidence?: (localUri: string) => Promise<ArrayBuffer>;
  store: ReturnEvidenceStore;
  uploadEvidence?: (evidence: ReturnEvidencePhoto) => Promise<void>;
};

export type ReturnEvidenceSyncResult =
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
        : "return_evidence_rejected",
    message:
      typeof envelope.error?.message === "string"
        ? envelope.error.message
        : "The server rejected this Return evidence.",
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

export async function syncReturnEvidence(
  options: SyncReturnEvidenceOptions,
): Promise<ReturnEvidenceSyncResult> {
  const pending = await options.store.listPending();
  if (pending.length === 0) return { kind: "empty" };
  const now = options.now ?? (() => new Date().toISOString());
  let count = 0;
  for (const item of pending) {
    await options.store.markAttempted(item.sequence, now());
    for (const evidence of item.evidence) {
      if (evidence.kind === "photo" && evidence.status !== "uploaded") {
        try {
          if (options.uploadEvidence === undefined) {
            await uploadPhotoToServer(options, item, evidence);
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
    }
    const notes = item.evidence.filter(
      (evidence): evidence is LocalReturnEvidence & { kind: "note" } =>
        evidence.kind === "note",
    );
    if (notes.length > 0) {
      const correlationId = options.createCorrelationId();
      try {
        const client = createTradeFlowClient({
          accessToken: options.accessToken ?? "",
          baseUrl: options.baseUrl,
          correlationId,
          ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
        });
        const response = await client.POST(
          "/v1/return-requests/{return_request_id}/offline-evidence",
          {
            body: {
              correlation_id: correlationId,
              expected_request_version: item.expectedRequestVersion ?? 1,
              evidence: notes.map((note) => ({
                evidence_id: note.evidenceId,
                kind: "note" as const,
                note_text: note.noteText,
              })),
            },
            params: { path: { return_request_id: item.requestId } },
          },
        );
        if (response.data !== undefined) {
          if (response.data.status === "acknowledged") {
            await options.store.markSynced(item.sequence, now());
            await options.onSynced?.(item.requestId);
            count += 1;
            continue;
          }
          await options.store.markState(
            item.sequence,
            "conflict",
            correlationId,
            now(),
            "return_request_version_conflict",
            response.data.conflict_reason ??
              "Return Request changed while offline.",
          );
          return { kind: "paused", reason: "conflict" };
        }
        const detail = readError(response.error);
        const reason =
          response.response.status === 401
            ? "unauthenticated"
            : response.response.status === 403
              ? "forbidden"
              : [400, 409, 422].includes(response.response.status)
                ? "conflict"
                : "unavailable";
        if (reason !== "unavailable") {
          const responseCorrelation = readCorrelation(
            response.error,
            response.response,
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
    } else {
      await options.store.markSynced(item.sequence, now());
      await options.onSynced?.(item.requestId);
      count += 1;
    }
  }
  return { count, kind: "synced" };
}

async function uploadPhotoToServer(
  options: SyncReturnEvidenceOptions,
  item: ReturnEvidenceCapture,
  evidence: ReturnEvidencePhoto,
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
        device_captured_at: new Date().toISOString(),
        evidence_id: evidence.evidenceId,
        kind: "photo",
        sha256: evidence.sha256,
        size_bytes: evidence.sizeBytes,
      },
      params: { path: { return_request_id: item.requestId } },
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
            return_request_id: item.requestId,
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
