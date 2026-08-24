import { expect, test } from "@playwright/test";

test("public landing presents TradeFlow as a commercial product without an API", async ({
  page,
}) => {
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Control every order from sale to settlement.",
  );
  await expect(page.getByRole("link", { name: "Open live demo" })).toHaveCount(
    3,
  );
  await expect(
    page.getByRole("heading", { name: "Keep every order moving." }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "One system for every team." }),
  ).toBeVisible();
  await expect(page.getByText(/portfolio release|my role/i)).toHaveCount(0);
  await expect(page.locator(".hero-product-frame img")).toHaveAttribute(
    "src",
    /operations-overview/u,
  );
});

test("commercial navigation opens the operations overview", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Open live demo" }).first().click();
  await expect(page).toHaveURL(/\/demo$/u);
  await expect(
    page.getByRole("heading", { name: "Operations overview" }),
  ).toBeVisible();
});

test("public responses contain no bearer credential", async ({ request }) => {
  for (const path of ["/", "/demo", "/api/demo/status"]) {
    const response = await request.get(path);
    const body = await response.text();
    expect(body).not.toContain("Authorization: Bearer");
    expect(body).not.toMatch(
      /eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/u,
    );
  }
});

test("public links resolve", async ({ page }) => {
  await page.goto("/");
  for (const href of [
    "/",
    "/case-study",
    "/demo",
    "/robots.txt",
    "/sitemap.xml",
  ]) {
    const response = await page.request.get(href);
    expect(response.ok(), `${href} should resolve`).toBeTruthy();
  }
});

test("commercial homepage has no horizontal overflow", async ({ page }) => {
  await page.goto("/");
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("edge protection limits one visitor before the shared API token", async ({
  request,
}, testInfo) => {
  const visitor = `commercial-abuse-${testInfo.project.name}-${crypto.randomUUID()}`;
  let finalStatus = 0;
  for (let index = 0; index < 31; index += 1) {
    finalStatus = (
      await request.post("/api/platform-session", {
        headers: { Cookie: `tradeflow_demo_visitor=${visitor}` },
      })
    ).status();
  }
  expect(finalStatus).toBe(429);
});
