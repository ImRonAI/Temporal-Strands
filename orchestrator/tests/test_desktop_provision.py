"""Per-session desktop provisioning: warm-pool claim, cold start, teardown, pool refill."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import desktop_pool
import desktop_provision_activity as provision
from config import desktop_container_name, desktop_task_queue


class FakeDocker:
    """Records ``docker`` CLI calls and answers them like a daemon with a pool."""

    def __init__(self, idle: list[str] | None = None, *, health: str = "healthy") -> None:
        self.idle = list(idle or [])
        self.health = health
        self.calls: list[tuple[str, ...]] = []
        self.ports: dict[str, int] = {}

    async def __call__(self, *args: str, timeout: float) -> str:
        self.calls.append(args)
        match args:
            case ("ps", "--filter", "label=io.gwen.desktop.pool=idle", "--filter", "health=healthy", *_):
                return "\n".join(self.idle)
            case ("ps", "--filter", "label=io.gwen.desktop.pool=idle", "--filter", "health=starting", *_):
                return ""
            case ("ps", "--all", "--filter", name_filter, *_):
                name = name_filter.removeprefix("name=^").removesuffix("$")
                return name if name in self.ports else ""
            case ("rename", source, target):
                if source not in self.idle:
                    raise RuntimeError("Error response from daemon: No such container: " + source)
                self.idle.remove(source)
                self.ports[target] = self.ports.pop(source, 50000)
                return ""
            case ("run", *rest):
                name = rest[rest.index("--name") + 1]
                self.ports[name] = 50000 + len(self.ports)
                return "containerid\n"
            case ("inspect", "--format", "{{.State.Health.Status}}", _):
                return self.health
            case ("port", name, "6080/tcp"):
                return f"127.0.0.1:{self.ports[name]}\n"
            case ("stop", "-t", _, name):
                if name not in self.ports:
                    raise RuntimeError("Error response from daemon: No such container: " + name)
                del self.ports[name]
                return ""
        raise AssertionError(f"unexpected docker call: {args}")


@pytest.fixture
def docker(monkeypatch, tmp_path):
    fake = FakeDocker(idle=["gwen-desktop-idle-aaaa"])
    fake.ports["gwen-desktop-idle-aaaa"] = 51234
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    (tmp_path / "gwen-desktop-idle-aaaa").mkdir()
    monkeypatch.setattr(provision, "_docker", fake)
    monkeypatch.setattr(desktop_pool, "_docker", fake)
    monkeypatch.setattr(provision.activity, "in_activity", lambda: False)
    return fake


@pytest.mark.asyncio
async def test_provision_claims_a_warm_container_and_assigns_the_session_queue(docker, tmp_path):
    result = await provision.provision_desktop("chat-1")

    container = desktop_container_name("chat-1")
    assert result == {
        "container": container,
        "queue": desktop_task_queue("chat-1"),
        "novnc_url": "http://localhost:51234/vnc.html?autoconnect=true&resize=scale&reconnect=true",
    }
    # The idle container was renamed, never a second one started.
    assert ("rename", "gwen-desktop-idle-aaaa", container) in docker.calls
    assert not any(call[0] == "run" for call in docker.calls)
    # Its artifact directory followed the rename and now carries the claim the
    # container's worker is waiting for.
    assert (tmp_path / container / provision.QUEUE_FILE).read_text() == "desktop-browser-chat-1"
    assert not (tmp_path / "gwen-desktop-idle-aaaa").exists()


@pytest.mark.asyncio
async def test_provision_cold_starts_only_when_the_pool_is_empty(docker, tmp_path):
    docker.idle.clear()

    result = await provision.provision_desktop("chat-2")

    run = next(call for call in docker.calls if call[0] == "run")
    name = run[run.index("--name") + 1]
    assert name == "gwen-desktop-chat-2"
    # Isolated, non-root, no host/repo/credential mounts: only its own
    # artifact directory, bound to a random loopback port for noVNC.
    root = provision.Path(provision.__file__).resolve().parent
    assert list(run) == [
        "run", "--detach", "--pull=never", "--rm", "--name", name,
        "--label", "io.gwen.desktop.pool=idle",
        "--user", str(provision.os.getuid()),
        "--shm-size=1g", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
        "--security-opt", f"seccomp={root / 'desktop' / 'seccomp.json'}",
        "-p", "127.0.0.1:0:6080",
        "--mount", f"type=bind,source={tmp_path / name},target=/var/lib/gwen-desktop-artifacts",
        "-e", "DESKTOP_ARTIFACT_ROOT=/var/lib/gwen-desktop-artifacts",
        "-e", f"TEMPORAL_ADDRESS={provision.DESKTOP_TEMPORAL_ADDRESS}",
        provision.DESKTOP_IMAGE,
    ]
    assert result["queue"] == "desktop-browser-chat-2"


def _claimed_between_ps_and_rename(docker: FakeDocker, *lost: str):
    """A ``_docker`` where concurrent sessions take ``lost`` right after this one lists the pool."""

    async def racing(*args: str, timeout: float) -> str:
        out = await docker(*args, timeout=timeout)
        if args[:2] == ("ps", "--filter"):
            for name in lost:
                if name in docker.idle:
                    docker.idle.remove(name)
                    docker.ports.pop(name, None)
        return out

    return racing


@pytest.mark.asyncio
async def test_provision_takes_the_next_idle_container_when_the_first_is_claimed_concurrently(
    docker, tmp_path, monkeypatch,
):
    docker.idle.append("gwen-desktop-idle-bbbb")
    docker.ports["gwen-desktop-idle-bbbb"] = 51235
    (tmp_path / "gwen-desktop-idle-bbbb").mkdir()
    monkeypatch.setattr(provision, "_docker", _claimed_between_ps_and_rename(docker, "gwen-desktop-idle-aaaa"))

    result = await provision.provision_desktop("chat-4")

    container = desktop_container_name("chat-4")
    assert result == {
        "container": container,
        "queue": desktop_task_queue("chat-4"),
        "novnc_url": "http://localhost:51235/vnc.html?autoconnect=true&resize=scale&reconnect=true",
    }
    # The lost rename was tolerated and the second idle container was claimed instead.
    renames = [call for call in docker.calls if call[0] == "rename"]
    assert renames == [
        ("rename", "gwen-desktop-idle-aaaa", container),
        ("rename", "gwen-desktop-idle-bbbb", container),
    ]
    assert not any(call[0] == "run" for call in docker.calls)
    assert (tmp_path / container / provision.QUEUE_FILE).read_text() == "desktop-browser-chat-4"
    assert not (tmp_path / "gwen-desktop-idle-bbbb").exists()
    # The other session's container and its artifacts were left alone.
    assert (tmp_path / "gwen-desktop-idle-aaaa").exists()


@pytest.mark.asyncio
async def test_provision_cold_starts_when_every_idle_container_is_claimed_concurrently(
    docker, tmp_path, monkeypatch,
):
    docker.idle.append("gwen-desktop-idle-bbbb")
    docker.ports["gwen-desktop-idle-bbbb"] = 51235
    (tmp_path / "gwen-desktop-idle-bbbb").mkdir()
    monkeypatch.setattr(
        provision, "_docker",
        _claimed_between_ps_and_rename(docker, "gwen-desktop-idle-aaaa", "gwen-desktop-idle-bbbb"),
    )

    result = await provision.provision_desktop("chat-5")

    container = desktop_container_name("chat-5")
    assert [call for call in docker.calls if call[0] == "rename"] == [
        ("rename", "gwen-desktop-idle-aaaa", container),
        ("rename", "gwen-desktop-idle-bbbb", container),
    ]
    run = next(call for call in docker.calls if call[0] == "run")
    assert run[run.index("--name") + 1] == container
    assert result["container"] == container
    assert result["queue"] == "desktop-browser-chat-5"
    assert (tmp_path / container / provision.QUEUE_FILE).read_text() == "desktop-browser-chat-5"


@pytest.mark.asyncio
async def test_provision_is_idempotent_for_an_already_claimed_session(docker):
    first = await provision.provision_desktop("chat-1")
    second = await provision.provision_desktop("chat-1")
    assert first == second
    assert sum(1 for call in docker.calls if call[0] in {"rename", "run"}) == 1


@pytest.mark.asyncio
async def test_provision_fails_when_the_container_never_becomes_healthy(docker, monkeypatch):
    docker.health = "unhealthy"
    with pytest.raises(provision.ApplicationError, match="unhealthy"):
        await provision.provision_desktop("chat-3")


@pytest.mark.asyncio
async def test_teardown_stops_the_session_container_and_tolerates_its_absence(docker, tmp_path):
    await provision.provision_desktop("chat-1")
    await provision.teardown_desktop("chat-1")
    assert ("stop", "-t", "15", "gwen-desktop-chat-1") in docker.calls
    assert not (tmp_path / "gwen-desktop-chat-1" / provision.QUEUE_FILE).exists()
    # Already gone (crashed, --rm): not an error.
    await provision.teardown_desktop("chat-1")


@pytest.mark.asyncio
async def test_pool_keeper_tops_up_to_the_target_and_not_beyond(docker, monkeypatch):
    monkeypatch.setattr(desktop_pool, "DESKTOP_POOL_SIZE", 2)
    assert await desktop_pool.refill_once() == 1
    runs = [call for call in docker.calls if call[0] == "run"]
    assert len(runs) == 1
    started = runs[0][runs[0].index("--name") + 1]
    assert started.startswith(provision.POOL_PREFIX)
    docker.idle.append(started)
    assert await desktop_pool.refill_once() == 0


def test_host_worker_registers_provisioning_but_never_desktop_input():
    from temporalio import activity

    import run_worker
    from computer_use_activity import COMPUTER_USE_TOOL_NAMES

    names = {activity._Definition.must_from_callable(fn).name for fn in run_worker.WORKER_ACTIVITIES}
    assert {"provision_desktop", "teardown_desktop"} <= names
    assert not names & {"browser", "desktop_control", "release_desktop", *COMPUTER_USE_TOOL_NAMES}


def test_desktop_hook_claims_only_before_desktop_tools():
    from strands.hooks.events import BeforeToolCallEvent

    from agent_tools import DESKTOP_TOOL_NAMES
    from desktop_hook import DesktopHook

    ensure = AsyncMock()
    hook = DesktopHook(DESKTOP_TOOL_NAMES, ensure)

    def event(name: str) -> BeforeToolCallEvent:
        return BeforeToolCallEvent(agent=None, selected_tool=None, tool_use={"name": name, "toolUseId": "t", "input": {}}, invocation_state={})

    asyncio.run(hook._before(event("load_tool")))
    ensure.assert_not_awaited()
    asyncio.run(hook._before(event("browser")))
    asyncio.run(hook._before(event("take_screenshot")))
    assert ensure.await_count == 2
