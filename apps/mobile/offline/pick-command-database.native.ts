import { openDatabaseAsync, type SQLiteDatabase } from "expo-sqlite";

import {
  statusFromResponse,
  type LocalPickCommand,
  type PickCommandOutboxItem,
  type PickCommandStore,
} from "./pick-command-store";

type CaptureRow = {
  command_json: string;
  correlation_id: string | null;
  fulfillment_order_id: string;
  idempotency_key: string;
  pick_id: string;
  response_json: string | null;
  status: LocalPickCommand["status"];
  updated_at: string;
};

type OutboxRow = {
  attempted_at: string | null;
  command_json: string;
  fulfillment_order_id: string;
  idempotency_key: string;
  pick_id: string;
  sequence: number;
};

const schema = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS pick_command_captures (
  pick_id TEXT PRIMARY KEY,
  fulfillment_order_id TEXT NOT NULL,
  command_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (
    status IN (
      'pending_sync', 'conflict', 'forbidden', 'scan_denied',
      'partially_picked', 'complete', 'reversed'
    )
  ),
  correlation_id TEXT,
  response_json TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pick_command_outbox (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  pick_id TEXT NOT NULL REFERENCES pick_command_captures(pick_id),
  fulfillment_order_id TEXT NOT NULL,
  command_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  attempted_at TEXT
);
`;

function mapCapture(row: CaptureRow): LocalPickCommand {
  return {
    command: JSON.parse(row.command_json) as LocalPickCommand["command"],
    correlationId: row.correlation_id,
    fulfillmentOrderId: row.fulfillment_order_id,
    idempotencyKey: row.idempotency_key,
    pickId: row.pick_id,
    response:
      row.response_json === null
        ? null
        : (JSON.parse(row.response_json) as LocalPickCommand["response"]),
    status: row.status,
    updatedAt: row.updated_at,
  };
}

function mapOutbox(row: OutboxRow): PickCommandOutboxItem {
  return {
    attemptedAt: row.attempted_at,
    command: JSON.parse(row.command_json) as PickCommandOutboxItem["command"],
    fulfillmentOrderId: row.fulfillment_order_id,
    idempotencyKey: row.idempotency_key,
    pickId: row.pick_id,
    sequence: row.sequence,
  };
}

export async function createPickCommandStore(
  databaseName = "tradeflow-picking.db",
): Promise<PickCommandStore> {
  const database = await openDatabaseAsync(databaseName);
  return createSqlitePickCommandStore(database);
}

export function createSqlitePickCommandStore(
  database: SQLiteDatabase,
): PickCommandStore {
  return {
    async initialize() {
      await database.execAsync(schema);
    },
    async listCaptures() {
      const rows = await database.getAllAsync<CaptureRow>(
        "SELECT * FROM pick_command_captures ORDER BY updated_at DESC",
      );
      return rows.map(mapCapture);
    },
    async listPending() {
      const rows = await database.getAllAsync<OutboxRow>(
        "SELECT * FROM pick_command_outbox ORDER BY sequence",
      );
      return rows.map(mapOutbox);
    },
    async load(pickId) {
      const row = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM pick_command_captures WHERE pick_id = ?",
        pickId,
      );
      return row === null ? null : mapCapture(row);
    },
    async markAttempted(sequence, attemptedAt) {
      await database.runAsync(
        `UPDATE pick_command_outbox
            SET attempted_at = COALESCE(attempted_at, ?)
          WHERE sequence = ?`,
        attemptedAt,
        sequence,
      );
    },
    async markState(sequence, status, correlationId, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE pick_command_captures
              SET status = ?, correlation_id = ?, updated_at = ?
            WHERE pick_id = (
              SELECT pick_id FROM pick_command_outbox WHERE sequence = ?
            )`,
          status,
          correlationId,
          updatedAt,
          sequence,
        );
        await transaction.runAsync(
          "DELETE FROM pick_command_outbox WHERE sequence = ?",
          sequence,
        );
      });
    },
    async markSynced(sequence, response, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE pick_command_captures
              SET status = ?, correlation_id = NULL,
                  response_json = ?, updated_at = ?
            WHERE pick_id = (
              SELECT pick_id FROM pick_command_outbox WHERE sequence = ?
            )`,
          statusFromResponse(response),
          JSON.stringify(response),
          updatedAt,
          sequence,
        );
        await transaction.runAsync(
          "DELETE FROM pick_command_outbox WHERE sequence = ?",
          sequence,
        );
      });
    },
    async markReversed(pickId, correlationId, updatedAt) {
      await database.runAsync(
        `UPDATE pick_command_captures
            SET status = 'reversed', correlation_id = ?, updated_at = ?
          WHERE pick_id = ?`,
        correlationId,
        updatedAt,
        pickId,
      );
    },
    async saveAndEnqueue(
      fulfillmentOrderId,
      command,
      idempotencyKey,
      updatedAt,
    ) {
      const existing = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM pick_command_captures WHERE pick_id = ?",
        command.pick_id,
      );
      if (existing !== null) return mapCapture(existing);
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `INSERT INTO pick_command_captures(
             pick_id, fulfillment_order_id, command_json, idempotency_key,
             status, correlation_id, response_json, updated_at
           ) VALUES (?, ?, ?, ?, 'pending_sync', NULL, NULL, ?)`,
          command.pick_id,
          fulfillmentOrderId,
          JSON.stringify(command),
          idempotencyKey,
          updatedAt,
        );
        await transaction.runAsync(
          `INSERT INTO pick_command_outbox(
             pick_id, fulfillment_order_id, command_json,
             idempotency_key, attempted_at
           ) VALUES (?, ?, ?, ?, NULL)`,
          command.pick_id,
          fulfillmentOrderId,
          JSON.stringify(command),
          idempotencyKey,
        );
      });
      const saved = await database.getFirstAsync<CaptureRow>(
        "SELECT * FROM pick_command_captures WHERE pick_id = ?",
        command.pick_id,
      );
      if (saved === null) throw new Error("Local Pick command was not saved.");
      return mapCapture(saved);
    },
  };
}
