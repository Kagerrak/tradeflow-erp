import {
  createMemoryPickCommandBacking,
  createMemoryPickCommandStore,
  type MemoryPickCommandBacking,
  type PickCommandStore,
} from "./pick-command-store";

type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

type SerializedBacking = {
  captures: Array<
    [
      string,
      MemoryPickCommandBacking["captures"] extends Map<string, infer T>
        ? T
        : never,
    ]
  >;
  nextSequence: number;
  outbox: Array<
    [
      number,
      MemoryPickCommandBacking["outbox"] extends Map<number, infer T>
        ? T
        : never,
    ]
  >;
};

function hydrate(value: string | null): MemoryPickCommandBacking {
  if (value === null) return createMemoryPickCommandBacking();
  try {
    const parsed = JSON.parse(value) as SerializedBacking;
    return {
      captures: new Map(parsed.captures),
      nextSequence: parsed.nextSequence,
      outbox: new Map(parsed.outbox),
    };
  } catch {
    return createMemoryPickCommandBacking();
  }
}

function serialize(backing: MemoryPickCommandBacking): string {
  return JSON.stringify({
    captures: [...backing.captures.entries()],
    nextSequence: backing.nextSequence,
    outbox: [...backing.outbox.entries()],
  } satisfies SerializedBacking);
}

export function createWebPickCommandStore(
  storage: StorageLike,
  storageKey = "tradeflow-pick-command-outbox",
): PickCommandStore {
  const backing = hydrate(storage.getItem(storageKey));
  const memory = createMemoryPickCommandStore(backing);
  const persist = () => storage.setItem(storageKey, serialize(backing));
  return {
    ...memory,
    async initialize() {},
    async markAttempted(sequence, attemptedAt) {
      await memory.markAttempted(sequence, attemptedAt);
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
    async markReversed(pickId, correlationId, updatedAt) {
      await memory.markReversed(pickId, correlationId, updatedAt);
      persist();
    },
    async saveAndEnqueue(
      fulfillmentOrderId,
      command,
      idempotencyKey,
      updatedAt,
    ) {
      const result = await memory.saveAndEnqueue(
        fulfillmentOrderId,
        command,
        idempotencyKey,
        updatedAt,
      );
      persist();
      return result;
    },
  };
}

export async function createPickCommandStore(
  databaseName = "tradeflow-picking",
): Promise<PickCommandStore> {
  if (typeof globalThis.localStorage === "undefined") {
    throw new Error("Durable Pick capture requires browser storage.");
  }
  return createWebPickCommandStore(
    globalThis.localStorage,
    `tradeflow:${databaseName}:pick-outbox`,
  );
}
