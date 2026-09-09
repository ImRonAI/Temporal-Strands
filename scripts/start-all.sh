#!/usr/bin/env bash
# Gwen All-In-One Local Startup & Development Script
# Runs Temporal, Worker, FastAPI Orchestrator, and Next.js frontend concurrently.
set -Eeuo pipefail

cd "$(dirname "$0")/.."

echo "==> Checking prerequisites..."
command -v pnpm >/dev/null || { echo "pnpm is required" >&2; exit 1; }
command -v uv >/dev/null || { echo "uv is required" >&2; exit 1; }
command -v temporal >/dev/null || { echo "Temporal CLI is required" >&2; exit 1; }

if [ ! -d "orchestrator/.venv" ]; then
  echo "==> Setting up Python virtual environment..."
  cd orchestrator
  uv venv --python 3.13 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
  cd ..
fi

if [ ! -d "node_modules" ]; then
  echo "==> Installing Node dependencies..."
  pnpm install
fi

echo "==> Cleaning stale ports..."
bash scripts/free-ports.sh || true

echo "==> Starting Gwen stack (Temporal, Worker, API, Web)..."
pnpm dev:all
