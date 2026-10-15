"""Per-session Linux desktop containers, provisioned from a warm pool.

Runs on the host worker. Each chat session owns one ``gwen-desktop`` container
whose worker polls ``desktop_task_queue(session_id)``; nothing is shared
between sessions, so there is no cross-session ownership to arbitrate.

A warm pool hides container start-up: idle containers already running the
desktop stack poll nothing and wait for a claim. ``provision_desktop`` claims
one by renaming it and handing the session's queue to its worker through the
container's artifact directory; the pool keeper (``desktop_pool.py``) starts
a replacement in the background. Only when the pool is empty does a claim pay
for a cold start.

Everything here is the stock ``docker`` CLI; no Docker SDK, no daemon socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from config import (
    DESKTOP_DOCKER_CONTEXT, DESKTOP_HEALTH_POLL_INTERVAL, DESKTOP_IMAGE, DESKTOP_NOVNC_HOST,
    DESKTOP_PROVISION_TIMEOUT, DESKTOP_TEARDOWN_TIMEOUT, DESKTOP_TEMPORAL_ADDRESS,
    desktop_container_name, desktop_task_queue,
)

logger = logging.getLogger(__name__)

POOL_PREFIX = "gwen-desktop-idle-"
POOL_LABEL = "io.gwen.desktop.pool=idle"
SESSION_LABEL = "io.gwen.desktop.session"
# File the container's worker watches for its queue assignment.
QUEUE_FILE = "queue"
CONTAINER_ARTIFACT_ROOT = "/var/lib/gwen-desktop-artifacts"


def artifact_parent() -> Path:
    return Path(os.environ.get(
        "DESKTOP_ARTIFACT_ROOT", str(Path(tempfile.gettempdir()) / "kilo" / "gwen-desktop-artifacts"),
    ))


def container_artifact_dir(container: str) -> Path:
    return artifact_parent() / container


def session_artifact_root(session_id: str) -> Path:
    """Host-side bind mount of ``session_id``'s desktop: where its worker writes
    ``runtime.json`` and screenshot observations."""
    return container_artifact_dir(desktop_container_name(session_id))


async def _docker(*args: str, timeout: float) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker", "--context", DESKTOP_DOCKER_CONTEXT, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        raise
    if process.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:2])} failed: {stderr.decode().strip()}")
    return stdout.decode()


def _run_args(container: str, host_port: int | None) -> list[str]:
    """Same hardening as scripts/run-desktop.sh; only the name/port/mount differ."""
    root = Path(__file__).resolve().parent
    artifacts = container_artifact_dir(container)
    artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
    port = f"127.0.0.1:{host_port or 0}:6080"
    return [
        "run", "--detach", "--pull=never", "--rm", "--name", container,
        "--label", POOL_LABEL,
        "--user", str(os.getuid()),
        "--shm-size=1g", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
        "--security-opt", f"seccomp={root / 'desktop' / 'seccomp.json'}",
        "-p", port,
        "--mount", f"type=bind,source={artifacts},target={CONTAINER_ARTIFACT_ROOT}",
        "-e", f"DESKTOP_ARTIFACT_ROOT={CONTAINER_ARTIFACT_ROOT}",
        "-e", f"TEMPORAL_ADDRESS={DESKTOP_TEMPORAL_ADDRESS}",
        DESKTOP_IMAGE,
    ]


async def start_idle_container() -> str:
    """Start one warm desktop that polls nothing until claimed."""
    container = f"{POOL_PREFIX}{secrets.token_hex(4)}"
    await _docker(*_run_args(container, None), timeout=DESKTOP_PROVISION_TIMEOUT.total_seconds())
    return container


async def list_idle_containers() -> list[str]:
    out = await _docker(
        "ps", "--filter", f"label={POOL_LABEL}", "--filter", "health=healthy",
        "--format", "{{.Names}}", timeout=DESKTOP_TEARDOWN_TIMEOUT.total_seconds(),
    )
    return [name for name in out.split() if name.startswith(POOL_PREFIX)]


async def wait_healthy(container: str, deadline: float) -> None:
    while True:
        status = (await _docker(
            "inspect", "--format", "{{.State.Health.Status}}", container,
            timeout=DESKTOP_TEARDOWN_TIMEOUT.total_seconds(),
        )).strip()
        if status == "healthy":
            return
        if status == "unhealthy" or time.monotonic() >= deadline:
            raise RuntimeError(f"Desktop container {container} is {status or 'gone'}")
        if activity.in_activity():
            activity.heartbeat()
        await asyncio.sleep(DESKTOP_HEALTH_POLL_INTERVAL.total_seconds())


async def novnc_url(container: str) -> str:
    port = (await _docker(
        "port", container, "6080/tcp", timeout=DESKTOP_TEARDOWN_TIMEOUT.total_seconds(),
    )).strip().rsplit(":", 1)[-1]
    return f"http://{DESKTOP_NOVNC_HOST}:{port}/vnc.html?autoconnect=true&resize=scale&reconnect=true"


def _assign_queue(artifacts: Path, queue: str) -> None:
    with tempfile.NamedTemporaryFile("w", dir=artifacts, delete=False) as file:
        file.write(queue)
        name = file.name
    os.replace(name, artifacts / QUEUE_FILE)


@activity.defn(name="provision_desktop")
async def provision_desktop(session_id: str) -> dict[str, str]:
    """Claim a warm desktop for ``session_id``; cold-start one only if the pool is empty."""
    container = desktop_container_name(session_id)
    deadline = time.monotonic() + DESKTOP_PROVISION_TIMEOUT.total_seconds()
    existing = (await _docker(
        "ps", "--all", "--filter", f"name=^{container}$", "--format", "{{.Names}}",
        timeout=DESKTOP_TEARDOWN_TIMEOUT.total_seconds(),
    )).strip()
    if not existing:
        # `docker rename` is atomic on the daemon, so it doubles as the claim:
        # a concurrent session may take a container between `ps` and `rename`,
        # in which case the rename fails and the next idle one is tried.
        for source in await list_idle_containers():
            try:
                await _docker("rename", source, container, timeout=DESKTOP_TEARDOWN_TIMEOUT.total_seconds())
            except RuntimeError:
                logger.info("Idle desktop %s was claimed by another session; trying the next one", source)
                continue
            os.replace(container_artifact_dir(source), container_artifact_dir(container))
            break
        else:
            logger.warning("Desktop pool empty; cold-starting %s", container)
            await _docker(*_run_args(container, None), timeout=DESKTOP_PROVISION_TIMEOUT.total_seconds())
    try:
        await wait_healthy(container, deadline)
    except Exception as error:
        raise ApplicationError(str(error), non_retryable=False) from error
    queue = desktop_task_queue(session_id)
    _assign_queue(container_artifact_dir(container), queue)
    return {"container": container, "queue": queue, "novnc_url": await novnc_url(container)}


@activity.defn(name="teardown_desktop")
async def teardown_desktop(session_id: str) -> None:
    """Stop the session's desktop. ``--rm`` removes it; the pool keeper refills."""
    container = desktop_container_name(session_id)
    try:
        await _docker("stop", "-t", "15", container, timeout=DESKTOP_TEARDOWN_TIMEOUT.total_seconds())
    except RuntimeError as error:
        if "No such container" not in str(error):
            raise
    directory = container_artifact_dir(container)
    if directory.is_dir():
        (directory / QUEUE_FILE).unlink(missing_ok=True)


DESKTOP_PROVISION_ACTIVITIES = [provision_desktop, teardown_desktop]
