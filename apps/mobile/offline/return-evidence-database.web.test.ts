import { createWebReturnEvidenceStore } from "./return-evidence-database.web";

it("hydrates the browser Return Evidence outbox from durable storage", async () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
  const first = createWebReturnEvidenceStore(storage);
  await first.saveAndEnqueue(
    {
      evidence: [
        {
          evidenceId: "evidence-note-1",
          kind: "note",
          noteText: "Customer sealed-unit defect.",
          status: "pending_sync",
        },
      ],
      idempotencyKey: "stable-evidence-key",
      requestId: "a341427a-9442-4c31-8591-230160028a2a",
    },
    1,
    "2026-08-28T12:00:00Z",
  );
  await first.markRetryableAuth(
    1,
    "auth-correlation",
    "2026-08-28T12:01:00Z",
    "authentication_required",
    "Sign in again.",
  );

  const restarted = createWebReturnEvidenceStore(storage);
  expect(await restarted.listPending()).toEqual([
    expect.objectContaining({
      captureId: "stable-evidence-key",
      sequence: 1,
    }),
  ]);
  expect(await restarted.load("stable-evidence-key")).toMatchObject({
    authPaused: true,
    correlationId: "auth-correlation",
    errorCode: "authentication_required",
    status: "pending",
  });
});
