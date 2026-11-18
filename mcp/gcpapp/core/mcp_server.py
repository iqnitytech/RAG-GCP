"""Unified bridge module for importing MCPServer from the official mcp pip package,
resolving the package collision where the local 'mcp/' directory shadows 'mcp' (ARCH-06).
"""
from __future__ import annotations

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        from mcp.server import MCPServer as FastMCP

__all__ = ["FastMCP"]
