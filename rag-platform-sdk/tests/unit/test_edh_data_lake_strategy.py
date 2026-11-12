"""Unit and contract tests for ``DataLakeStrategy``."""

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
def data_lake_connector(tmp_path) -> EdhConnector:
    return EdhConnector(
        EdhConnectorConfig(
            strategy="data_lake",
            namespace="rag-enterprise",
            root_path=str(tmp_path / "lake"),
        )
    )


def test_data_lake_strategy_implements_protocol(data_lake_connector: EdhConnector):
    assert_strategy_implements_protocol(data_lake_connector)


def test_data_lake_strategy_blob_crud_and_pagination(data_lake_connector: EdhConnector):
    assert_blob_crud_and_pagination_contract(data_lake_connector)


def test_data_lake_strategy_partitioned_records(data_lake_connector: EdhConnector):
    assert_partitioned_records_contract(data_lake_connector)
