from __future__ import annotations

from dataclasses import asdict
from typing import Any

from domain.entities import Customer
from domain.ports import CustomerRepository
from mcp_servers.customer.ports import AccountRepositoryPort, CustomerRepositoryPort

_DEFAULT_MOCK_CUSTOMERS: list[dict[str, Any]] = [
    {
        "id": "C-1",
        "name": "Acme Corp",
        "type": "Contact",
        "tier": "Gold",
        "phone": "+1-555-0101",
        "number": "C-1",
        "billing_status": "ACTIVE",
        "region": "NA-EAST",
    },
    {
        "id": "C-2",
        "name": "Globex Industries",
        "type": "Contact",
        "tier": "Silver",
        "phone": "+1-555-0102",
        "number": "C-2",
        "billing_status": "ACTIVE",
        "region": "NA-WEST",
    },
    {
        "id": "2323",
        "name": "Enterprise Client 2323",
        "type": "Contact",
        "tier": "Gold",
        "phone": "+1-555-2323",
        "number": "2323",
        "billing_status": "ACTIVE",
        "region": "NA-EAST",
    },
]

_DEFAULT_MOCK_ACCOUNTS: list[dict[str, Any]] = [
    {
        "id": "ACC-1",
        "name": "Acme Corp",
        "type": "Account",
        "tier": "Gold",
        "phone": "+1-555-0101",
        "number": "ACC-1",
        "billing_status": "ACTIVE",
        "region": "NA-EAST",
        "industry": "Manufacturing",
    },
    {
        "id": "CUST-001001",
        "name": "Nexus Industrial Corp",
        "type": "Account",
        "tier": "ENTERPRISE",
        "phone": "+1-617-555-0101",
        "number": "CUST-001001",
        "billing_status": "ACTIVE",
        "region": "NA-EAST",
        "industry": "Manufacturing",
    },
    {
        "id": "CUST-001002",
        "name": "Apex Manufacturing Ltd",
        "type": "Account",
        "tier": "ENTERPRISE",
        "phone": "+49-40-5550103",
        "number": "CUST-001002",
        "billing_status": "ACTIVE",
        "region": "EU-WEST",
        "industry": "Heavy Machinery",
    },
]


class MockCustomerAdapter(CustomerRepositoryPort):
    """Mock customer data provider used when DATA_PROVIDER_MODE=MOCK."""

    def __init__(
        self,
        repo: CustomerRepository | None = None,
        custom_data: list[dict[str, Any]] | None = None,
    ) -> None:
        self._repo = repo
        # Combine default customers and accounts for flexible customer lookup
        self._records = (
            custom_data
            if custom_data is not None
            else list(_DEFAULT_MOCK_CUSTOMERS) + list(_DEFAULT_MOCK_ACCOUNTS)
        )

    async def get_by_id(self, customer_id: str) -> dict[str, Any] | None:
        cid_clean = customer_id.strip()
        if not cid_clean:
            return None

        # 1. If wrapped domain repository is present, query it first
        if self._repo is not None:
            c = await self._repo.get(cid_clean)
            if c is not None:
                d = asdict(c) if isinstance(c, Customer) else dict(c)
                match = next((r for r in self._records if r["id"].lower() == cid_clean.lower()), None)
                if match:
                    d.setdefault("phone", match.get("phone", ""))
                    d.setdefault("number", match.get("number", match["id"]))
                    d.setdefault("type", match.get("type", "Contact"))
                else:
                    d.setdefault("phone", getattr(c, "phone", "") or "")
                    d.setdefault("number", getattr(c, "number", "") or d.get("id", ""))
                    d.setdefault("type", "Contact")
                return d

        # 2. Local mock records
        for r in self._records:
            if r["id"].lower() == cid_clean.lower():
                return dict(r)
        return None

    async def search_by_name(self, name: str) -> list[dict[str, Any]]:
        name_clean = name.strip().lower()

        # 1. If wrapped domain repository is present, search it
        if self._repo is not None:
            customers = await self._repo.search(name)
            results = []
            for c in customers:
                d = asdict(c) if isinstance(c, Customer) else dict(c)
                match = next((r for r in self._records if r["id"].lower() == d["id"].lower()), None)
                if match:
                    d.setdefault("phone", match.get("phone", ""))
                    d.setdefault("number", match.get("number", match["id"]))
                    d.setdefault("type", match.get("type", "Contact"))
                else:
                    d.setdefault("phone", getattr(c, "phone", "") or "")
                    d.setdefault("number", getattr(c, "number", "") or d.get("id", ""))
                    d.setdefault("type", "Contact")
                results.append(d)
            return results

        # 2. Local mock records
        return [
            dict(r)
            for r in self._records
            if not name_clean or name_clean in r["name"].lower()
        ]

    async def get_by_number(self, number: str) -> dict[str, Any] | None:
        num_clean = number.strip().lower()
        if not num_clean:
            return None

        # 1. If wrapped repo has get_by_number, query it
        if self._repo is not None and hasattr(self._repo, "get_by_number"):
            c = await self._repo.get_by_number(number.strip())
            if c is not None:
                d = asdict(c) if isinstance(c, Customer) else dict(c)
                d.setdefault("phone", getattr(c, "phone", "") or "")
                d.setdefault("number", getattr(c, "number", "") or d.get("id", ""))
                d.setdefault("type", "Contact")
                return d

        # 2. Local mock records
        for r in self._records:
            if (
                r.get("number", "").lower() == num_clean
                or r["id"].lower() == num_clean
            ):
                return dict(r)
        return None

    async def get_by_phone(self, phone: str) -> dict[str, Any] | None:
        phone_clean = phone.strip()
        if not phone_clean:
            return None

        # 1. If wrapped repo has get_by_phone, query it
        if self._repo is not None and hasattr(self._repo, "get_by_phone"):
            c = await self._repo.get_by_phone(phone_clean)
            if c is not None:
                d = asdict(c) if isinstance(c, Customer) else dict(c)
                d.setdefault("phone", getattr(c, "phone", "") or "")
                d.setdefault("number", getattr(c, "number", "") or d.get("id", ""))
                d.setdefault("type", "Contact")
                return d

        # 2. Local mock records (exact or normalized digits match)
        clean_digits = "".join(ch for ch in phone_clean if ch.isdigit())
        for r in self._records:
            p = r.get("phone", "")
            if p == phone_clean or (clean_digits and "".join(ch for ch in p if ch.isdigit()) == clean_digits):
                return dict(r)
        return None


class MockAccountAdapter(AccountRepositoryPort):
    """Mock account data provider used when DATA_PROVIDER_MODE=MOCK."""

    def __init__(self, custom_data: list[dict[str, Any]] | None = None) -> None:
        self._records = custom_data if custom_data is not None else list(_DEFAULT_MOCK_ACCOUNTS)

    async def get_by_id(self, account_id: str) -> dict[str, Any] | None:
        aid_clean = account_id.strip()
        if not aid_clean:
            return None
        for r in self._records:
            if r["id"].lower() == aid_clean.lower():
                return dict(r)
        return None

    async def search_by_name(self, name: str) -> list[dict[str, Any]]:
        name_clean = name.strip().lower()
        return [
            dict(r)
            for r in self._records
            if not name_clean or name_clean in r["name"].lower()
        ]

    async def get_by_number(self, number: str) -> dict[str, Any] | None:
        num_clean = number.strip().lower()
        if not num_clean:
            return None
        for r in self._records:
            if (
                r.get("number", "").lower() == num_clean
                or r["id"].lower() == num_clean
            ):
                return dict(r)
        return None

    async def get_by_phone(self, phone: str) -> dict[str, Any] | None:
        phone_clean = phone.strip()
        if not phone_clean:
            return None
        clean_digits = "".join(ch for ch in phone_clean if ch.isdigit())
        for r in self._records:
            p = r.get("phone", "")
            if p == phone_clean or (clean_digits and "".join(ch for ch in p if ch.isdigit()) == clean_digits):
                return dict(r)
        return None
