import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "out/**",
      "build/**",
      "next-env.d.ts",
      // Generated from the backend's OpenAPI document by `npm run api:types`.
      // Linting it would mean editing it, and the next regeneration would
      // throw the edits away.
      "lib/api/schema.d.ts",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  {
    // The end-to-end suite is Node, not React. Playwright fixtures take a
    // callback conventionally named `use`, and the React hooks rule reads
    // that as a call to React's `use` hook inside a non-component - a false
    // positive that would otherwise be silenced by renaming the parameter
    // away from the name every Playwright example uses.
    files: ["e2e/**/*.ts", "playwright.config.ts"],
    rules: {
      "react-hooks/rules-of-hooks": "off",
    },
  },
];

export default eslintConfig;
