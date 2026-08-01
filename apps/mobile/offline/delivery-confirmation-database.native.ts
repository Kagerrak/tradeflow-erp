import { openDatabaseAsync, type SQLiteDatabase } from "expo-sqlite";

import type {
  DeliveryConfirmationCapture,
  DeliveryConfirmationOutboxItem,
  DeliveryConfirmationStore,
  LocalDeliveryConfirmation,
  LocalDeliveryEvidence,
} from "./delivery-confirmation-store";

type CaptureRow = {
  command_json: string;
  confirmation_id: string;
  correlation_id: string | null;
  delivery_id: string;
  evidence_json: string;
  idempotency_key: string;
  response_json: string | null;
  status: LocalDeliveryConfirmation["status"];
  updated_at: string;
};

type OutboxRow = {
  attempted_at: string | null;
  command_json: string;
  confirmation_id: string;
  delivery_id: string;
  evidence_json: string;
  idempotency_key: string;
  sequence: number;
};

const schema = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS delivery_confirmation_captures (
  confirmation_id TEXT PRIMARY KEY,
  delivery_id TEXT NOT NULL,
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
  response_json TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_confirmation_outbox (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  confirmation_id TEXT NOT NULL
    REFERENCES delivery_confirmation_captures(confirmation_id),
  delivery_id TEXT NOT NULL,
  command_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  attempted_at TEXT
);
`;

function mapCapture(row: CaptureRow): LocalDeliveryConfirmation {
  return {
    command: JSON.parse(
      row.command_json,
    ) as LocalDeliveryConfirmation["command"],
    confirmationId: row.confirmation_id,
    correlationId: row.correlation_id,
    deliveryId: row.delivery_id,
    evidence: JSON.parse(row.evidence_json) as LocalDeliveryEvidence[],
    idempotencyKey: row.idempotency_key,
    response:
      row.response_json === null
        ? null
        : (JSON.parse(
            row.response_json,
          ) as LocalDeliveryConfirmation["response"]),
    status: row.status,
    updatedAt: row.updated_at,
  };
}

function mapOutbox(row: OutboxRow): DeliveryConfirmationOutboxItem {
  return {
    attemptedAt: row.attempted_at,
    command: JSON.parse(
      row.command_json,
    ) as DeliveryConfirmationOutboxItem["command"],
    confirmationId: row.confirmation_id,
    deliveryId: row.delivery_id,
    evidence: JSON.parse(row.evidence_json) as LocalDeliveryEvidence[],
    idempotencyKey: row.idempotency_key,
    sequence: row.sequence,
  };
}

function localStatus(evidence: LocalDeliveryEvidence[]) {
  return evidence.every((item) => item.status === "uploaded")
    ? "pending_confirmation"
    : "pending_upload";
}

export async function createDeliveryConfirmationStore(
  databaseName = "tradeflow-delivery-confirmation.db",
): Promise<DeliveryConfirmationStore> {
  const database = await openDatabaseAsync(databaseName);
  return createSqliteDeliveryConfirmationStore(database);
}

export function createSqliteDeliveryConfirmationStore(
  database: SQLiteDatabase,
): DeliveryConfirmationStore {
  return {
    async initialize() {
      await database.execAsync(schema);
    },
    async listPending() {
      const rows = await database.getAllAsync<OutboxRow>(
        "SELECT * FROM delivery_confirmation_outbox ORDER BY sequence",
      );
      return rows.map(mapOutbox);
    },
    async load(confirmationId) {
      const row = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM delivery_confirmation_captures WHERE confirmation_id = ?",
        confirmationId,
      );
      return row === null ? null : mapCapture(row);
    },
    async markAttempted(sequence, attemptedAt) {
      await database.runAsync(
        `UPDATE delivery_confirmation_outbox
            SET attempted_at = COALESCE(attempted_at, ?)
          WHERE sequence = ?`,
        attemptedAt,
        sequence,
      );
    },
    async markEvidenceUploaded(sequence, evidenceId, updatedAt) {
      const row = await database.getFirstAsync<OutboxRow>(
        "SELECT * FROM delivery_confirmation_outbox WHERE sequence = ?",
        sequence,
      );
      if (row === null) return;
      const evidence = (
        JSON.parse(row.evidence_json) as LocalDeliveryEvidence[]
      ).map((item) =>
        item.evidenceId === evidenceId
          ? { ...item, status: "uploaded" as const }
          : item,
      );
      const evidenceJson = JSON.stringify(evidence);
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE delivery_confirmation_outbox
              SET evidence_json = ? WHERE sequence = ?`,
          evidenceJson,
          sequence,
        );
        await transaction.runAsync(
          `UPDATE delivery_confirmation_captures
              SET evidence_json = ?, status = ?, updated_at = ?
            WHERE confirmation_id = ?`,
          evidenceJson,
          localStatus(evidence),
          updatedAt,
          row.confirmation_id,
        );
      });
    },
    async markState(sequence, status, correlationId, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE delivery_confirmation_captures
              SET status = ?, correlation_id = ?, updated_at = ?
            WHERE confirmation_id = (
              SELECT confirmation_id FROM delivery_confirmation_outbox
              WHERE sequence = ?
            )`,
          status,
          correlationId,
          updatedAt,
          sequence,
        );
        if (status !== "upload_failed") {
          await transaction.runAsync(
            "DELETE FROM delivery_confirmation_outbox WHERE sequence = ?",
            sequence,
          );
        }
      });
    },
    async markSynced(sequence, response, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE delivery_confirmation_captures
              SET status = 'confirmed', correlation_id = NULL,
                  response_json = ?, updated_at = ?
            WHERE confirmation_id = (
              SELECT confirmation_id FROM delivery_confirmation_outbox
              WHERE sequence = ?
            )`,
          JSON.stringify(response),
          updatedAt,
          sequence,
        );
        await transaction.runAsync(
          "DELETE FROM delivery_confirmation_outbox WHERE sequence = ?",
          sequence,
        );
      });
    },
    async saveAndEnqueue(capture, updatedAt) {
      const confirmationId = capture.command.confirmation_id;
      const existing = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM delivery_confirmation_captures WHERE confirmation_id = ?",
        confirmationId,
      );
      if (existing !== null) return mapCapture(existing);
      await saveCapture(database, capture, updatedAt);
      const saved = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM delivery_confirmation_captures WHERE confirmation_id = ?",
        confirmationId,
      );
      if (saved === null)
        throw new Error("Delivery Confirmation was not saved.");
      return mapCapture(saved);
    },
  };
}

async function saveCapture(
  database: SQLiteDatabase,
  capture: DeliveryConfirmationCapture,
  updatedAt: string,
) {
  const confirmationId = capture.command.confirmation_id;
  const commandJson = JSON.stringify(capture.command);
  const evidenceJson = JSON.stringify(capture.evidence);
  await database.withExclusiveTransactionAsync(async (transaction) => {
    await transaction.runAsync(
      `INSERT INTO delivery_confirmation_captures(
         confirmation_id, delivery_id, command_json, evidence_json,
         idempotency_key, status, correlation_id, response_json, updated_at
       ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)`,
      confirmationId,
      capture.deliveryId,
      commandJson,
      evidenceJson,
      capture.idempotencyKey,
      localStatus(capture.evidence),
      updatedAt,
    );
    await transaction.runAsync(
      `INSERT INTO delivery_confirmation_outbox(
         confirmation_id, delivery_id, command_json, evidence_json,
         idempotency_key, attempted_at
       ) VALUES (?, ?, ?, ?, ?, NULL)`,
      confirmationId,
      capture.deliveryId,
      commandJson,
      evidenceJson,
      capture.idempotencyKey,
    );
  });
}
