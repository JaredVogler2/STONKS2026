import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**", "next-env.d.ts"],
  },
  {
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "MemberExpression[object.object.name='process'][object.property.name='env'][property.name=/^NEXT_PUBLIC_.*(KEY|SECRET|TOKEN|PASSWORD)/]",
          message: "Secrets must never be exposed through NEXT_PUBLIC_* variables.",
        },
      ],
    },
  },
  {
    files: ["src/components/client/**/*.tsx", "src/**/*.client.tsx"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [{ group: ["@/server/*", "**/server/**"], message: "Client components must not import server-only modules." }] }],
    },
  },
];

export default config;
