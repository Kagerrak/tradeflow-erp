import { expect, test } from "@playwright/test";

test.skip(
  process.env.TRADEFLOW_SEEDED_DEMO !== "1",
  "Runs only against the migrated and seeded commercial demo stack.",
);

test("@seeded-demo completes the commercial order-to-payment smoke journey", async ({
  page,
}) => {
  const statusResponse = await page.request.get("/api/demo/status");
  expect(statusResponse.ok()).toBeTruthy();
  const demo = (await statusResponse.json()) as {
    records: {
      customers: Record<string, string>;
      orders: Record<
        string,
        {
          delivery_id?: string;
          fulfillment_order_id?: string;
          order_id: string;
        }
      >;
    };
    status: string;
  };
  expect(demo.status).toBe("ready");

  const partiallyPicked = demo.records.orders.partially_picked;
  const delivery = demo.records.orders.delivery_awaiting_confirmation;
  const customerId = demo.records.customers.HARBOR;
  if (
    !partiallyPicked?.fulfillment_order_id ||
    !delivery?.delivery_id ||
    !customerId
  ) {
    throw new Error(
      "The seeded demo manifest is missing smoke-journey records.",
    );
  }

  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "Control every order from sale to settlement.",
    }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Explore the product" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Operations overview" }),
  ).toBeVisible();
  await expect(page.getByText("Harbor & Pine Retail").first()).toBeVisible();
  await expect(page.getByText("5 requiring attention")).toBeVisible();

  await page.goto(
    `/picking?fulfillmentOrderId=${partiallyPicked.fulfillment_order_id}`,
  );
  await expect(
    page.getByText("Partially picked", { exact: false }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Highland Coffee 500 g" }),
  ).toBeVisible();

  await page.goto(`/deliveries?deliveryId=${delivery.delivery_id}`);
  await expect(
    page.getByRole("button", {
      name: new RegExp(`${delivery.delivery_id} dispatched`, "iu"),
    }),
  ).toBeVisible();

  await page.goto("/finance/statement");
  await page.getByLabel("Customer ID").fill(customerId);
  await page.getByRole("button", { name: "Run statement" }).click();
  await expect(
    page.getByRole("heading", { name: "Closing balance" }),
  ).toBeVisible();
  await expect(page.getByText("partially paid")).toBeVisible();
  await expect(page.getByText("1,371.20", { exact: true })).toBeVisible();
});

test("@seeded-demo dashboard endpoint exposes every authoritative attention metric", async ({
  request,
}) => {
  const response = await request.get("/api/operations/overview");
  expect(response.status()).toBe(200);
  const body = (await response.json()) as {
    action_queue: unknown[];
    finance: { collected_value: string; posted_invoices: number };
    metrics: Array<{ count?: number; key: string }>;
  };
  expect(
    Object.fromEntries(
      body.metrics.map((metric) => [metric.key, metric.count]),
    ),
  ).toMatchObject({
    awaiting_approval: 1,
    awaiting_confirmation: 1,
    awaiting_verification: 1,
    low_stock: 1,
    ready_to_pick: 1,
  });
  expect(body.action_queue).toHaveLength(5);
  expect(body.finance.posted_invoices).toBe(1);
  expect(Number(body.finance.collected_value)).toBe(600);
});
