import { expect, type Page } from "@playwright/test";

export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await expect(page.getByText("DEMO mode.")).toBeVisible();
  await Promise.all([page.waitForURL((u) => !u.pathname.startsWith("/login")), page.getByRole("button", { name: /Enter local DEMO/ }).click()]);
}

export const PAGES = ["/", "/signals", "/positions", "/catalysts", "/lab", "/models", "/alerts", "/operations", "/settings", "/onboarding"];

export async function noHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "page must not scroll horizontally").toBeLessThanOrEqual(0);
}
