from __future__ import annotations
import pytest
from datetime import datetime, timedelta, timezone
from core.exceptions import DomainValidationError, EntityNotFoundError
from mcp.telemetry_mcp.application.use_cases import (
    DiagnoseFaultCodeUseCase,
    GetActiveAlertsUseCase,
    GetLatestMetricsUseCase,
    QueryRangeUseCase,
)


@pytest.mark.asyncio
async def test_get_latest_metrics_all(telemetry_repo):
    uc = GetLatestMetricsUseCase(telemetry_repo)
    res = await uc.execute("MCH-A1B2-C3D4")
    assert res.machine_id == "MCH-A1B2-C3D4"
    assert len(res.metrics) == 8


@pytest.mark.asyncio
async def test_get_latest_metrics_filtered(telemetry_repo):
    uc = GetLatestMetricsUseCase(telemetry_repo)
    res = await uc.execute("MCH-A1B2-C3D4", ["temperature", "rpm"])
    assert len(res.metrics) == 2
    names = {m.metric_name for m in res.metrics}
    assert names == {"temperature", "rpm"}


@pytest.mark.asyncio
async def test_get_latest_metrics_invalid_metric(telemetry_repo):
    uc = GetLatestMetricsUseCase(telemetry_repo)
    with pytest.raises(DomainValidationError, match="Invalid metric names"):
        await uc.execute("MCH-A1B2-C3D4", ["warp_speed"])


@pytest.mark.asyncio
async def test_query_range_success(telemetry_repo):
    uc = QueryRangeUseCase(telemetry_repo)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    res = await uc.execute("MCH-A1B2-C3D4", start, end, "5m", "AVG")
    assert len(res) >= 1
    assert res[0].total_points >= 2


@pytest.mark.asyncio
async def test_query_range_invalid_timerange(telemetry_repo):
    uc = QueryRangeUseCase(telemetry_repo)
    end = datetime.now(timezone.utc)
    start = end + timedelta(hours=1)  # start > end
    with pytest.raises(DomainValidationError, match="must be before"):
        await uc.execute("MCH-A1B2-C3D4", start, end, "5m", "AVG")


@pytest.mark.asyncio
async def test_query_range_invalid_resolution(telemetry_repo):
    uc = QueryRangeUseCase(telemetry_repo)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    with pytest.raises(DomainValidationError, match="Invalid resolution"):
        await uc.execute("MCH-A1B2-C3D4", start, end, "99m", "AVG")


@pytest.mark.asyncio
async def test_get_active_alerts_critical(telemetry_repo):
    uc = GetActiveAlertsUseCase(telemetry_repo)
    alerts = await uc.execute(severity="CRITICAL")
    assert len(alerts) >= 1
    for a in alerts:
        assert a.severity == "CRITICAL"


@pytest.mark.asyncio
async def test_diagnose_fault_code_known(telemetry_repo):
    uc = DiagnoseFaultCodeUseCase(telemetry_repo)
    diag = await uc.execute("E-001")
    assert diag.fault_code == "E-001"
    assert diag.name == "Motor Overheat"
    assert diag.severity == "CRITICAL"
    assert len(diag.root_causes) >= 3


@pytest.mark.asyncio
async def test_diagnose_fault_code_unknown(telemetry_repo):
    uc = DiagnoseFaultCodeUseCase(telemetry_repo)
    with pytest.raises(EntityNotFoundError):
        await uc.execute("Z-999")
