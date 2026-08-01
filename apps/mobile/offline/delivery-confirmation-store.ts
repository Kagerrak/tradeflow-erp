import type { components } from "@tradeflow/api-client";

export type DeliveryConfirmationCommand =
  components["schemas"]["ConfirmDeliveryCommand"];
export type DeliveryConfirmationResponse =
  components["schemas"]["DeliveryConfirmationResponse"];

export type LocalDeliveryEvidence = {
  contentType: "image/jpeg" | "image/png" | "image/webp";
  evidenceId: string;
  kind: "photo" | "signature";
  localUri: string;
  sha256: string;
  sizeBytes: number;
  status: "pending_upload" | "uploaded" | "upload_failed";
};

export type DeliveryConfirmationCapture = {
  command: DeliveryConfirmationCommand;
  deliveryId: string;
  evidence: LocalDeliveryEvidence[];
  idempotencyKey: string;
};

export type LocalDeliveryConfirmationStatus =
  | "confirmed"
  | "conflict"
  | "forbidden"
  | "pending_confirmation"
  | "pending_upload"
  | "upload_failed";

export type LocalDeliveryConfirmation = DeliveryConfirmationCapture & {
  confirmationId: string;
  correlationId: string | null;
  response: DeliveryConfirmationResponse | null;
  status: LocalDeliveryConfirmationStatus;
  updatedAt: string;
};

export type DeliveryConfirmationOutboxItem = DeliveryConfirmationCapture & {
  attemptedAt: string | null;
  confirmationId: string;
  sequence: number;
};

export type DeliveryConfirmationStore = {
  initialize(): Promise<void>;
  listCaptures(): Promise<LocalDeliveryConfirmation[]>;
  listPending(): Promise<DeliveryConfirmationOutboxItem[]>;
  load(confirmationId: string): Promise<LocalDeliveryConfirmation | null>;
  markAttempted(sequence: number, attemptedAt: string): Promise<void>;
  markEvidenceUploaded(
    sequence: number,
    evidenceId: string,
    updatedAt: string,
  ): Promise<void>;
  markState(
    sequence: number,
    status: "conflict" | "forbidden" | "upload_failed",
    correlationId: string,
    updatedAt: string,
  ): Promise<void>;
  markSynced(
    sequence: number,
    response: DeliveryConfirmationResponse,
    updatedAt: string,
  ): Promise<void>;
  saveAndEnqueue(
    capture: DeliveryConfirmationCapture,
    updatedAt: string,
  ): Promise<LocalDeliveryConfirmation>;
};

export type MemoryDeliveryConfirmationBacking = {
  captures: Map<string, LocalDeliveryConfirmation>;
  nextSequence: number;
  outbox: Map<number, DeliveryConfirmationOutboxItem>;
};

export function createMemoryDeliveryConfirmationBacking(): MemoryDeliveryConfirmationBacking {
  return { captures: new Map(), nextSequence: 1, outbox: new Map() };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function statusFor(
  evidence: LocalDeliveryEvidence[],
): LocalDeliveryConfirmationStatus {
  return evidence.every((item) => item.status === "uploaded")
    ? "pending_confirmation"
    : "pending_upload";
}

export function createMemoryDeliveryConfirmationStore(
  backing = createMemoryDeliveryConfirmationBacking(),
): DeliveryConfirmationStore {
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
    async load(confirmationId) {
      const value = backing.captures.get(confirmationId);
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
      const current = backing.captures.get(item.confirmationId);
      if (current !== undefined) {
        backing.captures.set(item.confirmationId, {
          ...current,
          evidence: clone(evidence),
          status: statusFor(evidence),
          updatedAt,
        });
      }
    },
    async markState(sequence, status, correlationId, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      if (status !== "upload_failed") backing.outbox.delete(sequence);
      const current = backing.captures.get(item.confirmationId);
      if (current !== undefined) {
        backing.captures.set(item.confirmationId, {
          ...current,
          correlationId,
          status,
          updatedAt,
        });
      }
    },
    async markSynced(sequence, response, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      backing.outbox.delete(sequence);
      const current = backing.captures.get(item.confirmationId);
      if (current !== undefined) {
        backing.captures.set(item.confirmationId, {
          ...current,
          correlationId: null,
          response: clone(response),
          status: "confirmed",
          updatedAt,
        });
      }
    },
    async saveAndEnqueue(capture, updatedAt) {
      const confirmationId = capture.command.confirmation_id;
      const existing = backing.captures.get(confirmationId);
      if (existing !== undefined) return clone(existing);
      const value: LocalDeliveryConfirmation = {
        ...clone(capture),
        confirmationId,
        correlationId: null,
        response: null,
        status: statusFor(capture.evidence),
        updatedAt,
      };
      backing.captures.set(confirmationId, value);
      const sequence = backing.nextSequence++;
      backing.outbox.set(sequence, {
        ...clone(capture),
        attemptedAt: null,
        confirmationId,
        sequence,
      });
      return clone(value);
    },
  };
}
