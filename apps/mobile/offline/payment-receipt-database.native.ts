import { openDatabaseAsync, type SQLiteDatabase } from "expo-sqlite";

import type {
  LocalPaymentReceipt,
  PaymentReceiptOutboxItem,
  PaymentReceiptStore,
} from "./payment-receipt-store";

type ReceiptRow = {
  command_json: string;
  correlation_id: string | null;
  idempotency_key: string;
  receipt_id: string;
  receipt_json: string | null;
  status: LocalPaymentReceipt["status"];
  updated_at: string;
};

type OutboxRow = {
  attempted_at: string | null;
  command_json: string;
  idempotency_key: string;
  receipt_id: string;
  sequence: number;
};

const schema = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS local_schema_migrations (
  version INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS payment_receipt_drafts (
  receipt_id TEXT PRIMARY KEY,
  command_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('pending', 'paused', 'synced')),
  correlation_id TEXT,
  receipt_json TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_receipt_outbox (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id TEXT NOT NULL REFERENCES payment_receipt_drafts(receipt_id),
  command_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  attempted_at TEXT
);
INSERT OR IGNORE INTO local_schema_migrations(version) VALUES (2);
`;

function mapReceipt(row: ReceiptRow): LocalPaymentReceipt {
  return {
    command: JSON.parse(row.command_json) as LocalPaymentReceipt["command"],
    correlationId: row.correlation_id,
    idempotencyKey: row.idempotency_key,
    receipt:
      row.receipt_json === null
        ? null
        : (JSON.parse(row.receipt_json) as LocalPaymentReceipt["receipt"]),
    receiptId: row.receipt_id,
    status: row.status,
    updatedAt: row.updated_at,
  };
}

function mapOutbox(row: OutboxRow): PaymentReceiptOutboxItem {
  return {
    attemptedAt: row.attempted_at,
    command: JSON.parse(
      row.command_json,
    ) as PaymentReceiptOutboxItem["command"],
    idempotencyKey: row.idempotency_key,
    receiptId: row.receipt_id,
    sequence: row.sequence,
  };
}

export async function createPaymentReceiptStore(
  databaseName = "tradeflow-payments.db",
): Promise<PaymentReceiptStore> {
  const database = await openDatabaseAsync(databaseName);
  return createSqlitePaymentReceiptStore(database);
}

export function createSqlitePaymentReceiptStore(
  database: SQLiteDatabase,
): PaymentReceiptStore {
  return {
    async initialize() {
      await database.execAsync(schema);
    },
    async listPending() {
      const rows = await database.getAllAsync<OutboxRow>(
        "SELECT * FROM payment_receipt_outbox ORDER BY sequence",
      );
      return rows.map(mapOutbox);
    },
    async load(receiptId) {
      const row = await database.getFirstAsync<ReceiptRow>(
        "SELECT * FROM payment_receipt_drafts WHERE receipt_id = ?",
        receiptId,
      );
      return row === null ? null : mapReceipt(row);
    },
    async markAttempted(sequence, attemptedAt) {
      await database.runAsync(
        `UPDATE payment_receipt_outbox
            SET attempted_at = COALESCE(attempted_at, ?)
          WHERE sequence = ?`,
        attemptedAt,
        sequence,
      );
    },
    async markPaused(sequence, correlationId, updatedAt) {
      await database.runAsync(
        `UPDATE payment_receipt_drafts
            SET status = 'paused', correlation_id = ?, updated_at = ?
          WHERE receipt_id = (
            SELECT receipt_id FROM payment_receipt_outbox WHERE sequence = ?
          )`,
        correlationId,
        updatedAt,
        sequence,
      );
    },
    async markSynced(sequence, receipt, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE payment_receipt_drafts
              SET status = 'synced', correlation_id = NULL,
                  receipt_json = ?, updated_at = ?
            WHERE receipt_id = (
              SELECT receipt_id FROM payment_receipt_outbox WHERE sequence = ?
            )`,
          JSON.stringify(receipt),
          updatedAt,
          sequence,
        );
        await transaction.runAsync(
          "DELETE FROM payment_receipt_outbox WHERE sequence = ?",
          sequence,
        );
      });
    },
    async saveAndEnqueue(command, idempotencyKey, updatedAt) {
      const existing = await database.getFirstAsync<ReceiptRow>(
        "SELECT * FROM payment_receipt_drafts WHERE receipt_id = ?",
        command.payment_receipt_id,
      );
      if (existing !== null) return mapReceipt(existing);
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `INSERT INTO payment_receipt_drafts(
             receipt_id, command_json, idempotency_key, status,
             correlation_id, receipt_json, updated_at
           ) VALUES (?, ?, ?, 'pending', NULL, NULL, ?)`,
          command.payment_receipt_id,
          JSON.stringify(command),
          idempotencyKey,
          updatedAt,
        );
        await transaction.runAsync(
          `INSERT INTO payment_receipt_outbox(
             receipt_id, command_json, idempotency_key, attempted_at
           ) VALUES (?, ?, ?, NULL)`,
          command.payment_receipt_id,
          JSON.stringify(command),
          idempotencyKey,
        );
      });
      const saved = await database.getFirstAsync<ReceiptRow>(
        "SELECT * FROM payment_receipt_drafts WHERE receipt_id = ?",
        command.payment_receipt_id,
      );
      if (saved === null)
        throw new Error("Local Payment Receipt was not saved.");
      return mapReceipt(saved);
    },
  };
}
