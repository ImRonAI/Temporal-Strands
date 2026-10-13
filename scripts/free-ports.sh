#!/usr/bin/env bash
# Free every port this project binds, before starting it.
#
# A previous `pnpm dev:all` that was killed rather than shut down cleanly can
# leave a worker, uvicorn, or the Temporal dev server holding its port. That
# fails confusingly: Next silently moves to :3001, and a stale worker keeps
# polling the task queue with OLD code, so turns get picked up by a process
# running whatever was on disk an hour ago.
#
# SIGTERM first so servers can close listeners and flush; SIGKILL only for
# whatever ignores it.
set -u

PORTS="3000 7233 8233 8787"

for port in $PORTS; do
  pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  [ -z "$pids" ] && continue

  echo "  port $port busy -> stopping $(echo "$pids" | tr '\n' ' ')"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true

  # Give it a moment, then force anything still holding on.
  for _ in 1 2 3 4 5 6; do
    sleep 0.25
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    [ -z "$pids" ] && break
  done
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.25
  fi
done

# Ports alone are not enough. run_worker.py binds NOTHING — it dials out to
# Temporal and polls a task queue. It only appears in `lsof -ti tcp:7233` via
# that outbound connection, so if Temporal died first the worker is invisible
# to the loop above and survives. A surviving worker is the worst failure
# here: it keeps claiming tasks with the code it started with, so edits
# silently do not take effect.
#
# Patterns are specific to this project's processes so nothing unrelated is
# touched. `next dev` is deliberately NOT matched by name — another project's
# dev server would match too; port 3000 above already covers ours.
PATTERNS="run_worker.py|uvicorn server:app --port 8787|temporal server start-dev"

leftovers=$(pgrep -f "$PATTERNS" 2>/dev/null || true)
if [ -n "$leftovers" ]; then
  echo "  stray project processes -> stopping $(echo "$leftovers" | tr '\n' ' ')"
  # shellcheck disable=SC2086
  kill $leftovers 2>/dev/null || true
  sleep 0.5
  leftovers=$(pgrep -f "$PATTERNS" 2>/dev/null || true)
  # shellcheck disable=SC2086
  [ -n "$leftovers" ] && kill -9 $leftovers 2>/dev/null || true
fi

echo "ports clear: $PORTS"
