"""Google Cloud Pub/Sub Publisher & Push/Pull Subscriber for event-driven RAG ingestion.

Supports:
1. **Publishing Ingestion Events** (:class:`GcpPubSubPublisher` / :class:`InMemoryPubSubBroker`)
   to ``projects/{project_id}/topics/{topic}:publish`` whenever a document is uploaded or
   a folder event is emitted.
2. **Native GCP Pub/Sub Push Subscription Receiver** (:func:`decode_pubsub_push_envelope`):
   Decodes both custom RAG ingestion event envelopes and **native GCS Bucket Notification**
   (``OBJECT_FINALIZE``) Pub/Sub messages pushed by Google Cloud Pub/Sub / Eventarc.
3. **GCP Pub/Sub Pull Subscriber** (:meth:`GcpPubSubSubscriber.pull_and_ack`):
   Pulls pending messages from ``projects/{project_id}/subscriptions/{subscription}:pull``
   and acknowledges ``ackIds`` once background ingestion jobs are queued.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Protocol, Sequence

import httpx
from sdk.errors import BadRequestError, ServiceUnavailableError

from app.services.ingestion.event_listener import IngestionEvent
from app.services.ingestion.registry import DEFAULT_APPLICATION_ID, validate_application_id

logger = logging.getLogger("app.ingestion.pubsub")


class PubSubError(ServiceUnavailableError):
    public_message = "Pub/Sub messaging service is temporarily unavailable"


@dataclass(frozen=True)
class PubSubReceivedMessage:
    """Decoded message from a GCP Pub/Sub Push or Pull subscription."""

    message_id: str
    event: IngestionEvent
    subscription: str = ""
    ack_id: str = ""
    raw_attributes: dict[str, str] = field(default_factory=dict)


class PubSubBroker(Protocol):
    def publish(self, event: IngestionEvent) -> str: ...

    def pull(self, max_messages: int = 10) -> list[PubSubReceivedMessage]: ...

    def ack(self, ack_ids: Sequence[str]) -> None: ...


def decode_pubsub_message(
    message: dict[str, Any],
    *,
    subscription: str = "",
    ack_id: str = "",
) -> PubSubReceivedMessage:
    """Decode a single GCP Pub/Sub ``message`` dict into a normalized :class:`IngestionEvent`.

    Handles two message formats automatically:
    1. **RAG IngestionEvent JSON** (``{"event_type": ..., "application_id": ..., "provider": ..., "paths": [...]}``)
    2. **Native GCS Bucket Notification (`OBJECT_FINALIZE`)** where ``attributes.eventType == "OBJECT_FINALIZE"``
       or the JSON payload contains ``{"bucket": "...", "name": "documents/CPQ/file.pdf"}``.
    """
    msg_id = str(message.get("messageId") or message.get("message_id") or uuid.uuid4().hex[:16])
    attrs: dict[str, str] = {
        str(k): str(v) for k, v in (message.get("attributes") or {}).items()
    }
    raw_data = message.get("data", "")
    payload: dict[str, Any] = {}
    if raw_data:
        try:
            decoded_bytes = base64.b64decode(raw_data)
            payload = json.loads(decoded_bytes.decode("utf-8"))
        except Exception as exc:
            raise BadRequestError(
                f"Invalid base64/JSON in Pub/Sub message.data: {exc}",
                public_message="Invalid Pub/Sub message payload",
            ) from exc

    # Case A: Native GCS Bucket Notification (OBJECT_FINALIZE)
    gcs_event_type = attrs.get("eventType") or payload.get("eventType")
    if gcs_event_type and gcs_event_type != "OBJECT_FINALIZE":
        raise BadRequestError(
            f"Unsupported GCS Pub/Sub eventType {gcs_event_type!r}",
            public_message=f"Ignored non-finalize GCS event: {gcs_event_type}",
        )

    object_name = str(payload.get("name") or attrs.get("objectId") or "").strip()
    if object_name and ("bucket" in payload or "bucketId" in attrs or gcs_event_type == "OBJECT_FINALIZE"):
        # Infer application_id from attributes, payload metadata, or path prefix (e.g. documents/CPQ/...)
        meta = payload.get("metadata") or {}
        app_id = (
            attrs.get("application_id")
            or meta.get("application_id")
            or _infer_app_id_from_path(object_name)
        )
        clean_app = validate_application_id(app_id)
        event = IngestionEvent(
            event_id=msg_id,
            event_type="file.created",
            application_id=clean_app,
            provider="gcs",
            paths=(object_name,),
            zone="raw",
            metadata={"bucket": str(payload.get("bucket") or attrs.get("bucketId") or "")},
        )
        return PubSubReceivedMessage(
            message_id=msg_id,
            event=event,
            subscription=subscription,
            ack_id=ack_id,
            raw_attributes=attrs,
        )

    # Case B: Custom RAG Ingestion Event payload
    paths_raw = payload.get("paths") or (
        [payload["path"]] if payload.get("path") else []
    )
    if not paths_raw and attrs.get("path"):
        paths_raw = [attrs["path"]]
    if not paths_raw:
        raise BadRequestError(
            "Pub/Sub message missing 'paths' or GCS object 'name'",
            public_message="Pub/Sub message must contain file 'paths' or GCS object 'name'",
        )

    app_id = str(
        payload.get("application_id")
        or attrs.get("application_id")
        or DEFAULT_APPLICATION_ID
    )
    clean_app = validate_application_id(app_id)
    provider = str(payload.get("provider") or attrs.get("provider") or "gcs")
    event_type = str(payload.get("event_type") or attrs.get("event_type") or "file.created")
    zone = str(payload.get("zone") or attrs.get("zone") or "raw")

    event = IngestionEvent(
        event_id=str(payload.get("event_id") or msg_id),
        event_type=event_type,
        application_id=clean_app,
        provider=provider,
        paths=tuple(str(p) for p in paths_raw),
        zone=zone,
        metadata=dict(payload.get("metadata") or {}),
    )
    return PubSubReceivedMessage(
        message_id=msg_id,
        event=event,
        subscription=subscription,
        ack_id=ack_id,
        raw_attributes=attrs,
    )


def _infer_app_id_from_path(object_name: str) -> str:
    """Extract ``application_id`` from ``documents/<application_id>/<file>`` if present."""
    parts = [p for p in object_name.strip("/").split("/") if p]
    if len(parts) >= 3 and parts[0].lower() in {"documents", "shared", "apps"}:
        return parts[1]
    return DEFAULT_APPLICATION_ID


class InMemoryPubSubBroker:
    """In-memory Pub/Sub topic & subscription broker for local dev and unit tests."""

    def __init__(self, topic: str = "rag-ingestion-events", subscription: str = "rag-ingestion-sub") -> None:
        self.topic = topic
        self.subscription = subscription
        self._lock = Lock()
        self._queue: list[tuple[str, dict[str, Any]]] = []  # (ack_id, message_dict)
        self.published_messages: list[dict[str, Any]] = []
        self.acked_ids: list[str] = []

    def publish(self, event: IngestionEvent) -> str:
        msg_id = f"pubsub-{uuid.uuid4().hex[:12]}"
        payload = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "application_id": event.application_id,
            "provider": event.provider,
            "paths": list(event.paths),
            "zone": event.zone,
            "received_at": event.received_at,
            "metadata": event.metadata,
        }
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        msg_dict = {
            "messageId": msg_id,
            "data": encoded,
            "attributes": {
                "application_id": event.application_id,
                "event_type": event.event_type,
                "provider": event.provider,
            },
            "publishTime": datetime.now(timezone.utc).isoformat(),
        }
        ack_id = f"ack-{msg_id}"
        with self._lock:
            self.published_messages.append(msg_dict)
            self._queue.append((ack_id, msg_dict))
        logger.info(
            "Published event to in-memory Pub/Sub topic=%s message_id=%s app=%s",
            self.topic,
            msg_id,
            event.application_id,
        )
        return msg_id

    def pull(self, max_messages: int = 10) -> list[PubSubReceivedMessage]:
        with self._lock:
            batch = self._queue[:max_messages]
        return [
            decode_pubsub_message(msg, subscription=self.subscription, ack_id=ack_id)
            for ack_id, msg in batch
        ]

    def ack(self, ack_ids: Sequence[str]) -> None:
        ack_set = set(ack_ids)
        with self._lock:
            self._queue = [(aid, msg) for aid, msg in self._queue if aid not in ack_set]
            self.acked_ids.extend(ack_ids)


class GcpPubSubBroker:
    """Google Cloud Pub/Sub REST v1 Publisher & Pull Subscriber using WIF/ADC credentials."""

    def __init__(
        self,
        *,
        project_id: str,
        topic: str,
        subscription: str = "",
        credentials_getter: Callable[[], Any] | None = None,
        timeout_seconds: float = 15.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._project_id = project_id
        self._topic = topic
        self._subscription = subscription
        self._credentials_getter = credentials_getter
        self._timeout = timeout_seconds
        self._client = http_client

    def _token(self) -> str:
        if self._credentials_getter is None:
            raise PubSubError("No GCP credentials configured for Cloud Pub/Sub")
        from google.auth.transport.requests import Request  # lazy import

        creds = self._credentials_getter()
        if not creds.valid:
            creds.refresh(Request())
        return creds.token

    def _request(self, method: str, url: str, json_body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            client = self._client or httpx.Client(timeout=self._timeout)
            try:
                resp = client.request(method, url, json=json_body, headers=headers)
            finally:
                if self._client is None:
                    client.close()
        except httpx.HTTPError as exc:
            logger.error("GCP Pub/Sub request failed: %s", exc)
            raise PubSubError(f"GCP Pub/Sub request failed: {exc}") from exc
        if resp.status_code >= 300:
            logger.error("GCP Pub/Sub HTTP %s: %s", resp.status_code, resp.text[:300])
            raise PubSubError(f"GCP Pub/Sub returned HTTP {resp.status_code}")
        return resp.json() if resp.content else {}

    def publish(self, event: IngestionEvent) -> str:
        url = (
            f"https://pubsub.googleapis.com/v1/projects/{self._project_id}"
            f"/topics/{self._topic}:publish"
        )
        payload = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "application_id": event.application_id,
            "provider": event.provider,
            "paths": list(event.paths),
            "zone": event.zone,
            "received_at": event.received_at,
            "metadata": event.metadata,
        }
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        doc = self._request(
            "POST",
            url,
            {
                "messages": [
                    {
                        "data": encoded,
                        "attributes": {
                            "application_id": event.application_id,
                            "event_type": event.event_type,
                            "provider": event.provider,
                        },
                    }
                ]
            },
        )
        ids = doc.get("messageIds") or []
        msg_id = str(ids[0]) if ids else uuid.uuid4().hex[:16]
        logger.info(
            "Published event to GCP Pub/Sub topic=%s message_id=%s app=%s",
            self._topic,
            msg_id,
            event.application_id,
        )
        return msg_id

    def pull(self, max_messages: int = 10) -> list[PubSubReceivedMessage]:
        if not self._subscription:
            raise PubSubError("PUBSUB_INGESTION_SUBSCRIPTION is not configured")
        url = (
            f"https://pubsub.googleapis.com/v1/projects/{self._project_id}"
            f"/subscriptions/{self._subscription}:pull"
        )
        doc = self._request("POST", url, {"maxMessages": max_messages})
        out: list[PubSubReceivedMessage] = []
        for item in doc.get("receivedMessages") or []:
            ack_id = str(item.get("ackId") or "")
            msg = item.get("message") or {}
            out.append(
                decode_pubsub_message(
                    msg,
                    subscription=f"projects/{self._project_id}/subscriptions/{self._subscription}",
                    ack_id=ack_id,
                )
            )
        return out

    def ack(self, ack_ids: Sequence[str]) -> None:
        if not ack_ids or not self._subscription:
            return
        url = (
            f"https://pubsub.googleapis.com/v1/projects/{self._project_id}"
            f"/subscriptions/{self._subscription}:acknowledge"
        )
        self._request("POST", url, {"ackIds": list(ack_ids)})
