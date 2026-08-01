import type {
  AssignedDeliveryCache,
  AssignedDeliverySnapshot,
} from "./assigned-delivery-cache";

export async function createAssignedDeliveryCache(
  databaseName: string,
): Promise<AssignedDeliveryCache> {
  const key = `${databaseName}:assigned-deliveries`;
  return {
    async initialize() {},
    async load(subject) {
      const value = globalThis.localStorage?.getItem(`${key}:${subject}`);
      return value === null || value === undefined
        ? null
        : (JSON.parse(value) as AssignedDeliverySnapshot);
    },
    async remove(subject) {
      globalThis.localStorage?.removeItem(`${key}:${subject}`);
    },
    async replace(snapshot) {
      globalThis.localStorage?.setItem(
        `${key}:${snapshot.subject}`,
        JSON.stringify(snapshot),
      );
    },
  };
}
