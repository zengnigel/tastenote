"""
Resolve AI Builders API base URL from the ai-builders-coach MCP server.
Falls back to default if MCP is unavailable (e.g. npx not in PATH, or timeout).

Same implementation as agent/mcp_config.py and aha_app/mcp_config.py.
"""
import asyncio
import json
import os
import shutil
import sys

# Default used when MCP is not available
DEFAULT_API_BASE = "https://space.ai-builders.com/backend/v1"

# Same as .cursor/mcp.json
NPX_ARGS = ["-y", "@aibuilders/mcp-coach-server"]


def _get_mcp_env() -> dict:
    """Build env for MCP server: current env + AI_BUILDER_TOKEN from app token."""
    env = os.environ.copy()
    token = os.getenv("SUPER_MIND_API_KEY") or os.getenv("AI_BUILDER_TOKEN")
    if token:
        env["AI_BUILDER_TOKEN"] = token
    return env


def _server_params():
    """StdioServerParameters for the coach server (same as .cursor/mcp.json)."""
    from mcp import StdioServerParameters

    env = _get_mcp_env()
    if sys.platform == "win32":
        for name in ("npx", "npx.cmd"):
            path = shutil.which(name)
            if path:
                from pathlib import Path
                parent = Path(path).resolve().parent
                npx_no_ext = parent / "npx"
                if npx_no_ext.exists():
                    cmd = str(npx_no_ext)
                    try:
                        import ctypes
                        buf = ctypes.create_unicode_buffer(1024)
                        if ctypes.windll.kernel32.GetShortPathNameW(cmd, buf, 1024):
                            cmd = buf.value
                    except Exception:
                        pass
                    return StdioServerParameters(command=cmd, args=NPX_ARGS, env=env)
                break
    return StdioServerParameters(
        command="npx",
        args=NPX_ARGS,
        env=env,
    )


async def get_api_base_from_mcp() -> tuple[str, bool]:
    """
    Spawn the ai-builders-coach MCP server, call get_base_url, return (sdk_base_url, mcp_ok).
    On failure returns (DEFAULT_API_BASE, False).
    """
    try:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client
        from mcp.types import TextContent
    except ImportError:
        return (DEFAULT_API_BASE, False)

    server_params = _server_params()
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_base_url", arguments={})
                for content in result.content:
                    if isinstance(content, TextContent):
                        data = json.loads(content.text)
                        return (data.get("sdk_base_url", DEFAULT_API_BASE), True)
    except Exception:
        pass
    return (DEFAULT_API_BASE, False)
