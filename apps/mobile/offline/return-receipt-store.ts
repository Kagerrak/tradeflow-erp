import type { components } from "@tradeflow/api-client";

export type ReturnReceiptCommand = components["schemas"]["CreateReturnReceipt"];
export type ReturnReceiptResponse =
  components["schemas"]["ReturnReceiptResponse"];

export type LocalReturnEvidence = {
  contentType: "image/jpeg" | "image/png" | "image/webp";
  evidenceId: string;
  kind: "photo";
  localUri: string;
  sha256: string;
  sizeBytes: number;
  status: "pending_upload" | "uploaded" | "upload_failed";
};

export type ReturnReceiptCapture = {
  command: ReturnReceiptCommand;
  evidence: LocalReturnEvidence[];
  idempotencyKey: string;
  requestId: string;
};

export type LocalReturnReceiptStatus =
  | "confirmed"
  | "conflict"
  | "forbidden"
  | "pending_confirmation"
  | "pending_upload"
  | "upload_failed";

export type LocalReturnReceipt = ReturnReceiptCapture & {
  authPaused: boolean;
  correlationId: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  receiptId: string;
  replacedByReceiptId: string | null;
  replacesReceiptId: string | null;
  response: ReturnReceiptResponse | null;
  status: LocalReturnReceiptStatus;
  updatedAt: string;
};

export type ReturnReceiptOutboxItem = ReturnReceiptCapture & {
  attemptedAt: string | null;
  receiptId: string;
  sequence: number;
};

export type ReturnReceiptStore = {
  initialize(): Promise<void>;
  listCaptures(): Promise<LocalReturnReceipt[]>;
  listPending(): Promise<ReturnReceiptOutboxItem[]>;
  load(receiptId: string): Promise<LocalReturnReceipt | null>;
  markAttempted(sequence: number, attemptedAt: string): Promise<void>;
  markEvidenceUploaded(
    sequence: number,
    evidenceId: string,
    updatedAt: string,
  ): Promise<void>;
  markRetryableAuth(
    sequence: number,
    correlationId: string,
    updatedAt: string,
    errorCode: string,
    errorMessage: string,
  ): Promise<void>;
  markState(
    sequence: number,
    status: "conflict" | "forbidden" | "upload_failed",
    correlationId: string,
    updatedAt: string,
    errorCode?: string,
    errorMessage?: string,
  ): Promise<void>;
  markSynced(
    sequence: number,
    response: ReturnReceiptResponse,
    updatedAt: string,
  ): Promise<void>;
  saveAndEnqueue(
    capture: ReturnReceiptCapture,
    updatedAt: string,
  ): Promise<LocalReturnReceipt>;
  replaceConflict(
    receiptId: string,
    capture: ReturnReceiptCapture,
    updatedAt: string,
  ): Promise<LocalReturnReceipt>;
};

export type MemoryReturnReceiptBacking = {
  captures: Map<string, LocalReturnReceipt>;
  nextSequence: number;
  outbox: Map<number, ReturnReceiptOutboxItem>;
};

export function createMemoryReturnReceiptBacking(): MemoryReturnReceiptBacking {
  return { captures: new Map(), nextSequence: 1, outbox: new Map() };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function statusFor(evidence: LocalReturnEvidence[]): LocalReturnReceiptStatus {
  return evidence.every((item) => item.status === "uploaded")
    ? "pending_confirmation"
    : "pending_upload";
}

export function createMemoryReturnReceiptStore(
  backing = createMemoryReturnReceiptBacking(),
): ReturnReceiptStore {
  return {
    async initialize() {},
    async listCaptures() {
      return [...backing.captures.values()]
        .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
        .map(clone);
    },
    async listPending() {
      return [...backing.outbox.values()]
        .sort((left, right) => left.sequence - right.sequence)
        .map(clone);
    },
    async load(receiptId) {
      const value = backing.captures.get(receiptId);
      return value === undefined ? null : clone(value);
    },
    async markAttempted(sequence, attemptedAt) {
      const item = backing.outbox.get(sequence);
      if (item !== undefined && item.attemptedAt === null) {
        backing.outbox.set(sequence, { ...item, attemptedAt });
      }
    },
    async markEvidenceUploaded(sequence, evidenceId, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      const evidence = item.evidence.map((value) =>
        value.evidenceId === evidenceId
          ? { ...value, status: "uploaded" as const }
          : value,
      );
      backing.outbox.set(sequence, { ...item, evidence });
      const current = backing.captures.get(item.receiptId);
      if (current !== undefined) {
        backing.captures.set(item.receiptId, {
          ...current,
          authPaused: false,
          correlationId: null,
          evidence: clone(evidence),
          errorCode: null,
          errorMessage: null,
          status: statusFor(evidence),
          updatedAt,
        });
      }
    },
    async markRetryableAuth(
      sequence,
      correlationId,
      updatedAt,
      errorCode,
      errorMessage,
    ) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      const current = backing.captures.get(item.receiptId);
      if (current !== undefined) {
        backing.captures.set(item.receiptId, {
          ...current,
          authPaused: true,
          correlationId,
          errorCode,
          errorMessage,
          updatedAt,
        });
      }
    },
    async markState(
      sequence,
      status,
      correlationId,
      updatedAt,
      errorCode = "",
      errorMessage = "",
    ) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      if (status !== "upload_failed") backing.outbox.delete(sequence);
      const current = backing.captures.get(item.receiptId);
      if (current !== undefined) {
        backing.captures.set(item.receiptId, {
          ...current,
          authPaused: false,
          correlationId,
          errorCode: errorCode || null,
          errorMessage: errorMessage || null,
          status,
          updatedAt,
        });
      }
    },
    async markSynced(sequence, response, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      backing.outbox.delete(sequence);
      const current = backing.captures.get(item.receiptId);
      if (current !== undefined) {
        backing.captures.set(item.receiptId, {
          ...current,
          authPaused: false,
          correlationId: null,
          errorCode: null,
          errorMessage: null,
          response: clone(response),
          status: "confirmed",
          updatedAt,
        });
      }
    },
    async saveAndEnqueue(capture, updatedAt) {
      const receiptId = capture.command.return_receipt_id;
      const existing = backing.captures.get(receiptId);
      if (existing !== undefined) return clone(existing);
      const value: LocalReturnReceipt = {
        ...clone(capture),
        authPaused: false,
        correlationId: null,
        errorCode: null,
        errorMessage: null,
        receiptId,
        replacedByReceiptId: null,
        replacesReceiptId: null,
        response: null,
        status: statusFor(capture.evidence),
        updatedAt,
      };
      backing.captures.set(receiptId, value);
      const sequence = backing.nextSequence++;
      backing.outbox.set(sequence, {
        ...clone(capture),
        attemptedAt: null,
        receiptId,
        sequence,
      });
      return clone(value);
    },
    async replaceConflict(receiptId, capture, updatedAt) {
      const current = backing.captures.get(receiptId);
      if (current?.status !== "conflict") {
        throw new Error("Only a reviewed conflict can be replaced.");
      }
      if (capture.command.return_receipt_id === receiptId) {
        throw new Error("A replacement requires a new receipt identity.");
      }
      if (capture.requestId !== current.requestId) {
        throw new Error(
          "A replacement must belong to the same Return Request.",
        );
      }
      if (current.replacedByReceiptId !== null) {
        throw new Error("This conflict already has a replacement.");
      }
      const replacement = await this.saveAndEnqueue(capture, updatedAt);
      backing.captures.set(receiptId, {
        ...current,
        replacedByReceiptId: replacement.receiptId,
        updatedAt,
      });
      const saved = backing.captures.get(replacement.receiptId);
      if (saved !== undefined) {
        backing.captures.set(replacement.receiptId, {
          ...saved,
          replacesReceiptId: receiptId,
        });
      }
      return clone(backing.captures.get(replacement.receiptId)!);
    },
  };
}
