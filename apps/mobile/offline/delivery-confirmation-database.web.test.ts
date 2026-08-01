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
            line_id: "4af0c99a-b55d-4f68-bf34-6f0805630032",
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

  const restarted = createWebDeliveryConfirmationStore(storage);
  expect(await restarted.listPending()).toEqual([
    expect.objectContaining({
      confirmationId: "65a4745a-7d07-4cc2-a497-bc27f60be7a0",
      sequence: 1,
    }),
  ]);
});
