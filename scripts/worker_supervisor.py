#!/usr/bin/env python3
"""Supervise a child process: restart on any exit, with capped backoff.

Purpose: keep the Temporal dev worker alive under `pnpm dev:all` without the
whole concurrently stack dying when the worker crashes. The supervisor itself
stays up; only the child restarts.

Behavior:
- Restarts the child on ANY exit -- crash, signal, or unexpected clean exit --
  because a dev worker should only stop when the supervisor is told to stop.
- Exponential backoff between restarts (BACKOFF_INITIAL doubling to
  BACKOFF_MAX). A child that stayed alive >= STABLE_SECONDS resets the backoff,
  so one-off crashes restart fast but tight crash loops slow to BACKOFF_MAX.
- SIGTERM/SIGINT/SIGHUP to the supervisor are forwarded to the child, the child
  is reaped (with a SIGKILL escalation after KILL_TIMEOUT), and the supervisor
  exits WITHOUT restarting -- no orphaned or resurrected children.
- The wait on the child is polled in POLL_INTERVAL slices, never a bare
  blocking wait(), so a child that ignores SIGTERM cannot pin the supervisor:
  shutdown always completes within roughly POLL_INTERVAL + KILL_TIMEOUT.

Usage:
    worker_supervisor.py [--] <command> [args...]

Stdlib only; runs on any Python 3.9+. No orchestrator imports so it stays
launchable by the system interpreter or orchestrator/.venv/bin/python alike.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

BACKOFF_INITIAL = 1.0
BACKOFF_MAX = 30.0
STABLE_SECONDS = 30.0
KILL_TIMEOUT = 10.0
POLL_INTERVAL = 0.1

_FORWARD_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def _forwarded_signal(signum: int) -> int:
    # SIGHUP (terminal closed) is not a meaningful request to a worker; ask it
    # to terminate instead.
    return signal.SIGTERM if signum == signal.SIGHUP else signum


class Supervisor:
    def __init__(
        self,
        command: list[str],
        *,
        backoff_initial: float = BACKOFF_INITIAL,
        backoff_max: float = BACKOFF_MAX,
        stable_seconds: float = STABLE_SECONDS,
        kill_timeout: float = KILL_TIMEOUT,
        poll_interval: float = POLL_INTERVAL,
    ) -> None:
        self.command = command
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.stable_seconds = stable_seconds
        self.kill_timeout = kill_timeout
        self.poll_interval = poll_interval
        self.child: subprocess.Popen | None = None
        self.shutdown_signal: int | None = None

    def log(self, message: str) -> None:
        print(f"[worker-supervisor] {message}", file=sys.stderr, flush=True)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        # Record intent only. The run loop notices it within one poll interval
        # and performs the forwarding/reaping outside the handler frame, so a
        # child that ignores the signal cannot pin the supervisor in wait().
        self.shutdown_signal = signum

    def _spawn(self) -> subprocess.Popen:
        # start_new_session=False: the child stays in our process group so a
        # group-wide SIGINT from concurrently/ctrl-c reaches it too.
        return subprocess.Popen(self.command)

    def _wait_child(self, child: subprocess.Popen) -> int | None:
        """Wait for the child to exit, returning early if shutdown is requested.

        Returns the exit code, or None when a shutdown signal interrupted the
        wait while the child was still running. Never blocks unboundedly:
        each wait() slice is capped at poll_interval.
        """
        while self.shutdown_signal is None:
            try:
                return child.wait(timeout=self.poll_interval)
            except subprocess.TimeoutExpired:
                continue
        return child.poll()

    def _terminate(self, child: subprocess.Popen, signum: int) -> int:
        """Forward the shutdown signal, then reap with a bounded SIGKILL escalation."""
        if child.poll() is not None:
            return child.returncode
        try:
            child.send_signal(_forwarded_signal(signum))
        except OSError:
            pass
        try:
            return child.wait(timeout=self.kill_timeout)
        except subprocess.TimeoutExpired:
            self.log(
                f"child {child.pid} ignored signal for {self.kill_timeout:.1f}s; "
                "sending SIGKILL"
            )
            try:
                child.kill()
            except OSError:
                pass
            return child.wait()

    def run(self) -> int:
        for signum in _FORWARD_SIGNALS:
            signal.signal(signum, self._handle_signal)

        backoff = self.backoff_initial
        while self.shutdown_signal is None:
            started = time.monotonic()
            try:
                self.child = self._spawn()
            except OSError as error:
                self.log(f"spawn failed: {error}; retrying in {backoff:.1f}s")
                self._sleep(backoff)
                backoff = min(backoff * 2, self.backoff_max)
                continue
            self.log(f"child started pid={self.child.pid}")
            code = self._wait_child(self.child)
            uptime = time.monotonic() - started
            if self.shutdown_signal is not None:
                break
            self.log(
                f"child pid={self.child.pid} exited code={code} "
                f"after {uptime:.1f}s; restarting in {backoff:.1f}s"
            )
            if uptime >= self.stable_seconds:
                backoff = self.backoff_initial
            self._sleep(backoff)
            backoff = min(backoff * 2, self.backoff_max)

        # Shutdown path: forward the signal and reap without restarting. The
        # reap is bounded by kill_timeout, after which SIGKILL is sent.
        child = self.child
        exit_code = 0
        signum = self.shutdown_signal
        if child is not None:
            if child.poll() is None and signum is not None:
                exit_code = self._terminate(child, signum)
            else:
                exit_code = child.returncode
        self.log(f"shutdown (signal {signum}); child reaped, exiting")
        return 128 + signum if signum else exit_code

    def _sleep(self, seconds: float) -> None:
        # Interruptible sleep: a shutdown signal during backoff exits promptly.
        deadline = time.monotonic() + seconds
        while self.shutdown_signal is None and time.monotonic() < deadline:
            time.sleep(self.poll_interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a child command is required")
    return Supervisor(command).run()


if __name__ == "__main__":
    sys.exit(main())
