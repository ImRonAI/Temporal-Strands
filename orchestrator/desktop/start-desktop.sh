#!/usr/bin/env bash
# Desktop services + Temporal desktop worker. Everything here is stock
# software on stock configuration; the only app process is desktop_worker.py.
set -euo pipefail

pids=()
cleanup() {
    trap - EXIT TERM INT
    if ((${#pids[@]})); then
        kill "${pids[@]}" 2>/dev/null || true
        # Bound shutdown even if a native service ignores TERM.
        for ((attempt=0; attempt<10; attempt++)); do
            kill -0 "${pids[@]}" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

Xvfb "$DISPLAY" -screen 0 1440x900x24 -nolisten tcp &
pids+=("$!")
for ((attempt=0; attempt<30; attempt++)); do
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
    kill -0 "${pids[0]}" 2>/dev/null || exit 1
    sleep 1
done
xdpyinfo -display "$DISPLAY" >/dev/null
openbox &
pids+=("$!")

# Input starts fenced (view-only). The API grants/revokes through x11vnc's
# native remote-control commands on handoff.
x11vnc -display "$DISPLAY" -localhost -rfbport 5900 -forever -shared -viewonly -nopw -nosel -noclipboard -nosetclipboard -nosetprimary -noxrandr -clear_all &
pids+=("$!")

websockify --web=/usr/share/novnc 6080 127.0.0.1:5900 &
pids+=("$!")

python -u desktop_worker.py &
pids+=("$!")

# Any service exit invalidates the shared desktop, including clean exits.
wait -n "${pids[@]}" || exit "$?"
exit 1
