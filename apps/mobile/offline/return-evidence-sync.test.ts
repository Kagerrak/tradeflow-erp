import {
  createMemoryReturnEvidenceStore,
  type ReturnEvidenceCapture,
} from "./return-evidence-store";
import { syncReturnEvidence } from "./return-evidence-sync";

const requestId = "a341427a-9442-4c31-8591-230160028a2a";
const evidenceId = "evidence-note-1";

const capture: ReturnEvidenceCapture = {
  evidence: [
    {
      evidenceId,
      kind: "note",
      noteText: "Customer reported sealed-unit defect.",
      status: "pending_sync",
    },
  ],
  idempotencyKey: "stable-evidence-key",
  requestId,
};

it("syncs a pending note via the offline-evidence endpoint", async () => {
  const store = createMemoryReturnEvidenceStore();
  await store.saveAndEnqueue(capture, 1, "2026-08-28T12:00:00Z");
  const requests: Request[] = [];
  const result = await syncReturnEvidence({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "sync-correlation",
    fetch: async (request: Request) => {
      requests.push(request);
      return new Response(
        JSON.stringify({
          acknowledged_at: "2026-08-28T12:01:00Z",
          conflict_detected_at: null,
          conflict_reason: null,
          current_version: 1,
          expected_version: 1,
          return_request_id: requestId,
          status: "acknowledged",
        }),
        { headers: { "Content-Type": "application/json" }, status: 200 },
      );
    },
    now: () => "2026-08-28T12:01:00Z",
    store,
  });

  expect(result).toEqual({ count: 1, kind: "synced" });
  expect(requests).toHaveLength(1);
  expect(requests[0]!.url).toBe(
    `https://api.test/v1/return-requests/${requestId}/offline-evidence`,
  );
  expect(await store.listPending()).toEqual([]);
  expect(await store.load("stable-evidence-key")).toMatchObject({
    status: "synced",
  });
});

it("pauses on a return-request version conflict", async () => {
  const store = createMemoryReturnEvidenceStore();
  await store.saveAndEnqueue(capture, 1, "2026-08-28T12:00:00Z");
  const result = await syncReturnEvidence({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "conflict-correlation",
    fetch: async () =>
      new Response(
        JSON.stringify({
          acknowledged_at: null,
          conflict_detected_at: "2026-08-28T12:01:00Z",
          conflict_reason:
            "Return Request changed from version 1 to version 2 before the offline evidence could be synced.",
          current_version: 2,
          expected_version: 1,
          return_request_id: requestId,
          status: "conflict",
        }),
        { headers: { "Content-Type": "application/json" }, status: 200 },
      ),
    store,
  });

  expect(result).toEqual({ kind: "paused", reason: "conflict" });
  expect(await store.listPending()).toEqual([]);
  expect(await store.load("stable-evidence-key")).toMatchObject({
    correlationId: "conflict-correlation",
    errorCode: "return_request_version_conflict",
    status: "conflict",
  });
});

it.each([
  [401, "authentication_required", "unauthenticated"],
  [403, "capability_required", "forbidden"],
] as const)(
  "persists authentication detail for HTTP %s",
  async (status, code, reason) => {
    const store = createMemoryReturnEvidenceStore();
    await store.saveAndEnqueue(capture, 1, "2026-08-28T12:00:00Z");
    const result = await syncReturnEvidence({
      accessToken: "expired-token",
      baseUrl: "https://api.test",
      createCorrelationId: () => "auth-correlation",
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
    });
    expect(result).toEqual({ kind: "paused", reason });
    expect(await store.load("stable-evidence-key")).toMatchObject({
      authPaused: status === 401,
      correlationId: "server-auth-correlation",
      errorCode: code,
      errorMessage: "Authenticate before posting.",
      status: status === 401 ? "pending" : "forbidden",
    });
  },
);

it("uploads a photo before syncing notes", async () => {
  const photoCapture: ReturnEvidenceCapture = {
    evidence: [
      {
        contentType: "image/png",
        evidenceId: "evidence-photo-1",
        kind: "photo",
        localUri: "file:///proof/defect.png",
        sha256: "a".repeat(64),
        sizeBytes: 128,
        status: "pending_upload",
      },
      {
        evidenceId: "evidence-note-2",
        kind: "note",
        noteText: "Defect visible in photo.",
        status: "pending_sync",
      },
    ],
    idempotencyKey: "mixed-evidence-key",
    requestId,
  };
  const store = createMemoryReturnEvidenceStore();
  await store.saveAndEnqueue(photoCapture, 1, "2026-08-28T12:00:00Z");
  const uploads: string[] = [];
  const result = await syncReturnEvidence({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "photo-correlation",
    fetch: async () =>
      new Response(
        JSON.stringify({
          acknowledged_at: "2026-08-28T12:01:00Z",
          current_version: 1,
          expected_version: 1,
          return_request_id: requestId,
          status: "acknowledged",
        }),
        { headers: { "Content-Type": "application/json" }, status: 200 },
      ),
    now: () => "2026-08-28T12:01:00Z",
    store,
    uploadEvidence: async (evidence) => {
      uploads.push(evidence.evidenceId);
    },
  });

  expect(result).toEqual({ count: 1, kind: "synced" });
  expect(uploads).toEqual(["evidence-photo-1"]);
  expect(await store.load("mixed-evidence-key")).toMatchObject({
    status: "synced",
  });
});

it("pauses on upload failure without marking the capture synced", async () => {
  const photoCapture: ReturnEvidenceCapture = {
    evidence: [
      {
        contentType: "image/png",
        evidenceId: "evidence-photo-1",
        kind: "photo",
        localUri: "file:///proof/defect.png",
        sha256: "a".repeat(64),
        sizeBytes: 128,
        status: "pending_upload",
      },
    ],
    idempotencyKey: "photo-fail-key",
    requestId,
  };
  const store = createMemoryReturnEvidenceStore();
  await store.saveAndEnqueue(photoCapture, 1, "2026-08-28T12:00:00Z");
  const result = await syncReturnEvidence({
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "fail-correlation",
    fetch: async () => new Response("Internal Server Error", { status: 500 }),
    store,
    uploadEvidence: async () => {
      throw new Error("Network unreachable.");
    },
  });

  expect(result).toEqual({ kind: "paused", reason: "upload_failed" });
  expect(await store.load("photo-fail-key")).toMatchObject({
    status: "upload_failed",
  });
});
