import { openDatabaseAsync, type SQLiteDatabase } from "expo-sqlite";

import type {
  LocalSalesDraft,
  SalesDraftOutboxItem,
  SalesDraftStore,
} from "./sales-draft-store";

type DraftRow = {
  command_json: string;
  conflict_correlation_id: string | null;
  expected_version: number | null;
  idempotency_key: string;
  order_id: string;
  saved_draft_json: string | null;
  status: LocalSalesDraft["status"];
  updated_at: string;
};

type OutboxRow = {
  attempted_at: string | null;
  command_json: string;
  expected_version: number | null;
  idempotency_key: string;
  order_id: string;
  sequence: number;
};

const schema = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS local_schema_migrations (
  version INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS sales_drafts (
  order_id TEXT PRIMARY KEY,
  command_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  expected_version INTEGER,
  status TEXT NOT NULL CHECK (status IN ('pending', 'syncing', 'conflict', 'synced')),
  conflict_correlation_id TEXT,
  saved_draft_json TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sales_outbox (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT NOT NULL REFERENCES sales_drafts(order_id),
  command_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  expected_version INTEGER,
  attempted_at TEXT,
  UNIQUE(order_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS sales_reference_cache (
  cache_key TEXT PRIMARY KEY,
  reference_json TEXT NOT NULL,
  cached_at TEXT NOT NULL
);
INSERT OR IGNORE INTO local_schema_migrations(version) VALUES (1);
`;

function mapDraft(row: DraftRow): LocalSalesDraft {
  return {
    command: JSON.parse(row.command_json) as LocalSalesDraft["command"],
    conflictCorrelationId: row.conflict_correlation_id,
    expectedVersion: row.expected_version,
    idempotencyKey: row.idempotency_key,
    orderId: row.order_id,
    savedDraft:
      row.saved_draft_json === null
        ? null
        : (JSON.parse(row.saved_draft_json) as LocalSalesDraft["savedDraft"]),
    status: row.status,
    updatedAt: row.updated_at,
  };
}

function mapOutbox(row: OutboxRow): SalesDraftOutboxItem {
  return {
    attemptedAt: row.attempted_at,
    command: JSON.parse(row.command_json) as SalesDraftOutboxItem["command"],
    expectedVersion: row.expected_version,
    idempotencyKey: row.idempotency_key,
    orderId: row.order_id,
    sequence: row.sequence,
  };
}

export async function createSalesDraftStore(
  databaseName = "tradeflow.db",
): Promise<SalesDraftStore> {
  const database = await openDatabaseAsync(databaseName);
  return createSqliteSalesDraftStore(database);
}

export function createSqliteSalesDraftStore(
  database: SQLiteDatabase,
): SalesDraftStore {
  return {
    async initialize() {
      await database.execAsync(schema);
    },
    async listDrafts() {
      const rows = await database.getAllAsync<DraftRow>(
        "SELECT * FROM sales_drafts ORDER BY updated_at DESC",
      );
      return rows.map(mapDraft);
    },
    async loadReference(cacheKey) {
      const row = await database.getFirstAsync<{ reference_json: string }>(
        "SELECT reference_json FROM sales_reference_cache WHERE cache_key = ?",
        cacheKey,
      );
      return row === null
        ? null
        : (JSON.parse(row.reference_json) as Awaited<
            ReturnType<SalesDraftStore["loadReference"]>
          >);
    },
    async listPending() {
      const rows = await database.getAllAsync<OutboxRow>(
        "SELECT * FROM sales_outbox ORDER BY sequence",
      );
      return rows.map(mapOutbox);
    },
    async load(orderId) {
      const row = await database.getFirstAsync<DraftRow>(
        "SELECT * FROM sales_drafts WHERE order_id = ?",
        orderId,
      );
      return row === null ? null : mapDraft(row);
    },
    async markAttempted(sequence, attemptedAt) {
      await database.runAsync(
        "UPDATE sales_outbox SET attempted_at = COALESCE(attempted_at, ?) WHERE sequence = ?",
        attemptedAt,
        sequence,
      );
    },
    async markConflict(sequence, correlationId, authoritativeDraft, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE sales_drafts
             SET status = 'conflict', conflict_correlation_id = ?,
                 saved_draft_json = ?, updated_at = ?
           WHERE order_id = (SELECT order_id FROM sales_outbox WHERE sequence = ?)`,
          correlationId,
          authoritativeDraft === null
            ? null
            : JSON.stringify(authoritativeDraft),
          updatedAt,
          sequence,
        );
      });
    },
    async markSynced(sequence, savedDraft, updatedAt) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `UPDATE sales_drafts
             SET status = 'synced', conflict_correlation_id = NULL,
                 expected_version = ?, saved_draft_json = ?, updated_at = ?
           WHERE order_id = (SELECT order_id FROM sales_outbox WHERE sequence = ?)`,
          savedDraft.version,
          JSON.stringify(savedDraft),
          updatedAt,
          sequence,
        );
        await transaction.runAsync(
          "DELETE FROM sales_outbox WHERE sequence = ?",
          sequence,
        );
      });
    },
    async retryConflict(
      orderId,
      command,
      expectedVersion,
      idempotencyKey,
      updatedAt,
    ) {
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          "DELETE FROM sales_outbox WHERE order_id = ?",
          orderId,
        );
        await transaction.runAsync(
          `UPDATE sales_drafts
             SET command_json = ?, idempotency_key = ?, expected_version = ?,
                 status = 'pending', conflict_correlation_id = NULL, updated_at = ?
           WHERE order_id = ? AND status = 'conflict'`,
          JSON.stringify(command),
          idempotencyKey,
          expectedVersion,
          updatedAt,
          orderId,
        );
        await transaction.runAsync(
          `INSERT INTO sales_outbox(
             order_id, command_json, idempotency_key, expected_version, attempted_at
           ) VALUES (?, ?, ?, ?, NULL)`,
          orderId,
          JSON.stringify(command),
          idempotencyKey,
          expectedVersion,
        );
      });
    },
    async saveReference(cacheKey, reference, cachedAt) {
      await database.runAsync(
        `INSERT INTO sales_reference_cache(cache_key, reference_json, cached_at)
         VALUES (?, ?, ?)
         ON CONFLICT(cache_key) DO UPDATE SET
           reference_json = excluded.reference_json,
           cached_at = excluded.cached_at`,
        cacheKey,
        JSON.stringify(reference),
        cachedAt,
      );
    },
    async saveAndEnqueue(command, idempotencyKey, updatedAt) {
      const orderId = command.sales_order_id;
      const attempted = await database.getFirstAsync<{ sequence: number }>(
        `SELECT sequence FROM sales_outbox
          WHERE order_id = ? AND attempted_at IS NOT NULL LIMIT 1`,
        orderId,
      );
      if (attempted !== null) {
        const existing = await database.getFirstAsync<DraftRow>(
          "SELECT * FROM sales_drafts WHERE order_id = ?",
          orderId,
        );
        if (existing !== null) return mapDraft(existing);
      }
      const current = await database.getFirstAsync<DraftRow>(
        "SELECT * FROM sales_drafts WHERE order_id = ?",
        orderId,
      );
      const expectedVersion =
        current?.saved_draft_json === null ||
        current?.saved_draft_json === undefined
          ? null
          : (
              JSON.parse(current.saved_draft_json) as NonNullable<
                LocalSalesDraft["savedDraft"]
              >
            ).version;
      await database.withExclusiveTransactionAsync(async (transaction) => {
        await transaction.runAsync(
          `INSERT INTO sales_drafts(
             order_id, command_json, idempotency_key, expected_version, status,
             conflict_correlation_id, saved_draft_json, updated_at
           ) VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?)
           ON CONFLICT(order_id) DO UPDATE SET
             command_json = excluded.command_json,
             idempotency_key = excluded.idempotency_key,
             expected_version = excluded.expected_version,
             status = 'pending',
             conflict_correlation_id = NULL,
             updated_at = excluded.updated_at`,
          orderId,
          JSON.stringify(command),
          idempotencyKey,
          expectedVersion,
          updatedAt,
        );
        await transaction.runAsync(
          "DELETE FROM sales_outbox WHERE order_id = ? AND attempted_at IS NULL",
          orderId,
        );
        await transaction.runAsync(
          `INSERT INTO sales_outbox(
             order_id, command_json, idempotency_key, expected_version, attempted_at
           ) VALUES (?, ?, ?, ?, NULL)`,
          orderId,
          JSON.stringify(command),
          idempotencyKey,
          expectedVersion,
        );
      });
      const saved = await database.getFirstAsync<DraftRow>(
        "SELECT * FROM sales_drafts WHERE order_id = ?",
        orderId,
      );
      if (saved === null)
        throw new Error("Local Sales Order Draft was not saved.");
      return mapDraft(saved);
    },
  };
}
