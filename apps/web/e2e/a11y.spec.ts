import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { PAGES, signIn } from "./helpers";

/* WCAG 2.1 A/AA automated checks (axe-core) on every page in both themes. Automated checks catch a subset of issues;
 * keyboard and screen-reader passes are still needed. */
for (const theme of ["dark", "light"] as const) {
  test(`accessibility (${theme} theme): no serious or critical axe violations`, async ({ page, context }) => {
    test.skip(test.info().project.name !== "desktop", "one viewport is enough for rule coverage");
    await signIn(page);
    await context.addCookies([{ name: "apex_theme", value: theme, url: page.url() }]);
    const problems: string[] = [];
    for (const path of PAGES) {
      await page.goto(path);
      // audit the rendered page, not the streaming skeleton
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 20_000 });
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      for (const v of res.violations.filter((x) => x.impact === "serious" || x.impact === "critical")) {
        problems.push(`${path} ${v.id} (${v.impact}): ${v.nodes.length} node(s), e.g. ${v.nodes[0]?.target.join(" ")} ${v.nodes[0]?.html.slice(0, 160)}`);
      }
    }
    expect(problems).toEqual([]);
  });
}
