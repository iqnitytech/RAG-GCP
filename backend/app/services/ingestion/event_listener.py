"""Event Listener & Folder Watcher for automatic background ingestion.

Supports two enterprise event-driven ingestion patterns:
1. **Push Event / Webhook Listener** (:meth:`IngestionEventListener.handle_event`):
   Receives real-time file/record events (from ``/documents/upload``, GCS Pub/Sub / Eventarc,
   Salesforce Platform Events, S3/Data Lake notifications, or custom webhooks) and
   automatically queues background ingestion jobs scoped to ``application_id``.
2. **Shared Folder Watch Subscriptions** (:meth:`IngestionEventListener.add_subscription` /
   :meth:`IngestionEventListener.poll_subscriptions`):
   Watches registered ``(application_id, provider, shared_path)`` folders, detects newly added
   or modified files (via file fingerprint), and triggers background ingestion jobs only for
   new/changed files while tracking ``events_received`` and ``jobs_triggered``.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Sequence

from sdk.errors import NotFoundError

from app.services.ingestion.registry import DEFAULT_APPLICATION_ID, validate_application_id

logger = logging.getLogger("app.ingestion.event_listener")


@dataclass(frozen=True)
class IngestionEvent:
    """Normalized ingestion event emitted by uploads, webhooks, or folder watchers."""

    event_id: str
    event_type: str  # "file.created" | "file.updated" | "record.updated" | "folder.detected"
    application_id: str
    provider: str  # "filesystem" | "gcs" | "salesforce" | "databricks" | "data_lake" | "object_storage_hub" | "api_hub"
    paths: tuple[str, ...]
    zone: str = "raw"
    received_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FolderWatchSubscription:
    """Registered folder-watcher subscription for an ``application_id``."""

    subscription_id: str
    application_id: str
    provider: str
    shared_path: str
    zone: str = "raw"
    enabled: bool = True
    events_triggered: int = 0
    files_detected_total: int = 0
    last_polled_at: str | None = None
    last_job_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


SubscriberCallback = Callable[[IngestionEvent], None]


class IngestionEventListener:
    """In-process Event Bus + Shared Folder Watcher for automatic background ingestion."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._subscriptions: dict[str, FolderWatchSubscription] = {}
        self._seen_fingerprints: dict[str, set[str]] = {}  # subscription_id -> set of file fingerprints
        self._events: list[tuple[IngestionEvent, str]] = []  # (event, triggered_job_id)
        self._subscribers: list[SubscriberCallback] = []

    def subscribe(self, callback: SubscriberCallback) -> None:
        """Register an in-process listener callback invoked on every :class:`IngestionEvent`."""
        with self._lock:
            self._subscribers.append(callback)

    def add_subscription(
        self,
        *,
        application_id: str = DEFAULT_APPLICATION_ID,
        provider: str = "filesystem",
        shared_path: str,
        zone: str = "raw",
    ) -> FolderWatchSubscription:
        clean_app = validate_application_id(application_id)
        clean_path = shared_path.strip().strip("/\\")
        with self._lock:
            for existing in self._subscriptions.values():
                if (
                    existing.application_id == clean_app
                    and existing.provider == provider
                    and existing.shared_path == clean_path
                    and existing.zone == zone
                ):
                    existing.enabled = True
                    return existing
            sub = FolderWatchSubscription(
                subscription_id=uuid.uuid4().hex[:16],
                application_id=clean_app,
                provider=provider,
                shared_path=clean_path,
                zone=zone,
            )
            self._subscriptions[sub.subscription_id] = sub
            self._seen_fingerprints[sub.subscription_id] = set()
            logger.info(
                "Registered folder watch subscription -- id=%s app=%s provider=%s path=%s",
                sub.subscription_id,
                clean_app,
                provider,
                clean_path,
            )
            return sub

    def remove_subscription(self, subscription_id: str) -> FolderWatchSubscription:
        with self._lock:
            sub = self._subscriptions.pop(subscription_id, None)
            self._seen_fingerprints.pop(subscription_id, None)
            if sub is None:
                raise NotFoundError(
                    f"Subscription {subscription_id} not found",
                    public_message="Event subscription not found",
                )
            return sub

    def list_subscriptions(
        self, application_id: str | None = None
    ) -> list[FolderWatchSubscription]:
        clean_app = validate_application_id(application_id) if application_id else None
        with self._lock:
            subs = [
                s
                for s in self._subscriptions.values()
                if clean_app is None or s.application_id == clean_app
            ]
            return sorted(subs, key=lambda s: s.created_at, reverse=True)

    def record_event(self, event: IngestionEvent, job_id: str) -> None:
        with self._lock:
            self._events.append((event, job_id))
            if len(self._events) > 200:
                self._events = self._events[-200:]
            callbacks = list(self._subscribers)
        for cb in callbacks:
            try:
                cb(event)
            except Exception:
                logger.exception("Event subscriber callback failed for event %s", event.event_id)

    def list_recent_events(
        self, application_id: str | None = None
    ) -> list[tuple[IngestionEvent, str]]:
        clean_app = validate_application_id(application_id) if application_id else None
        with self._lock:
            items = [
                (ev, jid)
                for ev, jid in reversed(self._events)
                if clean_app is None or ev.application_id == clean_app
            ]
            return items[:50]

    def detect_new_files(
        self,
        subscription_id: str,
        files_with_fingerprints: Sequence[tuple[str, str]],
    ) -> list[str]:
        """Return only file keys whose ``(file_key, content_hash_or_size)`` has not been seen yet."""
        with self._lock:
            sub = self._subscriptions.get(subscription_id)
            if sub is None:
                return []
            seen = self._seen_fingerprints.setdefault(subscription_id, set())
            new_keys: list[str] = []
            for file_key, fp in files_with_fingerprints:
                token = hashlib.sha256(f"{file_key}::{fp}".encode("utf-8")).hexdigest()[:24]
                if token not in seen:
                    seen.add(token)
                    new_keys.append(file_key)
            sub.last_polled_at = datetime.now(timezone.utc).isoformat()
            if new_keys:
                sub.events_triggered += 1
                sub.files_detected_total += len(new_keys)
            return new_keys
