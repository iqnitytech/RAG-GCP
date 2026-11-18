#!/usr/bin/env python3
"""Interactive CLI tool runner for testing and debugging MCP tools with custom input data."""

from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from typing import Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config.settings import Settings
from core.logging import configure_logging


def get_server_for_tool(tool_name: str) -> tuple[Any, str]:
    """Find and instantiate the server that owns the given tool."""
    from mcp.customer_mcp.server import create_server as create_customer_server
    from mcp.machine_mcp.server import create_server as create_machine_server
    from mcp.telemetry_mcp.server import create_server as create_telemetry_server

    if tool_name.startswith("customer_"):
        return create_customer_server(), "CustomerMCP"
    elif tool_name.startswith("machine_"):
        return create_machine_server(), "MachineMCP"
    elif tool_name.startswith("telemetry_"):
        return create_telemetry_server(), "TelemetryMCP"
    else:
        raise ValueError(
            f"Unknown tool prefix for '{tool_name}'. Tools must start with 'customer_', 'machine_', or 'telemetry_'."
        )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke any MCP tool with custom input data and inspect the response."
    )
    parser.add_argument(
        "tool",
        type=str,
        help="Name of the tool (e.g., customer_get_profile, machine_get_details, telemetry_get_latest_metrics)",
    )
    parser.add_argument(
        "--args",
        type=str,
        default="{}",
        help="JSON string of tool arguments. e.g. '{\"customer_id\": \"CUST-001001\"}'",
    )
    parser.add_argument(
        "--mode",
        choices=["MOCK", "SDK"],
        default="MOCK",
        help="Data provider mode: MOCK (default) or SDK",
    )
    args = parser.parse_args()

    os.environ["DATA_PROVIDER_MODE"] = args.mode
    configure_logging(level="INFO", format="TEXT")

    try:
        kwargs = json.loads(args.args)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON in --args: {e}")
        sys.exit(1)

    print("=" * 80)
    print(f"  MCP TOOL RUNNER & DEBUGGER")
    print(f"  Tool: {args.tool} | Mode: {args.mode}")
    print(f"  Input Arguments: {json.dumps(kwargs, indent=2)}")
    print("=" * 80)

    try:
        app, server_name = get_server_for_tool(args.tool)
        print(f"📡 Calling '{args.tool}' on {server_name}...\n")
        
        # Invoke the FastMCP / MCPServer tool
        result = await app.call_tool(args.tool, kwargs)

        print("--- [TOOL OUTPUT RESPONSE] ---")
        if hasattr(result, "content"):
            for item in result.content:
                if hasattr(item, "text"):
                    try:
                        parsed = json.loads(item.text)
                        print(json.dumps(parsed, indent=2))
                    except Exception:
                        print(item.text)
                else:
                    print(item)
        else:
            print(json.dumps(result, indent=2, default=str))

        print("\n✅ Execution completed successfully.")
    except Exception as exc:
        print(f"\n❌ Tool Execution Failed with Exception:\n{type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
