import { expect, test } from "@playwright/test";

import { GET as getExceptions } from "../app/api/delivery-exceptions/route";
import { POST as postRetry } from "../app/api/deliveries/[deliveryId]/retries/route";
import { POST as postReturn } from "../app/api/deliveries/[deliveryId]/return-to-warehouse-receipts/route";
import { POST as postResolution } from "../app/api/delivery-investigations/[investigationId]/resolutions/route";

const exception = {
  age_days: 4,
  custody: "in_transit",
  delivery_id: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
  delivery_version: 2,
  delivery_line_id: "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9",
  evidence_ids: ["dc0de2b2-e6d8-4d4f-b898-42398bab8eaa"],
  exception_case_id: "64ac8d81-3acd-4d66-a787-89ca1781f35f",
  exception_kind: "damaged",
  investigation_id: null,
  opened_at: "2026-08-06T09:00:00Z",
  open_quantity_base: "2.000000",
  original_quantity_base: "2.000000",
  responsible_party_type: "carrier",
  status: "awaiting_return",
  tracking_policy: "untracked",
  version: 1,
};

test("shows aging, custody, evidence, and quarantine-only return action", async ({
  page,
}) => {
  await page.route("**/api/delivery-exceptions?queue=return_pending", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [exception] },
    }),
  );
  await page.goto("/delivery-exceptions");
  await expect(page.getByText("4d")).toBeVisible();
  await expect(page.getByText(/damaged · in transit/)).toBeVisible();
  await page.getByRole("button", { name: /Delivery line/ }).click();
  await expect(page.getByText("1 retained")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Receive into Quarantine" }),
  ).toBeVisible();
});

test("posts Delivery version and selected evidence for warehouse return", async ({
  page,
}) => {
  await page.route("**/api/delivery-exceptions?queue=return_pending", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [exception] },
    }),
  );
  let posted: Record<string, unknown> | null = null;
  await page.route(
    "**/api/deliveries/*/return-to-warehouse-receipts",
    (route) => {
      posted = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        contentType: "application/json",
        json: { status: "received" },
      });
    },
  );
  await page.goto("/delivery-exceptions");
  await page.getByRole("button", { name: /Delivery line/ }).click();
  await page.getByLabel("Resolution reason").fill("Seal broken");
  await page.getByRole("button", { name: "Receive into Quarantine" }).click();
  await expect.poll(() => posted).not.toBeNull();
  expect(posted).toMatchObject({
    command: {
      evidence_ids: exception.evidence_ids,
      expected_delivery_version: 2,
    },
  });
});

test("creates a linked Retry Delivery from still-undelivered custody", async ({
  page,
}) => {
  const retry = {
    ...exception,
    exception_kind: "still_undelivered",
    evidence_ids: [],
    status: "awaiting_retry",
  };
  await page.route("**/api/delivery-exceptions?queue=*", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [retry] },
    }),
  );
  let posted: Record<string, unknown> | null = null;
  await page.route("**/api/deliveries/*/retries", (route) => {
    posted = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      contentType: "application/json",
      json: { status: "dispatched" },
    });
  });
  await page.goto("/delivery-exceptions");
  await page.getByRole("button", { name: "Retry delivery" }).click();
  await page.getByRole("button", { name: /Delivery line/ }).click();
  await page.getByLabel("Retry assigned coordinator").fill("driver-2");
  await page.getByLabel("Resolution reason").fill("Recipient rescheduled");
  await page.getByRole("button", { name: "Create Retry Delivery" }).click();
  await expect.poll(() => posted).not.toBeNull();
  expect(posted).toMatchObject({
    command: {
      assigned_to: "driver-2",
      expected_delivery_version: 2,
      reason: "Recipient rescheduled",
    },
  });
});

test("retains reason and quantity when return custody conflicts", async ({
  page,
}) => {
  await page.route("**/api/delivery-exceptions?queue=return_pending", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [exception] },
    }),
  );
  await page.route(
    "**/api/deliveries/*/return-to-warehouse-receipts",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: {
          code: "delivery_exception_version_conflict",
          correlationId: "return-conflict",
          kind: "conflict",
          message: "The open return quantity changed.",
        },
        status: 409,
      }),
  );
  await page.goto("/delivery-exceptions");
  await page.getByRole("button", { name: /Delivery line/ }).click();
  await page.getByLabel("Resolution reason").fill("Seal broken in transit");
  await page.getByRole("button", { name: "Receive into Quarantine" }).click();
  await expect(
    page.getByText("Custody changed — review required"),
  ).toBeVisible();
  await expect(page.getByLabel("Resolution reason")).toHaveValue(
    "Seal broken in transit",
  );
  await expect(page.getByText(/return-conflict/)).toBeVisible();
});

test("renders forbidden and queue-specific empty states", async ({ page }) => {
  await page.route("**/api/delivery-exceptions?queue=return_pending", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        code: "delivery_exception_read_required",
        correlationId: "exception-forbidden",
        kind: "forbidden",
        message: "Exception review capability is required.",
      },
      status: 403,
    }),
  );
  await page.goto("/delivery-exceptions");
  await expect(
    page.getByRole("heading", { name: "Exception queue forbidden" }),
  ).toBeVisible();
  await expect(page.getByText(/exception-forbidden/)).toBeVisible();

  await page.unroute("**/api/delivery-exceptions?queue=return_pending");
  await page.route("**/api/delivery-exceptions?queue=return_pending", (route) =>
    route.fulfill({ contentType: "application/json", json: { items: [] } }),
  );
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "No stock is awaiting warehouse return",
    }),
  ).toBeVisible();
});

test("renders queue authentication as sign-in required without a false retry", async ({
  page,
}) => {
  await page.route("**/api/delivery-exceptions?queue=return_pending", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        code: "authentication_required",
        correlationId: "exception-auth",
        kind: "unauthenticated",
        message: "Sign in before reviewing exception custody.",
      },
      status: 401,
    }),
  );
  await page.goto("/delivery-exceptions");
  await expect(
    page.getByRole("heading", {
      name: "Sign in required for exception custody",
    }),
  ).toBeVisible();
  await expect(page.getByText(/exception-auth/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry queue" })).toHaveCount(
    0,
  );
});

test("renders action authentication separately from forbidden", async ({
  page,
}) => {
  await page.route("**/api/delivery-exceptions?queue=return_pending", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [exception] },
    }),
  );
  await page.route(
    "**/api/deliveries/*/return-to-warehouse-receipts",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: {
          code: "authentication_required",
          correlationId: "return-auth",
          kind: "unauthenticated",
          message: "Sign in before receiving custody.",
        },
        status: 401,
      }),
  );
  await page.goto("/delivery-exceptions");
  await page.getByRole("button", { name: /Delivery line/ }).click();
  await page.getByLabel("Resolution reason").fill("Seal broken");
  await page.getByRole("button", { name: "Receive into Quarantine" }).click();
  await expect(page.getByText("Sign in required before posting")).toBeVisible();
  await expect(page.getByText(/return-auth/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Receive into Quarantine" }),
  ).toBeDisabled();
});

test("BFF routes normalize upstream HTTP 401 as unauthenticated", async () => {
  const originalToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  const originalFetch = globalThis.fetch;
  process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN = "test-token";
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        error: {
          code: "authentication_required",
          correlation_id: "upstream-auth",
          message: "Token expired.",
        },
      }),
      { headers: { "Content-Type": "application/json" }, status: 401 },
    );
  try {
    const commandRequest = () =>
      new Request("http://web.test/action", {
        body: JSON.stringify({ command: {}, idempotencyKey: "auth-test" }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
    const responses = await Promise.all([
      getExceptions(
        new Request(
          "http://web.test/api/delivery-exceptions?queue=return_pending",
        ),
      ),
      postReturn(commandRequest(), {
        params: Promise.resolve({ deliveryId: exception.delivery_id }),
      }),
      postRetry(commandRequest(), {
        params: Promise.resolve({ deliveryId: exception.delivery_id }),
      }),
      postResolution(commandRequest(), {
        params: Promise.resolve({
          investigationId: "64ac8d81-3acd-4d66-a787-89ca1781f35f",
        }),
      }),
    ]);
    for (const response of responses) {
      expect(response.status).toBe(401);
      await expect(response.json()).resolves.toMatchObject({
        code: "authentication_required",
        correlationId: "upstream-auth",
        kind: "unauthenticated",
      });
    }
  } finally {
    globalThis.fetch = originalFetch;
    if (originalToken === undefined)
      delete process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
    else process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN = originalToken;
  }
});
