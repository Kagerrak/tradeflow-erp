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
    .mockReturnValue("fallback-id")
    .mockReturnValueOnce("dc0de2b2-e6d8-4d4f-b898-42398bab8eaa")
    .mockReturnValueOnce("65a4745a-7d07-4cc2-a497-bc27f60be7a0"),
}));

const delivery = {
  assignedTo: "delivery-mnl",
  collectionRequired: false,
  collectionAmountDue: null,
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

it("queues COD cash and proof together without posting while offline", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = jest.fn(
    async () => new Response(new Uint8Array(12)),
  ) as typeof fetch;
  await render(
    <DeliveryConfirmationCapture
      delivery={{
        ...delivery,
        collectionAmountDue: "224.00",
        collectionRequired: true,
        paymentTimingPolicy: "cash_on_delivery",
      }}
      onSaved={() => {}}
      persistEvidence={async (uri) => uri}
      store={store}
    />,
  );
  expect(
    screen.getByRole("header", {
      name: "Capture COD payment and Proof of Delivery",
    }),
  ).toBeOnTheScreen();
  expect(screen.getByText("COD due: PHP 224.00")).toBeOnTheScreen();
  await fireEvent.press(screen.getByText("CAPTURE SIGNATURE"));
  await fireEvent.press(screen.getByText("SAVE COD TO PENDING SYNC"));
  expect(await store.listPending()).toEqual([
    expect.objectContaining({
      command: expect.objectContaining({
        collection: expect.objectContaining({
          amount: "224.00",
          payment_method: "cash",
        }),
      }),
    }),
  ]);
  globalThis.fetch = originalFetch;
});

it("links a cleared non-cash receipt or approved On Account conversion", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = jest.fn(
    async () => new Response(new Uint8Array(12)),
  ) as typeof fetch;
  try {
    const noncash = await render(
      <DeliveryConfirmationCapture
        delivery={{
          ...delivery,
          collectionAmountDue: "224.00",
          collectionRequired: true,
          paymentTimingPolicy: "cash_on_delivery",
        }}
        onSaved={() => {}}
        persistEvidence={async (uri) => uri}
        store={store}
      />,
    );
    await fireEvent.press(screen.getByText("CAPTURE SIGNATURE"));
    await fireEvent.press(screen.getByText("○ NONCASH"));
    await fireEvent.changeText(
      screen.getByLabelText("Cleared Payment Receipt ID"),
      "7bf0d080-e08d-4bac-8375-0a6c2c914029",
    );
    await fireEvent.press(screen.getByText("SAVE COD TO PENDING SYNC"));
    expect((await store.listPending())[0]?.command.collection).toMatchObject({
      payment_method: "bank_transfer",
      payment_receipt_id: "7bf0d080-e08d-4bac-8375-0a6c2c914029",
    });
    await noncash.unmount();

    const conversionStore = createMemoryDeliveryConfirmationStore();
    await render(
      <DeliveryConfirmationCapture
        delivery={{
          ...delivery,
          collectionAmountDue: "224.00",
          collectionRequired: true,
          paymentTimingPolicy: "cash_on_delivery",
        }}
        onSaved={() => {}}
        persistEvidence={async (uri) => uri}
        store={conversionStore}
      />,
    );
    await fireEvent.press(screen.getByText("CAPTURE SIGNATURE"));
    await fireEvent.press(screen.getByText("○ ON ACCOUNT"));
    await fireEvent.changeText(
      screen.getByLabelText("Approved On Account conversion ID"),
      "a704d621-df0d-487c-8b84-e822d049b411",
    );
    await fireEvent.press(screen.getByText("SAVE COD TO PENDING SYNC"));
    expect((await conversionStore.listPending())[0]?.command).toMatchObject({
      on_account_conversion_id: "a704d621-df0d-487c-8b84-e822d049b411",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

it("rejects malformed COD amounts before durable enqueue", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = jest.fn(
    async () => new Response(new Uint8Array(12)),
  ) as typeof fetch;
  try {
    await render(
      <DeliveryConfirmationCapture
        delivery={{
          ...delivery,
          collectionAmountDue: "224.00",
          collectionRequired: true,
          paymentTimingPolicy: "cash_on_delivery",
        }}
        onSaved={() => {}}
        persistEvidence={async (uri) => uri}
        store={store}
      />,
    );
    await fireEvent.press(screen.getByText("CAPTURE SIGNATURE"));
    await fireEvent.changeText(
      screen.getByLabelText("COD amount collected"),
      ".",
    );
    await fireEvent.press(screen.getByText("SAVE COD TO PENDING SYNC"));
    expect(
      screen.getByText(/Collection must be a positive decimal/),
    ).toBeOnTheScreen();
    expect(await store.listPending()).toEqual([]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
