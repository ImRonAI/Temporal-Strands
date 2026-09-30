#!/usr/bin/env bash
# Single entry point for pnpm dev:all.
# Always tear down this checkout's previous stack, then start a fresh one.
set -Eeuo pipefail

cd "$(dirname "$0")/.."

echo "==> Checking prerequisites..."
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
for tool in node pnpm uv temporal docker; do
  command -v "$tool" >/dev/null || { echo "$tool is required on PATH" >&2; exit 1; }
done

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
fi

echo "==> Resetting local test sessions and previous Gwen workers..."
bash scripts/free-ports.sh
node scripts/reset-dev-state.mjs

echo "==> Reconciling dependencies..."
pnpm install --frozen-lockfile
if [ ! -x "orchestrator/.venv/bin/python" ]; then
  uv venv --python 3.13 orchestrator/.venv
fi
(cd orchestrator && uv pip install --python .venv/bin/python -r requirements.txt)
uv pip check --python orchestrator/.venv/bin/python

# dev:all is always the disposable local stack, never a remote Temporal queue.
export TEMPORAL_ADDRESS=localhost:7233
export DESKTOP_TEMPORAL_ADDRESS=host.lima.internal:7233
export ORCHESTRATOR_URL=http://127.0.0.1:8787

echo "==> Preparing desktop image..."
bash scripts/run-desktop.sh prepare

echo "==> Starting Gwen stack; web waits for backend and desktop readiness..."
exec pnpm exec concurrently --kill-others --kill-timeout 15000 \
  --teardown "bash scripts/free-ports.sh --desktop-only" --names temporal,worker,api,desktop,web \
  -c magenta,yellow,cyan,blue,green \
  "pnpm dev:temporal" "pnpm dev:worker" "pnpm dev:api" \
  "pnpm dev:desktop" "pnpm dev:web"
