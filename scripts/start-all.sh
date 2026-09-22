#!/usr/bin/env bash
# Single entry point for pnpm dev:all.
# Always tear down this checkout's previous stack, then start a fresh one.
set -Eeuo pipefail

cd "$(dirname "$0")/.."

echo "==> Resetting previous Gwen stack..."
bash scripts/free-ports.sh

echo "==> Checking prerequisites..."
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
for tool in pnpm uv temporal docker; do
  command -v "$tool" >/dev/null || { echo "$tool is required on PATH" >&2; exit 1; }
done

if [ ! -d "node_modules" ]; then
  pnpm install --frozen-lockfile
fi

if [ ! -x "orchestrator/.venv/bin/python" ]; then
  echo "==> Setting up Python virtual environment..."
  cd orchestrator
  uv venv --python 3.13 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
  cd ..
else
  # Reconcile the pinned requirements on warm starts too. uv skips packages
  # already installed; an existing venv is not proof it has today's deps.
  (cd orchestrator && uv pip install --python .venv/bin/python -r requirements.txt)
fi
uv pip check --python orchestrator/.venv/bin/python

context="${DESKTOP_DOCKER_CONTEXT:-colima}"
if ! docker --context "$context" info >/dev/null 2>&1; then
  case "$context" in
    colima) profile=default ;;
    colima-*) profile="${context#colima-}" ;;
    *) echo "Docker context '$context' is unavailable. Start its Docker daemon and retry." >&2; exit 1 ;;
  esac
  command -v colima >/dev/null || { echo "colima is required to start '$context'" >&2; exit 1; }
  echo "==> Starting Colima profile '$profile' with its saved configuration..."
  colima start --profile "$profile"
  docker --context "$context" info >/dev/null
  # The first reset skipped Docker if Colima was down. Reclaim the leftover
  # desktop container now that the daemon is up, before image prepare/start.
  echo "==> Resetting leftover desktop container..."
  bash scripts/free-ports.sh
fi

echo "==> Preparing desktop image..."
bash scripts/run-desktop.sh prepare

echo "==> Starting Gwen stack; web waits for backend and desktop readiness..."
exec pnpm exec concurrently --kill-others --names temporal,worker,api,desktop,web \
  -c magenta,yellow,cyan,blue,green \
  "pnpm dev:temporal" "pnpm dev:worker" "pnpm dev:api" \
  "pnpm dev:desktop" "pnpm dev:web"
