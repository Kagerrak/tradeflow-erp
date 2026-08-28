export type ReturnEvidencePhoto = {
  contentType: "image/jpeg" | "image/png" | "image/webp";
  evidenceId: string;
  kind: "photo";
  localUri: string;
  sha256: string;
  sizeBytes: number;
  status: "pending_upload" | "uploaded" | "upload_failed";
};

export type ReturnEvidenceNote = {
  evidenceId: string;
  kind: "note";
  noteText: string;
  status: "pending_sync" | "synced" | "sync_failed";
};

export type LocalReturnEvidence = ReturnEvidencePhoto | ReturnEvidenceNote;

export type ReturnEvidenceCapture = {
  evidence: LocalReturnEvidence[];
  idempotencyKey: string;
  requestId: string;
};

export type LocalReturnEvidenceStatus =
  | "conflict"
  | "forbidden"
  | "pending"
  | "synced"
  | "unauthenticated"
  | "upload_failed";

export type LocalReturnEvidenceCapture = ReturnEvidenceCapture & {
  authPaused: boolean;
  captureId: string;
  correlationId: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  expectedRequestVersion: number | null;
  status: LocalReturnEvidenceStatus;
  updatedAt: string;
};

export type ReturnEvidenceOutboxItem = ReturnEvidenceCapture & {
  attemptedAt: string | null;
  captureId: string;
  expectedRequestVersion: number | null;
  sequence: number;
};

export type ReturnEvidenceStore = {
  initialize(): Promise<void>;
  listCaptures(): Promise<LocalReturnEvidenceCapture[]>;
  listPending(): Promise<ReturnEvidenceOutboxItem[]>;
  load(captureId: string): Promise<LocalReturnEvidenceCapture | null>;
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
  markSynced(sequence: number, updatedAt: string): Promise<void>;
  saveAndEnqueue(
    capture: ReturnEvidenceCapture,
    expectedRequestVersion: number | null,
    updatedAt: string,
  ): Promise<LocalReturnEvidenceCapture>;
};

export type MemoryReturnEvidenceBacking = {
  captures: Map<string, LocalReturnEvidenceCapture>;
  nextSequence: number;
  outbox: Map<number, ReturnEvidenceOutboxItem>;
};

export function createMemoryReturnEvidenceBacking(): MemoryReturnEvidenceBacking {
  return { captures: new Map(), nextSequence: 1, outbox: new Map() };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function statusFor(evidence: LocalReturnEvidence[]): LocalReturnEvidenceStatus {
  if (evidence.length === 0) return "pending";
  const photos = evidence.filter(
    (item): item is ReturnEvidencePhoto => item.kind === "photo",
  );
  const notes = evidence.filter(
    (item): item is ReturnEvidenceNote => item.kind === "note",
  );
  const photosReady = photos.every((item) => item.status === "uploaded");
  const notesReady = notes.every(
    (item) => item.status === "synced" || item.status === "pending_sync",
  );
  return photosReady && notesReady ? "pending" : "upload_failed";
}

export function createMemoryReturnEvidenceStore(
  backing = createMemoryReturnEvidenceBacking(),
): ReturnEvidenceStore {
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
    async load(captureId) {
      const value = backing.captures.get(captureId);
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
        value.evidenceId === evidenceId && value.kind === "photo"
          ? { ...value, status: "uploaded" as const }
          : value,
      );
      backing.outbox.set(sequence, { ...item, evidence });
      const current = backing.captures.get(item.captureId);
      if (current !== undefined) {
        backing.captures.set(item.captureId, {
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
      const current = backing.captures.get(item.captureId);
      if (current !== undefined) {
        backing.captures.set(item.captureId, {
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
      const current = backing.captures.get(item.captureId);
      if (current !== undefined) {
        backing.captures.set(item.captureId, {
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
    async markSynced(sequence, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      backing.outbox.delete(sequence);
      const current = backing.captures.get(item.captureId);
      if (current !== undefined) {
        backing.captures.set(item.captureId, {
          ...current,
          authPaused: false,
          correlationId: null,
          errorCode: null,
          errorMessage: null,
          status: "synced",
          updatedAt,
        });
      }
    },
    async saveAndEnqueue(capture, expectedRequestVersion, updatedAt) {
      const captureId = capture.idempotencyKey;
      const existing = backing.captures.get(captureId);
      if (existing !== undefined) return clone(existing);
      const value: LocalReturnEvidenceCapture = {
        ...clone(capture),
        authPaused: false,
        captureId,
        correlationId: null,
        errorCode: null,
        errorMessage: null,
        expectedRequestVersion,
        status: statusFor(capture.evidence),
        updatedAt,
      };
      backing.captures.set(captureId, value);
      const sequence = backing.nextSequence++;
      backing.outbox.set(sequence, {
        ...clone(capture),
        attemptedAt: null,
        captureId,
        expectedRequestVersion,
        sequence,
      });
      return clone(value);
    },
  };
}
