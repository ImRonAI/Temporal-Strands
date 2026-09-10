#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
context="${DESKTOP_DOCKER_CONTEXT:-colima}"
mode="${1:-start}"
if [[ $# -gt 1 || ( "$mode" != start && "$mode" != build ) ]]; then
    printf 'Usage: %s [start|build]\n' "$0" >&2
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

if [[ "$mode" == start ]]; then
    if ! image_info="$(docker --context "$context" image inspect --format \
        "{{.Id}} {{index .Config.Labels \"$source_label\"}}" "$image" 2>/dev/null)"; then
        printf 'Desktop image %s is missing in Docker context %s. Run pnpm build:desktop first.\n' "$image" "$context" >&2
        exit 1
    fi
fi

# Hash and send only these source/config files, never the full checkout or env.
# Keep this list aligned with the desktop Dockerfile's COPY dependencies.
sources=(
    .dockerignore desktop/Dockerfile desktop/requirements.txt
    desktop/start-desktop.sh desktop/seccomp.json
    browser_activity.py computer_use_activity.py desktop_observation.py
    workspace_state.py desktop_worker.py config.py telemetry.py
)
source_hash="$(
    cd -- "$root/orchestrator"
    printf 'DESKTOP_UID=%s\n' "$desktop_uid"
    shasum -a 256 "${sources[@]}"
)"
source_hash="$(printf '%s' "$source_hash" | shasum -a 256)"
source_hash="${source_hash%% *}"

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

if [[ "${image_info#* }" != "$source_hash" ]]; then
    printf 'Desktop image source/config or UID mismatch (or missing verification label). Run pnpm build:desktop before starting.\n' >&2
    exit 1
fi
if docker --context "$context" container inspect gwen-desktop >/dev/null 2>&1; then
    printf 'Container name gwen-desktop is already in use in context %s. Resolve it explicitly when safe; startup will not stop or remove it.\n' "$context" >&2
    exit 1
fi
artifacts="${DESKTOP_ARTIFACT_ROOT:-${TMPDIR:-/tmp/}kilo/gwen-desktop-artifacts}"
mkdir -p "$artifacts"
# No host/repository/credential mount. The profile is Playwright v1.61.0's
# namespace-enabled seccomp policy, plus chroot inside the user namespace.
# Run the verified ID, not a tag that could be replaced or pulled after inspection.
exec docker --context "$context" run --pull=never --rm --name gwen-desktop \
    --user "$desktop_uid" \
    --shm-size=1g --cap-drop=ALL --security-opt no-new-privileges \
    --security-opt "seccomp=$root/orchestrator/desktop/seccomp.json" \
    -p 127.0.0.1:6080:6080 \
    --mount "type=bind,source=$artifacts,target=/var/lib/gwen-desktop-artifacts" \
    -e DESKTOP_ARTIFACT_ROOT=/var/lib/gwen-desktop-artifacts \
    -e "TEMPORAL_ADDRESS=${DESKTOP_TEMPORAL_ADDRESS:-host.lima.internal:7233}" \
    "${image_info%% *}"
