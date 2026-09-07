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

_FORWARD_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class Supervisor:
    def __init__(
        self,
        command: list[str],
        *,
        backoff_initial: float = BACKOFF_INITIAL,
        backoff_max: float = BACKOFF_MAX,
        stable_seconds: float = STABLE_SECONDS,
        kill_timeout: float = KILL_TIMEOUT,
    ) -> None:
        self.command = command
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.stable_seconds = stable_seconds
        self.kill_timeout = kill_timeout
        self.child: subprocess.Popen | None = None
        self.shutdown_signal: int | None = None

    def log(self, message: str) -> None:
        print(f"[worker-supervisor] {message}", file=sys.stderr, flush=True)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        # Record intent; the run loop does the forwarding/reaping so shutdown
        # is not performed inside a signal handler frame.
        self.shutdown_signal = signum
        child = self.child
        if child is not None and child.poll() is None:
            try:
                child.send_signal(
                    signal.SIGTERM if signum == signal.SIGHUP else signum
                )
            except OSError:
                pass

    def _spawn(self) -> subprocess.Popen:
        # start_new_session=False: the child stays in our process group so a
        # group-wide SIGINT from concurrently/ctrl-c reaches it too.
        return subprocess.Popen(self.command)

    def _reap(self, child: subprocess.Popen) -> int:
        try:
            return child.wait(timeout=self.kill_timeout)
        except subprocess.TimeoutExpired:
            self.log(f"child {child.pid} ignored signal; sending SIGKILL")
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
            code = self.child.wait()
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

        # Shutdown path: signal already forwarded by the handler; reap without
        # restarting.
        child = self.child
        exit_code = 0
        if child is not None:
            if child.poll() is None:
                exit_code = self._reap(child)
            else:
                exit_code = child.returncode
        self.log(
            f"shutdown (signal {self.shutdown_signal}); child reaped, exiting"
        )
        return 128 + self.shutdown_signal if self.shutdown_signal else exit_code

    def _sleep(self, seconds: float) -> None:
        # Interruptible sleep: a shutdown signal during backoff exits promptly.
        deadline = time.monotonic() + seconds
        while self.shutdown_signal is None and time.monotonic() < deadline:
            time.sleep(0.1)


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
