import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";

test("contracts validate via tsx", () => {
  const code = `
    import { validateContract } from "./src/validators.ts";
    const ok = validateContract("PositionFill", { price: "180.5", quantity: "20", fees: "0", fill_at: "2026-10-20T14:31:00Z" });
    const bad = validateContract("PositionFill", { price: -1, quantity: "20", fees: "0", fill_at: "2026-10-20" });
    const extra = validateContract("PositionFill", { price: "1", quantity: "1", fees: "0", fill_at: "2026-10-20T14:31:00Z", x: 1 });
    if (!ok.ok || bad.ok || extra.ok) process.exit(1);
  `;
  const r = spawnSync("npx", ["tsx", "--eval", code], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
});
