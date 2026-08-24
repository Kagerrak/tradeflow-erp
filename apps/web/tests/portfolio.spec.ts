import { expect, test } from "@playwright/test";

test("public landing explains the product without an API", async ({ page }) => {
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(
    "Every handoff",
  );
  await expect(page.getByText("Order to payment")).toBeVisible();
  await expect(page.getByText("My role")).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Explore the live demo/i }).first(),
  ).toHaveAttribute("href", "/demo");
});

test("marketing navigation opens the guided demo", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("link", { name: /Explore the live demo/i })
    .first()
    .click();
  await expect(page).toHaveURL(/\/demo$/);
  await expect(
    page.getByRole("heading", { name: "Follow one accountable handoff." }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Open record" })).toHaveCount(4);
});

test("public responses contain no bearer credential", async ({ request }) => {
  for (const path of ["/", "/demo", "/api/demo/status"]) {
    const response = await request.get(path);
    const body = await response.text();
    expect(body).not.toContain("Authorization: Bearer");
    expect(body).not.toMatch(
      /eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/,
    );
  }
});

test("public links resolve on desktop and mobile", async ({ page }) => {
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

test("edge protection limits one visitor before the shared API token", async ({
  request,
}) => {
  let finalStatus = 0;
  for (let index = 0; index < 31; index += 1) {
    finalStatus = (await request.post("/api/platform-session")).status();
  }
  expect(finalStatus).toBe(429);
});
