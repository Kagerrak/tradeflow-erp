import type {
  CreateSalesOrderDraftInput,
  OrderEntryReference,
  SalesOrderDraft,
} from "@tradeflow/sales-order-draft";

export type LocalSalesDraftStatus =
  "pending" | "syncing" | "conflict" | "synced";

export type LocalSalesDraft = {
  command: CreateSalesOrderDraftInput;
  conflictCorrelationId: string | null;
  expectedVersion: number | null;
  idempotencyKey: string;
  orderId: string;
  savedDraft: SalesOrderDraft | null;
  status: LocalSalesDraftStatus;
  updatedAt: string;
};

export type SalesDraftOutboxItem = {
  attemptedAt: string | null;
  command: CreateSalesOrderDraftInput;
  expectedVersion: number | null;
  idempotencyKey: string;
  orderId: string;
  sequence: number;
};

export type SalesDraftStore = {
  initialize(): Promise<void>;
  listDrafts(): Promise<LocalSalesDraft[]>;
  loadReference(cacheKey: string): Promise<OrderEntryReference | null>;
  listPending(): Promise<SalesDraftOutboxItem[]>;
  load(orderId: string): Promise<LocalSalesDraft | null>;
  markAttempted(sequence: number, attemptedAt: string): Promise<void>;
  markConflict(
    sequence: number,
    correlationId: string,
    authoritativeDraft: SalesOrderDraft | null,
    updatedAt: string,
  ): Promise<void>;
  markSynced(
    sequence: number,
    draft: SalesOrderDraft,
    updatedAt: string,
  ): Promise<void>;
  retryConflict(
    orderId: string,
    command: CreateSalesOrderDraftInput,
    expectedVersion: number,
    idempotencyKey: string,
    updatedAt: string,
  ): Promise<void>;
  saveReference(
    cacheKey: string,
    reference: OrderEntryReference,
    cachedAt: string,
  ): Promise<void>;
  saveAndEnqueue(
    command: CreateSalesOrderDraftInput,
    idempotencyKey: string,
    updatedAt: string,
  ): Promise<LocalSalesDraft>;
};

export type MemorySalesDraftBacking = {
  drafts: Map<string, LocalSalesDraft>;
  nextSequence: number;
  outbox: Map<number, SalesDraftOutboxItem>;
  references: Map<string, OrderEntryReference>;
};

export function createMemorySalesDraftBacking(): MemorySalesDraftBacking {
  return {
    drafts: new Map(),
    nextSequence: 1,
    outbox: new Map(),
    references: new Map(),
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function createMemorySalesDraftStore(
  backing = createMemorySalesDraftBacking(),
): SalesDraftStore {
  return {
    async initialize() {},
    async listDrafts() {
      return [...backing.drafts.values()]
        .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
        .map(clone);
    },
    async loadReference(cacheKey) {
      const reference = backing.references.get(cacheKey);
      return reference === undefined ? null : clone(reference);
    },
    async listPending() {
      return [...backing.outbox.values()]
        .sort((left, right) => left.sequence - right.sequence)
        .map(clone);
    },
    async load(orderId) {
      const draft = backing.drafts.get(orderId);
      return draft === undefined ? null : clone(draft);
    },
    async markAttempted(sequence, attemptedAt) {
      const item = backing.outbox.get(sequence);
      if (item !== undefined && item.attemptedAt === null) {
        backing.outbox.set(sequence, { ...item, attemptedAt });
      }
    },
    async markConflict(sequence, correlationId, authoritativeDraft, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      const draft = backing.drafts.get(item.orderId);
      if (draft !== undefined) {
        backing.drafts.set(item.orderId, {
          ...draft,
          conflictCorrelationId: correlationId,
          savedDraft: clone(authoritativeDraft),
          status: "conflict",
          updatedAt,
        });
      }
    },
    async markSynced(sequence, savedDraft, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      backing.outbox.delete(sequence);
      const current = backing.drafts.get(item.orderId);
      if (current !== undefined) {
        backing.drafts.set(item.orderId, {
          ...current,
          conflictCorrelationId: null,
          expectedVersion: savedDraft.version,
          savedDraft: clone(savedDraft),
          status: "synced",
          updatedAt,
        });
      }
    },
    async retryConflict(
      orderId,
      command,
      expectedVersion,
      idempotencyKey,
      updatedAt,
    ) {
      const current = backing.drafts.get(orderId);
      if (current === undefined || current.status !== "conflict") return;
      for (const [sequence, item] of backing.outbox) {
        if (item.orderId === orderId) backing.outbox.delete(sequence);
      }
      backing.drafts.set(orderId, {
        ...current,
        command: clone(command),
        conflictCorrelationId: null,
        expectedVersion,
        idempotencyKey,
        status: "pending",
        updatedAt,
      });
      const sequence = backing.nextSequence++;
      backing.outbox.set(sequence, {
        attemptedAt: null,
        command: clone(command),
        expectedVersion,
        idempotencyKey,
        orderId,
        sequence,
      });
    },
    async saveReference(cacheKey, reference) {
      backing.references.set(cacheKey, clone(reference));
    },
    async saveAndEnqueue(command, idempotencyKey, updatedAt) {
      const attempted = [...backing.outbox.values()].find(
        (item) => item.orderId === command.sales_order_id && item.attemptedAt,
      );
      if (attempted !== undefined) {
        const existing = backing.drafts.get(command.sales_order_id);
        if (existing !== undefined) return clone(existing);
      }
      const current = backing.drafts.get(command.sales_order_id);
      const expectedVersion = current?.savedDraft?.version ?? null;
      const orderId = command.sales_order_id;
      const draft: LocalSalesDraft = {
        command: clone(command),
        conflictCorrelationId: null,
        expectedVersion,
        idempotencyKey,
        orderId,
        savedDraft: current?.savedDraft ?? null,
        status: "pending",
        updatedAt,
      };
      backing.drafts.set(orderId, draft);
      const existingPending = [...backing.outbox.values()].find(
        (item) => item.orderId === orderId && item.attemptedAt === null,
      );
      if (existingPending === undefined) {
        const sequence = backing.nextSequence++;
        backing.outbox.set(sequence, {
          attemptedAt: null,
          command: clone(command),
          expectedVersion,
          idempotencyKey,
          orderId,
          sequence,
        });
      } else {
        backing.outbox.set(existingPending.sequence, {
          ...existingPending,
          command: clone(command),
          expectedVersion,
          idempotencyKey,
        });
      }
      return clone(draft);
    },
  };
}
