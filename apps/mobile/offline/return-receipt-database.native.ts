import { openDatabaseAsync, type SQLiteDatabase } from "expo-sqlite";

import type {
  LocalReturnEvidence,
  LocalReturnReceipt,
  ReturnReceiptCapture,
  ReturnReceiptOutboxItem,
  ReturnReceiptStore,
} from "./return-receipt-store";

type CaptureRow = {
  auth_paused: number;
  command_json: string;
  correlation_id: string | null;
  error_code: string | null;
  error_message: string | null;
  evidence_json: string;
  idempotency_key: string;
  receipt_id: string;
  request_id: string;
  replaced_by_receipt_id: string | null;
  replaces_receipt_id: string | null;
  response_json: string | null;
  status: LocalReturnReceipt["status"];
  updated_at: string;
};

type OutboxRow = {
  attempted_at: string | null;
  command_json: string;
  evidence_json: string;
  idempotency_key: string;
  receipt_id: string;
  request_id: string;
  sequence: number;
};

const schema = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS return_receipt_captures (
  receipt_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  command_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (
    status IN (
      'pending_upload', 'upload_failed', 'pending_confirmation',
      'confirmed', 'conflict', 'forbidden'
    )
  ),
  correlation_id TEXT,
  error_code TEXT,
  error_message TEXT,
  auth_paused INTEGER NOT NULL DEFAULT 0 CHECK (auth_paused IN (0, 1)),
  response_json TEXT,
  replaces_receipt_id TEXT REFERENCES return_receipt_captures(receipt_id),
  replaced_by_receipt_id TEXT REFERENCES return_receipt_captures(receipt_id),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS return_receipt_outbox (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id TEXT NOT NULL
    REFERENCES return_receipt_captures(receipt_id),
  request_id TEXT NOT NULL,
  command_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  attempted_at TEXT
);
`;

function mapCapture(row: CaptureRow): LocalReturnReceipt {
  return {
    authPaused: row.auth_paused === 1,
    command: JSON.parse(row.command_json) as LocalReturnReceipt["command"],
    correlationId: row.correlation_id,
    errorCode: row.error_code,
    errorMessage: row.error_message,
    evidence: JSON.parse(row.evidence_json) as LocalReturnEvidence[],
    idempotencyKey: row.idempotency_key,
    receiptId: row.receipt_id,
    requestId: row.request_id,
    replacedByReceiptId: row.replaced_by_receipt_id,
    replacesReceiptId: row.replaces_receipt_id,
    response:
      row.response_json === null
        ? null
        : (JSON.parse(row.response_json) as LocalReturnReceipt["response"]),
    status: row.status,
    updatedAt: row.updated_at,
  };
}

function mapOutbox(row: OutboxRow): ReturnReceiptOutboxItem {
  return {
    attemptedAt: row.attempted_at,
    command: JSON.parse(row.command_json) as ReturnReceiptOutboxItem["command"],
    evidence: JSON.parse(row.evidence_json) as LocalReturnEvidence[],
    idempotencyKey: row.idempotency_key,
    receiptId: row.receipt_id,
    requestId: row.request_id,
    sequence: row.sequence,
  };
}

function localStatus(evidence: LocalReturnEvidence[]) {
  return evidence.every((item) => item.status === "uploaded")
    ? "pending_confirmation"
    : "pending_upload";
}

export async function createReturnReceiptStore(
  databaseName = "tradeflow-return-receipt.db",
): Promise<ReturnReceiptStore> {
  const database = await openDatabaseAsync(databaseName);
  return createSqliteReturnReceiptStore(database);
}

export function createSqliteReturnReceiptStore(
  database: SQLiteDatabase,
): ReturnReceiptStore {
  return {
    async initialize() {
      await database.execAsync(schema);
      const columns = await database.getAllAsync<{ name: string }>(
        "PRAGMA table_info(return_receipt_captures)",
      );
      const names = new Set(columns.map((column) => column.name));
      if (!names.has("error_code")) {
        await database.execAsync(
          "ALTER TABLE return_receipt_captures ADD COLUMN error_code TEXT",
        );
      }
      if (!names.has("error_message")) {
        await database.execAsync(
          "ALTER TABLE return_receipt_captures ADD COLUMN error_message TEXT",
        );
      }
      if (!names.has("auth_paused")) {
        await database.execAsync(
          "ALTER TABLE return_receipt_captures ADD COLUMN auth_paused INTEGER NOT NULL DEFAULT 0 CHECK (auth_paused IN (0, 1))",
        );
      }
      if (!names.has("replaces_receipt_id")) {
        await database.execAsync(
          "ALTER TABLE return_receipt_captures ADD COLUMN replaces_receipt_id TEXT",
        );
      }
      if (!names.has("replaced_by_receipt_id")) {
        await database.execAsync(
          "ALTER TABLE return_receipt_captures ADD COLUMN replaced_by_receipt_id TEXT",
        );
      }
    },
    async listCaptures() {
      const rows = await database.getAllAsync<CaptureRow>(
        "SELECT * FROM return_receipt_captures ORDER BY updated_at DESC",
      );
      return rows.map(mapCapture);
    },
    async listPending() {
      const rows = await database.getAllAsync<OutboxRow>(
        "SELECT * FROM return_receipt_outbox ORDER BY sequence",
      );
      return rows.map(mapOutbox);
    },
    async load(receiptId) {
      const row = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM return_receipt_captures WHERE receipt_id = ?",
        receiptId,
      );
      return row === null ? null : mapCapture(row);
    },
    async markAttempted(sequence, attemptedAt) {
      await database.runAsync(
        `UPDATE return_receipt_outbox
            SET attempted_at = COALESCE(attempted_at, ?)
          WHERE sequence = ?`,
        attemptedAt,
        sequence,
      );
    },
    async markEvidenceUploaded(sequence, evidenceId, updatedAt) {
      const row = await database.getFirstAsync<OutboxRow>(
        "SELECT * FROM return_receipt_outbox WHERE sequence = ?",
        sequence,
      );
      if (row === null) return;
      const evidence = (
        JSON.parse(row.evidence_json) as LocalReturnEvidence[]
      ).map((item) =>
        item.evidenceId === evidenceId
          ? { ...item, status: "uploaded" as const }
          : item,
      );
      const evidenceJson = JSON.stringify(evidence);
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE return_receipt_outbox
              SET evidence_json = ? WHERE sequence = ?`,
          evidenceJson,
          sequence,
        );
        await transaction.runAsync(
          `UPDATE return_receipt_captures
              SET evidence_json = ?, status = ?, auth_paused = 0,
                  correlation_id = NULL, error_code = NULL,
                  error_message = NULL, updated_at = ?
            WHERE receipt_id = ?`,
          evidenceJson,
          localStatus(evidence),
          updatedAt,
          row.receipt_id,
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
        `UPDATE return_receipt_captures
            SET auth_paused = 1, correlation_id = ?, error_code = ?, error_message = ?,
                updated_at = ?
          WHERE receipt_id = (
            SELECT receipt_id FROM return_receipt_outbox
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
          `UPDATE return_receipt_captures
              SET status = ?, auth_paused = 0, correlation_id = ?, error_code = ?,
                  error_message = ?, updated_at = ?
            WHERE receipt_id = (
              SELECT receipt_id FROM return_receipt_outbox
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
            "DELETE FROM return_receipt_outbox WHERE sequence = ?",
            sequence,
          );
        }
      });
    },
    async markSynced(sequence, response, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE return_receipt_captures
              SET status = 'confirmed', auth_paused = 0, correlation_id = NULL,
                  error_code = NULL, error_message = NULL,
                  response_json = ?, updated_at = ?
            WHERE receipt_id = (
              SELECT receipt_id FROM return_receipt_outbox
              WHERE sequence = ?
            )`,
          JSON.stringify(response),
          updatedAt,
          sequence,
        );
        await transaction.runAsync(
          "DELETE FROM return_receipt_outbox WHERE sequence = ?",
          sequence,
        );
      });
    },
    async saveAndEnqueue(capture, updatedAt) {
      const receiptId = capture.command.return_receipt_id;
      const existing = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM return_receipt_captures WHERE receipt_id = ?",
        receiptId,
      );
      if (existing !== null) return mapCapture(existing);
      await saveCapture(database, capture, updatedAt);
      const saved = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM return_receipt_captures WHERE receipt_id = ?",
        receiptId,
      );
      if (saved === null) throw new Error("Return Receipt was not saved.");
      return mapCapture(saved);
    },
    async replaceConflict(receiptId, capture, updatedAt) {
      const current = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM return_receipt_captures WHERE receipt_id = ?",
        receiptId,
      );
      if (current?.status !== "conflict") {
        throw new Error("Only a reviewed conflict can be replaced.");
      }
      if (capture.command.return_receipt_id === receiptId) {
        throw new Error("A replacement requires a new receipt identity.");
      }
      if (capture.requestId !== current.request_id) {
        throw new Error(
          "A replacement must belong to the same Return Request.",
        );
      }
      if (current.replaced_by_receipt_id !== null) {
        throw new Error("This conflict already has a replacement.");
      }
      await saveCapture(database, capture, updatedAt);
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE return_receipt_captures
              SET replaced_by_receipt_id = ?, updated_at = ?
            WHERE receipt_id = ?`,
          capture.command.return_receipt_id,
          updatedAt,
          receiptId,
        );
        await transaction.runAsync(
          `UPDATE return_receipt_captures
              SET replaces_receipt_id = ?
            WHERE receipt_id = ?`,
          receiptId,
          capture.command.return_receipt_id,
        );
      });
      const saved = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM return_receipt_captures WHERE receipt_id = ?",
        capture.command.return_receipt_id,
      );
      if (saved === null) throw new Error("Replacement was not saved.");
      return mapCapture(saved);
    },
  };
}

async function saveCapture(
  database: SQLiteDatabase,
  capture: ReturnReceiptCapture,
  updatedAt: string,
) {
  const receiptId = capture.command.return_receipt_id;
  const commandJson = JSON.stringify(capture.command);
  const evidenceJson = JSON.stringify(capture.evidence);
  await database.withExclusiveTransactionAsync(async (transaction) => {
    await transaction.runAsync(
      `INSERT INTO return_receipt_captures(
         receipt_id, request_id, command_json, evidence_json,
         idempotency_key, status, correlation_id, error_code, error_message,
         response_json, replaces_receipt_id, replaced_by_receipt_id,
         updated_at
       ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?)`,
      receiptId,
      capture.requestId,
      commandJson,
      evidenceJson,
      capture.idempotencyKey,
      localStatus(capture.evidence),
      updatedAt,
    );
    await transaction.runAsync(
      `INSERT INTO return_receipt_outbox(
         receipt_id, request_id, command_json, evidence_json,
         idempotency_key, attempted_at
       ) VALUES (?, ?, ?, ?, ?, NULL)`,
      receiptId,
      capture.requestId,
      commandJson,
      evidenceJson,
      capture.idempotencyKey,
    );
  });
}
