#!/usr/bin/env python3
"""Entry point for the interactive Terminal MCP Client."""

from __future__ import annotations
import asyncio
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from client_app.presentation.cli import TerminalMcpClient


def main() -> None:
    mode = os.getenv("DATA_PROVIDER_MODE", "MOCK")
    client = TerminalMcpClient(mode=mode)
    asyncio.run(client.run())


if __name__ == "__main__":
    main()
