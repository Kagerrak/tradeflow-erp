import { openDatabaseAsync, type SQLiteDatabase } from "expo-sqlite";

import type {
  LocalReturnEvidence,
  LocalReturnEvidenceCapture,
  ReturnEvidenceOutboxItem,
  ReturnEvidencePhoto,
  ReturnEvidenceStore,
} from "./return-evidence-store";

type CaptureRow = {
  auth_paused: number;
  capture_id: string;
  evidence_json: string;
  error_code: string | null;
  error_message: string | null;
  expected_request_version: number | null;
  idempotency_key: string;
  request_id: string;
  status: LocalReturnEvidenceCapture["status"];
  correlation_id: string | null;
  updated_at: string;
};

type OutboxRow = {
  attempted_at: string | null;
  capture_id: string;
  evidence_json: string;
  expected_request_version: number | null;
  idempotency_key: string;
  request_id: string;
  sequence: number;
};

const schema = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS return_evidence_captures (
  capture_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (
    status IN (
      'pending', 'synced', 'conflict', 'forbidden',
      'unauthenticated', 'upload_failed'
    )
  ),
  correlation_id TEXT,
  error_code TEXT,
  error_message TEXT,
  auth_paused INTEGER NOT NULL DEFAULT 0 CHECK (auth_paused IN (0, 1)),
  expected_request_version INTEGER,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS return_evidence_outbox (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  capture_id TEXT NOT NULL
    REFERENCES return_evidence_captures(capture_id),
  request_id TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  expected_request_version INTEGER,
  attempted_at TEXT
);
`;

function mapCapture(row: CaptureRow): LocalReturnEvidenceCapture {
  return {
    authPaused: row.auth_paused === 1,
    captureId: row.capture_id,
    correlationId: row.correlation_id,
    errorCode: row.error_code,
    errorMessage: row.error_message,
    evidence: JSON.parse(row.evidence_json) as LocalReturnEvidence[],
    expectedRequestVersion: row.expected_request_version,
    idempotencyKey: row.idempotency_key,
    requestId: row.request_id,
    status: row.status,
    updatedAt: row.updated_at,
  };
}

function mapOutbox(row: OutboxRow): ReturnEvidenceOutboxItem {
  return {
    attemptedAt: row.attempted_at,
    captureId: row.capture_id,
    evidence: JSON.parse(row.evidence_json) as LocalReturnEvidence[],
    expectedRequestVersion: row.expected_request_version,
    idempotencyKey: row.idempotency_key,
    requestId: row.request_id,
    sequence: row.sequence,
  };
}

function localStatus(evidence: LocalReturnEvidence[]) {
  if (evidence.length === 0) return "pending";
  const photos = evidence.filter(
    (item): item is ReturnEvidencePhoto => item.kind === "photo",
  );
  const notes = evidence.filter((item) => item.kind === "note");
  const photosReady = photos.every((item) => item.status === "uploaded");
  const notesReady = notes.every(
    (item) => item.status === "synced" || item.status === "pending_sync",
  );
  return photosReady && notesReady ? "pending" : "upload_failed";
}

export async function createReturnEvidenceStore(
  databaseName = "tradeflow-return-evidence.db",
): Promise<ReturnEvidenceStore> {
  const database = await openDatabaseAsync(databaseName);
  return createSqliteReturnEvidenceStore(database);
}

export function createSqliteReturnEvidenceStore(
  database: SQLiteDatabase,
): ReturnEvidenceStore {
  return {
    async initialize() {
      await database.execAsync(schema);
      const columns = await database.getAllAsync<{ name: string }>(
        "PRAGMA table_info(return_evidence_captures)",
      );
      const names = new Set(columns.map((column) => column.name));
      if (!names.has("error_code")) {
        await database.execAsync(
          "ALTER TABLE return_evidence_captures ADD COLUMN error_code TEXT",
        );
      }
      if (!names.has("error_message")) {
        await database.execAsync(
          "ALTER TABLE return_evidence_captures ADD COLUMN error_message TEXT",
        );
      }
      if (!names.has("auth_paused")) {
        await database.execAsync(
          "ALTER TABLE return_evidence_captures ADD COLUMN auth_paused INTEGER NOT NULL DEFAULT 0 CHECK (auth_paused IN (0, 1))",
        );
      }
      if (!names.has("expected_request_version")) {
        await database.execAsync(
          "ALTER TABLE return_evidence_captures ADD COLUMN expected_request_version INTEGER",
        );
      }
    },
    async listCaptures() {
      const rows = await database.getAllAsync<CaptureRow>(
        "SELECT * FROM return_evidence_captures ORDER BY updated_at DESC",
      );
      return rows.map(mapCapture);
    },
    async listPending() {
      const rows = await database.getAllAsync<OutboxRow>(
        "SELECT * FROM return_evidence_outbox ORDER BY sequence",
      );
      return rows.map(mapOutbox);
    },
    async load(captureId) {
      const row = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM return_evidence_captures WHERE capture_id = ?",
        captureId,
      );
      return row === null ? null : mapCapture(row);
    },
    async markAttempted(sequence, attemptedAt) {
      await database.runAsync(
        `UPDATE return_evidence_outbox
            SET attempted_at = COALESCE(attempted_at, ?)
          WHERE sequence = ?`,
        attemptedAt,
        sequence,
      );
    },
    async markEvidenceUploaded(sequence, evidenceId, updatedAt) {
      const row = await database.getFirstAsync<OutboxRow>(
        "SELECT * FROM return_evidence_outbox WHERE sequence = ?",
        sequence,
      );
      if (row === null) return;
      const evidence = (
        JSON.parse(row.evidence_json) as LocalReturnEvidence[]
      ).map((item) =>
        item.evidenceId === evidenceId && item.kind === "photo"
          ? { ...item, status: "uploaded" as const }
          : item,
      );
      const evidenceJson = JSON.stringify(evidence);
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE return_evidence_outbox
              SET evidence_json = ? WHERE sequence = ?`,
          evidenceJson,
          sequence,
        );
        await transaction.runAsync(
          `UPDATE return_evidence_captures
              SET evidence_json = ?, status = ?, auth_paused = 0,
                  correlation_id = NULL, error_code = NULL,
                  error_message = NULL, updated_at = ?
            WHERE capture_id = ?`,
          evidenceJson,
          localStatus(evidence),
          updatedAt,
          row.capture_id,
        );
      });
    },
    async markRetryableAuth(
      sequence,
      correlationId,
      updatedAt,
      errorCode,
      errorMessage,
    ) {
      await database.runAsync(
        `UPDATE return_evidence_captures
            SET auth_paused = 1, correlation_id = ?, error_code = ?, error_message = ?,
                updated_at = ?
          WHERE capture_id = (
            SELECT capture_id FROM return_evidence_outbox
            WHERE sequence = ?
          )`,
        correlationId,
        errorCode,
        errorMessage,
        updatedAt,
        sequence,
      );
    },
    async markState(
      sequence,
      status,
      correlationId,
      updatedAt,
      errorCode = "",
      errorMessage = "",
    ) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE return_evidence_captures
              SET status = ?, auth_paused = 0, correlation_id = ?, error_code = ?,
                  error_message = ?, updated_at = ?
            WHERE capture_id = (
              SELECT capture_id FROM return_evidence_outbox
              WHERE sequence = ?
            )`,
          status,
          correlationId,
          errorCode || null,
          errorMessage || null,
          updatedAt,
          sequence,
        );
        if (status !== "upload_failed") {
          await transaction.runAsync(
            "DELETE FROM return_evidence_outbox WHERE sequence = ?",
            sequence,
          );
        }
      });
    },
    async markSynced(sequence, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE return_evidence_captures
              SET status = 'synced', auth_paused = 0, correlation_id = NULL,
                  error_code = NULL, error_message = NULL, updated_at = ?
            WHERE capture_id = (
              SELECT capture_id FROM return_evidence_outbox
              WHERE sequence = ?
            )`,
          updatedAt,
          sequence,
        );
        await transaction.runAsync(
          "DELETE FROM return_evidence_outbox WHERE sequence = ?",
          sequence,
        );
      });
    },
    async saveAndEnqueue(capture, expectedRequestVersion, updatedAt) {
      const captureId = capture.idempotencyKey;
      const existing = await database.getFirstAsync<{ capture_id: string }>(
        "SELECT capture_id FROM return_evidence_captures WHERE capture_id = ?",
        captureId,
      );
      if (existing !== null) {
        const loaded = await this.load(captureId);
        return loaded!;
      }
      const evidenceJson = JSON.stringify(capture.evidence);
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `INSERT INTO return_evidence_captures (
            capture_id, request_id, evidence_json, idempotency_key, status,
            auth_paused, expected_request_version, updated_at
          ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)`,
          captureId,
          capture.requestId,
          evidenceJson,
          capture.idempotencyKey,
          localStatus(capture.evidence),
          expectedRequestVersion,
          updatedAt,
        );
        await transaction.runAsync(
          `INSERT INTO return_evidence_outbox (
            capture_id, request_id, evidence_json, idempotency_key,
            expected_request_version, attempted_at
          ) VALUES (?, ?, ?, ?, ?, NULL)`,
          captureId,
          capture.requestId,
          evidenceJson,
          capture.idempotencyKey,
          expectedRequestVersion,
        );
      });
      const loaded = await this.load(captureId);
      return loaded!;
    },
  };
}
