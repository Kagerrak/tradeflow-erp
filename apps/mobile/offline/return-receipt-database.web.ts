import {
  createMemoryReturnReceiptBacking,
  createMemoryReturnReceiptStore,
  type MemoryReturnReceiptBacking,
  type ReturnReceiptStore,
} from "./return-receipt-store";

type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

type SerializedBacking = {
  captures: Array<
    [
      string,
      MemoryReturnReceiptBacking["captures"] extends Map<string, infer T>
        ? T
        : never,
    ]
  >;
  nextSequence: number;
  outbox: Array<
    [
      number,
      MemoryReturnReceiptBacking["outbox"] extends Map<number, infer T>
        ? T
        : never,
    ]
  >;
};

function hydrate(value: string | null): MemoryReturnReceiptBacking {
  if (value === null) return createMemoryReturnReceiptBacking();
  try {
    const parsed = JSON.parse(value) as SerializedBacking;
    return {
      captures: new Map(
        parsed.captures.map(([key, capture]) => [
          key,
          {
            ...capture,
            authPaused: capture.authPaused ?? false,
            replacedByReceiptId: capture.replacedByReceiptId ?? null,
            replacesReceiptId: capture.replacesReceiptId ?? null,
          },
        ]),
      ),
      nextSequence: parsed.nextSequence,
      outbox: new Map(parsed.outbox),
    };
  } catch {
    return createMemoryReturnReceiptBacking();
  }
}

function serialize(backing: MemoryReturnReceiptBacking): string {
  return JSON.stringify({
    captures: [...backing.captures.entries()],
    nextSequence: backing.nextSequence,
    outbox: [...backing.outbox.entries()],
  } satisfies SerializedBacking);
}

export function createWebReturnReceiptStore(
  storage: StorageLike,
  storageKey = "tradeflow-return-receipt-outbox",
): ReturnReceiptStore {
  const backing = hydrate(storage.getItem(storageKey));
  const memory = createMemoryReturnReceiptStore(backing);
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
    async markRetryableAuth(
      sequence,
      correlationId,
      updatedAt,
      errorCode,
      errorMessage,
    ) {
      await memory.markRetryableAuth(
        sequence,
        correlationId,
        updatedAt,
        errorCode,
        errorMessage,
      );
      persist();
    },
    async markState(
      sequence,
      status,
      correlationId,
      updatedAt,
      errorCode,
      errorMessage,
    ) {
      await memory.markState(
        sequence,
        status,
        correlationId,
        updatedAt,
        errorCode,
        errorMessage,
      );
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
    async replaceConflict(receiptId, capture, updatedAt) {
      const result = await memory.replaceConflict(
        receiptId,
        capture,
        updatedAt,
      );
      persist();
      return result;
    },
  };
}

export async function createReturnReceiptStore(
  databaseName = "tradeflow-return-receipt",
): Promise<ReturnReceiptStore> {
  if (typeof globalThis.localStorage === "undefined") {
    throw new Error("Durable Return Receipt requires browser storage.");
  }
  return createWebReturnReceiptStore(
    globalThis.localStorage,
    `tradeflow:${databaseName}:return-receipt-outbox`,
  );
}
