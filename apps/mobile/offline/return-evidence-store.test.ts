import {
  createMemoryReturnEvidenceBacking,
  createMemoryReturnEvidenceStore,
  type ReturnEvidenceCapture,
} from "./return-evidence-store";

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

it("restores Pending Sync evidence identities after restart", async () => {
  const backing = createMemoryReturnEvidenceBacking();
  const beforeRestart = createMemoryReturnEvidenceStore(backing);
  await beforeRestart.saveAndEnqueue(capture, 1, "2026-08-28T12:00:00Z");

  const afterRestart = createMemoryReturnEvidenceStore(backing);
  expect(await afterRestart.listPending()).toEqual([
    expect.objectContaining({
      captureId: "stable-evidence-key",
      expectedRequestVersion: 1,
      requestId,
      sequence: 1,
    }),
  ]);
  expect(await afterRestart.load("stable-evidence-key")).toMatchObject({
    evidence: [expect.objectContaining({ evidenceId, status: "pending_sync" })],
    status: "pending",
  });
});

it("marks a note-only capture as synced", async () => {
  const store = createMemoryReturnEvidenceStore();
  await store.saveAndEnqueue(capture, 1, "2026-08-28T12:00:00Z");
  await store.markSynced(1, "2026-08-28T12:01:00Z");
  expect(await store.listPending()).toEqual([]);
  expect(await store.load("stable-evidence-key")).toMatchObject({
    status: "synced",
  });
});

it("tracks photo upload state independently", async () => {
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
    idempotencyKey: "photo-evidence-key",
    requestId,
  };
  const store = createMemoryReturnEvidenceStore();
  await store.saveAndEnqueue(photoCapture, 1, "2026-08-28T12:00:00Z");
  await store.markEvidenceUploaded(
    1,
    "evidence-photo-1",
    "2026-08-28T12:01:00Z",
  );
  expect(await store.load("photo-evidence-key")).toMatchObject({
    evidence: [expect.objectContaining({ status: "uploaded" })],
    status: "pending",
  });
});

it("records a conflict with correlation detail", async () => {
  const store = createMemoryReturnEvidenceStore();
  await store.saveAndEnqueue(capture, 1, "2026-08-28T12:00:00Z");
  await store.markState(
    1,
    "conflict",
    "conflict-correlation",
    "2026-08-28T12:01:00Z",
    "return_request_version_conflict",
    "Return Request changed while offline.",
  );
  expect(await store.listPending()).toEqual([]);
  expect(await store.load("stable-evidence-key")).toMatchObject({
    correlationId: "conflict-correlation",
    errorCode: "return_request_version_conflict",
    errorMessage: "Return Request changed while offline.",
    status: "conflict",
  });
});

it.each([
  [401, "authentication_required", "unauthenticated"],
  [403, "capability_required", "forbidden"],
] as const)(
  "persists authentication detail for HTTP %s",
  async (_status, code, _reason) => {
    const store = createMemoryReturnEvidenceStore();
    await store.saveAndEnqueue(capture, 1, "2026-08-28T12:00:00Z");
    await store.markRetryableAuth(
      1,
      "auth-correlation",
      "2026-08-28T12:01:00Z",
      code,
      "Authenticate before posting.",
    );
    expect(await store.load("stable-evidence-key")).toMatchObject({
      authPaused: true,
      correlationId: "auth-correlation",
      errorCode: code,
      errorMessage: "Authenticate before posting.",
    });
  },
);
