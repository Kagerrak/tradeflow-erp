import {
  createMemoryReturnReceiptBacking,
  createMemoryReturnReceiptStore,
  type ReturnReceiptCapture,
} from "./return-receipt-store";
import { syncReturnReceipts } from "./return-receipt-sync";

const requestId = "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef";
const receiptId = "65a4745a-7d07-4cc2-a497-bc27f60be7a0";
const evidenceId = "dc0de2b2-e6d8-4d4f-b898-42398bab8eaa";
const lineId = "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9";

const capture: ReturnReceiptCapture = {
  command: {
    evidence_ids: [evidenceId],
    expected_request_version: 2,
    lines: [
      {
        notes: null,
        outcome: "restock",
        received_quantity_base: "2.000000",
        return_request_line_id: lineId,
      },
    ],
    notes: "Cartons resealed.",
    received_at: "2026-08-01T13:00:00Z",
    return_receipt_id: receiptId,
  },
  evidence: [
    {
      contentType: "image/png",
      evidenceId,
      kind: "photo",
      localUri: "file:///proof/photo.png",
      sha256: "a".repeat(64),
      sizeBytes: 128,
      status: "pending_upload",
    },
  ],
  idempotencyKey: "stable-receipt-key",
  requestId,
};

it("restores Pending Sync evidence and receipt identities after restart", async () => {
  const backing = createMemoryReturnReceiptBacking();
  const beforeRestart = createMemoryReturnReceiptStore(backing);
  await beforeRestart.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");

  const afterRestart = createMemoryReturnReceiptStore(backing);
  expect(await afterRestart.listPending()).toEqual([
    expect.objectContaining({
      idempotencyKey: "stable-receipt-key",
      receiptId,
      requestId,
      sequence: 1,
    }),
  ]);
  expect(await afterRestart.load(receiptId)).toMatchObject({
    evidence: [
      expect.objectContaining({ evidenceId, status: "pending_upload" }),
    ],
    status: "pending_upload",
  });
});

it("resumes after upload and retries a lost response with the same identities", async () => {
  const store = createMemoryReturnReceiptStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  const uploads: string[] = [];
  const requests: Request[] = [];
  let postAttempt = 0;
  const options = {
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => `receipt-${postAttempt + 1}`,
    fetch: async (request: Request) => {
      requests.push(request);
      postAttempt += 1;
      if (postAttempt === 1) throw new TypeError("response lost after commit");
      return new Response(
        JSON.stringify({
          lines: [
            {
              custody: "available",
              delivery_line_id: "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9",
              line_id: "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9",
              movement_id: "c0c6c6d6-7e6e-4e6e-8e6e-9f6f6f6f6f6f",
              outcome: "restock",
              received_quantity_base: "2.000000",
              return_request_line_id: lineId,
              sku_id: "37989314-b1b7-4bea-9c68-b6390ddae80f",
            },
          ],
          notes: "Cartons resealed.",
          received_at: "2026-08-01T13:00:00Z",
          received_by: "warehouse-mnl",
          return_receipt_id: receiptId,
          return_request_id: requestId,
          status: "received",
          version: 3,
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

  expect(await syncReturnReceipts(options)).toEqual({
    kind: "paused",
    reason: "unavailable",
  });
  expect(await store.load(receiptId)).toMatchObject({
    evidence: [expect.objectContaining({ status: "uploaded" })],
    status: "pending_confirmation",
  });

  expect(await syncReturnReceipts(options)).toEqual({
    count: 1,
    kind: "synced",
  });
  expect(uploads).toEqual([evidenceId]);
  expect(
    requests.map((request) => request.headers.get("Idempotency-Key")),
  ).toEqual(["stable-receipt-key", "stable-receipt-key"]);
  expect(requests.map((request) => request.url)).toEqual([
    `https://api.test/v1/return-requests/${requestId}/receipts`,
    `https://api.test/v1/return-requests/${requestId}/receipts`,
  ]);
  expect(await store.load(receiptId)).toMatchObject({
    response: expect.objectContaining({ return_receipt_id: receiptId }),
    status: "confirmed",
  });
  expect(await store.listPending()).toEqual([]);
});

it("removes a server-invalid command from FIFO and surfaces a conflict", async () => {
  const store = createMemoryReturnReceiptStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  const result = await syncReturnReceipts({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "validation-correlation",
    fetch: async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "return_quantity_exceeds_authorized",
            correlation_id: "server-validation-correlation",
            message: "The authorized quantity changed.",
          },
        }),
        { headers: { "Content-Type": "application/json" }, status: 422 },
      ),
    store,
    uploadEvidence: async () => {},
  });

  expect(result).toEqual({ kind: "paused", reason: "conflict" });
  expect(await store.listPending()).toEqual([]);
  expect(await store.load(receiptId)).toMatchObject({
    correlationId: "server-validation-correlation",
    errorCode: "return_quantity_exceeds_authorized",
    errorMessage: "The authorized quantity changed.",
    status: "conflict",
  });
});

it.each([
  [401, "authentication_required", "unauthenticated"],
  [403, "operational_scope_required", "forbidden"],
] as const)(
  "persists authentication detail for HTTP %s",
  async (status, code, reason) => {
    const store = createMemoryReturnReceiptStore();
    await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
    const result = await syncReturnReceipts({
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
    expect(await store.load(receiptId)).toMatchObject({
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
  const store = createMemoryReturnReceiptStore();
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
          lines: [],
          notes: null,
          received_at: "2026-08-01T13:00:00Z",
          received_by: "warehouse-mnl",
          return_receipt_id: receiptId,
          return_request_id: requestId,
          status: "received",
          version: 3,
        }),
        { headers: { "Content-Type": "application/json" }, status: 200 },
      );
    },
    store,
  };
  const result = await syncReturnReceipts(options);
  expect(result).toEqual({ kind: "paused", reason: "unauthenticated" });
  expect(await store.load(receiptId)).toMatchObject({
    authPaused: true,
    correlationId: "upload-auth-server",
    errorCode: "authentication_required",
    errorMessage: "Token expired.",
    status: "pending_upload",
  });
  expect(await store.listPending()).toEqual([
    expect.objectContaining({ receiptId }),
  ]);
  expect(await syncReturnReceipts(options)).toEqual({
    count: 1,
    kind: "synced",
  });
  expect(await store.listPending()).toEqual([]);
});

it("retains a conflict and enqueues an explicitly reviewed replacement", async () => {
  const store = createMemoryReturnReceiptStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  await store.markState(
    1,
    "conflict",
    "return-conflict",
    "2026-08-01T13:02:00Z",
    "return_request_version_conflict",
    "Refresh before retrying.",
  );
  const replacementId = "cbe7a13e-52c5-49d5-9db8-6f0c7638b5f5";
  await store.replaceConflict(
    receiptId,
    {
      ...capture,
      command: {
        ...capture.command,
        expected_request_version: 3,
        return_receipt_id: replacementId,
      },
      idempotencyKey: `return-receipt:${replacementId}`,
    },
    "2026-08-01T13:03:00Z",
  );
  expect(await store.load(receiptId)).toMatchObject({
    errorCode: "return_request_version_conflict",
    replacedByReceiptId: replacementId,
    status: "conflict",
  });
  expect(await store.load(replacementId)).toMatchObject({
    replacesReceiptId: receiptId,
    requestId,
  });
  expect(await store.listPending()).toEqual([
    expect.objectContaining({ receiptId: replacementId }),
  ]);
});

it("rejects replacement lineage across Return Requests", async () => {
  const store = createMemoryReturnReceiptStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  await store.markState(1, "conflict", "conflict", "2026-08-01T13:02:00Z");
  await expect(
    store.replaceConflict(
      receiptId,
      {
        ...capture,
        command: {
          ...capture.command,
          return_receipt_id: crypto.randomUUID(),
        },
        idempotencyKey: "different-request",
        requestId: crypto.randomUUID(),
      },
      "2026-08-01T13:03:00Z",
    ),
  ).rejects.toThrow("same Return Request");
});

it("invalidates the Return Request after server acknowledgement", async () => {
  const store = createMemoryReturnReceiptStore();
  await store.saveAndEnqueue(capture, "2026-08-01T13:01:00Z");
  const invalidated: string[] = [];
  await syncReturnReceipts({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "sync-success",
    fetch: async () =>
      new Response(
        JSON.stringify({
          lines: [],
          notes: null,
          received_at: "2026-08-01T13:00:00Z",
          received_by: "warehouse-mnl",
          return_receipt_id: receiptId,
          return_request_id: requestId,
          status: "received",
          version: 3,
        }),
        { headers: { "Content-Type": "application/json" }, status: 201 },
      ),
    onSynced: async (value) => {
      invalidated.push(value);
    },
    store,
    uploadEvidence: async () => {},
  });
  expect(invalidated).toEqual([requestId]);
});

it("uploads only missing multipart bytes after an interrupted evidence transfer", async () => {
  const store = createMemoryReturnReceiptStore();
  const largeCapture: ReturnReceiptCapture = {
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
  const result = await syncReturnReceipts({
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
          lines: [],
          notes: null,
          received_at: "2026-08-01T13:00:00Z",
          received_by: "warehouse-mnl",
          return_receipt_id: receiptId,
          return_request_id: requestId,
          status: "received",
          version: 3,
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
