"""Generic MCP probe client used to verify user-supplied MCP servers.

Connects to a server over stdio or streamable-http, performs the official MCP
handshake (``ClientSession.initialize``), lists tools, and optionally invokes a
set of safe read-only tools. This is a *test client only* — it never wraps,
adapts, or modifies the servers under test.

Usage:
    probe.py --stdio -- <command> [args...]
    probe.py --http <url>
    options:
      --call '<tool_name> <json-args>'   (repeatable)
      --env KEY=VALUE                    (repeatable, stdio only)
      --cwd DIR                          (stdio only)
      --timeout SECONDS                  (default 60)
      --list-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--http", default=None)
    parser.add_argument("--call", action="append", default=[])
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("command", nargs="*")
    return parser.parse_args()


def _log(payload: dict) -> None:
    payload["ts"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(payload, default=str), flush=True)


async def _run_session(session, args) -> None:
    init = await session.initialize()
    _log(
        {
            "event": "initialized",
            "server_name": init.serverInfo.name,
            "server_version": init.serverInfo.version,
            "protocol": init.protocolVersion,
        }
    )
    tools = await session.list_tools()
    _log(
        {
            "event": "tools",
            "count": len(tools.tools),
            "names": [t.name for t in tools.tools],
        }
    )
    if args.list_only:
        return
    for spec in args.call:
        name, _, raw = spec.partition(" ")
        call_args = json.loads(raw) if raw.strip() else {}
        try:
            result = await asyncio.wait_for(
                session.call_tool(name, call_args), timeout=args.timeout
            )
            texts = [
                c.text for c in result.content if getattr(c, "text", None) is not None
            ]
            _log(
                {
                    "event": "call",
                    "tool": name,
                    "args": call_args,
                    "is_error": bool(result.isError),
                    "content": [t[:2000] for t in texts],
                }
            )
        except Exception as exc:  # noqa: BLE001 - report and continue probing
            _log({"event": "call", "tool": name, "args": call_args, "exception": repr(exc)})


async def _main() -> int:
    args = _parse_args()
    from mcp import ClientSession

    if args.http:
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(args.http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await _run_session(session, args)
        return 0

    if not args.stdio or not args.command:
        print("need --stdio -- <command> or --http <url>", file=sys.stderr)
        return 2

    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    for pair in args.env:
        key, _, value = pair.partition("=")
        env[key] = value
    params = StdioServerParameters(
        command=args.command[0], args=args.command[1:], env=env, cwd=args.cwd
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await _run_session(session, args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(asyncio.wait_for(_main(), timeout=180)))
    except (TimeoutError, asyncio.TimeoutError):
        _log({"event": "fatal", "error": "probe timeout"})
        raise SystemExit(3)
