import {
  createMemoryDeliveryConfirmationBacking,
  createMemoryDeliveryConfirmationStore,
  type DeliveryConfirmationCapture,
} from "./delivery-confirmation-store";
import { syncDeliveryConfirmations } from "./delivery-confirmation-sync";

const deliveryId = "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef";
const confirmationId = "65a4745a-7d07-4cc2-a497-bc27f60be7a0";
const evidenceId = "dc0de2b2-e6d8-4d4f-b898-42398bab8eaa";

const capture: DeliveryConfirmationCapture = {
  command: {
    confirmation_id: confirmationId,
    device_captured_at: "2026-08-01T13:00:00Z",
    evidence_ids: [evidenceId],
    expected_delivery_version: 1,
    lines: [
      {
        accepted_quantity_base: "2.000000",
        line_id: "4af0c99a-b55d-4f68-bf34-6f0805630032",
      },
    ],
    notes: "Two sealed cartons accepted.",
    recipient_name: "Ana Santos",
  },
  deliveryId,
  evidence: [
    {
      contentType: "image/png",
      evidenceId,
      kind: "signature",
      localUri: "file:///proof/signature.png",
      sha256: "a".repeat(64),
      sizeBytes: 128,
      status: "pending_upload",
    },
  ],
  idempotencyKey: "stable-confirmation-key",
};

it("restores Pending Sync evidence and confirmation identities after restart", async () => {
  const backing = createMemoryDeliveryConfirmationBacking();
  const beforeRestart = createMemoryDeliveryConfirmationStore(backing);
  await beforeRestart.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");

  const afterRestart = createMemoryDeliveryConfirmationStore(backing);
  expect(await afterRestart.listPending()).toEqual([
    expect.objectContaining({
      confirmationId,
      deliveryId,
      idempotencyKey: "stable-confirmation-key",
      sequence: 1,
    }),
  ]);
  expect(await afterRestart.load(confirmationId)).toMatchObject({
    evidence: [
      expect.objectContaining({ evidenceId, status: "pending_upload" }),
    ],
    status: "pending_upload",
  });
});

it("resumes after upload and retries a lost response with the same identities", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  const uploads: string[] = [];
  const requests: Request[] = [];
  let postAttempt = 0;
  const options = {
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => `confirmation-${postAttempt + 1}`,
    fetch: async (request: Request) => {
      requests.push(request);
      postAttempt += 1;
      if (postAttempt === 1) throw new TypeError("response lost after commit");
      return new Response(
        JSON.stringify({
          confirmation_id: confirmationId,
          delivery_id: deliveryId,
          delivery_receipt: {
            delivery_receipt_id: "d98873ae-7cf1-48b6-b2e5-129d23bd9f81",
            number: "DR-MNL-00000001",
            status: "pending_document",
          },
          lines: [],
          outbox_event_id: "af9cf881-e5af-48b0-95e8-04534241b330",
          status: "confirmed",
          version: 2,
        }),
        { headers: { "content-type": "application/json" }, status: 200 },
      );
    },
    now: () => "2026-08-01T13:02:00Z",
    store,
    uploadEvidence: async (evidence: { evidenceId: string }) => {
      uploads.push(evidence.evidenceId);
    },
  };

  expect(await syncDeliveryConfirmations(options)).toEqual({
    kind: "paused",
    reason: "unavailable",
  });
  expect(await store.load(confirmationId)).toMatchObject({
    evidence: [expect.objectContaining({ status: "uploaded" })],
    status: "pending_confirmation",
  });

  expect(await syncDeliveryConfirmations(options)).toEqual({
    count: 1,
    kind: "synced",
  });
  expect(uploads).toEqual([evidenceId]);
  expect(
    requests.map((request) => request.headers.get("Idempotency-Key")),
  ).toEqual(["stable-confirmation-key", "stable-confirmation-key"]);
  expect(requests.map((request) => request.url)).toEqual([
    `https://api.test/v1/deliveries/${deliveryId}/confirmations`,
    `https://api.test/v1/deliveries/${deliveryId}/confirmations`,
  ]);
  expect(await store.load(confirmationId)).toMatchObject({
    response: expect.objectContaining({ confirmation_id: confirmationId }),
    status: "confirmed",
  });
  expect(await store.listPending()).toEqual([]);
});

it("removes a server-invalid command from FIFO and surfaces a conflict", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  const result = await syncDeliveryConfirmations({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "validation-correlation",
    fetch: async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "validation_error",
            correlation_id: "server-validation-correlation",
          },
        }),
        { headers: { "Content-Type": "application/json" }, status: 422 },
      ),
    store,
    uploadEvidence: async () => {},
  });

  expect(result).toEqual({ kind: "paused", reason: "conflict" });
  expect(await store.listPending()).toEqual([]);
  expect(await store.load(confirmationId)).toMatchObject({
    correlationId: "server-validation-correlation",
    status: "conflict",
  });
});

it("uploads only missing multipart bytes after an interrupted evidence transfer", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  const largeCapture: DeliveryConfirmationCapture = {
    ...capture,
    evidence: [
      {
        ...capture.evidence[0]!,
        sizeBytes: 5 * 1024 * 1024 + 9,
      },
    ],
  };
  await store.saveAndEnqueue(largeCapture, "2026-08-01T13:01:00Z");
  const uploadedSizes: number[] = [];
  const result = await syncDeliveryConfirmations({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "multipart-resume",
    fetch: async (request) => {
      if (request.url.includes("signed.test")) {
        uploadedSizes.push((await request.arrayBuffer()).byteLength);
        return new Response(null, { status: 200 });
      }
      if (request.url.endsWith("/evidence/uploads")) {
        return new Response(
          JSON.stringify({
            evidence_id: evidenceId,
            expires_at: "2026-08-01T14:00:00Z",
            part_size: 5 * 1024 * 1024,
            parts: [
              {
                end_byte: 5 * 1024 * 1024 + 9,
                part_number: 2,
                start_byte: 5 * 1024 * 1024,
                upload_headers: {},
                upload_url: "https://signed.test/part-2",
              },
            ],
            status: "uploading",
            upload_id: "stable-upload",
          }),
          { headers: { "Content-Type": "application/json" }, status: 201 },
        );
      }
      if (request.url.endsWith("/complete")) {
        return new Response(
          JSON.stringify({
            evidence_id: evidenceId,
            expires_at: null,
            part_size: null,
            parts: [],
            status: "verified",
            upload_id: null,
          }),
          { headers: { "Content-Type": "application/json" }, status: 200 },
        );
      }
      return new Response(
        JSON.stringify({
          confirmation_id: confirmationId,
          delivery_id: deliveryId,
          delivery_receipt: {
            delivery_receipt_id: "d98873ae-7cf1-48b6-b2e5-129d23bd9f81",
            number: "DR-MNL-00000001",
            status: "pending_document",
          },
          lines: [],
          outbox_event_id: "af9cf881-e5af-48b0-95e8-04534241b330",
          status: "confirmed",
          version: 2,
        }),
        { headers: { "Content-Type": "application/json" }, status: 200 },
      );
    },
    readEvidence: async () => new ArrayBuffer(5 * 1024 * 1024 + 9),
    store,
  });
  expect(result).toEqual({ count: 1, kind: "synced" });
  expect(uploadedSizes).toEqual([9]);
});
