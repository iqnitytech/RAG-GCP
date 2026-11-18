"""Vector store adapters: in-memory (dev/test) and Qdrant (REST)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol, Sequence

import httpx
from sdk.errors import ServiceUnavailableError

logger = logging.getLogger("app.ingestion.vector_store")


class VectorStoreError(ServiceUnavailableError):
    public_message = "Vector store is temporarily unavailable"


@dataclass(frozen=True)
class VectorPoint:
    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    def ensure_collection(self, dimensions: int) -> None: ...

    def upsert(self, points: Sequence[VectorPoint]) -> None: ...

    def delete_document(self, doc_id: str) -> None: ...

    def set_links(self, document_id: str, linked_document_ids: Sequence[str]) -> None: ...

    def count(self) -> int: ...

    def close(self) -> None: ...


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._points: dict[str, VectorPoint] = {}
        self.dimensions: int | None = None

    def ensure_collection(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def upsert(self, points: Sequence[VectorPoint]) -> None:
        with self._lock:
            for p in points:
                self._points[p.id] = p

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            for pid in [k for k, v in self._points.items() if v.payload.get("doc_id") == doc_id]:
                del self._points[pid]

    def count(self) -> int:
        with self._lock:
            return len(self._points)

    def set_links(self, document_id: str, linked_document_ids: Sequence[str]) -> None:
        with self._lock:
            for p in self._points.values():
                if p.payload.get("document_id") == document_id:
                    p.payload["linked_document_ids"] = list(linked_document_ids)

    def search(self, vector: Sequence[float], limit: int = 5) -> list[tuple[float, VectorPoint]]:
        """Cosine search (used by tests and by Phase 2 in dev mode)."""
        def cos(a: Sequence[float], b: Sequence[float]) -> float:
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return sum(x * y for x, y in zip(a, b)) / (na * nb)

        with self._lock:
            scored = [(cos(vector, p.vector), p) for p in self._points.values()]
        return sorted(scored, key=lambda t: t[0], reverse=True)[:limit]

    def close(self) -> None:
        return None


class QdrantVectorStore:
    """Qdrant REST adapter (``/collections`` API)."""

    def __init__(
        self,
        *,
        url: str,
        collection: str,
        api_key: str = "",
        timeout_seconds: float = 15.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._collection = collection
        headers = {"api-key": api_key} if api_key else {}
        self._client = http_client or httpx.Client(
            base_url=url.rstrip("/"), headers=headers, timeout=timeout_seconds
        )
        self._ensured = False

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            logger.error("Qdrant request %s %s failed: %s", method, path, exc)
            raise VectorStoreError(f"Qdrant request failed: {exc}") from exc
        if resp.status_code == 404:
            return {"_not_found": True}
        if resp.status_code >= 300:
            logger.error("Qdrant HTTP %s on %s %s: %s", resp.status_code, method, path, resp.text[:300])
            raise VectorStoreError(f"Qdrant returned HTTP {resp.status_code}")
        return resp.json() if resp.content else {}

    def ensure_collection(self, dimensions: int) -> None:
        if self._ensured:
            return
        info = self._call("GET", f"/collections/{self._collection}")
        if info.get("_not_found"):
            self._call(
                "PUT",
                f"/collections/{self._collection}",
                json={"vectors": {"size": dimensions, "distance": "Cosine"}},
            )
            self._call(
                "PUT",
                f"/collections/{self._collection}/index",
                json={"field_name": "doc_id", "field_schema": "keyword"},
            )
            self._call(
                "PUT",
                f"/collections/{self._collection}/index",
                json={"field_name": "document_id", "field_schema": "keyword"},
            )
            self._call(
                "PUT",
                f"/collections/{self._collection}/index",
                json={"field_name": "application_id", "field_schema": "keyword"},
            )
        self._ensured = True

    def upsert(self, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        self._call(
            "PUT",
            f"/collections/{self._collection}/points",
            params={"wait": "true"},
            json={"points": [{"id": p.id, "vector": p.vector, "payload": p.payload} for p in points]},
        )

    def delete_document(self, doc_id: str) -> None:
        self._call(
            "POST",
            f"/collections/{self._collection}/points/delete",
            params={"wait": "true"},
            json={"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}},
        )

    def count(self) -> int:
        res = self._call("POST", f"/collections/{self._collection}/points/count", json={"exact": True})
        return int(res.get("result", {}).get("count", 0))

    def set_links(self, document_id: str, linked_document_ids: Sequence[str]) -> None:
        self._call(
            "POST",
            f"/collections/{self._collection}/points/payload",
            params={"wait": "true"},
            json={
                "payload": {"linked_document_ids": list(linked_document_ids)},
                "filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]},
            },
        )

    def close(self) -> None:
        self._client.close()
