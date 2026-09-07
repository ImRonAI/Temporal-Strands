"""Readiness lease: atomic worker writes, ownership-scoped cleanup, and the
server-side lease validation (fresh/stale/dead/legacy/malformed/missing).

No network, no Temporal: everything runs against tmp_path files and the real
validation functions -- actual behavior, not mocked assertions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import run_worker
import server
from config import READINESS_LEASE_TTL


@pytest.fixture
def readiness_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "worker-readiness.json"
    monkeypatch.setattr(run_worker, "READINESS_PATH", path)
    monkeypatch.setattr(server, "READINESS_PATH", path)
    return path


def write_lease(readiness_path: Path, **overrides) -> dict:
    record = {
        "task_queue": "perplexity-orchestrator",
        "agent": "Gwen",
        "models": ["model-a"],
        "default_model": "model-a",
        "pid": os.getpid(),
        "heartbeat": time.time(),
        **overrides,
    }
    readiness_path.write_text(json.dumps(record))
    return record


# --- worker write side ------------------------------------------------------------


def test_write_readiness_is_atomic_and_carries_lease_fields(
    readiness_path: Path,
) -> None:
    run_worker.write_readiness(["m1", "m2"], "Gwen", "m1")
    record = json.loads(readiness_path.read_text())
    # Existing model fields preserved.
    assert record["models"] == ["m1", "m2"]
    assert record["default_model"] == "m1"
    assert record["agent"] == "Gwen"
    assert record["task_queue"] == "perplexity-orchestrator"
    # New lease fields.
    assert record["pid"] == os.getpid()
    assert abs(time.time() - record["heartbeat"]) < 5
    # Atomic replace leaves no temp files behind.
    leftovers = [p for p in readiness_path.parent.iterdir() if p != readiness_path]
    assert leftovers == []


def test_write_readiness_refresh_overwrites_previous_lease(
    readiness_path: Path,
) -> None:
    run_worker.write_readiness(["m1"], "Gwen", "m1")
    first = json.loads(readiness_path.read_text())["heartbeat"]
    time.sleep(0.01)
    run_worker.write_readiness(["m1"], "Gwen", "m1")
    second = json.loads(readiness_path.read_text())["heartbeat"]
    assert second > first


def test_clear_readiness_removes_own_lease(readiness_path: Path) -> None:
    write_lease(readiness_path, pid=os.getpid())
    run_worker.clear_readiness()
    assert not readiness_path.exists()


def test_clear_readiness_leaves_successor_lease(readiness_path: Path) -> None:
    """A restarted worker's fresh lease survives the old worker's cleanup."""
    write_lease(readiness_path, pid=os.getpid() + 12345)
    run_worker.clear_readiness()
    assert readiness_path.exists()


def test_clear_readiness_missing_file_is_noop(readiness_path: Path) -> None:
    run_worker.clear_readiness()  # must not raise
    assert not readiness_path.exists()


def test_clear_readiness_malformed_file_is_left_alone(readiness_path: Path) -> None:
    readiness_path.write_text("{not json")
    run_worker.clear_readiness()  # must not raise
    assert readiness_path.exists()


# --- server validation side -------------------------------------------------------


def test_validate_accepts_fresh_live_lease() -> None:
    record = {"models": ["m"], "pid": os.getpid(), "heartbeat": time.time()}
    assert server.validate_readiness_record(record) == record


def test_validate_rejects_stale_heartbeat() -> None:
    stale = time.time() - READINESS_LEASE_TTL.total_seconds() - 1
    record = {"models": ["m"], "pid": os.getpid(), "heartbeat": stale}
    assert server.validate_readiness_record(record) is None


def test_validate_rejects_future_heartbeat() -> None:
    future = time.time() + READINESS_LEASE_TTL.total_seconds() + 60
    record = {"models": ["m"], "pid": os.getpid(), "heartbeat": future}
    assert server.validate_readiness_record(record) is None


def test_validate_rejects_dead_pid() -> None:
    # A real, verified-dead PID: spawn and reap a short-lived child.
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    record = {"models": ["m"], "pid": child.pid, "heartbeat": time.time()}
    assert server.validate_readiness_record(record) is None


def test_validate_rejects_legacy_record_without_lease_fields() -> None:
    legacy = {
        "task_queue": "perplexity-orchestrator",
        "agent": "Gwen",
        "models": ["m"],
        "default_model": "m",
    }
    assert server.validate_readiness_record(legacy) is None


@pytest.mark.parametrize(
    "record",
    [
        None,
        [],
        "string",
        42,
        {"pid": "not-int", "heartbeat": time.time()},
        {"pid": True, "heartbeat": time.time()},
        {"pid": -1, "heartbeat": time.time()},
        {"pid": 0, "heartbeat": time.time()},
        {"pid": os.getpid(), "heartbeat": "soon"},
        {"pid": os.getpid(), "heartbeat": None},
        {"pid": os.getpid()},
        {"heartbeat": time.time()},
    ],
)
def test_validate_rejects_malformed_records(record) -> None:
    assert server.validate_readiness_record(record) is None


@pytest.mark.anyio
async def test_readiness_reads_fresh_lease(readiness_path: Path) -> None:
    record = write_lease(readiness_path)
    result = await server.readiness()
    assert result == record


@pytest.mark.anyio
async def test_readiness_none_for_missing_stale_and_malformed(
    readiness_path: Path,
) -> None:
    # Missing file.
    assert await server.readiness() is None
    # Malformed file.
    readiness_path.write_text("{torn write")
    assert await server.readiness() is None
    # Stale lease.
    write_lease(
        readiness_path,
        heartbeat=time.time() - READINESS_LEASE_TTL.total_seconds() - 1,
    )
    assert await server.readiness() is None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
