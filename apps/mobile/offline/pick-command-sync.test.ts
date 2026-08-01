import {
  createMemoryPickCommandBacking,
  createMemoryPickCommandStore,
  type PickCommand,
  type PickCommandStore,
} from "./pick-command-store";
import { reverseSyncedPick, syncPickCommands } from "./pick-command-sync";

const command: PickCommand = {
  expected_fulfillment_version: 2,
  lines: [
    {
      line_id: "4a7f72bc-9172-455f-adca-5472c655e658",
      quantity: "1.000000",
      selections: [{ barcode: "480000000003" }],
      unit_code: "EA",
    },
  ],
  pick_id: "5b914dde-9a1f-45d7-b7a9-cb5ff8a8b458",
};

const fulfillmentOrderId = "f6a25d93-d412-474a-8e37-f23716579a88";

const partialResponse = {
  fulfillment_order_id: fulfillmentOrderId,
  lines: [
    {
      line_id: command.lines[0]!.line_id,
      lot_selections: [],
      quantity_base: "1.000000",
      serial_selections: ["SN-001"],
      sku_id: "0317e7d8-33b8-4742-b58d-72dd68f6a541",
      source_movement_id: "cd182893-58a9-4b57-8546-8fc5dca05d54",
      staging_movement_id: "da7e852e-10c8-4f47-bad2-75857285dd80",
    },
  ],
  pick_id: command.pick_id,
  picked_quantity_base: "1.000000",
  remaining_quantity_base: "1.000000",
  status: "partially_picked",
  version: 3,
};

async function queuedStore(): Promise<PickCommandStore> {
  const store = createMemoryPickCommandStore();
  await store.saveAndEnqueue(
    fulfillmentOrderId,
    command,
    "stable-pick-key",
    "2026-07-29T02:00:00Z",
  );
  return store;
}

it("durably retains a Pick command across process restart without staging stock", async () => {
  const backing = createMemoryPickCommandBacking();
  const beforeRestart = createMemoryPickCommandStore(backing);
  await beforeRestart.saveAndEnqueue(
    fulfillmentOrderId,
    command,
    "restart-pick-key",
    "2026-07-29T02:00:00Z",
  );

  const afterRestart = createMemoryPickCommandStore(backing);
  expect(await afterRestart.listPending()).toEqual([
    expect.objectContaining({
      attemptedAt: null,
      command,
      fulfillmentOrderId,
      idempotencyKey: "restart-pick-key",
      sequence: 1,
    }),
  ]);
  expect(await afterRestart.load(command.pick_id)).toMatchObject({
    response: null,
    status: "pending_sync",
  });
});

it("retries a lost response with the same Pick and idempotency identities", async () => {
  const store = await queuedStore();
  const requests: Request[] = [];
  let attempt = 0;
  const common = {
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => `pick-attempt-${attempt + 1}`,
    fetch: async (request: Request) => {
      requests.push(request);
      attempt += 1;
      if (attempt === 1) throw new TypeError("response lost after send");
      return new Response(JSON.stringify(partialResponse), {
        headers: { "content-type": "application/json" },
        status: 200,
      });
    },
    store,
  };

  expect(await syncPickCommands(common)).toEqual({
    kind: "paused",
    reason: "unavailable",
  });
  expect(await store.load(command.pick_id)).toMatchObject({
    response: null,
    status: "pending_sync",
  });

  expect(await syncPickCommands(common)).toEqual({
    count: 1,
    kind: "synced",
    response: partialResponse,
  });
  expect(
    requests.map((request) => request.headers.get("Idempotency-Key")),
  ).toEqual(["stable-pick-key", "stable-pick-key"]);
  expect(requests.every((request) => request.headers.has("traceparent"))).toBe(
    true,
  );
  expect(requests.map((request) => request.method)).toEqual(["POST", "POST"]);
  expect(requests.map((request) => request.url)).toEqual([
    `https://api.test/v1/fulfillment/orders/${fulfillmentOrderId}/picks`,
    `https://api.test/v1/fulfillment/orders/${fulfillmentOrderId}/picks`,
  ]);
  expect(await store.load(command.pick_id)).toMatchObject({
    response: partialResponse,
    status: "partially_picked",
  });
  expect(await store.listPending()).toEqual([]);
});

it.each([
  [403, "operational_scope_required", "forbidden"],
  [409, "optimistic_version_conflict", "conflict"],
  [409, "serial_already_picked", "scan_denied"],
  [409, "pick_reversed", "reversed"],
] as const)(
  "maps server %s %s into the explicit %s state",
  async (status, code, expectedState) => {
    const store = await queuedStore();
    const result = await syncPickCommands({
      accessToken: "token",
      baseUrl: "https://api.test",
      createCorrelationId: () => "server-reference",
      fetch: async () =>
        new Response(
          JSON.stringify({
            error: {
              code,
              correlation_id: "authoritative-reference",
              message: code,
            },
          }),
          { headers: { "content-type": "application/json" }, status },
        ),
      store,
    });

    expect(result).toEqual({ kind: "paused", reason: expectedState });
    expect(await store.load(command.pick_id)).toMatchObject({
      correlationId: "authoritative-reference",
      response: null,
      status: expectedState,
    });
    expect(await store.listPending()).toEqual([]);
  },
);

it("marks a server-acknowledged final Pick complete", async () => {
  const store = await queuedStore();
  const complete = {
    ...partialResponse,
    picked_quantity_base: "2.000000",
    remaining_quantity_base: "0.000000",
    status: "picked",
    version: 4,
  };
  await syncPickCommands({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "complete-reference",
    fetch: async () =>
      new Response(JSON.stringify(complete), {
        headers: { "content-type": "application/json" },
        status: 201,
      }),
    store,
  });
  expect(await store.load(command.pick_id)).toMatchObject({
    response: complete,
    status: "complete",
  });
});

it("posts a reasoned reversal and durably marks the original capture reversed", async () => {
  const store = await queuedStore();
  await syncPickCommands({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "pick-reference",
    fetch: async () =>
      new Response(JSON.stringify(partialResponse), { status: 201 }),
    store,
  });
  const requests: Request[] = [];
  const result = await reverseSyncedPick({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "reversal-reference",
    expectedVersion: 3,
    fetch: async (request) => {
      requests.push(request);
      return new Response(
        JSON.stringify({
          fulfillment_order_id: fulfillmentOrderId,
          original_pick_id: command.pick_id,
          reversal_pick_id: "reversal-pick-id",
          reversed_quantity_base: "1.000000",
          source_movement_ids: ["source-reversal"],
          staging_movement_ids: ["staging-reversal"],
          status: "reversed",
          version: 4,
        }),
        {
          headers: { "X-Correlation-ID": "reversal-ack" },
          status: 201,
        },
      );
    },
    idempotencyKey: "stable-reversal-key",
    pickId: command.pick_id,
    reason: "Damaged tote before dispatch",
    reversalPickId: "reversal-pick-id",
    store,
  });
  expect(result).toEqual({
    correlationId: "reversal-ack",
    kind: "reversed",
  });
  expect(await requests[0]!.json()).toEqual({
    expected_fulfillment_version: 3,
    reason: "Damaged tote before dispatch",
    reversal_pick_id: "reversal-pick-id",
  });
  expect(requests[0]!.headers.get("Idempotency-Key")).toBe(
    "stable-reversal-key",
  );
  expect(requests[0]!.headers.has("traceparent")).toBe(true);
  expect(await store.load(command.pick_id)).toMatchObject({
    correlationId: "reversal-ack",
    status: "reversed",
  });
});
