"""Generic shared folder / path scanner across filesystem, GCS, and all EDH strategies.

Allows background ingestion jobs to scan and ingest files from *any* shared folder:
- ``filesystem``: Local or mounted shared network folders under ``settings.shared_folders_root``
- ``gcs``: Google Cloud Storage bucket folder prefixes
- Any EDH strategy (``data_lake``, ``salesforce``, ``databricks``, ``object_storage_hub``, ``api_hub``, ``memory``)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi.concurrency import run_in_threadpool
from sdk.connectors.edh import DataZone, EdhConfigError, EdhObjectRef

from app.connectors.edh_connector import create_async_edh_connector
from app.core.config import Settings, get_settings
from app.services.ingestion.parsers import supported_extensions
from app.services.salesforce.salesforce_service import SalesforceService
from app.services.storage.storage_service import StorageService

SUPPORTED_EXT_SET = frozenset(supported_extensions())


@dataclass(frozen=True)
class DiscoveredFile:
    """Metadata for a file discovered inside a shared folder/path."""

    provider: str
    shared_path: str
    file_key: str
    source_uri: str
    zone: str = "raw"


class FolderScanner(Protocol):
    async def list_files(
        self,
        shared_paths: list[str],
        *,
        zone: str = "raw",
        limit: int = 200,
    ) -> list[DiscoveredFile]: ...

    async def read_file(self, item: DiscoveredFile) -> bytes: ...


class FilesystemFolderScanner:
    """Scans local or mounted network/SMB shared folders under a configured root directory."""

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir).resolve()

    def _resolve_safe(self, rel_path: str) -> Path:
        cleaned = rel_path.strip().lstrip("/\\")
        target = (self._root / cleaned).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise EdhConfigError(
                f"Shared folder path escapes configured root: {rel_path!r}",
                public_message="Invalid shared folder path",
            ) from exc
        return target

    def _scan_sync(self, shared_paths: list[str], limit: int) -> list[DiscoveredFile]:
        discovered: list[DiscoveredFile] = []
        seen: set[str] = set()
        for raw_prefix in shared_paths:
            target = self._resolve_safe(raw_prefix)
            if not target.exists():
                continue
            candidates = [target] if target.is_file() else sorted(target.rglob("*"))
            for file_path in candidates:
                if not file_path.is_file() or file_path.name.startswith("."):
                    continue
                ext = file_path.suffix.lower()
                if ext and ext not in SUPPORTED_EXT_SET:
                    continue
                rel_key = file_path.relative_to(self._root).as_posix()
                if rel_key in seen:
                    continue
                seen.add(rel_key)
                discovered.append(
                    DiscoveredFile(
                        provider="filesystem",
                        shared_path=raw_prefix.strip().strip("/\\"),
                        file_key=rel_key,
                        source_uri=f"shared_path://filesystem/{rel_key}",
                    )
                )
                if len(discovered) >= limit:
                    return discovered
        return discovered

    async def list_files(
        self,
        shared_paths: list[str],
        *,
        zone: str = "raw",
        limit: int = 200,
    ) -> list[DiscoveredFile]:
        return await run_in_threadpool(self._scan_sync, shared_paths, limit)

    async def read_file(self, item: DiscoveredFile) -> bytes:
        target = self._resolve_safe(item.file_key)
        return await run_in_threadpool(target.read_bytes)


class GcsFolderScanner:
    """Scans folder prefixes inside the configured GCS document bucket."""

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

    async def list_files(
        self,
        shared_paths: list[str],
        *,
        zone: str = "raw",
        limit: int = 200,
    ) -> list[DiscoveredFile]:
        discovered: list[DiscoveredFile] = []
        seen: set[str] = set()
        for raw_prefix in shared_paths:
            prefix = raw_prefix.strip().strip("/")
            names = await run_in_threadpool(
                self._storage.list_objects,
                prefix,
                limit - len(discovered),
            )
            for name in names:
                if name.endswith("/") or name in seen:
                    continue
                seen.add(name)
                discovered.append(
                    DiscoveredFile(
                        provider="gcs",
                        shared_path=prefix,
                        file_key=name,
                        source_uri=f"gcs://{name}",
                    )
                )
                if len(discovered) >= limit:
                    return discovered
        return discovered

    async def read_file(self, item: DiscoveredFile) -> bytes:
        return await run_in_threadpool(self._storage.download_bytes, item.file_key)


class SalesforceFolderScanner:
    """Scans shared Salesforce paths via SalesforceService."""

    def __init__(self, salesforce: SalesforceService) -> None:
        self._sf = salesforce

    async def list_files(
        self,
        shared_paths: list[str],
        *,
        zone: str = "raw",
        limit: int = 200,
    ) -> list[DiscoveredFile]:
        data_zone = DataZone(zone)
        discovered: list[DiscoveredFile] = []
        seen: set[str] = set()
        for raw_prefix in shared_paths:
            prefix = raw_prefix.strip().strip("/")
            items = await self._sf.list_shared_files(
                prefix,
                zone=data_zone,
                max_files=limit - len(discovered),
            )
            for meta in items:
                key = meta.ref.key
                if key in seen:
                    continue
                seen.add(key)
                discovered.append(
                    DiscoveredFile(
                        provider="salesforce",
                        shared_path=prefix,
                        file_key=key,
                        source_uri=f"shared_path://salesforce/{data_zone.value}/{key}",
                        zone=data_zone.value,
                    )
                )
                if len(discovered) >= limit:
                    return discovered
        return discovered

    async def read_file(self, item: DiscoveredFile) -> bytes:
        return await self._sf.download_shared_file(
            item.file_key,
            zone=DataZone(item.zone),
        )


class EdhStrategyFolderScanner:
    """Scans shared folders across any SDK EDH strategy (data_lake, databricks, object_storage_hub, api_hub, memory)."""

    def __init__(self, strategy: str, settings: Settings | None = None) -> None:
        self._strategy = strategy.strip().lower()
        self._settings = settings or get_settings()

    async def list_files(
        self,
        shared_paths: list[str],
        *,
        zone: str = "raw",
        limit: int = 200,
    ) -> list[DiscoveredFile]:
        data_zone = DataZone(zone)
        connector = create_async_edh_connector(
            self._settings,
            strategy=self._strategy,
            root_path=self._settings.shared_folders_root,
        )
        discovered: list[DiscoveredFile] = []
        seen: set[str] = set()
        async with connector as edh:
            for raw_prefix in shared_paths:
                prefix = raw_prefix.strip().strip("/")
                token: str | None = None
                while len(discovered) < limit:
                    page_size = min(100, limit - len(discovered))
                    page = await edh.list_objects(
                        zone=data_zone,
                        prefix=prefix,
                        page_size=page_size,
                        page_token=token,
                    )
                    for meta in page.items:
                        key = meta.ref.key
                        if key in seen:
                            continue
                        seen.add(key)
                        discovered.append(
                            DiscoveredFile(
                                provider=self._strategy,
                                shared_path=prefix,
                                file_key=key,
                                source_uri=f"shared_path://{self._strategy}/{data_zone.value}/{key}",
                                zone=data_zone.value,
                            )
                        )
                        if len(discovered) >= limit:
                            break
                    if not page.next_page_token:
                        break
                    token = page.next_page_token
        return discovered

    async def read_file(self, item: DiscoveredFile) -> bytes:
        data_zone = DataZone(item.zone)
        connector = create_async_edh_connector(
            self._settings,
            strategy=self._strategy,
            root_path=self._settings.shared_folders_root,
        )
        async with connector as edh:
            ref = EdhObjectRef(
                namespace=edh.config.namespace,
                key=item.file_key,
                zone=data_zone,
            )
            return await edh.get_bytes(ref)


def resolve_folder_scanner(
    provider: str,
    *,
    storage: StorageService,
    salesforce: SalesforceService,
    settings: Settings | None = None,
) -> FolderScanner:
    """Resolve a :class:`FolderScanner` for any folder provider."""
    resolved = settings or get_settings()
    norm = provider.strip().lower()
    if norm in {"filesystem", "local", "folder", "shared_folder"}:
        return FilesystemFolderScanner(resolved.shared_folders_root)
    if norm == "gcs":
        return GcsFolderScanner(storage)
    if norm in {"salesforce", "sfdc", "salesforce_path"}:
        return SalesforceFolderScanner(salesforce)
    return EdhStrategyFolderScanner(norm, settings=resolved)
