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
        damaged_quantity_base: "0",
        delivery_line_id: "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9",
        exception_details: {},
        identity_partitions: [],
        refused_quantity_base: "0",
        short_missing_quantity_base: "0",
        still_undelivered_quantity_base: "0",
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
            message: "The Delivery remaining quantity changed.",
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
    errorCode: "validation_error",
    errorMessage: "The Delivery remaining quantity changed.",
    status: "conflict",
  });
});

it.each([
  [401, "authentication_required", "unauthenticated"],
  [403, "delivery_assignment_required", "forbidden"],
] as const)(
  "persists authentication detail for HTTP %s",
  async (status, code, reason) => {
    const store = createMemoryDeliveryConfirmationStore();
    await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
    const result = await syncDeliveryConfirmations({
      accessToken: "expired-token",
      baseUrl: "https://api.test",
      createCorrelationId: () => "client-auth-correlation",
      fetch: async () =>
        new Response(
          JSON.stringify({
            error: {
              code,
              correlation_id: "server-auth-correlation",
              message: "Authenticate before posting.",
            },
          }),
          { headers: { "Content-Type": "application/json" }, status },
        ),
      store,
      uploadEvidence: async () => {},
    });
    expect(result).toEqual({ kind: "paused", reason });
    expect(await store.load(confirmationId)).toMatchObject({
      authPaused: status === 401,
      correlationId: "server-auth-correlation",
      errorCode: code,
      errorMessage: "Authenticate before posting.",
      status: status === 401 ? "pending_confirmation" : "forbidden",
    });
    expect(await store.listPending()).toHaveLength(status === 401 ? 1 : 0);
  },
);

it("classifies an evidence-upload 401 without calling it a file failure", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  let requestCount = 0;
  const options = {
    accessToken: "expired-token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "upload-auth-client",
    fetch: async (request: Request) => {
      requestCount += 1;
      if (requestCount === 1)
        return new Response(
          JSON.stringify({
            error: {
              code: "authentication_required",
              correlation_id: "upload-auth-server",
              message: "Token expired.",
            },
          }),
          { headers: { "Content-Type": "application/json" }, status: 401 },
        );
      if (request.url.endsWith("/evidence/uploads"))
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
      return new Response(
        JSON.stringify({
          confirmation_id: confirmationId,
          delivery_id: deliveryId,
          delivery_receipt: null,
          lines: [],
          outbox_event_id: null,
          status: "confirmed",
          version: 2,
        }),
        { headers: { "Content-Type": "application/json" }, status: 200 },
      );
    },
    store,
  };
  const result = await syncDeliveryConfirmations(options);
  expect(result).toEqual({ kind: "paused", reason: "unauthenticated" });
  expect(await store.load(confirmationId)).toMatchObject({
    authPaused: true,
    correlationId: "upload-auth-server",
    errorCode: "authentication_required",
    errorMessage: "Token expired.",
    status: "pending_upload",
  });
  expect(await store.listPending()).toEqual([
    expect.objectContaining({ confirmationId }),
  ]);
  expect(await syncDeliveryConfirmations(options)).toEqual({
    count: 1,
    kind: "synced",
  });
  expect(await store.listPending()).toEqual([]);
});

it("resumes the unchanged FIFO command after confirmation re-authentication", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  let attempt = 0;
  const keys: string[] = [];
  const options = {
    accessToken: "refreshed-token",
    baseUrl: "https://api.test",
    createCorrelationId: () => `auth-resume-${attempt.toString()}`,
    fetch: async (request: Request) => {
      keys.push(request.headers.get("Idempotency-Key") ?? "");
      attempt += 1;
      if (attempt === 1)
        return new Response(
          JSON.stringify({
            error: {
              code: "authentication_required",
              correlation_id: "expired-session",
              message: "Sign in again.",
            },
          }),
          { headers: { "Content-Type": "application/json" }, status: 401 },
        );
      return new Response(
        JSON.stringify({
          confirmation_id: confirmationId,
          delivery_id: deliveryId,
          delivery_receipt: null,
          lines: [],
          outbox_event_id: null,
          status: "confirmed",
          version: 2,
        }),
        { headers: { "Content-Type": "application/json" }, status: 200 },
      );
    },
    store,
    uploadEvidence: async () => {},
  };
  expect(await syncDeliveryConfirmations(options)).toEqual({
    kind: "paused",
    reason: "unauthenticated",
  });
  expect(await store.listPending()).toHaveLength(1);
  expect(await syncDeliveryConfirmations(options)).toEqual({
    count: 1,
    kind: "synced",
  });
  expect(keys).toEqual(["stable-confirmation-key", "stable-confirmation-key"]);
  expect(await store.load(confirmationId)).toMatchObject({
    authPaused: false,
    errorCode: null,
    status: "confirmed",
  });
});

it("retains a conflict and enqueues an explicitly reviewed replacement", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  await store.markState(
    1,
    "conflict",
    "delivery-conflict",
    "2026-08-01T13:02:00Z",
    "delivery_version_conflict",
    "Refresh before retrying.",
  );
  const replacementId = "cbe7a13e-52c5-49d5-9db8-6f0c7638b5f5";
  await store.replaceConflict(
    confirmationId,
    {
      ...capture,
      command: {
        ...capture.command,
        confirmation_id: replacementId,
        expected_delivery_version: 2,
      },
      idempotencyKey: `delivery-confirmation:${replacementId}`,
    },
    "2026-08-01T13:03:00Z",
  );
  expect(await store.load(confirmationId)).toMatchObject({
    errorCode: "delivery_version_conflict",
    replacedByConfirmationId: replacementId,
    status: "conflict",
  });
  expect(await store.load(replacementId)).toMatchObject({
    deliveryId,
    replacesConfirmationId: confirmationId,
  });
  expect(await store.listPending()).toEqual([
    expect.objectContaining({ confirmationId: replacementId }),
  ]);
});

it("rejects replacement lineage across Deliveries", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  await store.markState(1, "conflict", "conflict", "2026-08-01T13:02:00Z");
  await expect(
    store.replaceConflict(
      confirmationId,
      {
        ...capture,
        command: { ...capture.command, confirmation_id: crypto.randomUUID() },
        deliveryId: crypto.randomUUID(),
        idempotencyKey: "different-delivery",
      },
      "2026-08-01T13:03:00Z",
    ),
  ).rejects.toThrow("same Delivery");
});

it("invalidates assigned Delivery custody after server acknowledgement", async () => {
  const store = createMemoryDeliveryConfirmationStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  const invalidated: string[] = [];
  await syncDeliveryConfirmations({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "sync-success",
    fetch: async () =>
      new Response(
        JSON.stringify({
          confirmation_id: confirmationId,
          delivery_id: deliveryId,
          delivery_receipt: null,
          lines: [],
          outbox_event_id: "af9cf881-e5af-48b0-95e8-04534241b330",
          status: "confirmed",
          version: 2,
        }),
        { headers: { "Content-Type": "application/json" }, status: 201 },
      ),
    onSynced: async (value) => {
      invalidated.push(value);
    },
    store,
    uploadEvidence: async () => {},
  });
  expect(invalidated).toEqual([deliveryId]);
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
