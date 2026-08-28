import {
  createMemoryReturnEvidenceBacking,
  createMemoryReturnEvidenceStore,
  type MemoryReturnEvidenceBacking,
  type ReturnEvidenceStore,
} from "./return-evidence-store";

type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

type SerializedBacking = {
  captures: Array<
    [
      string,
      MemoryReturnEvidenceBacking["captures"] extends Map<string, infer T>
        ? T
        : never,
    ]
  >;
  nextSequence: number;
  outbox: Array<
    [
      number,
      MemoryReturnEvidenceBacking["outbox"] extends Map<number, infer T>
        ? T
        : never,
    ]
  >;
};

function hydrate(value: string | null): MemoryReturnEvidenceBacking {
  if (value === null) return createMemoryReturnEvidenceBacking();
  try {
    const parsed = JSON.parse(value) as SerializedBacking;
    return {
      captures: new Map(
        parsed.captures.map(([key, capture]) => [
          key,
          {
            ...capture,
            authPaused: capture.authPaused ?? false,
            expectedRequestVersion: capture.expectedRequestVersion ?? null,
          },
        ]),
      ),
      nextSequence: parsed.nextSequence,
      outbox: new Map(parsed.outbox),
    };
  } catch {
    return createMemoryReturnEvidenceBacking();
  }
}

function serialize(backing: MemoryReturnEvidenceBacking): string {
  return JSON.stringify({
    captures: [...backing.captures.entries()],
    nextSequence: backing.nextSequence,
    outbox: [...backing.outbox.entries()],
  } satisfies SerializedBacking);
}

export function createWebReturnEvidenceStore(
  storage: StorageLike,
  storageKey = "tradeflow-return-evidence-outbox",
): ReturnEvidenceStore {
  const backing = hydrate(storage.getItem(storageKey));
  const memory = createMemoryReturnEvidenceStore(backing);
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
    async markSynced(sequence, updatedAt) {
      await memory.markSynced(sequence, updatedAt);
      persist();
    },
    async saveAndEnqueue(capture, expectedRequestVersion, updatedAt) {
      const result = await memory.saveAndEnqueue(
        capture,
        expectedRequestVersion,
        updatedAt,
      );
      persist();
      return result;
    },
  };
}

export async function createReturnEvidenceStore(
  databaseName = "tradeflow-return-evidence",
): Promise<ReturnEvidenceStore> {
  if (typeof globalThis.localStorage === "undefined") {
    throw new Error("Durable Return Evidence requires browser storage.");
  }
  return createWebReturnEvidenceStore(
    globalThis.localStorage,
    `tradeflow:${databaseName}:return-evidence-outbox`,
  );
}
