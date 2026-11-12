"""Unit and contract tests for ``InMemoryEdhStrategy``."""

from __future__ import annotations

import pytest

from sdk.connectors.edh import EdhConnector
from sdk.models.edh_models import EdhConnectorConfig
from tests.unit.conftest import (
    assert_blob_crud_and_pagination_contract,
    assert_partitioned_records_contract,
    assert_strategy_implements_protocol,
)


@pytest.fixture
def memory_connector() -> EdhConnector:
    return EdhConnector(EdhConnectorConfig(strategy="memory", namespace="rag-enterprise"))


def test_memory_strategy_implements_protocol(memory_connector: EdhConnector):
    assert_strategy_implements_protocol(memory_connector)


def test_memory_strategy_blob_crud_and_pagination(memory_connector: EdhConnector):
    assert_blob_crud_and_pagination_contract(memory_connector)


def test_memory_strategy_partitioned_records(memory_connector: EdhConnector):
    assert_partitioned_records_contract(memory_connector)
