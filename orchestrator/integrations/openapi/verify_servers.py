"""Verify converted MCP servers: spawn each via stdio, initialize, list tools.

Uses the official `mcp` Python SDK ClientSession against the native converter
binary (@ivotoby/openapi-mcp-server). No credentials are read or injected;
server startup and tools/list do not require upstream API auth. Live tool
invocation is limited to servers whose backends are public and read-only
(cms-coverage). Every spawned server exits when the stdio pipe closes, so no
background processes survive this script.

Run:
    .venv/bin/python verify_servers.py            # all servers
    .venv/bin/python verify_servers.py cms-coverage  # subset
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

LANE = Path(__file__).resolve().parent
MANIFEST = json.loads((LANE / "manifests" / "servers.json").read_text())
BIN = LANE / "node_modules" / ".bin" / "openapi-mcp-server"

# server-name -> (tool name substring to invoke, arguments) — read-only, public.
LIVE_CALLS: dict[str, tuple[str, dict[str, object]]] = {
    "cms-coverage": ("get-metadata-state-id", {}),
}


async def verify(name: str, cfg: dict[str, object]) -> dict[str, object]:
    spec_path = Path(str(MANIFEST["specDir"])) / str(cfg["spec"])
    params = StdioServerParameters(
        command=str(BIN),
        args=[
            "--transport", "stdio",
            "--api-base-url", str(cfg["apiBaseUrl"]),
            "--openapi-spec", str(spec_path),
        ],
    )
    result: dict[str, object] = {"server": name, "spec": cfg["spec"]}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), timeout=60)
            result["serverInfo"] = {
                "name": init.serverInfo.name,
                "version": init.serverInfo.version,
                "protocolVersion": init.protocolVersion,
            }
            tools = await asyncio.wait_for(session.list_tools(), timeout=120)
            result["toolCount"] = len(tools.tools)
            result["sampleTools"] = [t.name for t in tools.tools[:5]]

            live = LIVE_CALLS.get(name)
            if live is not None:
                needle, args = live
                match = next((t for t in tools.tools if needle in t.name), None)
                if match is None:
                    result["liveCall"] = f"no tool matching {needle!r}"
                else:
                    call = await asyncio.wait_for(
                        session.call_tool(match.name, dict(args)), timeout=60
                    )
                    text = call.content[0].text if call.content else ""
                    result["liveCall"] = {
                        "tool": match.name,
                        "isError": bool(call.isError),
                        "responsePreview": text[:300],
                    }
    return result


async def main() -> None:
    wanted = set(sys.argv[1:])
    servers: dict[str, dict[str, object]] = MANIFEST["servers"]
    outcomes: list[dict[str, object]] = []
    for name, cfg in servers.items():
        if wanted and name not in wanted:
            continue
        try:
            outcomes.append(await verify(name, cfg))
        except Exception as exc:  # noqa: BLE001 — verification report, not control flow
            outcomes.append({"server": name, "spec": cfg["spec"], "error": repr(exc)[:400]})
    report = LANE / "manifests" / "verification-report.json"
    report.write_text(json.dumps(outcomes, indent=2) + "\n")
    print(json.dumps(outcomes, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
