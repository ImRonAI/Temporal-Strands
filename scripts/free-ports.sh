#!/usr/bin/env bash
# Free every port and container this project binds, before starting it.
#
# A previous `pnpm dev:all` that was killed rather than shut down cleanly can
# leave a worker, uvicorn, the Temporal dev server, or the gwen-desktop
# container holding its port. That
# fails confusingly: Next silently moves off :3001, and a stale worker keeps
# polling the task queue with OLD code, so turns get picked up by a process
# running whatever was on disk an hour ago.
#
# SIGTERM first so servers can close listeners and flush; SIGKILL only for
# whatever ignores it.
set -euo pipefail

PORTS="3001 7233 8233 8787"
case "${1:-}" in
  "") ;;
  --desktop-only) PORTS="" ;;
  *) echo "Usage: $0 [--desktop-only]" >&2; exit 1 ;;
esac
root="${GWEN_PROJECT_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}"

# pnpm -> sh -c -> start-all -> cleanup can be several levels deep. None of
# the current launcher ancestry is an old stack, even if its command matches.
ancestors=" $$ "
ancestor="$PPID"
while [[ "$ancestor" =~ ^[0-9]+$ && "$ancestor" -gt 1 ]]; do
  ancestors+="$ancestor "
  ancestor="$(ps -p "$ancestor" -o ppid= 2>/dev/null | tr -d ' ' || true)"
done

# Verify both command and cwd before signalling. A TCP client of :3001 may
# be the user's browser; a server on that port may belong to another repo.
is_project_service() {
  local pid="$1" command cwd
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$command" ]] || return 1
  case "$command" in
    *run_worker.py*|*worker_supervisor.py*|*desktop_worker.py*|*"uvicorn server:app"*| \
    *"temporal server start-dev"*|*"next dev"*|next-server*| \
    *concurrently*|*wait-for-stack.mjs*|*run-desktop.sh*|*start-all.sh*)
      ;;
    *) return 1 ;;
  esac
  # Skip a shell whose diagnostic command merely mentions a service
  # (`bash -c 'pgrep run_worker.py'`). Keep our launch wrappers, and do not
  # treat concurrently's `-c <colors>` flag as that case.
  case "$command" in
    bash\ -c\ *|sh\ -c\ *|zsh\ -c\ *|/bin/bash\ -c\ *|/bin/sh\ -c\ *|/bin/zsh\ -c\ *)
      case "$command" in
        *wait-on*|*wait-for-stack.mjs*|*run-desktop.sh*|*start-all.sh*|*concurrently*) ;;
        *) return 1 ;;
      esac
      ;;
  esac
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true)"
  # Nested worktrees are independent checkouts, not children of this stack.
  [[ "$cwd" == "$root" || "$cwd" == "$root/orchestrator" || "$cwd" == "$root/scripts" ]]
}

# Refuse unrelated port owners BEFORE stopping any part of this stack.
for port in $PORTS; do
  for pid in $(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    if ! is_project_service "$pid"; then
      echo "Port $port belongs to another process (PID $pid); leaving it untouched. Free that port before starting Gwen." >&2
      exit 1
    fi
  done
done

# Include workers with no listeners, supervisors that would respawn them,
# same-checkout Next servers on alternate ports holding the dev lock, and
# the concurrently parent (whose `-c` flag is colors, not `sh -c`).
pids=()
while read -r pid; do
  [[ "$ancestors" == *" $pid "* ]] && continue
  if is_project_service "$pid"; then pids+=("$pid"); fi
done < <(if [[ -n "$PORTS" ]]; then pgrep -f 'run_worker.py|worker_supervisor.py|desktop_worker.py|uvicorn server:app|temporal server start-dev|next dev|next-server|concurrently|wait-for-stack[.]mjs|run-desktop[.]sh|start-all[.]sh' || true; fi)

# Capture descendants before signalling parents: MCP subprocesses and pnpm
# wait-on launchers may otherwise be orphaned and wake up on the new server.
index=0
while ((index < ${#pids[@]})); do
  while read -r child; do
    [[ "$ancestors" == *" $child "* || " ${pids[*]} " == *" $child "* ]] && continue
    pids+=("$child")
  done < <(pgrep -P "${pids[$index]}" || true)
  index=$((index + 1))
done

if ((${#pids[@]})); then
  echo "  stopping services owned by this checkout: ${pids[*]}"
  kill "${pids[@]}" 2>/dev/null || true
  for _ in {1..40}; do
    alive=0
    for pid in "${pids[@]}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [[ "$alive" == 0 ]] && break
    sleep 0.25
  done
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  # SIGKILL is asynchronous; never wipe runtime state while a worker lives.
  for _ in {1..20}; do
    alive=0
    for pid in "${pids[@]}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [[ "$alive" == 0 ]] && break
    sleep 0.1
  done
  if [[ "$alive" != 0 ]]; then
    echo "Old Gwen processes have not exited; refusing to reset runtime state." >&2
    exit 1
  fi
fi

lock="$root/.next/dev/lock"
if [[ -n "$PORTS" && -f "$lock" ]]; then
  lock_pid=""
  if command -v python3 >/dev/null 2>&1; then
    lock_pid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid") or "")' "$lock" 2>/dev/null || true)"
  fi
  if [[ -z "$lock_pid" ]] || ! kill -0 "$lock_pid" 2>/dev/null; then
    echo "  removing stale Next dev lock"
    rm -f "$lock"
  fi
fi

# Desktops are per-session containers (gwen-desktop-<session>) plus warm idle
# ones (gwen-desktop-idle-*), all started with --rm by the host worker / pool
# keeper. If `dev:all` is killed rather than shut down they are orphaned and
# keep polling with old code, so stop every container carrying this project's
# name prefix, in the same Docker context the orchestrator uses. Docker being
# absent is not an error.
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
DESKTOP_CONTAINER_PREFIX="gwen-desktop-"
DESKTOP_CONTEXT="${DESKTOP_DOCKER_CONTEXT:-colima}"

if command -v docker >/dev/null 2>&1 && docker --context "$DESKTOP_CONTEXT" info >/dev/null 2>&1; then
  containers=$(docker --context "$DESKTOP_CONTEXT" ps -a --filter "name=^${DESKTOP_CONTAINER_PREFIX}" --format '{{.Names}}' 2>/dev/null || true)
  for container in $containers; do
    echo "  desktop container $container -> stopping"
    # TERM reaches start-desktop.sh through tini; its cleanup is bounded at
    # ~10s, so allow that before Docker escalates to KILL.
    docker --context "$DESKTOP_CONTEXT" stop -t 15 "$container" >/dev/null 2>&1 || true
    docker --context "$DESKTOP_CONTEXT" rm -f "$container" >/dev/null 2>&1 || true
    if docker --context "$DESKTOP_CONTEXT" container inspect "$container" >/dev/null 2>&1; then
      echo "  ERROR: $container still exists in context $DESKTOP_CONTEXT; cannot safely restart" >&2
      exit 1
    fi
  done
fi

for port in $PORTS; do
  if lsof -nP -t -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is still occupied after cleanup; refusing a partial startup." >&2
    exit 1
  fi
done
if [[ -n "$PORTS" ]]; then echo "ports clear: $PORTS"; fi
