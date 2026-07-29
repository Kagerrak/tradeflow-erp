import type {
  PaymentReceipt,
  RecordPaymentReceiptInput,
} from "@tradeflow/payment-clearance";

export type LocalPaymentReceiptStatus = "paused" | "pending" | "synced";

export type LocalPaymentReceipt = {
  command: RecordPaymentReceiptInput;
  correlationId: string | null;
  idempotencyKey: string;
  receipt: PaymentReceipt | null;
  receiptId: string;
  status: LocalPaymentReceiptStatus;
  updatedAt: string;
};

export type PaymentReceiptOutboxItem = {
  attemptedAt: string | null;
  command: RecordPaymentReceiptInput;
  idempotencyKey: string;
  receiptId: string;
  sequence: number;
};

export type PaymentReceiptStore = {
  initialize(): Promise<void>;
  listPending(): Promise<PaymentReceiptOutboxItem[]>;
  load(receiptId: string): Promise<LocalPaymentReceipt | null>;
  markAttempted(sequence: number, attemptedAt: string): Promise<void>;
  markPaused(
    sequence: number,
    correlationId: string,
    updatedAt: string,
  ): Promise<void>;
  markSynced(
    sequence: number,
    receipt: PaymentReceipt,
    updatedAt: string,
  ): Promise<void>;
  saveAndEnqueue(
    command: RecordPaymentReceiptInput,
    idempotencyKey: string,
    updatedAt: string,
  ): Promise<LocalPaymentReceipt>;
};

export type MemoryPaymentReceiptBacking = {
  nextSequence: number;
  outbox: Map<number, PaymentReceiptOutboxItem>;
  receipts: Map<string, LocalPaymentReceipt>;
};

export function createMemoryPaymentReceiptBacking(): MemoryPaymentReceiptBacking {
  return {
    nextSequence: 1,
    outbox: new Map(),
    receipts: new Map(),
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function createMemoryPaymentReceiptStore(
  backing = createMemoryPaymentReceiptBacking(),
): PaymentReceiptStore {
  return {
    async initialize() {},
    async listPending() {
      return [...backing.outbox.values()]
        .sort((left, right) => left.sequence - right.sequence)
        .map(clone);
    },
    async load(receiptId) {
      const receipt = backing.receipts.get(receiptId);
      return receipt === undefined ? null : clone(receipt);
    },
    async markAttempted(sequence, attemptedAt) {
      const item = backing.outbox.get(sequence);
      if (item !== undefined) {
        backing.outbox.set(sequence, { ...item, attemptedAt });
      }
    },
    async markPaused(sequence, correlationId, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      const receipt = backing.receipts.get(item.receiptId);
      if (receipt !== undefined) {
        backing.receipts.set(item.receiptId, {
          ...receipt,
          correlationId,
          status: "paused",
          updatedAt,
        });
      }
    },
    async markSynced(sequence, receipt, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      backing.outbox.delete(sequence);
      const local = backing.receipts.get(item.receiptId);
      if (local !== undefined) {
        backing.receipts.set(item.receiptId, {
          ...local,
          correlationId: null,
          receipt: clone(receipt),
          status: "synced",
          updatedAt,
        });
      }
    },
    async saveAndEnqueue(command, idempotencyKey, updatedAt) {
      const receiptId = command.payment_receipt_id;
      const existing = backing.receipts.get(receiptId);
      if (existing !== undefined) return clone(existing);
      const receipt: LocalPaymentReceipt = {
        command: clone(command),
        correlationId: null,
        idempotencyKey,
        receipt: null,
        receiptId,
        status: "pending",
        updatedAt,
      };
      backing.receipts.set(receiptId, receipt);
      const sequence = backing.nextSequence++;
      backing.outbox.set(sequence, {
        attemptedAt: null,
        command: clone(command),
        idempotencyKey,
        receiptId,
        sequence,
      });
      return clone(receipt);
    },
  };
}
