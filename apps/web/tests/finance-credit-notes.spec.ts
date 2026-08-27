import { expect, test } from "@playwright/test";

type CreditNote = {
  amount: string;
  branch_id: string;
  credit_note_id: string;
  currency: string;
  customer_id: string;
  draft_invoice_id: string;
  ledger_entry_id: string | null;
  number: string | null;
  posted_at: string | null;
  posted_by: string | null;
  reason: string;
  requested_at: string;
  requested_by: string;
  reversal_ledger_entry_id: string | null;
  reversal_reason: string | null;
  reversed_at: string | null;
  reversed_by: string | null;
  status: string;
};

function makeNote(overrides: Partial<CreditNote> = {}): CreditNote {
  return {
    amount: "50.00",
    branch_id: "4a9f51d5-ad6a-4956-a715-fad44213d2c5",
    credit_note_id: "cn-00000000-0000-0000-0000-000000000001",
    currency: "PHP",
    customer_id: "17a4ac6a-4fbd-40e4-9f4b-19ac17c84e63",
    draft_invoice_id: "inv-00000000-0000-0000-0000-000000000001",
    ledger_entry_id: null,
    number: null,
    posted_at: null,
    posted_by: null,
    reason: "Pricing correction.",
    requested_at: "2026-08-14T10:00:00Z",
    requested_by: "finance-recorder",
    reversal_ledger_entry_id: null,
    reversal_reason: null,
    reversed_at: null,
    reversed_by: null,
    status: "pending_authorization",
    ...overrides,
  };
}

test("requests, authorizes, and reverses a credit note through the workspace", async ({
  page,
}) => {
  const notes: CreditNote[] = [];
  const requests: { body: Record<string, unknown>; idempotencyKey: string }[] =
    [];
  const posts: { body: Record<string, unknown>; idempotencyKey: string }[] = [];
  const reverses: { body: Record<string, unknown>; idempotencyKey: string }[] =
    [];

  await page.route("**/api/finance/credit-notes", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: notes, total: notes.length },
    }),
  );

  await page.route("**/api/finance/invoices/*/credit-notes", async (route) => {
    const idempotencyKey =
      (await route.request().headerValue("Idempotency-Key")) ?? "";
    requests.push({
      body: route.request().postDataJSON() as Record<string, unknown>,
      idempotencyKey,
    });
    const url = route.request().url();
    const parts = url.split("/");
    const invoiceId = parts[parts.length - 2] ?? "";
    const note = makeNote({
      credit_note_id: crypto.randomUUID(),
      draft_invoice_id: invoiceId,
    });
    notes.push(note);
    await route.fulfill({
      contentType: "application/json",
      json: note,
      status: 201,
    });
  });

  await page.route("**/api/finance/credit-notes/*/post", async (route) => {
    const idempotencyKey =
      (await route.request().headerValue("Idempotency-Key")) ?? "";
    posts.push({
      body: route.request().postDataJSON() as Record<string, unknown>,
      idempotencyKey,
    });
    const url = route.request().url();
    const creditNoteId = url.split("/").slice(-2)[0] ?? "";
    const note = notes.find((n) => n.credit_note_id === creditNoteId);
    if (note) {
      note.status = "posted";
      note.number = "CN-MNL-00000001";
      note.posted_by = "finance-verifier";
      note.posted_at = "2026-08-14T10:05:00Z";
    }
    await route.fulfill({
      contentType: "application/json",
      json: {
        credit_note_id: creditNoteId,
        draft_invoice_id: note?.draft_invoice_id ?? "",
        ledger_entry_id: crypto.randomUUID(),
        number: "CN-MNL-00000001",
        posted_at: "2026-08-14T10:05:00Z",
        posted_by: "finance-verifier",
        status: "posted",
      },
      status: 201,
    });
  });

  await page.route("**/api/finance/credit-notes/*/reverse", async (route) => {
    const idempotencyKey =
      (await route.request().headerValue("Idempotency-Key")) ?? "";
    reverses.push({
      body: route.request().postDataJSON() as Record<string, unknown>,
      idempotencyKey,
    });
    const url = route.request().url();
    const creditNoteId = url.split("/").slice(-2)[0] ?? "";
    const note = notes.find((n) => n.credit_note_id === creditNoteId);
    if (note) {
      note.status = "reversed";
      note.reversed_by = "finance-verifier";
      note.reversed_at = "2026-08-14T10:10:00Z";
    }
    await route.fulfill({
      contentType: "application/json",
      json: {
        credit_note_id: creditNoteId,
        reversal_ledger_entry_id: crypto.randomUUID(),
        reversed_at: "2026-08-14T10:10:00Z",
        reversed_by: "finance-verifier",
        status: "reversed",
      },
      status: 201,
    });
  });

  await page.goto("/finance/credit-notes");
  await expect(
    page.getByRole("heading", { level: 1, name: "Credit notes" }),
  ).toBeVisible();

  await page
    .getByTestId("credit-note-invoice-id")
    .fill("inv-00000000-0000-0000-0000-000000000001");
  await page.getByTestId("credit-note-amount").fill("50.00");
  await page.getByTestId("credit-note-reason").fill("Pricing correction.");
  await page.getByTestId("credit-note-request").click();

  await expect(page.getByTestId("credit-note-message")).toContainText(
    "requested",
  );
  expect(requests[0]?.body.amount).toBe("50.00");
  expect(requests[0]?.idempotencyKey).toEqual(expect.any(String));

  const note = notes[0];
  if (note === undefined) {
    throw new Error("Expected a credit note to be created");
  }
  const row = page.getByTestId(`credit-note-row-${note.credit_note_id}`);
  await expect(row).toContainText("pending_authorization");

  await page
    .getByTestId(`credit-note-authorize-${note.credit_note_id}`)
    .click();
  await expect(page.getByTestId("credit-note-message")).toContainText(
    "authorized",
  );
  expect(posts[0]?.idempotencyKey).toEqual(expect.any(String));
  await expect(row).toContainText("posted");

  await page.getByTestId(`credit-note-reverse-${note.credit_note_id}`).click();
  await expect(page.getByTestId("credit-note-message")).toContainText(
    "reversed",
  );
  expect(reverses[0]?.idempotencyKey).toEqual(expect.any(String));
  await expect(row).toContainText("reversed");
});

test("shows a maker-checker denial when authorization is rejected", async ({
  page,
}) => {
  const note = makeNote();

  await page.route("**/api/finance/credit-notes", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [note], total: 1 },
    }),
  );

  await page.route("**/api/finance/credit-notes/*/post", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        error: {
          code: "credit_note_maker_checker_required",
          correlation_id: "denial-123",
          message: "Requester cannot authorize the same Credit Note.",
        },
      },
      status: 403,
    });
  });

  await page.goto("/finance/credit-notes");
  await page
    .getByTestId(`credit-note-authorize-${note.credit_note_id}`)
    .click();
  await expect(page.getByTestId("credit-note-message")).toContainText(
    "Authorization was rejected",
  );
});
