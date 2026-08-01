import { fireEvent, render, screen } from "@testing-library/react-native";

import { createMemoryDeliveryConfirmationStore } from "../offline/delivery-confirmation-store";
import { DeliveryConfirmationCapture } from "./delivery-confirmation-capture";

jest.mock("expo-image-picker", () => ({
  launchCameraAsync: jest.fn(async () => ({
    assets: [{ mimeType: "image/png", uri: "file:///proof/signature.png" }],
    canceled: false,
  })),
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
}));

jest.mock("expo-crypto", () => ({
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  digest: jest.fn(async () => new Uint8Array(32).fill(0xaa).buffer),
  randomUUID: jest
    .fn()
    .mockReturnValueOnce("dc0de2b2-e6d8-4d4f-b898-42398bab8eaa")
    .mockReturnValueOnce("65a4745a-7d07-4cc2-a497-bc27f60be7a0"),
}));

const delivery = {
  assignedTo: "delivery-mnl",
  collectionRequired: false,
  deliveryAddress: { city: "Manila" },
  deliveryId: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
  evidenceRequirements: ["recipient_name", "signature"],
  fulfillmentOrderId: "765b5ab6-7f39-4671-8561-747755641016",
  lines: [
    {
      lineId: "4af0c99a-b55d-4f68-bf34-6f0805630032",
      lotSelections: [],
      quantityBase: "2.000000",
      serialNumbers: [],
      skuCode: "JUICE-1L",
      skuId: "37989314-b1b7-4bea-9c68-b6390ddae80f",
      skuName: "Mango Juice 1L",
    },
  ],
  paymentTimingPolicy: "prepaid" as const,
  recipientName: "Ana Santos",
  status: "dispatched" as const,
  version: 1,
};

it("captures signature proof and durably queues one stable confirmation", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  const onSaved = jest.fn();
  const persistEvidence = jest.fn(
    async (_uri: string, evidenceId: string) =>
      `file:///documents/delivery-evidence/${evidenceId}.png`,
  );
  const originalFetch = globalThis.fetch;
  globalThis.fetch = jest.fn(
    async () => new Response(new Uint8Array(12)),
  ) as typeof fetch;
  try {
    await render(
      <DeliveryConfirmationCapture
        delivery={delivery}
        now={() => "2026-08-01T13:00:00Z"}
        onSaved={onSaved}
        persistEvidence={persistEvidence}
        store={store}
      />,
    );
    await fireEvent.press(screen.getByText("CAPTURE SIGNATURE"));
    expect(
      await screen.findByText("Signature captured · 0 photos"),
    ).toBeOnTheScreen();
    await fireEvent.press(screen.getByText("SAVE TO PENDING SYNC"));
    await screen.findByText("Signature captured · 0 photos");
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(persistEvidence).toHaveBeenCalledWith(
      "file:///proof/signature.png",
      "dc0de2b2-e6d8-4d4f-b898-42398bab8eaa",
      "png",
    );
    expect(await store.listPending()).toEqual([
      expect.objectContaining({
        confirmationId: "65a4745a-7d07-4cc2-a497-bc27f60be7a0",
        deliveryId: delivery.deliveryId,
        idempotencyKey:
          "delivery-confirmation:65a4745a-7d07-4cc2-a497-bc27f60be7a0",
      }),
    ]);
    expect(
      await store.load("65a4745a-7d07-4cc2-a497-bc27f60be7a0"),
    ).toMatchObject({ status: "pending_upload" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

it("routes COD proof to the atomic collection workflow", async () => {
  await render(
    <DeliveryConfirmationCapture
      delivery={{ ...delivery, collectionRequired: true }}
      onSaved={() => {}}
      persistEvidence={async (uri) => uri}
      store={createMemoryDeliveryConfirmationStore()}
    />,
  );
  expect(
    screen.getByRole("header", {
      name: "Collection required before confirmation",
    }),
  ).toBeOnTheScreen();
  expect(screen.queryByText("SAVE TO PENDING SYNC")).not.toBeOnTheScreen();
});
