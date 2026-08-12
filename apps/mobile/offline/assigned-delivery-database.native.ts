import { openDatabaseAsync } from "expo-sqlite";

import type {
  AssignedDeliveryCache,
  AssignedDeliverySnapshot,
} from "./assigned-delivery-cache";

export async function createAssignedDeliveryCache(
  databaseName: string,
): Promise<AssignedDeliveryCache> {
  const database = await openDatabaseAsync(databaseName);
  return {
    async initialize() {
      await database.execAsync(`
        CREATE TABLE IF NOT EXISTS assigned_delivery_cache (
          subject TEXT PRIMARY KEY NOT NULL,
          snapshot_json TEXT NOT NULL
        )
      `);
    },
    async load(subject) {
      const row = await database.getFirstAsync<{ snapshot_json: string }>(
        "SELECT snapshot_json FROM assigned_delivery_cache WHERE subject = ?",
        subject,
      );
      return row === null
        ? null
        : (JSON.parse(row.snapshot_json) as AssignedDeliverySnapshot);
    },
    async remove(subject) {
      await database.runAsync(
        "DELETE FROM assigned_delivery_cache WHERE subject = ?",
        subject,
      );
    },
    async replace(snapshot) {
      await database.runAsync(
        `INSERT INTO assigned_delivery_cache (subject, snapshot_json)
         VALUES (?, ?)
         ON CONFLICT(subject) DO UPDATE SET snapshot_json = excluded.snapshot_json`,
        snapshot.subject,
        JSON.stringify(snapshot),
      );
    },
  };
}
