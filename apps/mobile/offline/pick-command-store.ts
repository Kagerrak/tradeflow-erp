import type { components } from "@tradeflow/api-client";

export type PickSelection = components["schemas"]["PickSelectionInput"];
export type PickCommandLine = components["schemas"]["PickLineInput"];
export type PickCommand = components["schemas"]["PostPickCommand"];
export type PickResponse = components["schemas"]["PickResponse"];

export type LocalPickStatus =
  | "complete"
  | "conflict"
  | "forbidden"
  | "partially_picked"
  | "pending_sync"
  | "reversed"
  | "scan_denied";

export type LocalPickCommand = {
  command: PickCommand;
  correlationId: string | null;
  fulfillmentOrderId: string;
  idempotencyKey: string;
  pickId: string;
  response: PickResponse | null;
  status: LocalPickStatus;
  updatedAt: string;
};

export type PickCommandOutboxItem = {
  attemptedAt: string | null;
  command: PickCommand;
  fulfillmentOrderId: string;
  idempotencyKey: string;
  pickId: string;
  sequence: number;
};

export type PickCommandStore = {
  initialize(): Promise<void>;
  listCaptures(): Promise<LocalPickCommand[]>;
  listPending(): Promise<PickCommandOutboxItem[]>;
  load(pickId: string): Promise<LocalPickCommand | null>;
  markAttempted(sequence: number, attemptedAt: string): Promise<void>;
  markState(
    sequence: number,
    status: Exclude<
      LocalPickStatus,
      "complete" | "partially_picked" | "pending_sync"
    >,
    correlationId: string,
    updatedAt: string,
  ): Promise<void>;
  markSynced(
    sequence: number,
    response: PickResponse,
    updatedAt: string,
  ): Promise<void>;
  markReversed(
    pickId: string,
    correlationId: string,
    updatedAt: string,
  ): Promise<void>;
  saveAndEnqueue(
    fulfillmentOrderId: string,
    command: PickCommand,
    idempotencyKey: string,
    updatedAt: string,
  ): Promise<LocalPickCommand>;
};

export type MemoryPickCommandBacking = {
  captures: Map<string, LocalPickCommand>;
  nextSequence: number;
  outbox: Map<number, PickCommandOutboxItem>;
};

export function createMemoryPickCommandBacking(): MemoryPickCommandBacking {
  return {
    captures: new Map(),
    nextSequence: 1,
    outbox: new Map(),
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function statusFromResponse(response: PickResponse): LocalPickStatus {
  return response.remaining_quantity_base === "0.000000" ||
    ["complete", "picked"].includes(response.status)
    ? "complete"
    : "partially_picked";
}

export function createMemoryPickCommandStore(
  backing = createMemoryPickCommandBacking(),
): PickCommandStore {
  return {
    async initialize() {},
    async listCaptures() {
      return [...backing.captures.values()]
        .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
        .map(clone);
    },
    async listPending() {
      return [...backing.outbox.values()]
        .sort((left, right) => left.sequence - right.sequence)
        .map(clone);
    },
    async load(pickId) {
      const capture = backing.captures.get(pickId);
      return capture === undefined ? null : clone(capture);
    },
    async markAttempted(sequence, attemptedAt) {
      const item = backing.outbox.get(sequence);
      if (item !== undefined && item.attemptedAt === null) {
        backing.outbox.set(sequence, { ...item, attemptedAt });
      }
    },
    async markState(sequence, status, correlationId, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      backing.outbox.delete(sequence);
      const capture = backing.captures.get(item.pickId);
      if (capture !== undefined) {
        backing.captures.set(item.pickId, {
          ...capture,
          correlationId,
          status,
          updatedAt,
        });
      }
    },
    async markSynced(sequence, response, updatedAt) {
      const item = backing.outbox.get(sequence);
      if (item === undefined) return;
      backing.outbox.delete(sequence);
      const capture = backing.captures.get(item.pickId);
      if (capture !== undefined) {
        backing.captures.set(item.pickId, {
          ...capture,
          correlationId: null,
          response: clone(response),
          status: statusFromResponse(response),
          updatedAt,
        });
      }
    },
    async markReversed(pickId, correlationId, updatedAt) {
      const capture = backing.captures.get(pickId);
      if (capture !== undefined) {
        backing.captures.set(pickId, {
          ...capture,
          correlationId,
          status: "reversed",
          updatedAt,
        });
      }
    },
    async saveAndEnqueue(
      fulfillmentOrderId,
      command,
      idempotencyKey,
      updatedAt,
    ) {
      const existing = backing.captures.get(command.pick_id);
      if (existing !== undefined) return clone(existing);
      const capture: LocalPickCommand = {
        command: clone(command),
        correlationId: null,
        fulfillmentOrderId,
        idempotencyKey,
        pickId: command.pick_id,
        response: null,
        status: "pending_sync",
        updatedAt,
      };
      backing.captures.set(command.pick_id, capture);
      const sequence = backing.nextSequence++;
      backing.outbox.set(sequence, {
        attemptedAt: null,
        command: clone(command),
        fulfillmentOrderId,
        idempotencyKey,
        pickId: command.pick_id,
        sequence,
      });
      return clone(capture);
    },
  };
}

export { statusFromResponse };
