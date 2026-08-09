import { fireEvent, render, screen } from "@testing-library/react-native";

import {
  createMemoryDeliveryConfirmationStore,
  type DeliveryConfirmationStore,
} from "../offline/delivery-confirmation-store";
import { DeliveryConfirmationStatus } from "./delivery-confirmation-status";

async function queued(): Promise<DeliveryConfirmationStore> {
  const store = createMemoryDeliveryConfirmationStore();
  await store.saveAndEnqueue(
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
  return store;
}

it("renders the empty operational state", async () => {
  await render(
    <DeliveryConfirmationStatus
      onSync={async () => {}}
      store={createMemoryDeliveryConfirmationStore()}
    />,
  );
  expect(
    await screen.findByRole("header", { name: "No captured confirmations" }),
  ).toBeOnTheScreen();
});

it.each([
  ["pending_upload", "Pending Sync"],
  ["upload_failed", "Upload failed — evidence retained"],
  ["conflict", "Confirmation conflict — review required"],
  ["forbidden", "Confirmation forbidden"],
] as const)("renders the %s operational state", async (state, title) => {
  const store = await queued();
  if (state !== "pending_upload") {
    await store.markState(1, state, "server-reference", "2026-08-01T13:02:00Z");
  }
  await render(
    <DeliveryConfirmationStatus onSync={async () => {}} store={store} />,
  );
  expect(await screen.findByRole("header", { name: title })).toBeOnTheScreen();
});

it("opens explicit conflict review without discarding original evidence", async () => {
  const store = await queued();
  await store.markState(
    1,
    "conflict",
    "server-reference",
    "2026-08-01T13:02:00Z",
    "delivery_version_conflict",
    "Refresh before replacement.",
  );
  const review = jest.fn(async () => {});
  await render(
    <DeliveryConfirmationStatus
      onReviewConflict={review}
      onSync={async () => {}}
      store={store}
    />,
  );
  await fireEvent.press(await screen.findByText("REVIEW AND REPLACE"));
  expect(review).toHaveBeenCalledWith(
    expect.objectContaining({
      errorCode: "delivery_version_conflict",
      evidence: [expect.objectContaining({ kind: "signature" })],
    }),
  );
});

it("tells the user to sign in and keeps retryable proof available to sync", async () => {
  const store = await queued();
  await store.markRetryableAuth(
    1,
    "auth-reference",
    "2026-08-01T13:02:00Z",
    "authentication_required",
    "Token expired.",
  );
  await render(
    <DeliveryConfirmationStatus onSync={async () => {}} store={store} />,
  );
  expect(
    await screen.findByRole("header", {
      name: "Sign in required — Pending Sync retained",
    }),
  ).toBeOnTheScreen();
  expect(
    screen.getByText(/Sign in again, then sync pending proof/),
  ).toBeOnTheScreen();
  expect(screen.getByText("SYNC PENDING PROOF")).toBeOnTheScreen();
  expect(await store.listPending()).toHaveLength(1);
});

it("renders a confirmed Delivery while its receipt is rendering", async () => {
  const store = await queued();
  await store.markSynced(
    1,
    {
      confirmation_id: "65a4745a-7d07-4cc2-a497-bc27f60be7a0",
      collection: {
        amount_collected: "224.00",
        amount_due: "224.00",
        application_status: "unapplied",
        cash_reconciliation_status: "pending",
        currency: "PHP",
        payment_method: "cash",
        payment_receipt_id: "7bf0d080-e08d-4bac-8375-0a6c2c914029",
        status: "cleared",
      },
      delivery_id: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
      delivery_receipt: {
        delivery_receipt_id: "d98873ae-7cf1-48b6-b2e5-129d23bd9f81",
        number: "DR-MNL-00000001",
        status: "pending_document",
      },
      lines: [],
      outbox_event_id: "af9cf881-e5af-48b0-95e8-04534241b330",
      status: "confirmed",
      version: 2,
    },
    "2026-08-01T13:03:00Z",
  );
  await render(
    <DeliveryConfirmationStatus
      onRefreshReceipt={async () => ({
        accessUrl: null,
        number: "DR-MNL-00000001",
        status: "unavailable",
      })}
      onSync={async () => {}}
      store={store}
    />,
  );
  expect(
    await screen.findByRole("header", { name: "Delivery confirmed" }),
  ).toBeOnTheScreen();
  expect(screen.getByText(/Receipt unavailable/)).toBeOnTheScreen();
  expect(screen.getByText(/PHP 224.00 cleared/)).toBeOnTheScreen();
  await fireEvent.press(screen.getByText("REFRESH RECEIPT"));
  expect(
    await screen.findByText(/Receipt rendering unavailable/),
  ).toBeOnTheScreen();
});
