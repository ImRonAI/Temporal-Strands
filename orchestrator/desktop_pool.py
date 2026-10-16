"""Warm pool keeper for per-session Linux desktops.

Keeps ``DESKTOP_POOL_SIZE`` idle ``gwen-desktop`` containers healthy so a new
chat session's first browser action never waits on a cold start. Claiming is
``desktop_provision_activity.provision_desktop`` on the host worker; this
process only tops the pool back up and exits cleanly on SIGTERM/SIGINT.

Run as ``pnpm dev:desktop`` (dev) or the ``gwen-desktop-pool`` systemd unit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from config import DESKTOP_POOL_REFILL_INTERVAL, DESKTOP_POOL_SIZE
from desktop_provision_activity import POOL_PREFIX, _docker, list_idle_containers, start_idle_container

logger = logging.getLogger(__name__)


async def pending_idle_containers() -> int:
    """Idle containers still starting (not yet healthy), so we never over-start."""
    out = await _docker(
        "ps", "--filter", "label=io.gwen.desktop.pool=idle",
        "--filter", "health=starting", "--format", "{{.Names}}", timeout=30,
    )
    return sum(1 for name in out.split() if name.startswith(POOL_PREFIX))


async def refill_once() -> int:
    """Start enough idle containers to reach the target. Returns how many started."""
    have = len(await list_idle_containers()) + await pending_idle_containers()
    started = 0
    for _ in range(max(0, DESKTOP_POOL_SIZE - have)):
        name = await start_idle_container()
        logger.info("Started warm desktop %s", name)
        started += 1
    return started


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    logger.info("Desktop pool keeper: target %d warm desktop(s)", DESKTOP_POOL_SIZE)
    while not stopped.is_set():
        try:
            await refill_once()
        except Exception as error:  # noqa: BLE001 - keep the keeper alive
            logger.warning("Desktop pool refill failed: %s", error)
        # Sleep until the next refill tick, waking early on shutdown.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopped.wait(), DESKTOP_POOL_REFILL_INTERVAL.total_seconds())


if __name__ == "__main__":
    asyncio.run(main())
