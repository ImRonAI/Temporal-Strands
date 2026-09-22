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
root="${GWEN_PROJECT_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}"

# Verify both command and cwd before signalling. A TCP client of :3001 may
# be the user's browser; a server on that port may belong to another repo.
is_project_service() {
  local pid="$1" command cwd
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$command" ]] || return 1
  case "$command" in
    *run_worker.py*|*worker_supervisor.py*|*"uvicorn server:app"*| \
    *"temporal server start-dev"*|*"next dev"*|next-server*| \
    *concurrently*|*wait-for-stack.mjs*|*run-desktop.sh*|*start-all.sh*)
      ;;
    *) return 1 ;;
  esac
  # Skip a shell whose diagnostic command merely mentions a service
  # (`bash -c 'pgrep run_worker.py'`). Keep our launch wrappers, and do not
  # treat concurrently's `-c <colors>` flag as that case.
  case "$command" in
    bash\ -c\ *|sh\ -c\ *|zsh\ -c\ *|/bin/bash\ -c\ *|/bin/sh\ -c\ *)
      case "$command" in
        *wait-on*|*wait-for-stack.mjs*|*run-desktop.sh*|*start-all.sh*|*concurrently*) ;;
        *) return 1 ;;
      esac
      ;;
  esac
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true)"
  [[ "$cwd" == "$root" || "$cwd" == "$root/"* ]]
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
  [[ "$pid" == "$$" || "$pid" == "$PPID" ]] && continue
  if is_project_service "$pid"; then pids+=("$pid"); fi
done < <(pgrep -f 'run_worker.py|worker_supervisor.py|uvicorn server:app|temporal server start-dev|next dev|next-server|concurrently|wait-for-stack[.]mjs|run-desktop[.]sh|start-all[.]sh' || true)

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
    if kill -0 "$pid" 2>/dev/null && is_project_service "$pid"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
fi

lock="$root/.next/dev/lock"
if [[ -f "$lock" ]]; then
  lock_pid=""
  if command -v python3 >/dev/null 2>&1; then
    lock_pid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid") or "")' "$lock" 2>/dev/null || true)"
  fi
  if [[ -z "$lock_pid" ]] || ! kill -0 "$lock_pid" 2>/dev/null; then
    echo "  removing stale Next dev lock"
    rm -f "$lock"
  fi
fi

# The desktop worker is the same failure in a container. `run-desktop.sh`
# starts `docker run --rm --name gwen-desktop`; if `dev:all` is killed rather
# than shut down, docker is orphaned, the container keeps polling the
# desktop-browser queue with old code and holds :6080, and the next startup
# refuses the taken name (run-desktop.sh deliberately never stops or removes
# anything). Only this exact project container name is touched, in the same
# Docker context run-desktop.sh uses. Docker being absent is not an error.
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
DESKTOP_CONTAINER="gwen-desktop"
DESKTOP_CONTEXT="${DESKTOP_DOCKER_CONTEXT:-colima}"

if command -v docker >/dev/null 2>&1 && docker --context "$DESKTOP_CONTEXT" info >/dev/null 2>&1; then
  state=$(docker --context "$DESKTOP_CONTEXT" container inspect --format '{{.State.Status}}' "$DESKTOP_CONTAINER" 2>/dev/null || true)
  if [ -n "$state" ]; then
    echo "  desktop container $DESKTOP_CONTAINER ($state) -> stopping"
    # TERM reaches start-desktop.sh through tini; its cleanup is bounded at
    # ~10s, so allow that before Docker escalates to KILL.
    docker --context "$DESKTOP_CONTEXT" stop -t 15 "$DESKTOP_CONTAINER" >/dev/null 2>&1 || true
    # `--rm` removes it on stop; a container created without --rm (or one
    # that was already exited) still needs removing so the name is free.
    docker --context "$DESKTOP_CONTEXT" rm -f "$DESKTOP_CONTAINER" >/dev/null 2>&1 || true
    if docker --context "$DESKTOP_CONTEXT" container inspect "$DESKTOP_CONTAINER" >/dev/null 2>&1; then
      echo "  ERROR: $DESKTOP_CONTAINER still exists in context $DESKTOP_CONTEXT; cannot safely restart" >&2
      exit 1
    fi
  fi
fi

for port in $PORTS; do
  if lsof -nP -t -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is still occupied after cleanup; refusing a partial startup." >&2
    exit 1
  fi
done
echo "ports clear: $PORTS"
