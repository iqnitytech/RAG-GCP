from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Optional

CUSTOMER_ID_RE = re.compile(r"CUST-[A-Z0-9\-]+", re.IGNORECASE)
MACHINE_ID_RE = re.compile(r"MCH-[A-Z0-9]{4}-[A-Z0-9]{4}", re.IGNORECASE)
FAULT_CODE_RE = re.compile(r"(E-\d{3}|FC-[A-Z0-9\-]+)", re.IGNORECASE)
SEVERITY_RE = re.compile(r"\b(CRITICAL|WARNING|INFO)\b", re.IGNORECASE)
STATE_RE = re.compile(r"\b(ACTIVE|MAINTENANCE|STANDBY|OFFLINE|DECOMMISSIONED)\b", re.IGNORECASE)
REGION_RE = re.compile(r"\b(NA-EAST|NA-WEST|EU-WEST|APAC|LATAM)\b", re.IGNORECASE)


def extract_customer_id(text: str) -> str | None:
    """Extract a customer ID from natural language query.

    Supports:
    - Standard CUST-XXXX patterns (e.g. CUST-001001, CUST-1002)
    - Natural language phrases: 'customer number 234', 'customer no. 234', 'customer #234', 'customer id 234'
    - Direct expressions: 'customer 234', 'customer: 234', 'client 234', 'customer number: 234'
    - '... for customer 234' or '... for customer number 234'
    """
    # 1. Standard pattern: CUST-XXXX
    m = re.search(r"\b(CUST-[A-Za-z0-9\-]+)\b", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # 2. "customer/client ... number/no/id/code/# [: #] <id>"
    # Handles: "customer number 234", "customer number: 234", "customer no 234", "customer id 234", "customer #234"
    m = re.search(
        r"\b(?:customer|client)?\s*(?:number|no\.?|id|code|#)\s*[:#]?\s*([A-Za-z0-9\-]+)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        val = m.group(1)
        if val.lower() not in {"is", "the", "for", "and", "or", "to", "in", "a", "an", "details", "profile"}:
            return val

    # 3. Direct digits following customer/client context
    # e.g. "customer 234", "customer: 234", "details for customer 234", "customer details for 234", "lookup customer 234"
    if any(w in text.lower() for w in ["customer", "client"]):
        m = re.search(r"\b(\d+)\b", text)
        if m:
            return m.group(1)

    # 4. "customer/client <id>" where <id> is alphanumeric and not a stopword
    m = re.search(r"\b(?:customer|client)\s+([A-Za-z0-9\-]+)\b", text, re.IGNORECASE)
    if m:
        val = m.group(1)
        stopwords = {
            "details", "profile", "search", "list", "name", "info", "data", "entitlement",
            "entitlements", "machine", "machines", "service", "support", "contract", "account",
            "tier", "region", "sla", "representative", "reps", "is", "the", "for", "with", "and",
        }
        if val.lower() not in stopwords:
            return val

    return None


def extract_machine_id(text: str) -> str | None:
    """Extract a machine ID from natural language query.

    Supports:
    - Standard MCH-XXXX-XXXX patterns
    - Natural language phrases: 'machine number 123', 'machine #123', 'machine 123'
    """
    m = re.search(r"\b(MCH-[A-Za-z0-9]{4}-[A-Za-z0-9]{4})\b", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    m = re.search(r"\b(MCH-[A-Za-z0-9\-]+)\b", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    m = re.search(
        r"\bmachine\s+(?:number|no\.?|id|code|#)\s*[:#]?\s*([A-Za-z0-9\-]+)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        val = m.group(1)
        if val.lower() not in {"is", "the", "for", "and", "or", "to", "in", "a", "an", "details", "specs"}:
            return val

    m = re.search(r"\bmachine\s*[:#]?\s*(\d+)\b", text, re.IGNORECASE)
    if m:
        return m.group(1)

    return None


@dataclass
class RoutedIntent:
    server_id: str
    tool_name: str
    arguments: dict[str, Any]
    confidence: float
    reasoning: str


class McpIntentRouter:
    """Intelligent natural language router that maps user questions to MCP tools and extracts parameters."""

    def route(self, query: str) -> RoutedIntent:
        q = query.strip()
        q_lower = q.lower()

        # Entity extraction
        cust_id = extract_customer_id(q)
        mch_id = extract_machine_id(q)
        fault_match = FAULT_CODE_RE.search(q)
        sev_match = SEVERITY_RE.search(q)
        state_match = STATE_RE.search(q)
        region_match = REGION_RE.search(q)

        fault_code = fault_match.group(0).upper() if fault_match else None
        severity = sev_match.group(0).upper() if sev_match else None
        state = state_match.group(0).upper() if state_match else None
        region = region_match.group(0).upper() if region_match else None

        # -------------------------------------------------------------
        # 1. Telemetry Domain Routing
        # -------------------------------------------------------------
        if any(w in q_lower for w in ["diagnos", "fault", "root cause", "error code", "troubleshoot"]):
            f_code = fault_code or "E-001"
            return RoutedIntent(
                server_id="telemetry",
                tool_name="telemetry_diagnose_fault_code",
                arguments={"fault_code": f_code},
                confidence=0.95,
                reasoning=f"Question asks for fault code diagnosis; extracted code '{f_code}'.",
            )

        if any(w in q_lower for w in ["alert", "alarm", "warning", "triggered"]):
            args: dict[str, Any] = {}
            if mch_id:
                args["machine_id"] = mch_id
            if severity:
                args["severity"] = severity
            else:
                args["severity"] = "CRITICAL"
            return RoutedIntent(
                server_id="telemetry",
                tool_name="telemetry_get_active_alerts",
                arguments=args,
                confidence=0.92,
                reasoning=f"Question asks about active telemetry alerts; severity='{args.get('severity')}'.",
            )

        if any(w in q_lower for w in ["trend", "time-series", "timeseries", "hourly", "over time", "history range"]):
            target_mch = mch_id or "MCH-A1B2-C3D4"
            return RoutedIntent(
                server_id="telemetry",
                tool_name="telemetry_query_range",
                arguments={
                    "machine_id": target_mch,
                    "start_time": "2026-11-17T08:00:00Z",
                    "end_time": "2026-11-17T12:00:00Z",
                    "resolution": "5m",
                    "aggregation": "AVG",
                },
                confidence=0.90,
                reasoning=f"Question asks for aggregated time-series telemetry for machine '{target_mch}'.",
            )

        if any(w in q_lower for w in ["telemetry", "sensor", "vibration", "temperature", "rpm", "metric", "reading"]):
            target_mch = mch_id or "MCH-A1B2-C3D4"
            return RoutedIntent(
                server_id="telemetry",
                tool_name="telemetry_get_latest_metrics",
                arguments={"machine_id": target_mch},
                confidence=0.95,
                reasoning=f"Question asks for live sensor snapshot for machine '{target_mch}'.",
            )

        # -------------------------------------------------------------
        # 2. Machine Domain Routing
        # -------------------------------------------------------------
        if (any(w in q_lower for w in ["update", "change", "switch", "set", "transition"]) and any(w in q_lower for w in ["state", "status"])) or ("put machine" in q_lower):
            target_mch = mch_id or "MCH-A1B2-C3D4"
            target_state = state or "MAINTENANCE"
            # Extract reason
            reason = "Operator manual state update via Chat Agent"
            if "for " in q_lower:
                reason = q[q_lower.index("for ") + 4:].strip()
            elif "due to " in q_lower:
                reason = q[q_lower.index("due to ") + 7:].strip()

            return RoutedIntent(
                server_id="machine",
                tool_name="machine_update_operational_state",
                arguments={
                    "machine_id": target_mch,
                    "new_state": target_state,
                    "reason": reason,
                },
                confidence=0.92,
                reasoning=f"Question asks to update machine '{target_mch}' operational state to '{target_state}'.",
            )

        if any(w in q_lower for w in ["maintenance history", "repair history", "past service", "work order", "repairs"]):
            target_mch = mch_id or "MCH-A1B2-C3D4"
            return RoutedIntent(
                server_id="machine",
                tool_name="machine_get_maintenance_history",
                arguments={"machine_id": target_mch, "limit": 5},
                confidence=0.95,
                reasoning=f"Question requests past maintenance logs for machine '{target_mch}'.",
            )

        if any(w in q_lower for w in ["fleet", "all machines", "list machines", "machines in", "machines with status"]):
            args = {"page": 1, "page_size": 10}
            if state:
                args["status"] = state
            return RoutedIntent(
                server_id="machine",
                tool_name="machine_list_fleet",
                arguments=args,
                confidence=0.90,
                reasoning=f"Question asks to list fleet machines with filter: {args}.",
            )

        if (mch_id and any(w in q_lower for w in ["machine", "specs", "hardware", "specifications", "location", "details", "serial", "cpu", "ram", "firmware"])) or (mch_id and not cust_id):
            target_mch = mch_id or "MCH-A1B2-C3D4"
            return RoutedIntent(
                server_id="machine",
                tool_name="machine_get_details",
                arguments={"machine_id": target_mch},
                confidence=0.95,
                reasoning=f"Question requests hardware specs and metadata for machine '{target_mch}'.",
            )

        # -------------------------------------------------------------
        # 3. Customer Domain Routing
        # -------------------------------------------------------------
        if ("machine" in q_lower and "assigned" in q_lower) or any(w in q_lower for w in ["assigned machine", "customer's machines", "customer machines", "machines assigned"]):
            target_cust = cust_id or "CUST-001001"
            return RoutedIntent(
                server_id="customer",
                tool_name="customer_get_assigned_machines",
                arguments={"customer_id": target_cust},
                confidence=0.95,
                reasoning=f"Question requests machine fleet assignments for customer '{target_cust}'.",
            )

        if any(w in q_lower for w in ["entitlement", "sla", "contract", "service level", "features", "tier support"]):
            target_cust = cust_id or "CUST-001001"
            return RoutedIntent(
                server_id="customer",
                tool_name="customer_get_entitlements",
                arguments={"customer_id": target_cust},
                confidence=0.95,
                reasoning=f"Question asks for SLA and service entitlements for customer '{target_cust}'.",
            )

        if (any(w in q_lower for w in ["search", "find customer", "lookup customer", "industry", "directory", "filter customers"]) and not cust_id) or (region and not cust_id):
            # Extract query term
            search_query = ""
            for word in ["mining", "construction", "logistics", "energy", "transport", "nexus"]:
                if word in q_lower:
                    search_query = word.capitalize()
                    break
            args = {"query": search_query or "Corp", "limit": 5}
            if region:
                args["region"] = region
            return RoutedIntent(
                server_id="customer",
                tool_name="customer_search",
                arguments=args,
                confidence=0.90,
                reasoning=f"Question requests customer directory search with query='{args.get('query')}' region='{region}'.",
            )

        if cust_id or any(w in q_lower for w in ["customer", "client", "profile", "account", "company"]):
            target_cust = cust_id or "CUST-001001"
            return RoutedIntent(
                server_id="customer",
                tool_name="customer_get_profile",
                arguments={"customer_id": target_cust},
                confidence=0.90,
                reasoning=f"Question asks for 360-degree customer profile for '{target_cust}'.",
            )

        # Default fallback: search customers with whatever query was given
        return RoutedIntent(
            server_id="customer",
            tool_name="customer_search",
            arguments={"query": q[:30], "limit": 5},
            confidence=0.50,
            reasoning=f"No specific identifier found; falling back to directory search with keyword '{q[:30]}'.",
        )
