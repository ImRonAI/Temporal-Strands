#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
context="${DESKTOP_DOCKER_CONTEXT:-colima}"
# Builds and verifies the per-session desktop image. Containers are started by
# the warm-pool keeper (orchestrator/desktop_pool.py) and claimed per chat by
# the host worker's provision_desktop activity; nothing here runs a desktop.
mode="${1:-prepare}"
if [[ $# -gt 1 || ( "$mode" != build && "$mode" != prepare ) ]]; then
    printf 'Usage: %s [build|prepare]\n' "$0" >&2
    exit 1
fi
image="gwen-desktop:native"
source_label="io.gwen.desktop.source-hash"
desktop_uid="$(id -u)"
if [[ "$desktop_uid" == 0 ]]; then
    printf 'Run desktop preparation/startup as a non-root user.\n' >&2
    exit 1
fi
docker --context "$context" info >/dev/null

if [[ "$mode" != build ]]; then
    if ! image_info="$(docker --context "$context" image inspect --format \
        "{{.Id}} {{index .Config.Labels \"$source_label\"}}" "$image" 2>/dev/null)"; then
        image_info=""
    fi
fi

# Hash and send only these source/config files, never the full checkout or env.
# Keep this list aligned with the desktop Dockerfile's COPY dependencies.
sources=(
    .dockerignore desktop/Dockerfile desktop/requirements.txt
    desktop/start-desktop.sh desktop/seccomp.json
    browser_activity.py computer_use_activity.py desktop_observation.py
    workspace_state.py desktop_worker.py desktop_provision_activity.py config.py telemetry.py
)
source_hash="$(
    cd -- "$root/orchestrator"
    printf 'DESKTOP_UID=%s\n' "$desktop_uid"
    shasum -a 256 "${sources[@]}"
)"
source_hash="$(printf '%s' "$source_hash" | shasum -a 256)"
source_hash="${source_hash%% *}"

if [[ "$mode" == prepare ]]; then
    if [[ "${image_info#* }" == "$source_hash" ]]; then
        printf 'Desktop image matches source and UID; reusing it.\n'
        exit 0
    fi
    printf 'Desktop image is missing or stale; rebuilding before starting services...\n'
    mode=build
fi

if [[ "$mode" == build ]]; then
    if docker --context "$context" buildx version >/dev/null 2>&1; then
        export DOCKER_BUILDKIT=1
    else
        printf 'Docker buildx is unavailable; using the deprecated legacy builder for this explicit build only. Install buildx to use BuildKit.\n' >&2
        export DOCKER_BUILDKIT=0
    fi
    COPYFILE_DISABLE=1 tar --no-xattrs -C "$root/orchestrator" -cf - "${sources[@]}" | \
        docker --context "$context" build \
            --build-arg "DESKTOP_UID=$desktop_uid" \
            --label "$source_label=$source_hash" \
            -t "$image" -f desktop/Dockerfile -
    exit 0
fi
