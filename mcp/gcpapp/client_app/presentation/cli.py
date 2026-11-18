from __future__ import annotations
import asyncio
import json
import os
import sys
from typing import Any

from client_app.application.use_cases import (
    PREDEFINED_SCENARIOS,
    ExecuteScenarioUseCase,
    GetServersAndToolsUseCase,
    InvokeToolUseCase,
)
from client_app.domain.entities import ToolInvocationRequest
from client_app.infrastructure.mcp_gateway import LocalMcpClientGateway


class TerminalMcpClient:
    """Interactive command-line client for discovering and executing MCP tools."""

    def __init__(self, mode: str = "MOCK") -> None:
        self.mode = mode.upper()
        self.gateway = LocalMcpClientGateway()

    async def run(self) -> None:
        print("\n" + "=" * 80)
        print("  ENTERPRISE MULTI-MCP TERMINAL CLIENT")
        print(f"  Mode: DATA_PROVIDER_MODE={self.mode}")
        print("=" * 80)

        servers = await GetServersAndToolsUseCase(self.gateway).execute()

        while True:
            print("\nSelect a Server to Inspect & Test:")
            for idx, s in enumerate(servers, 1):
                print(f"  [{idx}] {s.display_name} ({len(s.tools)} tools)")
            print(f"  [4] Run Predefined Multi-MCP Integration Scenarios")
            print(f"  [5] Toggle Mode (Current: {self.mode})")
            print(f"  [0] Exit")

            choice = input("\nEnter choice [0-5]: ").strip()
            if choice == "0":
                print("\nGoodbye!\n")
                break
            elif choice == "5":
                self.mode = "SDK" if self.mode == "MOCK" else "MOCK"
                print(f"\nSwitched mode to: {self.mode}")
            elif choice == "4":
                await self._handle_scenarios()
            elif choice in ("1", "2", "3"):
                server_idx = int(choice) - 1
                await self._handle_server_tools(servers[server_idx])
            else:
                print("Invalid choice, please retry.")

    async def _handle_server_tools(self, server: Any) -> None:
        while True:
            print(f"\n--- {server.display_name} ---")
            for idx, tool in enumerate(server.tools, 1):
                print(f"  [{idx}] {tool.name} - {tool.description[:60]}...")
            print("  [0] Back to main menu")

            choice = input(f"\nSelect tool to execute [0-{len(server.tools)}]: ").strip()
            if choice == "0":
                break

            try:
                tool_idx = int(choice) - 1
                if 0 <= tool_idx < len(server.tools):
                    tool = server.tools[tool_idx]
                    await self._invoke_tool_interactive(server.server_id, tool)
                else:
                    print("Invalid selection.")
            except ValueError:
                print("Invalid input.")

    async def _invoke_tool_interactive(self, server_id: str, tool: Any) -> None:
        print(f"\n" + "-" * 70)
        print(f"  TOOL: {tool.name}")
        print(f"  Description: {tool.description}")
        print("-" * 70)

        args = dict(tool.preset_arguments)
        print("\nDefault / Preset Arguments:")
        print(json.dumps(args, indent=2))

        custom = input("\nPress ENTER to use these defaults, or paste custom JSON arguments: ").strip()
        if custom:
            try:
                args = json.loads(custom)
            except json.JSONDecodeError as err:
                print(f"Invalid JSON: {err}. Falling back to defaults.")

        print(f"\n🚀 Invoking '{tool.name}' on {server_id} in {self.mode} mode...")
        use_case = InvokeToolUseCase(self.gateway)
        req = ToolInvocationRequest(
            server_id=server_id,
            tool_name=tool.name,
            arguments=args,
            mode=self.mode,
        )

        res = await use_case.execute(req)
        status_icon = "✅" if res.status == "success" else "❌"
        print(f"\n{status_icon} Status: {res.status.upper()} | Duration: {res.duration_ms} ms")
        print("Response Payload:")
        print(json.dumps(res.payload, indent=2, default=str))
        input("\nPress ENTER to continue...")

    async def _handle_scenarios(self) -> None:
        print("\n--- Predefined Multi-MCP Integration Scenarios ---")
        scenarios = list(PREDEFINED_SCENARIOS.values())
        for idx, sc in enumerate(scenarios, 1):
            print(f"  [{idx}] {sc['title']}")
            print(f"      {sc['description']}")
        print("  [0] Back")

        choice = input(f"\nSelect scenario [0-{len(scenarios)}]: ").strip()
        if choice == "0":
            return
        try:
            sc_idx = int(choice) - 1
            if 0 <= sc_idx < len(scenarios):
                sc = scenarios[sc_idx]
                print(f"\n🚀 Executing scenario '{sc['title']}'...")
                use_case = ExecuteScenarioUseCase(self.gateway)
                res = await use_case.execute(sc["scenario_id"], mode=self.mode)
                print(f"\nResults: {res.passed_steps}/{res.total_steps} steps passed in {res.duration_ms} ms")
                for s in res.step_results:
                    icon = "✅" if s.status == "success" else "❌"
                    print(f"  {icon} {s.tool_name:<36} | {s.duration_ms}ms | status: {s.status}")
                input("\nPress ENTER to continue...")
        except Exception as exc:
            print(f"Error executing scenario: {exc}")
