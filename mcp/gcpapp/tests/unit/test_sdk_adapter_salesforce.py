"""Unit tests for Salesforce record-retrieval methods on SdkCustomerAdapter.

Coverage targets
----------------
* get_record_by_id  (sync)        – success, SF exception, missing config
* aget_record_by_id (async)       – success, SF exception, missing config
* get_record_by_number  (sync)    – success, not-found, empty number, field fallback
* aget_record_by_number (async)   – success, not-found, empty number, exception

All Salesforce SDK calls are mocked; no real network is touched.

``aget_record_by_id`` and ``aget_record_by_number`` delegate to their sync
counterparts via ``asyncio.to_thread``.  In tests we patch ``asyncio.to_thread``
(inside the adapter module) with a synchronous shim so no event loop is needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from config.settings import Settings
from core.exceptions import EntityNotFoundError, SdkGatewayError
from mcp.customer_mcp.infrastructure.sdk_adapter import SdkCustomerAdapter


# ---------------------------------------------------------------------------
# Synchronous shim for asyncio.to_thread used in async tests
# ---------------------------------------------------------------------------

_TO_THREAD_PATH = "mcp.customer_mcp.infrastructure.sdk_adapter.asyncio.to_thread"


def _sync_to_thread(func, *args, **kwargs):
    """Drop-in for asyncio.to_thread that runs the callable synchronously.

    Returns the result directly (not a coroutine).
    """
    return func(*args, **kwargs)


def _run_aget(coro):
    """Drain a coroutine that was produced by an aget_* method patched with
    _sync_to_thread.  The outer coroutine itself is trivial — it just awaits a
    coroutine returned by asyncio.to_thread, which we have replaced with a plain
    value.  We step it manually using send().
    """
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    # If we reach here the coroutine yielded (suspended), which should not
    # happen with our synchronous shim — but propagate any error.
    raise RuntimeError("aget_* coroutine did not finish in a single step")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sf_settings() -> Settings:
    """Minimal settings that satisfy ``is_salesforce_configured``."""
    return Settings(
        DATA_PROVIDER_MODE="SDK",
        SDK_API_KEY="valid-test-key",
        SDK_BASE_URL="https://edh-test.internal",
        SF_INSTANCE_URL="https://test.salesforce.com",
        SF_ACCESS_TOKEN="fake-sf-access-token",
    )


@pytest.fixture
def sf_unconfigured_settings() -> Settings:
    """Settings with no Salesforce credentials."""
    return Settings(
        DATA_PROVIDER_MODE="SDK",
        SDK_API_KEY="valid-test-key",
        SDK_BASE_URL="https://edh-test.internal",
        SF_INSTANCE_URL="",
    )


def _make_sync_ctx(return_value: Any):
    """Return a sync context manager whose __enter__ returns *return_value*."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=return_value)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# get_record_by_id  (sync)
# ---------------------------------------------------------------------------

class TestGetRecordById:
    def test_success_returns_record(self, sf_settings: Settings):
        """Happy-path: connector returns a record dict."""
        expected = {"Id": "0010000001", "Name": "Acme Corp"}

        mock_sf = MagicMock()
        mock_sf.getRecordById.return_value = expected
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        result = adapter.get_record_by_id("Account", "0010000001")

        assert result == expected
        mock_sf.getRecordById.assert_called_once_with("Account", "0010000001", fields=None)

    def test_success_passes_fields(self, sf_settings: Settings):
        """Optional *fields* argument is forwarded to the SDK call."""
        mock_sf = MagicMock()
        mock_sf.getRecordById.return_value = {"Id": "001", "Name": "X"}
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        adapter.get_record_by_id("Account", "001", fields=["Id", "Name"])

        mock_sf.getRecordById.assert_called_once_with("Account", "001", fields=["Id", "Name"])

    def test_sf_exception_propagates(self, sf_settings: Settings):
        """SDK errors bubble up without being swallowed."""
        mock_sf = MagicMock()
        mock_sf.getRecordById.side_effect = RuntimeError("Salesforce unavailable")
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        with pytest.raises(RuntimeError, match="Salesforce unavailable"):
            adapter.get_record_by_id("Account", "001")

    def test_unconfigured_raises_sdk_gateway_error(self, sf_unconfigured_settings: Settings):
        """Missing Salesforce settings raise ``SdkGatewayError``."""
        adapter = SdkCustomerAdapter(sf_unconfigured_settings)
        with pytest.raises(SdkGatewayError):
            adapter.get_record_by_id("Account", "001")


# ---------------------------------------------------------------------------
# aget_record_by_id  (async – delegates to get_record_by_id via to_thread)
# ---------------------------------------------------------------------------

class TestAgetRecordById:
    """Tests for the async aget_record_by_id method.

    We patch asyncio.to_thread with a synchronous shim so the tests run without
    a live event loop, and we drive the coroutine manually with _run_aget().
    """

    def test_success_returns_record(self, sf_settings: Settings):
        """aget_record_by_id delegates to get_record_by_id; happy-path."""
        expected = {"Id": "0010000002", "Name": "Beta Corp"}
        called: dict[str, Any] = {}

        def _sync_impl(object_name, record_id, *, fields=None):
            called.update(object_name=object_name, record_id=record_id, fields=fields)
            return expected

        adapter = SdkCustomerAdapter(sf_settings)
        adapter.get_record_by_id = _sync_impl  # type: ignore[method-assign]

        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            result = _run_aget(adapter.aget_record_by_id("Account", "0010000002"))

        assert result == expected
        assert called == {"object_name": "Account", "record_id": "0010000002", "fields": None}

    def test_success_passes_fields(self, sf_settings: Settings):
        """Optional *fields* forwarded through the async delegation path."""
        captured: dict[str, Any] = {}

        def _sync_impl(object_name, record_id, *, fields=None):
            captured["fields"] = fields
            return {"Id": record_id}

        adapter = SdkCustomerAdapter(sf_settings)
        adapter.get_record_by_id = _sync_impl  # type: ignore[method-assign]

        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            _run_aget(adapter.aget_record_by_id("Case", "002", fields=["Id", "Subject"]))

        assert captured["fields"] == ["Id", "Subject"]

    def test_sf_exception_propagates(self, sf_settings: Settings):
        """Exceptions raised by get_record_by_id bubble through aget_record_by_id."""
        def _sync_impl(*_a, **_kw):
            raise ConnectionError("timeout")

        adapter = SdkCustomerAdapter(sf_settings)
        adapter.get_record_by_id = _sync_impl  # type: ignore[method-assign]

        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            with pytest.raises(ConnectionError, match="timeout"):
                _run_aget(adapter.aget_record_by_id("Account", "003"))

    def test_unconfigured_raises_sdk_gateway_error(self, sf_unconfigured_settings: Settings):
        """Missing Salesforce settings raise ``SdkGatewayError`` via async path.

        ``_resolve_sf_sync_connector`` is called by ``get_record_by_id`` inside the
        thread; with our shim it runs synchronously and raises before any event loop.
        """
        adapter = SdkCustomerAdapter(sf_unconfigured_settings)
        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            with pytest.raises(SdkGatewayError):
                _run_aget(adapter.aget_record_by_id("Account", "001"))


# ---------------------------------------------------------------------------
# get_record_by_number  (sync)
# ---------------------------------------------------------------------------

class TestGetRecordByNumber:
    def test_success_returns_first_record(self, sf_settings: Settings):
        """Happy-path: query returns records; first one is returned."""
        expected = {"Id": "500000001", "CaseNumber": "00001234"}
        mock_sf = MagicMock()
        mock_sf.query.return_value = [expected]
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        result = adapter.get_record_by_number("Case", "CaseNumber", "00001234")

        assert result == expected
        mock_sf.query.assert_called_once_with(
            "Case", fields=None, where={"CaseNumber": "00001234"}, limit=1
        )

    def test_default_field_resolved_from_map(self, sf_settings: Settings):
        """When *field_name* is empty, the default map resolves the correct field."""
        mock_sf = MagicMock()
        mock_sf.query.return_value = [{"Id": "1", "AccountNumber": "ACC-999"}]
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        adapter.get_record_by_number("Account", "", "ACC-999")

        mock_sf.query.assert_called_once_with(
            "Account", fields=None, where={"AccountNumber": "ACC-999"}, limit=1
        )

    def test_unknown_object_uses_fallback_field(self, sf_settings: Settings):
        """Objects not in the default map get ``{object_name}Number`` as fallback."""
        mock_sf = MagicMock()
        mock_sf.query.return_value = [{"Id": "9", "CustomObjectNumber": "X-1"}]
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        adapter.get_record_by_number("CustomObject", "", "X-1")

        mock_sf.query.assert_called_once_with(
            "CustomObject",
            fields=None,
            where={"CustomObjectNumber": "X-1"},
            limit=1,
        )

    def test_not_found_raises_entity_not_found_error(self, sf_settings: Settings):
        """Empty result list raises ``EntityNotFoundError``."""
        mock_sf = MagicMock()
        mock_sf.query.return_value = []
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        with pytest.raises(EntityNotFoundError, match="CaseNumber='00009999'"):
            adapter.get_record_by_number("Case", "CaseNumber", "00009999")

    def test_empty_record_number_raises_value_error(self, sf_settings: Settings):
        """Blank *record_number* raises ``ValueError`` before any SDK call."""
        mock_sf = MagicMock()
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        with pytest.raises(ValueError, match="record_number cannot be empty"):
            adapter.get_record_by_number("Case", "CaseNumber", "   ")

        mock_sf.query.assert_not_called()

    def test_sf_exception_propagates(self, sf_settings: Settings):
        """SDK errors bubble up without being swallowed."""
        mock_sf = MagicMock()
        mock_sf.query.side_effect = RuntimeError("query failed")
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        with pytest.raises(RuntimeError, match="query failed"):
            adapter.get_record_by_number("Case", "CaseNumber", "00001")

    def test_unconfigured_raises_sdk_gateway_error(self, sf_unconfigured_settings: Settings):
        """Missing Salesforce settings raise ``SdkGatewayError``."""
        adapter = SdkCustomerAdapter(sf_unconfigured_settings)
        with pytest.raises(SdkGatewayError):
            adapter.get_record_by_number("Case", "CaseNumber", "00001")


# ---------------------------------------------------------------------------
# aget_record_by_number  (async – delegates to get_record_by_number via to_thread)
# ---------------------------------------------------------------------------

class TestAgetRecordByNumber:
    """Tests for the async aget_record_by_number method.

    Uses the same asyncio.to_thread shim strategy as TestAgetRecordById.
    """

    def test_success_returns_first_record(self, sf_settings: Settings):
        """aget_record_by_number delegates to get_record_by_number; happy-path."""
        expected = {"Id": "500000002", "ContractNumber": "CN-5000"}
        captured: dict[str, Any] = {}

        def _sync_impl(object_name, field_name, record_number, *, fields=None):
            captured.update(
                object_name=object_name,
                field_name=field_name,
                record_number=record_number,
                fields=fields,
            )
            return expected

        adapter = SdkCustomerAdapter(sf_settings)
        adapter.get_record_by_number = _sync_impl  # type: ignore[method-assign]

        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            result = _run_aget(adapter.aget_record_by_number("Contract", "ContractNumber", "CN-5000"))

        assert result == expected
        assert captured == {
            "object_name": "Contract",
            "field_name": "ContractNumber",
            "record_number": "CN-5000",
            "fields": None,
        }

    def test_not_found_raises_entity_not_found_error(self, sf_settings: Settings):
        """EntityNotFoundError from get_record_by_number propagates through async path."""
        def _sync_impl(*_a, **_kw):
            raise EntityNotFoundError("ContractNumber='NO-MATCH'")

        adapter = SdkCustomerAdapter(sf_settings)
        adapter.get_record_by_number = _sync_impl  # type: ignore[method-assign]

        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            with pytest.raises(EntityNotFoundError, match="ContractNumber='NO-MATCH'"):
                _run_aget(adapter.aget_record_by_number("Contract", "ContractNumber", "NO-MATCH"))

    def test_empty_record_number_raises_value_error(self, sf_settings: Settings):
        """Blank *record_number* raises ``ValueError`` via delegation to sync method."""
        def _sync_impl(object_name, field_name, record_number, *, fields=None):
            clean = record_number.strip()
            if not clean:
                raise ValueError("record_number cannot be empty")
            return {}

        adapter = SdkCustomerAdapter(sf_settings)
        adapter.get_record_by_number = _sync_impl  # type: ignore[method-assign]

        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            with pytest.raises(ValueError, match="record_number cannot be empty"):
                _run_aget(adapter.aget_record_by_number("Contract", "ContractNumber", ""))

    def test_sf_exception_propagates(self, sf_settings: Settings):
        """SDK errors raised in get_record_by_number bubble through async path."""
        def _sync_impl(*_a, **_kw):
            raise IOError("network error")

        adapter = SdkCustomerAdapter(sf_settings)
        adapter.get_record_by_number = _sync_impl  # type: ignore[method-assign]

        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            with pytest.raises(IOError, match="network error"):
                _run_aget(adapter.aget_record_by_number("Order", "OrderNumber", "ORD-99"))

    def test_unconfigured_raises_sdk_gateway_error(self, sf_unconfigured_settings: Settings):
        """Missing Salesforce settings raise ``SdkGatewayError`` via async path."""
        adapter = SdkCustomerAdapter(sf_unconfigured_settings)
        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            with pytest.raises(SdkGatewayError):
                _run_aget(adapter.aget_record_by_number("Order", "OrderNumber", "ORD-99"))


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

class TestPrivateHelpers:
    def test_resolve_number_field_explicit_wins(self, sf_settings: Settings):
        adapter = SdkCustomerAdapter(sf_settings)
        assert adapter._resolve_number_field("Account", "CustomField__c") == "CustomField__c"

    def test_resolve_number_field_default_map(self, sf_settings: Settings):
        adapter = SdkCustomerAdapter(sf_settings)
        assert adapter._resolve_number_field("case", "") == "CaseNumber"
        assert adapter._resolve_number_field("asset", None) == "SerialNumber"

    def test_resolve_number_field_fallback(self, sf_settings: Settings):
        adapter = SdkCustomerAdapter(sf_settings)
        assert adapter._resolve_number_field("MyObject", "") == "MyObjectNumber"

    def test_validate_record_number_strips_whitespace(self, sf_settings: Settings):
        adapter = SdkCustomerAdapter(sf_settings)
        assert adapter._validate_record_number("  00001  ", "op") == "00001"

    def test_validate_record_number_empty_raises(self, sf_settings: Settings):
        adapter = SdkCustomerAdapter(sf_settings)
        with pytest.raises(ValueError):
            adapter._validate_record_number("   ", "op")


# ---------------------------------------------------------------------------
# query_salesforce (sync + async)
# ---------------------------------------------------------------------------

class TestQuerySalesforce:
    def test_query_salesforce_sync(self, sf_settings: Settings):
        mock_sf = MagicMock()
        mock_sf.query.return_value = [{"Id": "001", "Name": "Corp"}]
        adapter = SdkCustomerAdapter(sf_settings, sf_connector=_make_sync_ctx(mock_sf))

        result = adapter.query_salesforce("Account", fields=["Id", "Name"], where={"Active__c": "Yes"}, limit=5)

        assert result == [{"Id": "001", "Name": "Corp"}]
        mock_sf.query.assert_called_once_with("Account", fields=["Id", "Name"], where={"Active__c": "Yes"}, limit=5)

    def test_query_salesforce_async(self, sf_settings: Settings):
        expected = [{"Id": "001", "Name": "Async Corp"}]
        captured: dict[str, Any] = {}

        def _sync_impl(target, *, fields=None, where=None, limit=None):
            captured.update(target=target, fields=fields, where=where, limit=limit)
            return expected

        adapter = SdkCustomerAdapter(sf_settings)
        adapter.query_salesforce = _sync_impl  # type: ignore[method-assign]

        with patch(_TO_THREAD_PATH, side_effect=_sync_to_thread):
            result = _run_aget(
                adapter.aquery_salesforce("SELECT Id FROM Account", limit=10)
            )

        assert result == expected
        assert captured == {
            "target": "SELECT Id FROM Account",
            "fields": None,
            "where": None,
            "limit": 10,
        }
