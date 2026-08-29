import { fireEvent, render, screen } from "@testing-library/react-native";

import { createMemoryReturnReceiptStore } from "../offline/return-receipt-store";
import { ReturnReceiptCapture } from "./return-receipt-capture";

jest.mock("expo-image-picker", () => ({
  launchCameraAsync: jest.fn(async () => ({
    assets: [{ mimeType: "image/png", uri: "file:///proof/photo.png" }],
    canceled: false,
  })),
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
}));

jest.mock("expo-crypto", () => ({
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  digest: jest.fn(async () => new Uint8Array(32).fill(0xaa).buffer),
  randomUUID: jest
    .fn()
    .mockReturnValue("fallback-id")
    .mockReturnValueOnce("dc0de2b2-e6d8-4d4f-b898-42398bab8eaa")
    .mockReturnValueOnce("65a4745a-7d07-4cc2-a497-bc27f60be7a0"),
}));

const request = {
  affected_value_base_currency: "1000.00",
  authorized_at: "2026-08-01T10:00:00Z",
  authorized_by: "manager-mnl",
  base_currency: "PHP",
  branch_id: "b1b7b1b7-b1b7-b1b7-b1b7-b1b7b1b7b1b7",
  confirmation_id: "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1",
  delivery_id: "d1d1d1d1-d1d1-d1d1-d1d1-d1d1d1d1d1d1",
  delivery_receipt_id: "r1r1r1r1-r1r1-r1r1-r1r1-r1r1r1r1r1r1",
  lines: [
    {
      delivered_quantity_base: "2.000000",
      delivery_line_id: "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9",
      eligible_quantity_base: "2.000000",
      line_id: "4af0c99a-b55d-4f68-bf34-6f0805630032",
      quantity_base: "2.000000",
      return_request_line_id: "return-line-1",
      sku_id: "37989314-b1b7-4bea-9c68-b6390ddae80f",
    },
  ],
  notes: null,
  reason_code: "damaged_in_transit",
  reason_label: "Damaged in transit",
  requested_at: "2026-08-01T09:00:00Z",
  requested_by: "sales-mnl",
  responsible_party_code: "carrier",
  responsible_party_label: "Carrier",
  return_request_id: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
  status: "authorized" as const,
  version: 2,
  warehouse_id: "w1w1w1w1-w1w1-w1w1-w1w1-w1w1w1w1w1w1",
};

it("captures inspection photo and durably queues one stable receipt", async () => {
  const store = createMemoryReturnReceiptStore();
  const onSaved = jest.fn();
  const persistEvidence = jest.fn(
    async (_uri: string, evidenceId: string) =>
      `file:///documents/return-evidence/${evidenceId}.png`,
  );
  const originalFetch = globalThis.fetch;
  globalThis.fetch = jest.fn(
    async () => new Response(new Uint8Array(12)),
  ) as typeof fetch;
  try {
    await render(
      <ReturnReceiptCapture
        now={() => "2026-08-01T13:00:00Z"}
        onSaved={onSaved}
        persistEvidence={persistEvidence}
        request={request}
        store={store}
      />,
    );
    await fireEvent.press(screen.getByText("ADD INSPECTION PHOTO"));
    expect(await screen.findByText("1 inspection photo")).toBeOnTheScreen();
    await fireEvent.press(screen.getByText("SAVE TO PENDING SYNC"));
    await screen.findByText("1 inspection photo");
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(persistEvidence).toHaveBeenCalledWith(
      "file:///proof/photo.png",
      "dc0de2b2-e6d8-4d4f-b898-42398bab8eaa",
      "png",
    );
    expect(await store.listPending()).toEqual([
      expect.objectContaining({
        idempotencyKey: "return-receipt:65a4745a-7d07-4cc2-a497-bc27f60be7a0",
        receiptId: "65a4745a-7d07-4cc2-a497-bc27f60be7a0",
        requestId: request.return_request_id,
      }),
    ]);
    expect(
      await store.load("65a4745a-7d07-4cc2-a497-bc27f60be7a0"),
    ).toMatchObject({
      command: expect.objectContaining({
        expected_request_version: 2,
        lines: [
          expect.objectContaining({
            outcome: "restock",
            received_quantity_base: "2.000000",
            return_request_line_id: request.lines[0]!.return_request_line_id,
          }),
        ],
      }),
      status: "pending_upload",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

it("blocks save without inspection photos", async () => {
  const store = createMemoryReturnReceiptStore();
  await render(
    <ReturnReceiptCapture
      now={() => "2026-08-01T13:00:00Z"}
      onSaved={() => {}}
      persistEvidence={async (uri) => uri}
      request={request}
      store={store}
    />,
  );
  await fireEvent.press(screen.getByText("SAVE TO PENDING SYNC"));
  expect(
    screen.getByText("At least one inspection photo is required."),
  ).toBeOnTheScreen();
  expect(await store.listPending()).toEqual([]);
});

it("captures a rejected line with zero received quantity", async () => {
  const store = createMemoryReturnReceiptStore();
  const onSaved = jest.fn();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = jest.fn(
    async () => new Response(new Uint8Array(12)),
  ) as typeof fetch;
  try {
    await render(
      <ReturnReceiptCapture
        now={() => "2026-08-01T13:00:00Z"}
        onSaved={onSaved}
        persistEvidence={async (uri) => uri}
        request={request}
        store={store}
      />,
    );
    await fireEvent.press(screen.getByText("ADD INSPECTION PHOTO"));
    await fireEvent.press(screen.getByText("○ REJECTED"));
    await fireEvent.press(screen.getByText("SAVE TO PENDING SYNC"));
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect((await store.listPending())[0]?.command.lines[0]).toMatchObject({
      outcome: "rejected",
      received_quantity_base: "0",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
