import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      // React dev tooling needs eval; production builds never get 'unsafe-eval'
      `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data:",
      "font-src 'self'",
      "connect-src 'self'",
      "frame-ancestors 'none'",
      "base-uri 'none'",
      "form-action 'self' https://discord.com",
      "object-src 'none'",
    ].join("; "),
  },
];

const config: NextConfig = {
  reactStrictMode: true,
  agentRules: false, // do not write framework AGENTS.md/CLAUDE.md files into the repo
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  serverExternalPackages: ["@electric-sql/pglite", "postgres"],
  outputFileTracingRoot: resolve(here, "../.."),
  outputFileTracingIncludes: {
    "/**": ["../../supabase/migrations/**/*.sql", "../../fixtures/demo/web-seed.json", "../../packages/quant/calendar/*.json"],
  },
  turbopack: { root: resolve(here, "../..") },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default config;
