"""Stdlib tests for scripts/worker_supervisor.py shutdown and restart behavior.

Runs the supervisor as a real subprocess with a throwaway Python child so
signal delivery, polling, and SIGKILL escalation are exercised for real. No
application sources are imported or started.
"""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

SCRIPTS_DIR = Path(__file__).resolve().parent
SUPERVISOR = SCRIPTS_DIR / "worker_supervisor.py"
sys.path.insert(0, str(SCRIPTS_DIR))

import worker_supervisor  # noqa: E402

# A child that ignores SIGTERM/SIGINT/SIGHUP and sleeps until SIGKILLed.
STUBBORN_CHILD = (
    "import signal, time\n"
    "for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):\n"
    "    signal.signal(s, signal.SIG_IGN)\n"
    "while True:\n"
    "    time.sleep(0.05)\n"
)

# A child that exits immediately with a clean status.
QUICK_EXIT_CHILD = "raise SystemExit(0)\n"

# A child that terminates promptly when signalled (default handlers).
COOPERATIVE_CHILD = "import time\nwhile True:\n    time.sleep(0.05)\n"


def _supervisor_cmd(child_code, **overrides):
    """Build a command that runs the real Supervisor with tightened timing.

    A tiny bootstrap imports the module and constructs `Supervisor` with
    explicit keyword overrides, then calls the real `run()` loop in a fresh
    interpreter so signal handlers, polling, and reaping are exercised live.
    """
    options = {
        "backoff_initial": 0.2,
        "backoff_max": 0.4,
        "stable_seconds": 30.0,
        "kill_timeout": 1.0,
        "poll_interval": 0.05,
    }
    options.update(overrides)
    kwargs = ", ".join(f"{k}={v!r}" for k, v in options.items())
    bootstrap = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import worker_supervisor as ws; "
        f"sys.exit(ws.Supervisor(sys.argv[2:], {kwargs}).run())"
    )
    return [
        sys.executable,
        "-B",
        "-c",
        bootstrap,
        str(SCRIPTS_DIR),
        sys.executable,
        "-B",
        "-c",
        child_code,
    ]


def _start(child_code, **overrides):
    return subprocess.Popen(
        _supervisor_cmd(child_code, **overrides),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_log(proc, needle, timeout, count=1):
    """Read stderr lines until `needle` has appeared `count` times."""
    seen = 0
    lines = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stderr.readline()
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.01)
            continue
        lines.append(line)
        if needle in line:
            seen += 1
            if seen >= count:
                return lines
    raise AssertionError(
        f"expected {count}x {needle!r} within {timeout}s; got:\n{''.join(lines)}"
    )


def _drain(proc, timeout, earlier=()):
    """Wait for exit; return all stderr (lines already read plus the rest)."""
    out, err = proc.communicate(timeout=timeout)
    return "".join(earlier) + err


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _child_pid_from_log(lines):
    for line in lines:
        if "child started pid=" in line:
            return int(line.rsplit("pid=", 1)[1].strip())
    raise AssertionError(f"no child pid in log:\n{''.join(lines)}")


class WorkerSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.proc = None

    def tearDown(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def test_wait_is_polled_not_blocking(self):
        # Regression guard: the run loop must not call a bare child.wait().
        source = SUPERVISOR.read_text()
        self.assertNotIn(
            "code = self.child.wait()",
            source,
            "run() must use the bounded _wait_child poll, not a blocking wait()",
        )
        self.assertIn("def _wait_child", source)

    def test_stubborn_child_term_exits_bounded_without_respawn(self):
        kill_timeout = 1.0
        self.proc = _start(STUBBORN_CHILD, kill_timeout=kill_timeout)
        lines = _wait_for_log(self.proc, "child started pid=", timeout=10)
        child_pid = _child_pid_from_log(lines)
        # Give the child a moment to install SIG_IGN handlers.
        time.sleep(0.4)

        sent = time.monotonic()
        self.proc.send_signal(signal.SIGTERM)
        # Bound: poll interval + kill timeout + generous interpreter slack.
        err = _drain(self.proc, timeout=kill_timeout + 6, earlier=lines)
        elapsed = time.monotonic() - sent

        self.assertEqual(self.proc.returncode, 128 + signal.SIGTERM, err)
        self.assertLess(elapsed, kill_timeout + 5, err)
        self.assertIn("sending SIGKILL", err)
        self.assertIn("shutdown (signal 15)", err)
        self.assertEqual(err.count("child started pid="), 1, err)
        self.assertNotIn("restarting in", err)
        # Reaped: no orphan left behind.
        for _ in range(50):
            if not _pid_alive(child_pid):
                break
            time.sleep(0.05)
        self.assertFalse(_pid_alive(child_pid), f"child {child_pid} orphaned")

    def test_cooperative_child_term_exits_without_sigkill(self):
        self.proc = _start(COOPERATIVE_CHILD)
        lines = _wait_for_log(self.proc, "child started pid=", timeout=10)
        time.sleep(0.3)
        self.proc.send_signal(signal.SIGTERM)
        err = _drain(self.proc, timeout=6, earlier=lines)
        self.assertEqual(err.count("child started pid="), 1, err)
        self.assertEqual(self.proc.returncode, 128 + signal.SIGTERM, err)
        self.assertNotIn("sending SIGKILL", err)
        self.assertNotIn("restarting in", err)

    def test_clean_exit_child_is_restarted(self):
        self.proc = _start(QUICK_EXIT_CHILD)
        lines = _wait_for_log(self.proc, "child started pid=", timeout=10, count=2)
        joined = "".join(lines)
        self.assertIn("exited code=0", joined)
        self.assertIn("restarting in", joined)
        self.proc.send_signal(signal.SIGTERM)
        self.proc.communicate(timeout=6)
        self.assertEqual(self.proc.returncode, 128 + signal.SIGTERM)

    def test_shutdown_during_backoff_exits_promptly_without_respawn(self):
        # Long backoff so the supervisor is definitely sleeping when signalled.
        self.proc = _start(QUICK_EXIT_CHILD, backoff_initial=30.0, backoff_max=30.0)
        lines = _wait_for_log(self.proc, "restarting in 30.0s", timeout=10)
        sent = time.monotonic()
        self.proc.send_signal(signal.SIGTERM)
        err = _drain(self.proc, timeout=6, earlier=lines)
        elapsed = time.monotonic() - sent
        self.assertEqual(self.proc.returncode, 128 + signal.SIGTERM, err)
        self.assertLess(elapsed, 5.0, err)
        self.assertEqual(err.count("child started pid="), 1, err)
        self.assertNotIn("sending SIGKILL", err)

    def test_sighup_is_forwarded_as_sigterm(self):
        self.assertEqual(
            worker_supervisor._forwarded_signal(signal.SIGHUP), signal.SIGTERM
        )
        self.assertEqual(
            worker_supervisor._forwarded_signal(signal.SIGINT), signal.SIGINT
        )
        self.assertEqual(
            worker_supervisor._forwarded_signal(signal.SIGTERM), signal.SIGTERM
        )


if __name__ == "__main__":
    unittest.main()
