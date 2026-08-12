import { expect, test } from "@playwright/test";

import { GET as listCorrections } from "../app/api/delivery-corrections/route";
import { POST as authorizeCorrection } from "../app/api/delivery-corrections/[correctionId]/authorization/route";
import { GET as getCorrection } from "../app/api/delivery-corrections/[correctionId]/route";
import { POST as createCorrection } from "../app/api/delivery-receipts/[receiptId]/corrections/route";
import {
  GET as getReceipt,
  POST as accessReceipt,
} from "../app/api/delivery-receipts/[receiptId]/route";

const correctionId = "b341427a-9442-4c31-8591-230160028a2a";
const receiptId = "573508a8-7918-4c0c-a040-8bc9bb9e7152";
const deliveryLineId = "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9";
const baseLine = {
  accepted_quantity_base: "2.000000",
  damaged_quantity_base: "0.000000",
  delivery_line_id: deliveryLineId,
  identity_positions: [],
  line_id: "4af0c99a-b55d-4f68-bf34-6f0805630032",
  refused_quantity_base: "0.000000",
  short_missing_quantity_base: "0.000000",
  sku_id: "37989314-b1b7-4bea-9c68-b6390ddae80f",
  still_undelivered_quantity_base: "0.000000",
};
const proposedLine = {
  ...baseLine,
  accepted_quantity_base: "1.000000",
  damaged_quantity_base: "1.000000",
};
const receipt = {
  confirmation_lines: [baseLine],
  correction_id: null,
  correction_status: "current",
  corrects_delivery_receipt_id: null,
  created_by_correction_id: null,
  delivery_id: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
  delivery_receipt_id: receiptId,
  evidence_ids: ["50249057-145f-43ba-868f-dc4b3b43cabe"],
  number: "DR-MNL-00000042",
  replacement_delivery_receipt_id: null,
  snapshot: {
    customer_account_number: "CUS-00042",
    customer_legal_name: "Santos Trading",
    delivery_address: { city: "Manila", street: "42 Mabini Street" },
    recipient_name: "Ana Santos",
  },
  status: "ready",
  superseded_by_correction_id: null,
};
const summary = {
  affected_value_base_currency: "112.000000",
  authorized_at: null,
  authorized_by: null,
  base_currency: "PHP",
  branch_id: "branch-mnl",
  correction_id: correctionId,
  delivery_id: receipt.delivery_id,
  original_delivery_receipt_id: receiptId,
  reason: "One case was damaged before handoff",
  requested_at: "2026-08-11T08:00:00Z",
  requested_by: "maker-1",
  status: "pending_authorization",
  version: 1,
  warehouse_id: "warehouse-mnl",
};
const detail = {
  ...summary,
  draft_invoice_effect: {
    original_draft_invoice_id: "1d565eb5-6e47-43aa-bafe-13e6e08e7d09",
    replacement_draft_invoice_id: "22d4ee69-4ee7-42e5-8f38-c366319731e9",
    reversal_draft_invoice_id: "59e29fe7-e597-48e6-9ea9-1330c8b7ea41",
    status: "pending",
  },
  evidence_ids: ["50249057-145f-43ba-868f-dc4b3b43cabe"],
  lines: [
    {
      accepted_quantity_base: proposedLine.accepted_quantity_base,
      damaged_quantity_base: proposedLine.damaged_quantity_base,
      delivery_line_id: proposedLine.delivery_line_id,
      identity_positions: [],
      refused_quantity_base: proposedLine.refused_quantity_base,
      short_missing_quantity_base: proposedLine.short_missing_quantity_base,
      still_undelivered_quantity_base:
        proposedLine.still_undelivered_quantity_base,
    },
  ],
  outbox_event_id: "b5f89d86-e404-4d74-8bba-150875ce7c27",
  receipt_effect: {
    original_delivery_receipt_id: receiptId,
    original_number: "DR-MNL-00000042",
    replacement_delivery_receipt_id: null,
    replacement_document_status: null,
    replacement_number: null,
  },
  stock_effect: {
    expected_replacement_count: 1,
    expected_reversal_count: 1,
    original_movement_ids: ["5d0678c5-1307-461c-a034-a29c8940eed9"],
    replacement_movement_ids: ["6d0678c5-1307-461c-a034-a29c8940eed9"],
    reversal_movement_ids: ["7d0678c5-1307-461c-a034-a29c8940eed9"],
    status: "pending",
  },
};

async function routeReview(page: import("@playwright/test").Page) {
  await page.route(
    "**/api/delivery-corrections?status=pending_authorization",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: { items: [summary], total: 1 },
      }),
  );
  await page.route(`**/api/delivery-corrections/${correctionId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: detail }),
  );
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: receipt }),
  );
}

test("shows expected posting effects for a pending correction", async ({
  page,
}) => {
  await routeReview(page);
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: new RegExp(correctionId) }).click();
  const effects = page.locator(".correction-effects");
  await expect(effects).toContainText("Expected");
  await expect(effects).toContainText("1 reversal · 1 replacement");
  await expect(effects).toContainText("Reverse and replace");
  await expect(effects).toContainText("New Branch-series number");
});

test("shows posted status and counts for a posted correction", async ({
  page,
}) => {
  const posted = {
    ...detail,
    authorized_at: "2026-08-11T09:00:00Z",
    authorized_by: "approver-2",
    draft_invoice_effect: {
      ...detail.draft_invoice_effect,
      replacement_draft_invoice_id: "22d4ee69-4ee7-42e5-8f38-c366319731e9",
      status: "completed",
    },
    stock_effect: {
      expected_replacement_count: 1,
      expected_reversal_count: 1,
      original_movement_ids: ["5d0678c5-1307-461c-a034-a29c8940eed9"],
      replacement_movement_ids: ["6d0678c5-1307-461c-a034-a29c8940eed9"],
      reversal_movement_ids: ["7d0678c5-1307-461c-a034-a29c8940eed9"],
      status: "posted",
    },
    status: "posted",
    version: 2,
  };
  await page.route(
    "**/api/delivery-corrections?status=pending_authorization",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: { items: [], total: 0 },
      }),
  );
  await page.route("**/api/delivery-corrections?status=posted", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [posted], total: 1 },
    }),
  );
  await page.route(`**/api/delivery-corrections/${correctionId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: posted }),
  );
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: receipt }),
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: "Posted chain" }).click();
  await page.getByRole("button", { name: new RegExp(correctionId) }).click();
  const effects = page.locator(".correction-effects");
  await expect(effects).toContainText("posted");
  await expect(effects).toContainText("1 reversal · 1 replacement");
  await expect(effects).toContainText("Reversed and replaced");
});

test("shows immutable original, proposed partition, effects, and audit chain", async ({
  page,
}) => {
  await routeReview(page);
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: new RegExp(correctionId) }).click();
  await expect(page.getByLabel("Receipt correction chain")).toContainText(
    "DR-MNL-00000042",
  );
  await expect(page.getByLabel("Original receipt snapshot")).toContainText(
    "Santos Trading",
  );
  await expect(
    page.getByLabel("Original and proposed quantities"),
  ).toContainText("-1.000000");
  await expect(
    page.getByLabel("Original and proposed quantities"),
  ).toContainText(baseLine.sku_id);
  await expect(
    page.getByText("Reverse original; replace with corrected accepted total"),
  ).toBeVisible();
  await expect(page.getByLabel("Complete audit chain")).toContainText(
    "maker-1",
  );
  await expect(
    page.getByRole("button", { name: "Authorize and post correction" }),
  ).toBeDisabled();
});

test("authorizes the immutable proposal with version and stable command identity", async ({
  page,
}) => {
  await routeReview(page);
  let posted: {
    command: Record<string, unknown>;
    idempotencyKey: string;
  } | null = null;
  await page.route(
    `**/api/delivery-corrections/${correctionId}/authorization`,
    (route) => {
      posted = route.request().postDataJSON() as typeof posted;
      return route.fulfill({
        contentType: "application/json",
        json: {
          ...detail,
          authorized_at: "2026-08-11T09:00:00Z",
          authorized_by: "approver-2",
          status: "posted",
          version: 2,
        },
      });
    },
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: new RegExp(correctionId) }).click();
  await page.getByLabel(/I reviewed the original/).check();
  await page
    .getByRole("button", { name: "Authorize and post correction" })
    .click();
  await expect.poll(() => posted).not.toBeNull();
  expect(posted).toMatchObject({ command: { expected_correction_version: 1 } });
  expect(
    (posted as unknown as { idempotencyKey: string }).idempotencyKey,
  ).toMatch(/^delivery-correction-authorization:/);
  await expect(page.getByText("approver-2")).toBeVisible();
});

test("maker requests a complete exact partition with reason and evidence", async ({
  page,
}) => {
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: receipt }),
  );
  let posted: {
    command: Record<string, unknown>;
    idempotencyKey: string;
  } | null = null;
  await page.route(
    `**/api/delivery-receipts/${receiptId}/corrections`,
    (route) => {
      posted = route.request().postDataJSON() as typeof posted;
      return route.fulfill({
        contentType: "application/json",
        json: detail,
        status: 201,
      });
    },
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: "Request correction" }).click();
  await page.getByLabel("Delivery Receipt ID").fill(receiptId);
  await page.getByRole("button", { name: "Review original" }).click();
  await page
    .getByLabel(`Accepted quantity for ${deliveryLineId}`)
    .fill("1.000000");
  await page
    .getByLabel(`Damaged quantity for ${deliveryLineId}`)
    .fill("1.000000");
  await page
    .getByLabel("Correction reason")
    .fill("One case was damaged before handoff");
  await page.getByLabel(/Retained proof 1/).check();
  await page
    .getByRole("button", { name: "Request independent approval" })
    .click();
  await expect.poll(() => posted).not.toBeNull();
  expect(posted).toMatchObject({
    command: {
      evidence_ids: ["50249057-145f-43ba-868f-dc4b3b43cabe"],
      lines: [
        {
          accepted_quantity_base: "1.000000",
          damaged_quantity_base: "1.000000",
          delivery_line_id: deliveryLineId,
        },
      ],
      reason: "One case was damaged before handoff",
    },
  });
  await expect(
    page.getByRole("heading", { name: "Waiting for an independent approver." }),
  ).toBeVisible();
});

test("derives tracked aggregates and enforces serial one-hot partitions", async ({
  page,
}) => {
  const serial = "SER-0001";
  const trackedReceipt = {
    ...receipt,
    confirmation_lines: [
      {
        ...baseLine,
        accepted_quantity_base: "1.000000",
        identity_positions: [
          {
            accepted_quantity_base: "1.000000",
            damaged_quantity_base: "0.000000",
            delivery_line_identity_allocation_id:
              "e04bc325-0f51-4e17-a614-2bb5e9a9577c",
            expiration_date: null,
            lot_code: null,
            quantity_base: "1.000000",
            refused_quantity_base: "0.000000",
            serial_number: serial,
            short_missing_quantity_base: "0.000000",
            still_undelivered_quantity_base: "0.000000",
            tracking_policy: "serial",
          },
        ],
      },
    ],
  };
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: trackedReceipt }),
  );
  let posted: {
    command: { lines: typeof trackedReceipt.confirmation_lines };
  } | null = null;
  await page.route(
    `**/api/delivery-receipts/${receiptId}/corrections`,
    (route) => {
      posted = route.request().postDataJSON() as typeof posted;
      return route.fulfill({
        contentType: "application/json",
        json: detail,
        status: 201,
      });
    },
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: "Request correction" }).click();
  await page.getByLabel("Delivery Receipt ID").fill(receiptId);
  await page.getByRole("button", { name: "Review original" }).click();
  await expect(
    page.getByLabel(`Accepted quantity for ${deliveryLineId}`),
  ).toHaveAttribute("readonly", "");
  await page.getByLabel(`Accepted quantity for ${serial}`).fill("0.500000");
  await page.getByLabel(`Damaged quantity for ${serial}`).fill("0.500000");
  await page.getByLabel("Correction reason").fill("Serial was damaged");
  await page.getByLabel(/Retained proof 1/).check();
  await expect(
    page.getByRole("button", { name: "Request independent approval" }),
  ).toBeDisabled();
  await page.getByLabel(`Accepted quantity for ${serial}`).fill("0.000000");
  await page.getByLabel(`Damaged quantity for ${serial}`).fill("1.000000");
  await page
    .getByRole("button", { name: "Request independent approval" })
    .click();
  await expect.poll(() => posted).not.toBeNull();
  expect(posted).toMatchObject({
    command: {
      lines: [
        {
          accepted_quantity_base: "0.000000",
          damaged_quantity_base: "1.000000",
          identity_positions: [
            {
              accepted_quantity_base: "0.000000",
              damaged_quantity_base: "1.000000",
            },
          ],
        },
      ],
    },
  });
});

test("does not accept malformed source evidence identities", async ({
  page,
}) => {
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { ...receipt, evidence_ids: ["not-a-uuid"] },
    }),
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: "Request correction" }).click();
  await page.getByLabel("Delivery Receipt ID").fill(receiptId);
  await page.getByRole("button", { name: "Review original" }).click();
  await page.getByLabel("Correction reason").fill("Quantity correction");
  await page.getByLabel(/Retained proof 1/).click();
  await expect(page.getByLabel(/Retained proof 1/)).not.toBeChecked();
  await expect(
    page.getByRole("button", { name: "Request independent approval" }),
  ).toBeDisabled();
});

test("validates six-decimal partitions exactly beyond Number safe range", async ({
  page,
}) => {
  const exactReceipt = {
    ...receipt,
    confirmation_lines: [
      {
        ...baseLine,
        accepted_quantity_base: "9007199254740993.000001",
      },
    ],
  };
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: exactReceipt }),
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: "Request correction" }).click();
  await page.getByLabel("Delivery Receipt ID").fill(receiptId);
  await page.getByRole("button", { name: "Review original" }).click();
  await page
    .getByLabel(`Accepted quantity for ${deliveryLineId}`)
    .fill("9007199254740993.000000");
  await page
    .getByLabel(`Damaged quantity for ${deliveryLineId}`)
    .fill("0.000001");
  await page.getByLabel("Correction reason").fill("Exact unit correction");
  await page.getByLabel(/Retained proof 1/).check();
  await expect(
    page.getByRole("button", { name: "Request independent approval" }),
  ).toBeEnabled();
});

test("keeps maker-checker denial distinct and leaves the dossier visible", async ({
  page,
}) => {
  await routeReview(page);
  await page.route(
    `**/api/delivery-corrections/${correctionId}/authorization`,
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: {
          code: "delivery_correction_maker_checker_required",
          correlationId: "maker-checker",
          kind: "forbidden",
          message: "Requester cannot authorize the same correction.",
        },
        status: 403,
      }),
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: new RegExp(correctionId) }).click();
  await page.getByLabel(/I reviewed the original/).check();
  await page
    .getByRole("button", { name: "Authorize and post correction" })
    .click();
  await expect(page.getByText("Action forbidden")).toBeVisible();
  await expect(page.getByText(/maker-checker/)).toBeVisible();
  await expect(
    page.getByLabel("Original and proposed quantities"),
  ).toBeVisible();
});

test("retains a conflicted proposal and explicitly reloads the current dossier", async ({
  page,
}) => {
  await routeReview(page);
  await page.unroute(`**/api/delivery-corrections/${correctionId}`);
  let detailReads = 0;
  await page.route(`**/api/delivery-corrections/${correctionId}`, (route) => {
    detailReads += 1;
    return route.fulfill({ contentType: "application/json", json: detail });
  });
  await page.route(
    `**/api/delivery-corrections/${correctionId}/authorization`,
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: {
          code: "delivery_correction_version_conflict",
          correlationId: "correction-conflict",
          kind: "conflict",
          message: "Correction version changed.",
        },
        status: 409,
      }),
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: new RegExp(correctionId) }).click();
  await page.getByLabel(/I reviewed the original/).check();
  await page
    .getByRole("button", { name: "Authorize and post correction" })
    .click();
  await expect(
    page.getByText("Record changed — review required"),
  ).toBeVisible();
  await expect(page.getByText(/correction-conflict/)).toBeVisible();
  await expect(
    page.getByLabel("Original and proposed quantities"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Reload current record" }).click();
  await expect.poll(() => detailReads).toBe(2);
});

test("posted history links original and replacement receipt identities", async ({
  page,
}) => {
  const replacementId = "113508a8-7918-4c0c-a040-8bc9bb9e7152";
  const posted = {
    ...detail,
    authorized_at: "2026-08-11T09:00:00Z",
    authorized_by: "approver-2",
    receipt_effect: {
      ...detail.receipt_effect,
      replacement_delivery_receipt_id: replacementId,
      replacement_document_status: "ready",
      replacement_number: "DR-MNL-00000043",
    },
    draft_invoice_effect: {
      ...detail.draft_invoice_effect,
      replacement_draft_invoice_id: null,
      status: "completed",
    },
    status: "posted",
    version: 2,
  };
  await page.route(
    "**/api/delivery-corrections?status=pending_authorization",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: { items: [], total: 0 },
      }),
  );
  await page.route("**/api/delivery-corrections?status=posted", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [posted], total: 1 },
    }),
  );
  await page.route(`**/api/delivery-corrections/${correctionId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: posted }),
  );
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        contentType: "application/json",
        json: {
          access_url: "https://documents.test/receipt-42",
          expires_at: "2026-08-11T10:00:00Z",
        },
      });
    }
    return route.fulfill({ contentType: "application/json", json: receipt });
  });
  await page.route(`**/api/delivery-receipts/${replacementId}`, (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        contentType: "application/json",
        json: {
          access_url: "https://documents.test/receipt-43",
          expires_at: "2026-08-11T10:00:00Z",
        },
      });
    }
    return route.fulfill({
      contentType: "application/json",
      json: { ...receipt, delivery_receipt_id: replacementId },
    });
  });
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: "Posted chain" }).click();
  await page.getByRole("button", { name: new RegExp(correctionId) }).click();
  await expect(
    page.getByRole("link", { name: "DR-MNL-00000042" }),
  ).toHaveAttribute("href", "#");
  await expect(
    page.getByRole("link", { name: "DR-MNL-00000043" }),
  ).toHaveAttribute("href", "#");

  const openedUrls: string[] = [];
  await page.exposeFunction("recordOpenedUrl", (url: string) =>
    openedUrls.push(url),
  );
  await page.evaluate(() => {
    const originalOpen = window.open;
    window.open = (url?: string | URL, target?: string, features?: string) => {
      if (url !== undefined && url !== null) {
        const urlString = typeof url === "string" ? url : url.toString();
        void (
          window as unknown as { recordOpenedUrl: (u: string) => void }
        ).recordOpenedUrl(urlString);
      }
      return originalOpen(url, target, features);
    };
  });

  await page.getByRole("link", { name: "DR-MNL-00000042" }).click();
  await expect
    .poll(() => openedUrls)
    .toContain("https://documents.test/receipt-42");
  await page.getByRole("link", { name: "DR-MNL-00000043" }).click();
  await expect
    .poll(() => openedUrls)
    .toContain("https://documents.test/receipt-43");

  await expect(page.getByLabel("Complete audit chain")).toContainText(
    "approver-2",
  );
  await expect(
    page.getByText("No replacement source — corrected accepted total is zero"),
  ).toBeVisible();
});

test("shows sequential correction lineage and opens a linked predecessor directly", async ({
  page,
}) => {
  const previousCorrectionId = "a241427a-9442-4c31-8591-230160028a2a";
  const priorReceiptId = "473508a8-7918-4c0c-a040-8bc9bb9e7152";
  const sequentialReceipt = {
    ...receipt,
    correction_id: previousCorrectionId,
    correction_status: "corrected",
    corrects_delivery_receipt_id: priorReceiptId,
    created_by_correction_id: previousCorrectionId,
    superseded_by_correction_id: correctionId,
  };
  const previousDetail = {
    ...detail,
    correction_id: previousCorrectionId,
    original_delivery_receipt_id: priorReceiptId,
    receipt_effect: {
      ...detail.receipt_effect,
      original_delivery_receipt_id: priorReceiptId,
      original_number: "DR-MNL-00000041",
    },
    status: "posted",
    version: 2,
  };
  await routeReview(page);
  await page.route("**/api/delivery-corrections?status=posted", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [previousDetail], total: 1 },
    }),
  );
  await page.unroute(`**/api/delivery-receipts/${receiptId}`);
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: sequentialReceipt }),
  );
  await page.route(
    `**/api/delivery-corrections/${previousCorrectionId}`,
    (route) =>
      route.fulfill({ contentType: "application/json", json: previousDetail }),
  );
  await page.route(`**/api/delivery-receipts/${priorReceiptId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        ...receipt,
        correction_id: previousCorrectionId,
        correction_status: "corrected",
        delivery_receipt_id: priorReceiptId,
        number: "DR-MNL-00000041",
        replacement_delivery_receipt_id: receiptId,
        superseded_by_correction_id: previousCorrectionId,
      },
    }),
  );
  await page.goto(`/delivery-corrections?correction=${correctionId}`);
  await expect(page.getByLabel("Receipt correction lineage")).toContainText(
    previousCorrectionId,
  );
  await expect(
    page.getByRole("link", {
      name: `Previous correction ${previousCorrectionId}`,
    }),
  ).toHaveAttribute(
    "href",
    `/delivery-corrections?correction=${previousCorrectionId}`,
  );
  await expect(
    page.getByRole("link", { name: `Previous receipt ${priorReceiptId}` }),
  ).toHaveAttribute("href", "#");
  await expect(page.getByLabel("Receipt correction chain")).toContainText(
    correctionId,
  );
  await page
    .getByRole("link", {
      name: `Previous correction ${previousCorrectionId}`,
    })
    .click();
  await expect(page).toHaveURL(
    `/delivery-corrections?correction=${previousCorrectionId}`,
  );
  await expect(page.getByLabel("Receipt correction chain")).toContainText(
    previousCorrectionId,
  );
});

test("blocks a new proposal from a superseded receipt and links its chain head", async ({
  page,
}) => {
  const replacementId = "113508a8-7918-4c0c-a040-8bc9bb9e7152";
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        ...receipt,
        correction_id: correctionId,
        correction_status: "corrected",
        replacement_delivery_receipt_id: replacementId,
        superseded_by_correction_id: correctionId,
      },
    }),
  );
  await page.goto("/delivery-corrections");
  await page.getByRole("button", { name: "Request correction" }).click();
  await page.getByLabel("Delivery Receipt ID").fill(receiptId);
  await page.getByRole("button", { name: "Review original" }).click();
  await expect(page.getByText("Not the current chain head")).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: `Open replacement receipt ${replacementId}`,
    }),
  ).toHaveAttribute("href", "#");
  await expect(
    page.getByRole("button", { name: "Request independent approval" }),
  ).toBeDisabled();
});

test("labels a posted zero-accepted correction as intentionally having no replacements", async ({
  page,
}) => {
  const postedWithoutReplacement = {
    ...detail,
    authorized_at: "2026-08-11T09:00:00Z",
    authorized_by: "approver-2",
    draft_invoice_effect: {
      ...detail.draft_invoice_effect,
      replacement_draft_invoice_id: null,
      status: "completed",
    },
    receipt_effect: {
      ...detail.receipt_effect,
      replacement_delivery_receipt_id: null,
      replacement_document_status: null,
      replacement_number: null,
    },
    status: "posted",
    version: 2,
  };
  await page.route(
    "**/api/delivery-corrections?status=pending_authorization",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: { items: [], total: 0 },
      }),
  );
  await page.route("**/api/delivery-corrections?status=posted", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [postedWithoutReplacement], total: 1 },
    }),
  );
  await page.route(`**/api/delivery-corrections/${correctionId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: postedWithoutReplacement,
    }),
  );
  await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
    route.fulfill({ contentType: "application/json", json: receipt }),
  );
  await page.goto(`/delivery-corrections?correction=${correctionId}`);
  await expect(
    page.getByText("No replacement receipt — accepted total is zero"),
  ).toBeVisible();
  await expect(
    page.getByText("No replacement source — corrected accepted total is zero"),
  ).toBeVisible();
  await expect(page.getByText("Pending authorization")).toHaveCount(0);
});

test("makes corrections discoverable from delivery fulfillment navigation", async ({
  page,
}) => {
  await page.route("**/api/deliveries**", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [], total: 0 },
    }),
  );
  await page.goto("/deliveries");
  await expect(page.getByRole("link", { name: "Corrections" })).toHaveAttribute(
    "href",
    "/delivery-corrections",
  );
  await page.goto("/delivery-exceptions");
  await expect(page.getByRole("link", { name: "Corrections" })).toHaveAttribute(
    "href",
    "/delivery-corrections",
  );
});

test("receipt detail and signed access remain available to correction-role sessions", async () => {
  const originalToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  const originalFetch = globalThis.fetch;
  process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN = "correction-role-token";
  const requests: Request[] = [];
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    requests.push(request);
    const access = request.url.endsWith("/access");
    return Response.json(
      access
        ? {
            access_url: "https://documents.test/receipt-42",
            expires_at: "2026-08-11T10:00:00Z",
          }
        : receipt,
    );
  };
  try {
    const context = { params: Promise.resolve({ receiptId }) };
    const detailResponse = await getReceipt(
      new Request("http://web.test/receipt"),
      context,
    );
    const accessResponse = await accessReceipt(
      new Request("http://web.test/receipt", { method: "POST" }),
      context,
    );
    expect(detailResponse.status).toBe(200);
    await expect(detailResponse.json()).resolves.toMatchObject({
      correction_status: "current",
      delivery_receipt_id: receiptId,
      evidence_ids: receipt.evidence_ids,
    });
    expect(accessResponse.status).toBe(200);
    await expect(accessResponse.json()).resolves.toMatchObject({
      access_url: "https://documents.test/receipt-42",
    });
    expect(requests).toHaveLength(2);
    for (const request of requests)
      expect(request.headers.get("Authorization")).toBe(
        "Bearer correction-role-token",
      );
  } finally {
    globalThis.fetch = originalFetch;
    if (originalToken === undefined)
      delete process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
    else process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN = originalToken;
  }
});

test.describe("mobile-web", () => {
  test.use({
    hasTouch: true,
    isMobile: true,
    viewport: { height: 844, width: 390 },
  });

  test("shows correction ledger, chain, quantities, and effects responsively", async ({
    page,
  }) => {
    await routeReview(page);
    await page.goto("/delivery-corrections");
    await expect(
      page.getByRole("button", { name: new RegExp(correctionId) }),
    ).toBeVisible();
    await expect(page.locator(".correction-ledger-head")).toContainText(
      "Requested",
    );
    await expect(page.locator(".correction-ledger-head")).toContainText(
      "Value effect",
    );
    await page.getByRole("button", { name: new RegExp(correctionId) }).click();
    await expect(page.getByLabel("Receipt correction chain")).toContainText(
      "DR-MNL-00000042",
    );
    await expect(
      page.getByLabel("Original and proposed quantities"),
    ).toContainText("-1.000000");
    await expect(page.locator(".correction-effects")).toContainText(
      "1 reversal · 1 replacement",
    );
  });

  test("requests a correction on a narrow viewport", async ({ page }) => {
    await page.route(`**/api/delivery-receipts/${receiptId}`, (route) =>
      route.fulfill({ contentType: "application/json", json: receipt }),
    );
    await page.route(
      `**/api/delivery-receipts/${receiptId}/corrections`,
      (route) =>
        route.fulfill({
          contentType: "application/json",
          json: detail,
          status: 201,
        }),
    );
    await page.goto("/delivery-corrections");
    await page.getByRole("button", { name: "Request correction" }).click();
    await page.getByLabel("Delivery Receipt ID").fill(receiptId);
    await page.getByRole("button", { name: "Review original" }).click();
    await page
      .getByLabel(`Accepted quantity for ${deliveryLineId}`)
      .fill("1.000000");
    await page
      .getByLabel(`Damaged quantity for ${deliveryLineId}`)
      .fill("1.000000");
    await page.getByLabel("Correction reason").fill("Damaged on mobile view");
    await page.getByLabel(/Retained proof 1/).check();
    await page
      .getByRole("button", { name: "Request independent approval" })
      .click();
    await expect(
      page.getByRole("heading", {
        name: "Waiting for an independent approver.",
      }),
    ).toBeVisible();
  });
});

test("BFF routes normalize upstream authentication and forward command identity", async () => {
  const originalToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  const originalFetch = globalThis.fetch;
  process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN = "test-token";
  const requests: Request[] = [];
  globalThis.fetch = async (input, init) => {
    requests.push(new Request(input, init));
    return new Response(
      JSON.stringify({
        error: {
          code: "authentication_required",
          correlation_id: "expired",
          message: "Token expired.",
        },
      }),
      { headers: { "Content-Type": "application/json" }, status: 401 },
    );
  };
  try {
    const command = () =>
      new Request("http://web.test/action", {
        body: JSON.stringify({
          command: { expected_correction_version: 1 },
          idempotencyKey: "stable-key",
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
    const responses = await Promise.all([
      listCorrections(
        new Request("http://web.test/api/delivery-corrections?status=posted"),
      ),
      getCorrection(new Request("http://web.test/detail"), {
        params: Promise.resolve({ correctionId }),
      }),
      getReceipt(new Request("http://web.test/receipt"), {
        params: Promise.resolve({ receiptId }),
      }),
      createCorrection(command(), { params: Promise.resolve({ receiptId }) }),
      authorizeCorrection(command(), {
        params: Promise.resolve({ correctionId }),
      }),
    ]);
    for (const response of responses) {
      expect(response.status).toBe(401);
      await expect(response.json()).resolves.toMatchObject({
        code: "authentication_required",
        correlationId: "expired",
        kind: "unauthenticated",
      });
    }
    expect(
      requests.some(
        (request) => request.headers.get("Idempotency-Key") === "stable-key",
      ),
    ).toBe(true);
    expect(requests[0]?.url).toContain("status=posted");
  } finally {
    globalThis.fetch = originalFetch;
    if (originalToken === undefined)
      delete process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
    else process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN = originalToken;
  }
});
