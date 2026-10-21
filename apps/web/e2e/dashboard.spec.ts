import { expect, test } from "@playwright/test";
import { noHorizontalOverflow, PAGES, signIn } from "./helpers";

test.describe("owner dashboard (DEMO)", () => {
  test("unauthenticated visitors are sent to sign-in and see no data", async ({ page }) => {
    await page.goto("/signals");
    await expect(page).toHaveURL(/\/login$/);
    const res = await page.request.get("/api/v1/admin/overview");
    expect(res.status()).toBe(401);
  });

  test("every page renders, is labelled DEMO, has no console errors and no horizontal scroll", async ({ page }, info) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await signIn(page);
    for (const path of PAGES) {
      const res = await page.goto(path);
      expect(res?.status(), path).toBe(200);
      await expect(page.getByText("DEMO replay.").first()).toBeVisible();
      await expect(page.locator("main h1")).toBeVisible();
      await noHorizontalOverflow(page);
      // caret "initial": the default "hide" writes inline caret-color styles into inputs, which React reports as a
      // hydration mismatch when the shot lands before a slow page (Settings) has hydrated
      await page.screenshot({ path: info.outputPath(`${path === "/" ? "home" : path.slice(1)}.png`), fullPage: false, caret: "initial" });
    }
    expect(errors).toEqual([]);
  });

  test("signals: filters are applied server-side and remembered", async ({ page }) => {
    await signIn(page);
    await page.goto("/signals?reset=1");
    const total = Number((await page.getByRole("heading", { name: /signals?$/ }).textContent())?.split(" ")[0]);
    await page.getByLabel("Status").selectOption("CLOSED");
    await expect(page).toHaveURL(/state=CLOSED/);
    const closed = Number((await page.getByRole("heading", { name: /signals?$/ }).textContent())?.split(" ")[0]);
    expect(closed).toBeLessThan(total);
    for (const badge of await page.locator("tbody tr td:nth-child(10) span").first().allTextContents()) expect(badge).toContain("closed");
    await page.goto("/signals");
    await expect(page).toHaveURL(/state=CLOSED/); // restored from the remembered filter
  });

  test("signal detail shows the plan, evidence and no probability for UNVALIDATED setups", async ({ page }) => {
    await signIn(page);
    await page.goto("/signals?reset=1");
    await page.locator("tbody tr a").first().click();
    await expect(page.getByRole("heading", { name: "The plan as published" })).toBeVisible();
    await expect(page.getByText("Why it was selected")).toBeVisible();
    await expect(page.getByRole("img", { name: /daily bars/ })).toBeVisible();
    if (await page.getByText("Unvalidated research setup").count()) {
      await expect(page.getByText(/P \d+% calibrated/)).toHaveCount(0);
    }
  });

  test("a new position entered in the UI is persisted exactly and survives reload", async ({ page }) => {
    await signIn(page);
    await page.goto("/positions");
    await page.getByLabel("Symbol").fill("DMSOFT1");
    await page.getByLabel("Price per share").fill("520.25");
    await page.getByLabel(/^Quantity/).fill("12");
    await page.getByLabel("Fees").fill("1.5");
    await Promise.all([page.waitForURL(/\/positions\/[0-9a-f-]{36}$/), page.getByRole("button", { name: "Record a buy (new position)" }).click()]);
    await expect(page.getByRole("heading", { name: "DMSOFT1 · self-reported position" })).toBeVisible();
    await page.reload();
    const lots = page.getByRole("table", { name: "Lots" });
    await expect(lots.getByText("520.25")).toBeVisible();
    await expect(lots.getByText("12", { exact: true }).first()).toBeVisible();
    // partial sell then history shows both events, FIFO lot reduced
    await page.getByRole("radio", { name: "Record a partial sell" }).click();
    await page.getByLabel("Price per share").fill("530");
    await page.getByLabel(/^Quantity/).fill("5");
    await page.getByRole("button", { name: "Record a partial sell" }).last().click();
    await expect(page.getByRole("status").filter({ hasText: "Saved" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("table", { name: "Position events" }).getByText("partial exit")).toBeVisible();
    await expect(page.getByRole("table", { name: "Lots" }).getByText("7", { exact: true })).toBeVisible();
  });

  test("settings validation errors come from the server and nothing is saved", async ({ page }) => {
    await signIn(page);
    await page.goto("/settings#market");
    const slots = page.getByLabel("Live streamed symbols (IEX cap 30)");
    await slots.fill("31");
    const form = slots.locator("xpath=ancestor::form");
    await form.getByRole("button", { name: "Save" }).click();
    expect(await slots.evaluate((el: HTMLInputElement) => el.validity.rangeOverflow)).toBe(true); // browser blocks it first
    // a cross-field rule only the server knows: reserved slots may not exceed the total
    await slots.fill("10");
    await page.getByLabel("Slots reserved for open positions").fill("20");
    await form.getByRole("button", { name: "Save" }).click();
    await expect(form.getByText(/reserved slots cannot exceed max slots/)).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("Live streamed symbols (IEX cap 30)")).toHaveValue("25"); // unchanged default
  });

  test("keyboard: skip link and primary navigation are reachable", async ({ page, isMobile }) => {
    test.skip(Boolean(isMobile), "desktop sidebar only");
    await signIn(page);
    await page.goto("/");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    await page.keyboard.press("Enter");
    const nav = page.getByRole("navigation", { name: "Primary" });
    await expect(nav.getByRole("link", { name: "Signals Explorer" })).toBeVisible();
    await nav.getByRole("link", { name: "Signals Explorer" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/signals/);
  });

  test("no secrets or server-only env names reach the browser bundle", async ({ page }) => {
    await signIn(page);
    const bodies: string[] = [];
    page.on("response", async (r) => {
      const t = r.headers()["content-type"] ?? "";
      if (t.includes("javascript") || t.includes("html")) bodies.push(await r.text().catch(() => ""));
    });
    for (const p of ["/", "/settings", "/models"]) await page.goto(p);
    const all = bodies.join("\n");
    for (const needle of ["APEX_CONFIG_ENCRYPTION_KEY=", "AGENT_HMAC_SECRET=", "APEX_SESSION_SECRET=", "DATABASE_URL=", "-----BEGIN", "DISCORD_OAUTH_CLIENT_SECRET="]) {
      expect(all.includes(needle), needle).toBe(false);
    }
  });
});
