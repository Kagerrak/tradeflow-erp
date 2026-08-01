import {
  createMemoryDeliveryConfirmationBacking,
  createMemoryDeliveryConfirmationStore,
  type DeliveryConfirmationStore,
  type MemoryDeliveryConfirmationBacking,
} from "./delivery-confirmation-store";

type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

type SerializedBacking = {
  captures: Array<
    [
      string,
      MemoryDeliveryConfirmationBacking["captures"] extends Map<string, infer T>
        ? T
        : never,
    ]
  >;
  nextSequence: number;
  outbox: Array<
    [
      number,
      MemoryDeliveryConfirmationBacking["outbox"] extends Map<number, infer T>
        ? T
        : never,
    ]
  >;
};

function hydrate(value: string | null): MemoryDeliveryConfirmationBacking {
  if (value === null) return createMemoryDeliveryConfirmationBacking();
  try {
    const parsed = JSON.parse(value) as SerializedBacking;
    return {
      captures: new Map(parsed.captures),
      nextSequence: parsed.nextSequence,
      outbox: new Map(parsed.outbox),
    };
  } catch {
    return createMemoryDeliveryConfirmationBacking();
  }
}

function serialize(backing: MemoryDeliveryConfirmationBacking): string {
  return JSON.stringify({
    captures: [...backing.captures.entries()],
    nextSequence: backing.nextSequence,
    outbox: [...backing.outbox.entries()],
  } satisfies SerializedBacking);
}

export function createWebDeliveryConfirmationStore(
  storage: StorageLike,
  storageKey = "tradeflow-delivery-confirmation-outbox",
): DeliveryConfirmationStore {
  const backing = hydrate(storage.getItem(storageKey));
  const memory = createMemoryDeliveryConfirmationStore(backing);
  const persist = () => storage.setItem(storageKey, serialize(backing));
  return {
    ...memory,
    async initialize() {},
    async markAttempted(sequence, attemptedAt) {
      await memory.markAttempted(sequence, attemptedAt);
      persist();
    },
    async markEvidenceUploaded(sequence, evidenceId, updatedAt) {
      await memory.markEvidenceUploaded(sequence, evidenceId, updatedAt);
      persist();
    },
    async markState(sequence, status, correlationId, updatedAt) {
      await memory.markState(sequence, status, correlationId, updatedAt);
      persist();
    },
    async markSynced(sequence, response, updatedAt) {
      await memory.markSynced(sequence, response, updatedAt);
      persist();
    },
    async saveAndEnqueue(capture, updatedAt) {
      const result = await memory.saveAndEnqueue(capture, updatedAt);
      persist();
      return result;
    },
  };
}

export async function createDeliveryConfirmationStore(
  databaseName = "tradeflow-delivery-confirmation",
): Promise<DeliveryConfirmationStore> {
  if (typeof globalThis.localStorage === "undefined") {
    throw new Error("Durable Delivery Confirmation requires browser storage.");
  }
  return createWebDeliveryConfirmationStore(
    globalThis.localStorage,
    `tradeflow:${databaseName}:delivery-confirmation-outbox`,
  );
}
