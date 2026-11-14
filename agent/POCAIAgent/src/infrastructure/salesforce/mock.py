from __future__ import annotations

from typing import Any, Mapping, Sequence

_DATA: dict[str, list[dict[str, Any]]] = {
    "Customer": [
        {"Id": "C-1", "Name": "Acme Corp", "Tier": "Gold"},
        {"Id": "C-2", "Name": "Globex Industries", "Tier": "Silver"},
    ],
    "Machine": [
        {"Id": "M-1", "Name": "CNC-100", "Status": "Running", "CustomerId": "C-1"},
        {"Id": "M-2", "Name": "Press-200", "Status": "Maintenance", "CustomerId": "C-1"},
        {"Id": "M-3", "Name": "Lathe-300", "Status": "Running", "CustomerId": "C-2"},
    ],
    "Telemetry": [
        {"MachineId": "M-1", "Temp": 71.2, "Vibration": 0.30, "Ts": "2026-11-13T07:00:00Z"},
        {"MachineId": "M-1", "Temp": 73.8, "Vibration": 0.35, "Ts": "2026-11-13T07:05:00Z"},
        {"MachineId": "M-2", "Temp": 91.5, "Vibration": 0.92, "Ts": "2026-11-13T07:00:00Z"},
        {"MachineId": "M-3", "Temp": 65.0, "Vibration": 0.20, "Ts": "2026-11-13T07:00:00Z"},
    ],
}


class MockSalesforce:
    """In-memory stand-in for the Salesforce SDK adapter (same SalesforcePort contract)."""

    def __init__(self, data: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._data = data if data is not None else _DATA

    async def query(
        self,
        sobject: str,
        fields: Sequence[str],
        where: Mapping[str, str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = self._data.get(sobject, [])
        for key, value in (where or {}).items():
            rows = [r for r in rows if str(r.get(key, "")).lower() == str(value).lower()]
        return [{f: r.get(f) for f in fields} for r in rows[:limit]]

    async def search(self, sobject: str, fields: Sequence[str], field: str, text: str, limit: int = 50):
        rows = [r for r in self._data.get(sobject, []) if text.lower() in str(r.get(field, "")).lower()]
        return [{f: r.get(f) for f in fields} for r in rows[:limit]]
