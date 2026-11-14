from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

import anyio

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Illegal SOQL identifier: {name!r}")
    return name


def _lit(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _like(value: str) -> str:
    return _lit(value).replace("%", "\\%").replace("_", "\\_")


class SalesforceSdkClient:
    """Real adapter. `sf` is a simple_salesforce.Salesforce-compatible object (query_all).

    Replace/extend with your own SDK; only the SalesforcePort contract matters.
    SOQL is built from validated identifiers and escaped literals (no string concatenation of user input).
    """

    def __init__(self, sf: Any) -> None:
        self._sf = sf

    async def _run(self, soql: str) -> list[dict[str, Any]]:
        result = await anyio.to_thread.run_sync(self._sf.query_all, soql)
        return [{k: v for k, v in r.items() if k != "attributes"} for r in result["records"]]

    async def query(
        self,
        sobject: str,
        fields: Sequence[str],
        where: Mapping[str, str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        soql = f"SELECT {', '.join(_ident(f) for f in fields)} FROM {_ident(sobject)}"
        if where:
            soql += " WHERE " + " AND ".join(f"{_ident(k)} = {_lit(str(v))}" for k, v in where.items())
        return await self._run(f"{soql} LIMIT {int(limit)}")

    async def search(
        self, sobject: str, fields: Sequence[str], field: str, text: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        soql = (
            f"SELECT {', '.join(_ident(f) for f in fields)} FROM {_ident(sobject)} "
            f"WHERE {_ident(field)} LIKE {_like('%' + text + '%')} LIMIT {int(limit)}"
        )
        return await self._run(soql)
