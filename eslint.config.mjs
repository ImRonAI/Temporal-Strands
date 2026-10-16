import { defineConfig, globalIgnores } from "eslint/config"
import nextVitals from "eslint-config-next/core-web-vitals"
import nextTypeScript from "eslint-config-next/typescript"

export default defineConfig([
  ...nextVitals,
  ...nextTypeScript,
  globalIgnores([
    ".next/**",
    ".next-desktop-acceptance/**",
    ".next-obsidian-inspect/**",
    "**/.worktrees/**",
    "**/.kilo/worktrees/**",
    "orchestrator/.venv/**",
    "orchestrator/strands-tools/**",
    "next-env.d.ts",
  ]),
])
