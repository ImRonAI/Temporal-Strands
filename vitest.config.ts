import path from "node:path"
import { configDefaults, defineConfig } from "vitest/config"

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    // Vendored Python-side packages (strands-tools working copy and its
    // .venv site-packages install) ship their own TS test suites with
    // undeclared dependencies; they are not app source.
    exclude: [
      ...configDefaults.exclude,
      "orchestrator/.venv/**",
      "orchestrator/strands-tools/**",
    ],
  },
})
