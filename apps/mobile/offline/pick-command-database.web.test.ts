import { createWebPickCommandStore } from "./pick-command-database.web";

const command = {
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

it("hydrates the browser Pick outbox from durable storage", async () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
  const beforeRestart = createWebPickCommandStore(storage, "warehouse-mnl");
  await beforeRestart.saveAndEnqueue(
    "f6a25d93-d412-474a-8e37-f23716579a88",
    command,
    "stable-web-pick",
    "2026-07-29T02:00:00Z",
  );

  const afterRestart = createWebPickCommandStore(storage, "warehouse-mnl");
  expect(await afterRestart.listPending()).toEqual([
    expect.objectContaining({
      command,
      idempotencyKey: "stable-web-pick",
    }),
  ]);
  expect(await afterRestart.load(command.pick_id)).toMatchObject({
    response: null,
    status: "pending_sync",
  });
});
