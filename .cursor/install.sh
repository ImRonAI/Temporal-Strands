#!/usr/bin/env bash
# Idempotent Cloud Agent install for the temporal-strands (v0 chat) repo.
#
# Prepares three toolchains used by `pnpm dev:all`:
#   1. Next.js frontend  (pnpm)
#   2. Temporal dev server CLI
#   3. Python orchestrator virtualenv (orchestrator/.venv)
#
# Safe to run repeatedly: every step checks for existing state first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> [1/3] Frontend dependencies (pnpm)"
corepack enable >/dev/null 2>&1 || true
pnpm install --frozen-lockfile

echo "==> [2/3] Temporal CLI"
if ! command -v temporal >/dev/null 2>&1 && [ ! -x "$HOME/.temporalio/bin/temporal" ]; then
  curl -sSf https://temporal.download/cli.sh | sh
else
  echo "    temporal already installed"
fi

echo "==> [3/3] Python orchestrator venv"
# python3.12-venv (ensurepip) is required to create the venv on Debian/Ubuntu.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv || true
  fi
fi

cd "$ROOT/orchestrator"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip -q

# The formation-graph tool is a PRIVATE sibling package referenced by
# requirements.txt as a local path:
#     strands-heterogeneous-graph-tool @ file:../../strands-tools
# It is not published to PyPI. When that sibling checkout is present we install
# the full pinned set; otherwise we install everything else so the frontend +
# Temporal + FastAPI dependency graph is ready, and log a clear notice. This
# mirrors the repo's graceful-degradation philosophy (see telemetry.py).
if [ -d "$ROOT/../strands-tools" ] || [ -d "$ROOT/../../strands-tools" ]; then
  .venv/bin/python -m pip install -r requirements.txt
else
  echo "    NOTE: sibling repo 'strands-tools' not found."
  echo "    Installing orchestrator deps WITHOUT the private"
  echo "    strands-heterogeneous-graph-tool package. The Temporal worker and"
  echo "    FastAPI bridge import 'strands_graph_tool' and will not start until"
  echo "    that sibling repo is provided (and GEMINI_API_KEY / PERPLEXITY_API_KEY"
  echo "    secrets are set)."
  grep -v 'strands-heterogeneous-graph-tool @ file' requirements.txt > /tmp/requirements.no-graph.txt
  .venv/bin/python -m pip install -r /tmp/requirements.no-graph.txt
fi

echo "==> Install complete"
