import type { CreateSalesOrderDraftInput } from "@tradeflow/sales-order-draft";

import {
  createMemorySalesDraftBacking,
  createMemorySalesDraftStore,
} from "./sales-draft-store";
import { syncSalesDrafts } from "./sales-draft-sync";

const command: CreateSalesOrderDraftInput = {
  branch_id: "efad4205-5060-49fb-b752-3faca649ca6e",
  customer_id: "98481a1c-e493-41a6-851b-93142553ceab",
  expected_customer_version: 1,
  expected_price_list_version_id: "2903b3b0-608f-4caf-907a-0dd0886bb8f7",
  expected_pricing_date: "2026-07-29",
  delivery_address_version_id: "4d8ad09a-f96f-41b3-b30a-0af843353943",
  lines: [
    {
      expected_price_list_line_id: "d60c173e-efec-4b3a-b1c6-1e893e4cdfff",
      expected_unit_conversion_id: null,
      expected_unit_conversion_version: null,
      line_id: "a5551b35-34ff-4cb8-8062-d6386f7e4e25",
      manual_override_unit_price: null,
      price_override_reason: null,
      quantity: "3.000000",
      sku_id: "4d209f00-0c57-49fc-9f0b-fc5cf082cb02",
      unit_code: "EA",
    },
  ],
  order_discount_amount: "0.030000",
  payment_timing_override_reason: null,
  payment_timing_policy: null,
  sales_order_id: "323484f7-f3b5-4070-846f-83b9aad4fadb",
};

const saved = {
  branch_id: command.branch_id,
  currency: "PHP",
  customer_id: command.customer_id,
  customer_version: 1,
  delivery_address_snapshot: { line_1: "100 Draft Street" },
  discount_total: "0.03",
  grand_total: "31.89",
  lines: [
    {
      allocated_discount: "0.03",
      below_floor: false,
      entered_quantity: "3.000000",
      entered_unit: "EA",
      line_id: command.lines[0]!.line_id,
      line_position: 1,
      line_total: "31.89",
      list_unit_price: "9.500000",
      price_list_code: "MNL-CUSTOMER",
      price_list_version: 1,
      price_source: "customer",
      sku_code: "COLA-330",
      sku_name: "Cola 330 SKU",
      tax_amount: "3.42",
    },
  ],
  payment_timing_override_reason: null,
  payment_timing_policy: "prepaid",
  price_inclusion_mode: "exclusive",
  price_list_code: "MNL-CUSTOMER",
  sales_order_id: command.sales_order_id,
  status: "draft",
  subtotal: "28.50",
  tax_total: "3.42",
  version: 1,
};

it("atomically persists a draft and immutable FIFO outbox command", async () => {
  const store = createMemorySalesDraftStore();
  await store.saveAndEnqueue(command, "stable-key", "2026-07-29T01:00:00Z");
  const pending = await store.listPending();
  expect(pending).toHaveLength(1);
  expect(pending[0]).toMatchObject({
    attemptedAt: null,
    command,
    idempotencyKey: "stable-key",
    sequence: 1,
  });
  expect(await store.load(command.sales_order_id)).toMatchObject({
    command,
    status: "pending",
  });
});

it("coalesces pre-attempt edits into one create command", async () => {
  const store = createMemorySalesDraftStore();
  await store.saveAndEnqueue(command, "first-key", "2026-07-29T01:00:00Z");
  await store.saveAndEnqueue(
    {
      ...command,
      lines: [{ ...command.lines[0]!, quantity: "4.000000" }],
    },
    "latest-key",
    "2026-07-29T01:01:00Z",
  );
  expect(await store.listPending()).toEqual([
    expect.objectContaining({
      command: expect.objectContaining({
        lines: [expect.objectContaining({ quantity: "4.000000" })],
      }),
      idempotencyKey: "latest-key",
      sequence: 1,
    }),
  ]);
});

it("replays the same command after repository restart and a lost response", async () => {
  const backing = createMemorySalesDraftBacking();
  const beforeRestart = createMemorySalesDraftStore(backing);
  await beforeRestart.saveAndEnqueue(
    command,
    "lost-response-key",
    "2026-07-29T01:00:00Z",
  );
  await syncSalesDrafts({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "first-attempt",
    fetch: async () => {
      throw new TypeError("connection closed after send");
    },
    now: () => "2026-07-29T01:01:00Z",
    store: beforeRestart,
  });

  const afterRestart = createMemorySalesDraftStore(backing);
  const requests: Request[] = [];
  const result = await syncSalesDrafts({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "replay-attempt",
    fetch: async (request) => {
      requests.push(request);
      return new Response(JSON.stringify(saved), {
        headers: { "content-type": "application/json" },
        status: 201,
      });
    },
    now: () => "2026-07-29T01:02:00Z",
    store: afterRestart,
  });
  expect(result).toEqual({ count: 1, kind: "synced" });
  expect(requests[0]?.headers.get("Idempotency-Key")).toBe("lost-response-key");
  expect(await afterRestart.load(command.sales_order_id)).toMatchObject({
    savedDraft: { version: 1 },
    status: "synced",
  });
  expect(await afterRestart.listPending()).toEqual([]);
});

it("stops FIFO synchronization on an explicit server conflict", async () => {
  const store = createMemorySalesDraftStore();
  await store.saveAndEnqueue(command, "conflict-key", "2026-07-29T01:00:00Z");
  const result = await syncSalesDrafts({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "conflict-reference",
    fetch: async () => new Response("{}", { status: 409 }),
    store,
  });
  expect(result).toEqual({ kind: "paused", reason: "conflict" });
  expect(await store.load(command.sales_order_id)).toMatchObject({
    conflictCorrelationId: "conflict-reference",
    status: "conflict",
  });
  expect(await store.listPending()).toHaveLength(1);
});
