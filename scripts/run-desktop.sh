#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
context="${DESKTOP_DOCKER_CONTEXT:-colima}"
artifacts="${DESKTOP_ARTIFACT_ROOT:-${TMPDIR:-/tmp/}kilo/gwen-desktop-artifacts}"
mkdir -p "$artifacts"
docker --context "$context" info >/dev/null
# No host/repository/credential mount. The profile is Playwright v1.61.0's
# namespace-enabled seccomp policy, plus chroot inside the user namespace.
DOCKER_BUILDKIT=0 docker --context "$context" build \
    --build-arg "DESKTOP_UID=$(id -u)" \
    -t gwen-desktop:native -f "$root/orchestrator/desktop/Dockerfile" "$root/orchestrator"
exec docker --context "$context" run --rm --name gwen-desktop \
    --shm-size=1g --cap-drop=ALL --security-opt no-new-privileges \
    --security-opt "seccomp=$root/orchestrator/desktop/seccomp.json" \
    -p 127.0.0.1:6080:6080 \
    --mount "type=bind,source=$artifacts,target=/var/lib/gwen-desktop-artifacts" \
    -e DESKTOP_ARTIFACT_ROOT=/var/lib/gwen-desktop-artifacts \
    -e "TEMPORAL_ADDRESS=${DESKTOP_TEMPORAL_ADDRESS:-host.lima.internal:7233}" \
    gwen-desktop:native
