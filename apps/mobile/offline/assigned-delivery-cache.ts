import type { AssignedDelivery } from "@tradeflow/delivery-dispatch";

export type AssignedDeliverySnapshot = {
  cacheTag: string | null;
  deliveries: AssignedDelivery[];
  savedAt: string;
  subject: string;
};

export type AssignedDeliveryCache = {
  initialize(): Promise<void>;
  load(subject: string): Promise<AssignedDeliverySnapshot | null>;
  remove(subject: string): Promise<void>;
  replace(snapshot: AssignedDeliverySnapshot): Promise<void>;
};

export function createMemoryAssignedDeliveryCache(): AssignedDeliveryCache {
  const snapshots = new Map<string, AssignedDeliverySnapshot>();
  return {
    async initialize() {},
    async load(subject) {
      return snapshots.get(subject) ?? null;
    },
    async remove(subject) {
      snapshots.delete(subject);
    },
    async replace(snapshot) {
      snapshots.set(snapshot.subject, structuredClone(snapshot));
    },
  };
}
