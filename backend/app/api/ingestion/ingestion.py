"""Ingestion API: create and inspect indexing jobs, shared folders, applications, and admin document links."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sdk.errors import NotFoundError

from app.core.security import rate_limit, require_api_key
from app.dependencies import (
    get_ingestion_service_dependency,
    get_salesforce_service_dependency,
    get_storage_service_dependency,
)
from app.models.request.ingestion import (
    ApplicationCreateRequest,
    ApplicationInfoResponse,
    CreateDocumentLinkRequest,
    DocumentLinkResponse,
    DocumentLinksOverviewResponse,
    FolderWatchSubscriptionRequest,
    FolderWatchSubscriptionResponse,
    IndexedDocumentResponse,
    IngestionEventResponse,
    IngestionJobRequest,
    IngestionJobResponse,
    IngestionWebhookEventRequest,
    PubSubPublishResponse,
    PubSubPushEnvelopeRequest,
    SalesforceSharedPathIngestionRequest,
    SharedPathIngestionRequest,
)
from app.services.ingestion.event_listener import IngestionEvent
from app.services.ingestion.folder_scanner import resolve_folder_scanner
from app.services.ingestion.pubsub import decode_pubsub_message
from app.services.ingestion.service import IngestionJob, IngestionService, JobStatus
from app.services.salesforce.salesforce_service import SalesforceService
from app.services.storage.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ingestion",
    tags=["Ingestion"],
    dependencies=[Depends(require_api_key), Depends(rate_limit)],
)


def _to_response(job: IngestionJob) -> IngestionJobResponse:
    data = asdict(job)
    data["status"] = job.status.value
    return IngestionJobResponse(**data)


async def run_ingestion_job(
    job: IngestionJob,
    request: IngestionJobRequest,
    service: IngestionService,
    storage: StorageService,
    salesforce: SalesforceService,
) -> None:
    job.status = JobStatus.RUNNING
    app_id = request.application_id
    try:
        if request.source_type == "gcs":
            job.files_discovered = len(request.object_names)
            for name in request.object_names:
                try:
                    data = await run_in_threadpool(storage.download_bytes, name)
                    result = await run_in_threadpool(
                        service.ingest_bytes,
                        data,
                        filename=name,
                        source=f"gcs://{name}",
                        metadata={"source_type": "gcs", "object_name": name},
                        application_id=app_id,
                    )
                    service.record_result(job, result, file_path=name)
                except Exception as exc:  # per-document isolation
                    service.record_error(job, name, exc)
        elif request.source_type in {"shared_path", "salesforce_path"}:
            scanner = resolve_folder_scanner(
                request.provider,
                storage=storage,
                salesforce=salesforce,
            )
            discovered = await scanner.list_files(
                request.shared_paths,
                zone=request.zone,
                limit=request.limit,
            )
            job.files_discovered = len(discovered)
            for item in discovered:
                try:
                    data = await scanner.read_file(item)
                    result = await run_in_threadpool(
                        service.ingest_bytes,
                        data,
                        filename=item.file_key,
                        source=item.source_uri,
                        metadata={
                            "source_type": request.source_type,
                            "provider": item.provider,
                            "shared_path": item.shared_path,
                            "object_key": item.file_key,
                            "zone": item.zone,
                        },
                        application_id=app_id,
                    )
                    service.record_result(job, result, file_path=item.file_key)
                except Exception as exc:
                    service.record_error(job, item.file_key, exc)
        else:
            rows = await salesforce.query(
                request.sobject or "",
                fields=list({"Id", *(request.fields or []), *(request.text_fields or [])}),
                where=request.where,
                limit=request.limit,
            )
            job.files_discovered = len(rows)
            for row in rows:
                record_id = str(row.get("Id", ""))
                source = f"salesforce://{request.sobject}/{record_id}"
                try:
                    text = "\n".join(
                        f"{f}: {row[f]}" for f in (request.text_fields or []) if row.get(f)
                    )
                    result = await run_in_threadpool(
                        service.ingest_text,
                        text,
                        source=source,
                        metadata={
                            "source_type": "salesforce",
                            "sobject": request.sobject,
                            "record_id": record_id,
                        },
                        application_id=app_id,
                    )
                    service.record_result(job, result, file_path=source)
                except Exception as exc:
                    service.record_error(job, source, exc)
    except Exception as exc:
        service.record_error(job, request.source_type, exc)
    finally:
        service.finish(job)
        logger.info(
            "Ingestion job finished -- job_id=%s app=%s status=%s discovered=%d processed=%d succeeded=%d failed=%d chunks=%d",
            job.job_id,
            app_id,
            job.status.value,
            job.files_discovered,
            job.files_processed,
            job.files_succeeded,
            job.files_failed,
            job.chunks_indexed,
        )


# ------------------------------------------------------------------ Applications (CPQ, CustomerExperience, etc.)
@router.get("/applications", response_model=list[ApplicationInfoResponse])
async def list_applications(
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> list[ApplicationInfoResponse]:
    """List registered applications (e.g. ``CPQ``, ``CustomerExperience``) for the Admin Portal."""
    return [ApplicationInfoResponse(**asdict(a)) for a in service.registry.list_applications()]


@router.post("/applications", status_code=201, response_model=ApplicationInfoResponse)
async def register_application(
    request: ApplicationCreateRequest,
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> ApplicationInfoResponse:
    """Register or update an application ID in the Admin Portal."""
    info = service.registry.register_application(
        request.application_id,
        name=request.name,
        description=request.description,
    )
    return ApplicationInfoResponse(**asdict(info))


# ------------------------------------------------------------------ Background Ingestion Jobs
@router.post("/jobs", status_code=202, response_model=IngestionJobResponse)
async def create_ingestion_job(
    request: IngestionJobRequest,
    background_tasks: BackgroundTasks,
    service: IngestionService = Depends(get_ingestion_service_dependency),
    storage: StorageService = Depends(get_storage_service_dependency),
    salesforce: SalesforceService = Depends(get_salesforce_service_dependency),
) -> IngestionJobResponse:
    """Queue an indexing job scoped to ``request.application_id``."""
    service.registry.register_application(request.application_id)
    job = service.jobs.create(request.source_type, application_id=request.application_id)
    background_tasks.add_task(run_ingestion_job, job, request, service, storage, salesforce)
    return _to_response(job)


@router.post(
    "/shared-paths",
    status_code=202,
    response_model=IngestionJobResponse,
    summary="Ingest files from any shared folder/path in the background",
)
async def create_shared_path_ingestion_job(
    request: SharedPathIngestionRequest,
    background_tasks: BackgroundTasks,
    service: IngestionService = Depends(get_ingestion_service_dependency),
    storage: StorageService = Depends(get_storage_service_dependency),
    salesforce: SalesforceService = Depends(get_salesforce_service_dependency),
) -> IngestionJobResponse:
    """Discover and ingest all files under one or more shared folders across any provider."""
    service.registry.register_application(request.application_id)
    job_request = IngestionJobRequest(
        application_id=request.application_id,
        source_type="shared_path",
        provider=request.provider,
        shared_paths=request.shared_paths,
        zone=request.zone,
        limit=request.limit,
    )
    job = service.jobs.create("shared_path", application_id=request.application_id)
    background_tasks.add_task(run_ingestion_job, job, job_request, service, storage, salesforce)
    return _to_response(job)


@router.post(
    "/salesforce/shared-paths",
    status_code=202,
    response_model=IngestionJobResponse,
    summary="Ingest files from shared Salesforce paths in the background",
)
async def create_salesforce_shared_path_ingestion_job(
    request: SalesforceSharedPathIngestionRequest,
    background_tasks: BackgroundTasks,
    service: IngestionService = Depends(get_ingestion_service_dependency),
    storage: StorageService = Depends(get_storage_service_dependency),
    salesforce: SalesforceService = Depends(get_salesforce_service_dependency),
) -> IngestionJobResponse:
    """Discover and ingest all files under one or more shared Salesforce paths in the background."""
    service.registry.register_application(request.application_id)
    job_request = IngestionJobRequest(
        application_id=request.application_id,
        source_type="salesforce_path",
        provider="salesforce",
        shared_paths=request.shared_paths,
        zone=request.zone,
        limit=request.limit,
    )
    job = service.jobs.create("salesforce_path", application_id=request.application_id)
    background_tasks.add_task(run_ingestion_job, job, job_request, service, storage, salesforce)
    return _to_response(job)


@router.get("/jobs", response_model=list[IngestionJobResponse])
async def list_ingestion_jobs(
    application_id: str | None = Query(
        default=None,
        description="Optional filter by application_id (e.g. CPQ, CustomerExperience)",
    ),
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> list[IngestionJobResponse]:
    """List ingestion jobs (optionally filtered by ``application_id``) with file progress counts."""
    return [_to_response(j) for j in service.jobs.list_jobs(application_id=application_id)]


@router.get("/jobs/{job_id}", response_model=IngestionJobResponse)
async def get_ingestion_job(
    job_id: str,
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> IngestionJobResponse:
    job = service.jobs.get(job_id)
    if job is None:
        raise NotFoundError(f"Ingestion job {job_id} not found", public_message="Ingestion job not found")
    return _to_response(job)


# ------------------------------------------------------------------ Admin Portal Document Linking
@router.get("/documents", response_model=list[IndexedDocumentResponse])
async def list_indexed_documents(
    application_id: str | None = Query(
        default=None,
        description="Optional filter by application_id (e.g. CPQ, CustomerExperience)",
    ),
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> list[IndexedDocumentResponse]:
    """List indexed documents (optionally filtered by ``application_id``) and their links."""
    docs = service.registry.list_documents(application_id=application_id)
    return [
        IndexedDocumentResponse(
            **asdict(doc),
            linked_document_ids=service.registry.linked_ids(doc.document_id),
        )
        for doc in docs
    ]


@router.post(
    "/documents/{document_id}/links",
    status_code=201,
    response_model=DocumentLinkResponse,
)
async def create_document_link(
    document_id: str,
    request: CreateDocumentLinkRequest,
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> DocumentLinkResponse:
    """Link ``document_id`` to ``request.target_document_id`` and sync vector payloads."""
    link = await run_in_threadpool(
        service.link_documents,
        document_id,
        request.target_document_id,
        request.relation_type,
        request.created_by,
    )
    return DocumentLinkResponse(**asdict(link))


@router.get(
    "/documents/{document_id}/links",
    response_model=DocumentLinksOverviewResponse,
)
async def get_document_links(
    document_id: str,
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> DocumentLinksOverviewResponse:
    """Inspect outgoing and incoming links for ``document_id``."""
    doc = service.registry.get(document_id)
    outgoing, incoming = service.registry.links_for(document_id)
    return DocumentLinksOverviewResponse(
        document_id=document_id,
        application_id=doc.application_id,
        outgoing=[DocumentLinkResponse(**asdict(l)) for l in outgoing],
        incoming=[DocumentLinkResponse(**asdict(l)) for l in incoming],
        linked_document_ids=service.registry.linked_ids(document_id),
    )


@router.delete(
    "/documents/{document_id}/links/{link_id}",
    response_model=DocumentLinkResponse,
)
async def delete_document_link(
    document_id: str,
    link_id: str,
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> DocumentLinkResponse:
    """Remove an admin document link and update vector payloads."""
    link = await run_in_threadpool(service.unlink_documents, document_id, link_id)
    return DocumentLinkResponse(**asdict(link))


# ------------------------------------------------------------------ Event Listener & Folder Watcher
@router.post(
    "/events/webhook",
    status_code=202,
    response_model=IngestionEventResponse,
    summary="Receive file/folder events and trigger background ingestion",
)
async def receive_ingestion_webhook_event(
    request: IngestionWebhookEventRequest,
    background_tasks: BackgroundTasks,
    service: IngestionService = Depends(get_ingestion_service_dependency),
    storage: StorageService = Depends(get_storage_service_dependency),
    salesforce: SalesforceService = Depends(get_salesforce_service_dependency),
) -> IngestionEventResponse:
    """Handle a real-time file/folder event (upload, GCS Pub/Sub, Salesforce, S3) and queue ingestion."""
    import uuid

    service.registry.register_application(request.application_id)
    job_request = IngestionJobRequest(
        application_id=request.application_id,
        source_type="shared_path",
        provider=request.provider,
        shared_paths=request.paths,
        zone=request.zone,
    )
    job = service.jobs.create("shared_path", application_id=request.application_id)
    event = IngestionEvent(
        event_id=uuid.uuid4().hex[:16],
        event_type=request.event_type,
        application_id=request.application_id,
        provider=request.provider,
        paths=tuple(request.paths),
        zone=request.zone,
    )
    service.event_listener.record_event(event, job.job_id)
    background_tasks.add_task(run_ingestion_job, job, job_request, service, storage, salesforce)
    return IngestionEventResponse(
        event_id=event.event_id,
        event_type=event.event_type,
        application_id=event.application_id,
        provider=event.provider,
        paths=list(event.paths),
        zone=event.zone,
        received_at=event.received_at,
        job_id=job.job_id,
    )


@router.post(
    "/events/subscriptions",
    status_code=201,
    response_model=FolderWatchSubscriptionResponse,
    summary="Subscribe a shared folder to the Event Listener watcher",
)
async def create_folder_watch_subscription(
    request: FolderWatchSubscriptionRequest,
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> FolderWatchSubscriptionResponse:
    """Register a shared folder path to watch for newly added or modified files."""
    service.registry.register_application(request.application_id)
    sub = service.event_listener.add_subscription(
        application_id=request.application_id,
        provider=request.provider,
        shared_path=request.shared_path,
        zone=request.zone,
    )
    return FolderWatchSubscriptionResponse(**asdict(sub))


@router.get(
    "/events/subscriptions",
    response_model=list[FolderWatchSubscriptionResponse],
)
async def list_folder_watch_subscriptions(
    application_id: str | None = Query(default=None),
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> list[FolderWatchSubscriptionResponse]:
    """List active folder-watch subscriptions."""
    return [
        FolderWatchSubscriptionResponse(**asdict(s))
        for s in service.event_listener.list_subscriptions(application_id=application_id)
    ]


@router.delete(
    "/events/subscriptions/{subscription_id}",
    response_model=FolderWatchSubscriptionResponse,
)
async def delete_folder_watch_subscription(
    subscription_id: str,
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> FolderWatchSubscriptionResponse:
    sub = service.event_listener.remove_subscription(subscription_id)
    return FolderWatchSubscriptionResponse(**asdict(sub))


@router.post(
    "/events/poll",
    response_model=list[IngestionEventResponse],
    summary="Poll watched folders and trigger ingestion events for new/changed files",
)
async def poll_watched_folders(
    background_tasks: BackgroundTasks,
    application_id: str | None = Query(default=None),
    service: IngestionService = Depends(get_ingestion_service_dependency),
    storage: StorageService = Depends(get_storage_service_dependency),
    salesforce: SalesforceService = Depends(get_salesforce_service_dependency),
) -> list[IngestionEventResponse]:
    """Scan all watched folders, detect newly arrived or modified files, and queue background ingestion."""
    import hashlib
    import uuid

    triggered: list[IngestionEventResponse] = []
    subscriptions = service.event_listener.list_subscriptions(application_id=application_id)
    for sub in subscriptions:
        if not sub.enabled:
            continue
        scanner = resolve_folder_scanner(
            sub.provider,
            storage=storage,
            salesforce=salesforce,
        )
        discovered = await scanner.list_files([sub.shared_path], zone=sub.zone, limit=200)
        fingerprints: list[tuple[str, str]] = []
        for item in discovered:
            try:
                raw = await scanner.read_file(item)
                fp = hashlib.sha256(raw).hexdigest()[:16]
            except Exception:
                fp = item.file_key
            fingerprints.append((item.file_key, fp))

        new_keys = service.event_listener.detect_new_files(sub.subscription_id, fingerprints)
        if not new_keys:
            continue

        job_request = IngestionJobRequest(
            application_id=sub.application_id,
            source_type="shared_path",
            provider=sub.provider,  # type: ignore[arg-type]
            shared_paths=new_keys,
            zone=sub.zone,  # type: ignore[arg-type]
        )
        job = service.jobs.create("shared_path", application_id=sub.application_id)
        sub.last_job_id = job.job_id
        event = IngestionEvent(
            event_id=uuid.uuid4().hex[:16],
            event_type="folder.detected",
            application_id=sub.application_id,
            provider=sub.provider,
            paths=tuple(new_keys),
            zone=sub.zone,
        )
        service.event_listener.record_event(event, job.job_id)
        background_tasks.add_task(run_ingestion_job, job, job_request, service, storage, salesforce)
        triggered.append(
            IngestionEventResponse(
                event_id=event.event_id,
                event_type=event.event_type,
                application_id=event.application_id,
                provider=event.provider,
                paths=list(event.paths),
                zone=event.zone,
                received_at=event.received_at,
                job_id=job.job_id,
            )
        )
    return triggered


@router.get("/events", response_model=list[IngestionEventResponse])
async def list_ingestion_events(
    application_id: str | None = Query(default=None),
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> list[IngestionEventResponse]:
    """List recent events processed by the Event Listener."""
    return [
        IngestionEventResponse(
            event_id=ev.event_id,
            event_type=ev.event_type,
            application_id=ev.application_id,
            provider=ev.provider,
            paths=list(ev.paths),
            zone=ev.zone,
            received_at=ev.received_at,
            job_id=jid,
        )
        for ev, jid in service.event_listener.list_recent_events(application_id=application_id)
    ]


# ------------------------------------------------------------------ Google Cloud Pub/Sub Publisher & Subscriber
@router.post(
    "/events/pubsub/publish",
    status_code=202,
    response_model=PubSubPublishResponse,
    summary="Publish an ingestion event to Google Cloud Pub/Sub",
)
async def publish_ingestion_event_to_pubsub(
    request: IngestionWebhookEventRequest,
    service: IngestionService = Depends(get_ingestion_service_dependency),
) -> PubSubPublishResponse:
    """Publish a file/folder ingestion event to the configured Cloud Pub/Sub topic."""
    import uuid

    service.registry.register_application(request.application_id)
    event = IngestionEvent(
        event_id=uuid.uuid4().hex[:16],
        event_type=request.event_type,
        application_id=request.application_id,
        provider=request.provider,
        paths=tuple(request.paths),
        zone=request.zone,
    )
    msg_id = await run_in_threadpool(service.pubsub.publish, event)
    return PubSubPublishResponse(
        message_id=msg_id,
        event_id=event.event_id,
        application_id=event.application_id,
        provider=event.provider,
        paths=list(event.paths),
    )


@router.post(
    "/events/pubsub/push",
    status_code=202,
    response_model=IngestionEventResponse,
    summary="Receive Google Cloud Pub/Sub Push Subscription or GCS OBJECT_FINALIZE messages",
)
async def receive_pubsub_push_message(
    envelope: PubSubPushEnvelopeRequest,
    background_tasks: BackgroundTasks,
    service: IngestionService = Depends(get_ingestion_service_dependency),
    storage: StorageService = Depends(get_storage_service_dependency),
    salesforce: SalesforceService = Depends(get_salesforce_service_dependency),
) -> IngestionEventResponse:
    """Decode a Cloud Pub/Sub Push message (custom event or native GCS OBJECT_FINALIZE) and queue ingestion."""
    received = decode_pubsub_message(
        envelope.message,
        subscription=envelope.subscription,
    )
    ev = received.event
    service.registry.register_application(ev.application_id)
    job_request = IngestionJobRequest(
        application_id=ev.application_id,
        source_type="shared_path",
        provider=ev.provider,  # type: ignore[arg-type]
        shared_paths=list(ev.paths),
        zone=ev.zone,  # type: ignore[arg-type]
    )
    job = service.jobs.create("shared_path", application_id=ev.application_id)
    service.event_listener.record_event(ev, job.job_id)
    background_tasks.add_task(run_ingestion_job, job, job_request, service, storage, salesforce)
    return IngestionEventResponse(
        event_id=ev.event_id,
        event_type=ev.event_type,
        application_id=ev.application_id,
        provider=ev.provider,
        paths=list(ev.paths),
        zone=ev.zone,
        received_at=ev.received_at,
        job_id=job.job_id,
        pubsub_message_id=received.message_id,
    )


@router.post(
    "/events/pubsub/pull",
    response_model=list[IngestionEventResponse],
    summary="Pull pending messages from Google Cloud Pub/Sub subscription and queue ingestion",
)
async def pull_pubsub_messages(
    background_tasks: BackgroundTasks,
    max_messages: int = Query(default=10, ge=1, le=100),
    service: IngestionService = Depends(get_ingestion_service_dependency),
    storage: StorageService = Depends(get_storage_service_dependency),
    salesforce: SalesforceService = Depends(get_salesforce_service_dependency),
) -> list[IngestionEventResponse]:
    """Pull messages from the configured Pub/Sub subscription, queue background ingestion, and acknowledge."""
    messages = await run_in_threadpool(service.pubsub.pull, max_messages)
    if not messages:
        return []

    ack_ids: list[str] = []
    responses: list[IngestionEventResponse] = []
    for received in messages:
        ev = received.event
        service.registry.register_application(ev.application_id)
        job_request = IngestionJobRequest(
            application_id=ev.application_id,
            source_type="shared_path",
            provider=ev.provider,  # type: ignore[arg-type]
            shared_paths=list(ev.paths),
            zone=ev.zone,  # type: ignore[arg-type]
        )
        job = service.jobs.create("shared_path", application_id=ev.application_id)
        service.event_listener.record_event(ev, job.job_id)
        background_tasks.add_task(run_ingestion_job, job, job_request, service, storage, salesforce)
        if received.ack_id:
            ack_ids.append(received.ack_id)
        responses.append(
            IngestionEventResponse(
                event_id=ev.event_id,
                event_type=ev.event_type,
                application_id=ev.application_id,
                provider=ev.provider,
                paths=list(ev.paths),
                zone=ev.zone,
                received_at=ev.received_at,
                job_id=job.job_id,
                pubsub_message_id=received.message_id,
            )
        )

    if ack_ids:
        await run_in_threadpool(service.pubsub.ack, ack_ids)
    return responses
