import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "server-only": fileURLToPath(new URL("./tests/support/server-only-stub.ts", import.meta.url)),
    },
  },
  test: {
    include: ["tests/unit/**/*.test.ts", "tests/integration/**/*.test.ts"],
    environment: "node",
    testTimeout: 30000,
    hookTimeout: 60000,
    pool: "forks",
    // a shared scratch Postgres (APEX_TEST_DATABASE_URL) must not be reset by two files at once
    fileParallelism: !process.env.APEX_TEST_DATABASE_URL,
    // `pnpm test:coverage`: server code is what the unit/integration suites exercise; pages and components are covered
    // by the Playwright journeys instead
    coverage: { provider: "v8", include: ["src/server/**", "src/lib/**", "src/app/api/**"], reporter: ["text-summary", "json-summary"] },
  },
});
