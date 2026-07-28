import { expect, test, type Page } from "@playwright/test";

const scope = {
  branches: [
    {
      branch_id: "32d48052-c25a-4e75-9888-a356432053ff",
      code: "MNL",
      is_active: true,
      name: "Manila",
      version: 1,
    },
  ],
  capabilities: ["customer:read", "customer:write"],
  user: {
    display_name: "Mina Santos",
    is_operations_administrator: false,
    subject: "sales-mnl",
  },
  warehouses: [],
};
const branchId = "32d48052-c25a-4e75-9888-a356432053ff";

async function routeScope(page: Page) {
  await page.route("**/api/customer-scope", async (route) => {
    await route.fulfill({
      body: JSON.stringify(scope),
      contentType: "application/json",
    });
  });
}

async function fillRequiredDocket(page: Page) {
  await page.getByLabel("Legal name", { exact: true }).fill("Northstar Retail");
  await page.getByLabel("Account number", { exact: true }).fill("MNL-0042");
  await page.getByLabel("Contact name").fill("Maria Santos");
  await page.getByLabel("Contact role").fill("Purchasing");
  await page.getByLabel("Email").fill("maria@northstar.example");
  for (const kind of ["billing", "delivery"]) {
    await page.locator(`input[name="${kind}_line_1"]`).fill("18 Port Road");
    await page.locator(`input[name="${kind}_city"]`).fill("Manila");
    await page.locator(`input[name="${kind}_region"]`).fill("NCR");
    await page.locator(`input[name="${kind}_postal_code"]`).fill("1018");
    await page.locator(`input[name="${kind}_country"]`).fill("PH");
  }
}

test("shows progress while loading the authorized customer directory", async ({
  page,
}) => {
  let release: (() => void) | undefined;
  await page.route("**/api/customer-scope", async (route) => {
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    await route.fulfill({
      body: JSON.stringify(scope),
      contentType: "application/json",
    });
  });

  await page.goto("/customers");
  await expect(
    page.getByRole("status", { name: "Loading customer workspace" }),
  ).toBeVisible();
  release?.();
});

test("shows an empty scoped directory", async ({ page }) => {
  await routeScope(page);
  await page.route("**/api/customers?*", async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        correlationId: "search-1",
        items: [],
        kind: "ready",
        total: 0,
      }),
      contentType: "application/json",
    });
  });

  await page.goto("/customers");

  await expect(
    page.getByRole("heading", { name: "No accounts in this scope" }),
  ).toBeVisible();
  await expect(page.getByText("Manila / MNL")).toBeVisible();
});

test("renders only the server-scoped customer results", async ({ page }) => {
  await routeScope(page);
  await page.route("**/api/customers?*", async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        correlationId: "search-2",
        items: [
          {
            accountNumber: "MNL-0042",
            branchId,
            creditHold: false,
            customerId: "f2bee902-0096-43cb-a204-38c07672661f",
            legalName: "Northstar Retail",
            paymentTimingPolicy: "cash_on_delivery",
            status: "active",
            version: 1,
          },
        ],
        kind: "ready",
        total: 1,
      }),
      contentType: "application/json",
    });
  });

  await page.goto("/customers");

  await expect(
    page.getByRole("cell", { name: "Northstar Retail" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Cash on delivery" }),
  ).toBeVisible();
});

test("explains a forbidden workspace without exposing the form", async ({
  page,
}) => {
  await page.route("**/api/customer-scope", async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        correlationId: "scope-denied",
        kind: "forbidden",
      }),
      contentType: "application/json",
      status: 403,
    });
  });

  await page.goto("/customers");

  await expect(
    page.getByRole("heading", { name: "Customer access is not assigned" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Open new-account docket" }),
  ).toHaveCount(0);
});

test("keeps entered data and shows validation feedback", async ({ page }) => {
  await routeScope(page);
  await page.route("**/api/customers?*", async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        correlationId: "search-3",
        items: [],
        kind: "ready",
        total: 0,
      }),
      contentType: "application/json",
    });
  });
  await page.route("**/api/customers", async (route) => {
    await route.fulfill({
      body: JSON.stringify({ correlationId: "create-1", kind: "validation" }),
      contentType: "application/json",
      status: 422,
    });
  });

  await page.goto("/customers");
  await page.getByRole("button", { name: "Open new-account docket" }).click();
  await fillRequiredDocket(page);
  await page.getByRole("button", { name: "Create customer account" }).click();

  await expect(page.locator(".form-error")).toContainText(
    "Check the docket fields and submit again.",
  );
  await expect(page.locator(".form-error")).toContainText("create-1");
  await expect(page.getByLabel("Legal name", { exact: true })).toHaveValue(
    "Northstar Retail",
  );
});

test("creates a prepaid account and refreshes the directory", async ({
  page,
}) => {
  await routeScope(page);
  let searchCount = 0;
  await page.route("**/api/customers?*", async (route) => {
    searchCount += 1;
    await route.fulfill({
      body: JSON.stringify({
        correlationId: `search-${searchCount}`,
        items:
          searchCount === 1
            ? []
            : [
                {
                  accountNumber: "MNL-0042",
                  branchId,
                  creditHold: false,
                  customerId: "f2bee902-0096-43cb-a204-38c07672661f",
                  legalName: "Northstar Retail",
                  paymentTimingPolicy: "prepaid",
                  status: "active",
                  version: 1,
                },
              ],
        kind: "ready",
        total: searchCount === 1 ? 0 : 1,
      }),
      contentType: "application/json",
    });
  });
  await page.route("**/api/customers", async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        correlationId: "create-2",
        customer: {
          accountNumber: "MNL-0042",
          branchId,
          creditHold: false,
          creditLimit: null,
          customerId: "f2bee902-0096-43cb-a204-38c07672661f",
          legalName: "Northstar Retail",
          paymentTerms: "Due before release",
          paymentTimingPolicy: "prepaid",
          status: "active",
          version: 1,
        },
        kind: "created",
      }),
      contentType: "application/json",
      status: 201,
    });
  });

  await page.goto("/customers");
  await page.getByRole("button", { name: "Open new-account docket" }).click();
  await fillRequiredDocket(page);
  await page.getByLabel("Payment timing").selectOption("prepaid");
  await page.getByRole("button", { name: "Create customer account" }).click();

  await expect(page.getByRole("status")).toContainText("MNL-0042 created");
  await expect(
    page.getByRole("cell", { name: "Northstar Retail" }),
  ).toBeVisible();
});

test("contains keyboard focus and restores it when the docket closes", async ({
  page,
}) => {
  await routeScope(page);
  await page.route("**/api/customers?*", async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        correlationId: "search-focus",
        items: [],
        kind: "ready",
        total: 0,
      }),
      contentType: "application/json",
    });
  });

  await page.goto("/customers");
  const trigger = page.getByRole("button", { name: "Open new-account docket" });
  await trigger.click();
  await expect(page.getByRole("button", { name: "Close" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(
    page.getByRole("button", { name: "Create customer account" }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(trigger).toBeFocused();
});
