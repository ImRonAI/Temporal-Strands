"""Minimal MCP server for TemporalMCPClient {server}-call-tool tests."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Return the given text."""
    return text


if __name__ == "__main__":
    mcp.run(transport="stdio")
