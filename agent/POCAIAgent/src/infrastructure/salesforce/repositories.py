"""Salesforce-backed repositories: map Salesforce records to domain entities."""
from __future__ import annotations

from domain.entities import Customer, Machine, TelemetryReading
from domain.ports import SalesforcePort

_MACHINE = ("Id", "Name", "Status", "CustomerId")
_CUSTOMER = ("Id", "Name", "Tier")
_TELEMETRY = ("MachineId", "Temp", "Vibration", "Ts")


def _machine(r: dict) -> Machine:
    return Machine(r["Id"], r["Name"], r["Status"], r["CustomerId"])


class SalesforceMachineRepository:
    def __init__(self, sf: SalesforcePort) -> None:
        self._sf = sf

    async def get(self, machine_id: str) -> Machine | None:
        rows = await self._sf.query("Machine", _MACHINE, {"Id": machine_id}, 1)
        return _machine(rows[0]) if rows else None

    async def list_by_customer(self, customer_id: str) -> list[Machine]:
        rows = await self._sf.query("Machine", _MACHINE, {"CustomerId": customer_id})
        return [_machine(r) for r in rows]


class SalesforceCustomerRepository:
    def __init__(self, sf: SalesforcePort) -> None:
        self._sf = sf

    async def get(self, customer_id: str) -> Customer | None:
        rows = await self._sf.query("Customer", _CUSTOMER, {"Id": customer_id}, 1)
        return Customer(rows[0]["Id"], rows[0]["Name"], rows[0]["Tier"]) if rows else None

    async def search(self, name: str) -> list[Customer]:
        rows = await self._sf.search("Customer", _CUSTOMER, "Name", name)
        return [Customer(r["Id"], r["Name"], r["Tier"]) for r in rows]


class SalesforceTelemetryRepository:
    def __init__(self, sf: SalesforcePort) -> None:
        self._sf = sf

    async def latest(self, machine_id: str, limit: int = 10) -> list[TelemetryReading]:
        rows = await self._sf.query("Telemetry", _TELEMETRY, {"MachineId": machine_id}, 1000)
        readings = sorted(
            (TelemetryReading(r["MachineId"], float(r["Temp"]), float(r["Vibration"]), r["Ts"]) for r in rows),
            key=lambda t: t.timestamp,
            reverse=True,
        )
        return readings[:limit]
