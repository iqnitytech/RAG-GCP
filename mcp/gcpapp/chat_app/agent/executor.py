from __future__ import annotations
import time
from typing import Any
from client_app.domain.entities import ToolInvocationRequest, ToolInvocationResult
from client_app.infrastructure.mcp_gateway import LocalMcpClientGateway
from chat_app.agent.router import RoutedIntent


class ChatAgentExecutor:
    """Executes MCP tools selected by the router and formats natural language responses."""

    def __init__(self, gateway: LocalMcpClientGateway | None = None) -> None:
        self.gateway = gateway or LocalMcpClientGateway()

    async def execute(self, intent: RoutedIntent, mode: str = "MOCK") -> dict[str, Any]:
        req = ToolInvocationRequest(
            server_id=intent.server_id,
            tool_name=intent.tool_name,
            arguments=intent.arguments,
            mode=mode.upper(),
        )

        res: ToolInvocationResult = await self.gateway.invoke_tool(req)
        reply = self._format_reply(intent, res, mode.upper())

        return {
            "reply": reply,
            "tool_used": intent.tool_name,
            "server_id": intent.server_id,
            "arguments": intent.arguments,
            "reasoning": intent.reasoning,
            "confidence": intent.confidence,
            "execution_time_ms": res.duration_ms,
            "status": res.status,
            "mode": mode.upper(),
            "result_payload": res.payload,
        }

    def _format_reply(self, intent: RoutedIntent, res: ToolInvocationResult, mode: str) -> str:
        payload = res.payload
        is_error = res.status == "error" or payload.get("status") == "error"

        if is_error:
            error_data = payload.get("data", {}) if isinstance(payload, dict) else {}
            err_msg = error_data.get("message") or payload.get("error") or "Unknown error occurred"
            err_type = error_data.get("error_type") or payload.get("error_type") or "Error"

            lines = [
                f"⚠️ **Tool Execution Error ({err_type})**",
                f"",
                f"> **Server:** `{intent.server_id.upper()} MCP`  ",
                f"> **Tool:** `{intent.tool_name}`  ",
                f"> **Mode:** `{mode}`  ",
                f"",
                f"**Details:** {err_msg}",
            ]
            if mode == "SDK" and "Authentication" in err_type:
                lines.extend([
                    "",
                    "💡 **Debugging Hint (SDK Mode):**",
                    "- Enterprise SDK mode requires `SDK_API_KEY` and `SDK_BASE_URL` to connect to Enterprise Data Hub (EDH).",
                    "- Set these environment variables in your terminal or configure them in `.env`.",
                    "- Alternatively, switch the mode toggle to **MOCK** in the top-right corner to test with mock industrial data.",
                ])
            return "\n".join(lines)

        data = payload.get("data", payload)
        t_name = intent.tool_name

        # Custom domain formatters
        if t_name == "customer_get_profile":
            return self._format_customer_profile(data)
        elif t_name == "customer_search":
            return self._format_customer_search(data)
        elif t_name == "customer_get_entitlements":
            return self._format_customer_entitlements(data)
        elif t_name == "customer_get_assigned_machines":
            return self._format_customer_machines(data)
        elif t_name == "machine_get_details":
            return self._format_machine_details(data)
        elif t_name == "machine_list_fleet":
            return self._format_machine_fleet(data)
        elif t_name == "machine_get_maintenance_history":
            return self._format_maintenance_history(data)
        elif t_name == "machine_update_operational_state":
            return self._format_state_update(data)
        elif t_name == "telemetry_get_latest_metrics":
            return self._format_latest_metrics(data)
        elif t_name == "telemetry_query_range":
            return self._format_query_range(data)
        elif t_name == "telemetry_get_active_alerts":
            return self._format_active_alerts(data)
        elif t_name == "telemetry_diagnose_fault_code":
            return self._format_diagnose_fault(data)
        else:
            return f"✅ **Tool `{t_name}` executed successfully.**\n\n```json\n{data}\n```"

    def _format_customer_profile(self, d: dict) -> str:
        reps = d.get("account_representatives", [])
        rep_text = ", ".join(f"{r.get('name')} ({r.get('email')})" for r in reps) if reps else "None assigned"
        addr = d.get("address", {})
        addr_str = f"{addr.get('street', '')}, {addr.get('city', '')}, {addr.get('state', '')} {addr.get('country', '')}"

        return (
            f"### 🏢 Customer Profile: **{d.get('company_name', 'N/A')}**\n\n"
            f"- **Customer ID:** `{d.get('customer_id')}`\n"
            f"- **Tier:** `{d.get('tier')}` | **Status:** `{d.get('billing_status')}`\n"
            f"- **Industry:** {d.get('industry')} | **Region:** {d.get('region')}\n"
            f"- **Location:** {addr_str}\n"
            f"- **Employees:** {d.get('employee_count'):,} | **Annual Revenue:** ${d.get('annual_revenue_usd', 0):,}\n"
            f"- **Account Representatives:** {rep_text}\n"
        )

    def _format_customer_search(self, d: Any) -> str:
        records = d if isinstance(d, list) else d.get("customers", [])
        if not records:
            return "🔍 **No customers found matching the search criteria.**"

        lines = [f"### 🔍 Customer Search Results ({len(records)} found)\n"]
        for c in records:
            lines.append(
                f"- **{c.get('company_name', 'N/A')}** (`{c.get('customer_id')}`) — "
                f"Tier: `{c.get('tier')}` | Region: `{c.get('region')}` | Industry: *{c.get('industry')}*"
            )
        return "\n".join(lines)

    def _format_customer_entitlements(self, d: dict) -> str:
        entitlements = d.get("entitlements", [])
        lines = [
            f"### 📜 Service Entitlements for `{d.get('customer_id')}`\n",
            f"Total Active Entitlements: **{d.get('total_active_entitlements', len(entitlements))}**\n",
        ]
        for e in entitlements:
            lines.append(
                f"- **{e.get('service_name')}** (SLA: `{e.get('sla_level')}`) — "
                f"Max Machines: {e.get('max_machines')}, Expires: {e.get('expiration_date')}\n"
                f"  *Features:* {', '.join(e.get('features', []))}"
            )
        return "\n".join(lines)

    def _format_customer_machines(self, d: dict) -> str:
        machines = d.get("machines", [])
        lines = [
            f"### 🚜 Machines Assigned to Customer `{d.get('customer_id')}`\n",
            f"Total Assigned Machines: **{len(machines)}**\n",
        ]
        for m in machines:
            lines.append(
                f"- **Machine ID:** `{m.get('machine_id')}` | **Model:** {m.get('model_type')} | "
                f"**Site:** `{m.get('site_id')}` | **Status:** `{m.get('status')}`"
            )
        return "\n".join(lines)

    def _format_machine_details(self, d: dict) -> str:
        loc = d.get("location", {})
        spec = d.get("hardware_spec", {})
        return (
            f"### ⚙️ Machine Specifications: **{d.get('machine_id')}**\n\n"
            f"- **Model:** {d.get('model_type')} ({d.get('model_series')})\n"
            f"- **Serial Number:** `{d.get('serial_number')}` | **Firmware:** `{d.get('firmware_version')}`\n"
            f"- **Operational Status:** `{d.get('status')}`\n"
            f"- **Owner Customer ID:** `{d.get('owner_customer_id')}`\n"
            f"- **Location:** Site `{loc.get('site_id')}`, {loc.get('building')} Floor {loc.get('floor')} (GPS: {loc.get('gps_lat')}, {loc.get('gps_lon')})\n"
            f"- **Hardware Specs:** CPU: {spec.get('cpu_cores')} Cores | RAM: {spec.get('ram_gb')} GB | Storage: {spec.get('storage_tb')} TB | GPU: {spec.get('gpu_model') or 'None'}\n"
        )

    def _format_machine_fleet(self, d: Any) -> str:
        fleet = d if isinstance(d, list) else d.get("machines", [])
        lines = [f"### 🚜 Machine Fleet Registry ({len(fleet)} machines found)\n"]
        for m in fleet:
            lines.append(
                f"- **`{m.get('machine_id')}`** | Model: {m.get('model_type')} | "
                f"Status: `{m.get('status')}` | Customer: `{m.get('owner_customer_id')}`"
            )
        return "\n".join(lines)

    def _format_maintenance_history(self, d: dict) -> str:
        records = d.get("records", [])
        lines = [
            f"### 🛠️ Maintenance History for Machine `{d.get('machine_id')}`\n",
            f"- **Total Recorded Services:** {d.get('total_records', len(records))}\n"
            f"- **Total Maintenance Cost:** ${d.get('total_cost_usd', 0):,}\n",
        ]
        for r in records:
            lines.append(
                f"- **{r.get('performed_at', '')[:10]}** — *{r.get('work_type')}*: {r.get('description')}\n"
                f"  *Technician:* {r.get('technician_name')} | *Duration:* {r.get('duration_hours')} hrs | *Cost:* ${r.get('cost_usd', 0):,}  \n"
                f"  *Parts Replaced:* {', '.join(r.get('parts_replaced', [])) or 'None'}"
            )
        return "\n".join(lines)

    def _format_state_update(self, d: dict) -> str:
        return (
            f"### 🔄 Operational State Updated Successfully\n\n"
            f"- **Machine ID:** `{d.get('machine_id')}`\n"
            f"- **Transition:** `{d.get('previous_state')}` ➔ **`{d.get('new_state')}`**\n"
            f"- **Reason:** *{d.get('reason')}*\n"
            f"- **Audit ID:** `{d.get('audit_id')}` | **Updated By:** `{d.get('updated_by')}`\n"
        )

    def _format_latest_metrics(self, d: dict) -> str:
        metrics = d.get("metrics", [])
        lines = [
            f"### 📊 Live Telemetry Snapshot for `{d.get('machine_id')}`\n",
            f"*Snapshot Time: {d.get('snapshot_time')} (Source: {d.get('source')})*\n",
        ]
        for m in metrics:
            lines.append(f"- **{m.get('metric_name')}:** `{m.get('value')} {m.get('unit')}` (Quality: *{m.get('quality')}*)")
        return "\n".join(lines)

    def _format_query_range(self, d: Any) -> str:
        series = d if isinstance(d, list) else d.get("series", [])
        lines = [f"### 📈 Telemetry Time-Series Aggregates ({len(series)} metrics)\n"]
        for s in series:
            pts = s.get("points", [])
            lines.append(
                f"- **Metric `{s.get('metric_name')}` ({s.get('unit')}):** {len(pts)} aggregate points. "
                f"Min: `{s.get('min_value')}`, Max: `{s.get('max_value')}`, Avg: `{s.get('avg_value')}`"
            )
        return "\n".join(lines)

    def _format_active_alerts(self, d: Any) -> str:
        alerts = d if isinstance(d, list) else d.get("alerts", [])
        if not alerts:
            return "✅ **No active telemetry threshold alerts at this time.** Fleet is operating within normal boundaries."

        lines = [f"### 🚨 Active Telemetry Alerts ({len(alerts)} alerts)\n"]
        for a in alerts:
            lines.append(
                f"- **[{a.get('severity')}] Alert `{a.get('alert_id')}`** on Machine `{a.get('machine_id')}`\n"
                f"  *Metric:* {a.get('metric_name')} | *Actual:* `{a.get('actual_value')}` (Threshold: `{a.get('threshold_value')}`)\n"
                f"  *Triggered At:* {a.get('triggered_at')}  \n"
                f"  *Description:* {a.get('description')}  \n"
                f"  *Remediation:* **{a.get('remediation')}**\n"
            )
        return "\n".join(lines)

    def _format_diagnose_fault(self, d: dict) -> str:
        causes = d.get("root_causes", [])
        steps = d.get("diagnostic_steps", [])
        parts = d.get("recommended_parts", [])

        lines = [
            f"### 🩺 Diagnosis for Fault Code **`{d.get('fault_code')}`**: {d.get('name')}\n",
            f"- **Severity:** `{d.get('severity')}` | **Est. Repair Time:** `{d.get('estimated_repair_hours')} hours`\n",
            f"**Description:** {d.get('description')}\n",
            f"**Possible Root Causes:**\n" + "\n".join(f"  - {c}" for c in causes),
            f"\n**Diagnostic Steps:**\n" + "\n".join(f"  1. {s}" for s in steps),
            f"\n**Recommended Replacement Parts:**\n" + "\n".join(f"  - {p}" for p in parts),
        ]
        if d.get("documentation_url"):
            lines.append(f"\n📖 **Technical Documentation:** [View Manual]({d.get('documentation_url')})")
        return "\n".join(lines)
