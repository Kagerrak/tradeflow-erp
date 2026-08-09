import { createWebDeliveryConfirmationStore } from "./delivery-confirmation-database.web";

it("hydrates the browser Delivery Confirmation outbox from durable storage", async () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
  const first = createWebDeliveryConfirmationStore(storage);
  await first.saveAndEnqueue(
    {
      command: {
        confirmation_id: "65a4745a-7d07-4cc2-a497-bc27f60be7a0",
        device_captured_at: "2026-08-01T13:00:00Z",
        evidence_ids: ["dc0de2b2-e6d8-4d4f-b898-42398bab8eaa"],
        expected_delivery_version: 1,
        lines: [
          {
            accepted_quantity_base: "1.000000",
            damaged_quantity_base: "0",
            delivery_line_id: "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9",
            exception_details: {},
            identity_partitions: [],
            refused_quantity_base: "0",
            short_missing_quantity_base: "0",
            still_undelivered_quantity_base: "0",
          },
        ],
        recipient_name: "Ana Santos",
      },
      deliveryId: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
      evidence: [
        {
          contentType: "image/png",
          evidenceId: "dc0de2b2-e6d8-4d4f-b898-42398bab8eaa",
          kind: "signature",
          localUri: "file:///proof/signature.png",
          sha256: "a".repeat(64),
          sizeBytes: 100,
          status: "pending_upload",
        },
      ],
      idempotencyKey: "stable-confirmation-key",
    },
    "2026-08-01T13:01:00Z",
  );
  await first.markRetryableAuth(
    1,
    "auth-correlation",
    "2026-08-01T13:02:00Z",
    "authentication_required",
    "Sign in again.",
  );

  const restarted = createWebDeliveryConfirmationStore(storage);
  expect(await restarted.listPending()).toEqual([
    expect.objectContaining({
      confirmationId: "65a4745a-7d07-4cc2-a497-bc27f60be7a0",
      sequence: 1,
    }),
  ]);
  expect(
    await restarted.load("65a4745a-7d07-4cc2-a497-bc27f60be7a0"),
  ).toMatchObject({
    authPaused: true,
    correlationId: "auth-correlation",
    errorCode: "authentication_required",
    status: "pending_upload",
  });
});
