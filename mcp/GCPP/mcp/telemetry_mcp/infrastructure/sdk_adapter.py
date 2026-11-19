from __future__ import annotations
import asyncio
import itertools
import json
from datetime import datetime, timezone
from typing import Optional, Any

from config.settings import Settings
from common.domain.value_objects import MachineId
from common.infrastructure.connector import create_edh_connector
from common.infrastructure.resilience import call_sdk_with_retry, handle_sdk_exception, retry_with_backoff
from core.exceptions import (
    AuthenticationError,
    EntityNotFoundError,
    RateLimitError,
    SdkGatewayError,
)
from core.logging import get_logger
from mcp.telemetry_mcp.application.ports import TelemetryRepositoryPort
from mcp.telemetry_mcp.domain.entities import (
    AggregatedDataPoint,
    Alert,
    FaultCodeDiagnosis,
    LatestMetrics,
    MetricPoint,
    TimeSeriesResult,
)

logger = get_logger(__name__)


class SdkTelemetryAdapter(TelemetryRepositoryPort):
    """SDK adapter connecting to Enterprise Data Hub (EDH) via rag_platform_sdk for Telemetry operations."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connector: Any = None

    def _get_connector(self) -> Any:
        if self._connector is None:
            try:
                self._connector = create_edh_connector(
                    self._settings,
                    strategy="api_hub",
                    namespace="telemetry",
                )
            except (AuthenticationError, RateLimitError, SdkGatewayError):
                raise
            except Exception as e:
                logger.error("Failed to initialize EdhConnector for Telemetry", error=str(e))
                handle_sdk_exception(e, "initializing Telemetry connector")
        return self._connector

    @retry_with_backoff(max_attempts=3)
    async def get_latest_metrics(
        self,
        machine_id: MachineId,
        metric_names: Optional[list[str]],
    ) -> LatestMetrics:
        try:
            connector = self._get_connector()

            # ARCH-05: Stream iterator directly without loading 10K+ records into memory
            def _stream_metrics():
                from sdk.models import DataZone
                records_iter = connector.read_records(
                    "telemetry_snapshots",
                    zone=DataZone.CURATED,
                    partition_filter={"machine_id": str(machine_id)},
                )
                pts: list[MetricPoint] = []
                now_inner = datetime.now(timezone.utc)
                for r in records_iter:
                    m_name = r.get("metric_name", "")
                    if metric_names and m_name not in metric_names:
                        continue
                    pts.append(
                        MetricPoint(
                            metric_name=m_name,
                            value=float(r.get("value", 0.0)),
                            unit=r.get("unit", ""),
                            timestamp=datetime.fromisoformat(r.get("timestamp", now_inner.isoformat())),
                            quality=r.get("quality", "GOOD"),
                        )
                    )
                return pts

            points = await asyncio.to_thread(call_sdk_with_retry, _stream_metrics)
            now = datetime.now(timezone.utc)
            return LatestMetrics(
                machine_id=machine_id,
                metrics=points,
                snapshot_time=now,
                source="sdk-edh-telemetry",
            )
        except (AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error getting latest metrics", machine_id=str(machine_id), error=str(e))
            handle_sdk_exception(e, f"fetching metrics for {machine_id}")
            raise

    @retry_with_backoff(max_attempts=3)
    async def query_range(
        self,
        machine_id: MachineId,
        start_time: datetime,
        end_time: datetime,
        resolution: str,
        aggregation: str,
    ) -> list[TimeSeriesResult]:
        try:
            connector = self._get_connector()
            from sdk.models import DataZone
            records = await asyncio.to_thread(
                call_sdk_with_retry,
                lambda: list(
                    connector.read_records(
                        "telemetry_timeseries",
                        zone=DataZone.CURATED,
                        partition_filter={
                            "machine_id": str(machine_id),
                            "start_time": start_time.isoformat(),
                            "end_time": end_time.isoformat(),
                        },
                    )
                ),
            )
            by_metric: dict[str, list[AggregatedDataPoint]] = {}
            for r in records:
                m_name = r.get("metric_name", "unknown")
                if m_name not in by_metric:
                    by_metric[m_name] = []
                by_metric[m_name].append(
                    AggregatedDataPoint(
                        timestamp=datetime.fromisoformat(r.get("timestamp", start_time.isoformat())),
                        value=float(r.get("value", 0.0)),
                        metric_name=m_name,
                        aggregation=aggregation,
                    )
                )

            results: list[TimeSeriesResult] = []
            for m_name, d_pts in by_metric.items():
                results.append(
                    TimeSeriesResult(
                        machine_id=machine_id,
                        metric_name=m_name,
                        start_time=start_time,
                        end_time=end_time,
                        resolution=resolution,
                        aggregation=aggregation,
                        data_points=d_pts,
                        total_points=len(d_pts),
                    )
                )
            return results
        except (AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error querying telemetry range", machine_id=str(machine_id), error=str(e))
            handle_sdk_exception(e, f"querying range for {machine_id}")
            raise

    @retry_with_backoff(max_attempts=3)
    async def get_active_alerts(
        self,
        machine_id: Optional[MachineId],
        severity: Optional[str],
    ) -> list[Alert]:
        try:
            connector = self._get_connector()
            filter_map: dict[str, str] = {}
            if machine_id:
                filter_map["machine_id"] = str(machine_id)
            if severity:
                filter_map["severity"] = severity

            from sdk.models import DataZone
            def _fetch_alerts() -> list[dict]:
                records_iter = connector.read_records(
                    "active_alerts",
                    zone=DataZone.CURATED,
                    partition_filter=filter_map,
                )
                return list(itertools.islice(records_iter, 1000))

            records = await asyncio.to_thread(
                call_sdk_with_retry,
                _fetch_alerts,
            )
            return [
                Alert(
                    alert_id=r["alert_id"],
                    machine_id=MachineId(r["machine_id"]),
                    severity=r.get("severity", "WARNING"),
                    metric_name=r.get("metric_name", ""),
                    threshold_value=float(r.get("threshold_value", 0.0)),
                    actual_value=float(r.get("actual_value", 0.0)),
                    triggered_at=datetime.fromisoformat(r.get("triggered_at", "2026-11-17T00:00:00+00:00")),
                    description=r.get("description", ""),
                    remediation=r.get("remediation", ""),
                    is_acknowledged=bool(r.get("is_acknowledged", False)),
                )
                for r in records
            ]
        except (AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error fetching active alerts", error=str(e))
            handle_sdk_exception(e, "fetching active alerts")
            raise

    @retry_with_backoff(max_attempts=3)
    async def diagnose_fault_code(self, fault_code: str) -> FaultCodeDiagnosis:
        try:
            connector = self._get_connector()
            from sdk.models import EdhObjectRef, DataZone
            if hasattr(connector, "ref"):
                ref = connector.ref(f"fault_codes/{fault_code.upper()}.json", zone=DataZone.CURATED)
            else:
                ref = EdhObjectRef(namespace="telemetry", key=f"fault_codes/{fault_code.upper()}.json", zone=DataZone.CURATED)
            data_bytes = await asyncio.to_thread(
                call_sdk_with_retry,
                connector.get_bytes,
                ref,
            )
            rec = json.loads(data_bytes.decode("utf-8"))
            return FaultCodeDiagnosis(
                fault_code=rec["fault_code"],
                name=rec.get("name", ""),
                description=rec.get("description", ""),
                root_causes=list(rec.get("root_causes", [])),
                diagnostic_steps=list(rec.get("diagnostic_steps", [])),
                recommended_parts=list(rec.get("recommended_parts", [])),
                severity=rec.get("severity", "WARNING"),
                estimated_repair_hours=float(rec.get("estimated_repair_hours", 1.0)),
                documentation_url=rec.get("documentation_url", ""),
            )
        except (AuthenticationError, RateLimitError, EntityNotFoundError):
            raise
        except Exception as e:
            logger.error("SDK error diagnosing fault code", fault_code=fault_code, error=str(e))
            handle_sdk_exception(e, f"diagnosing fault code {fault_code}")
            raise
