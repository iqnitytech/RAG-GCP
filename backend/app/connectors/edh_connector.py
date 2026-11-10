"""Enterprise Data Hub (EDH) connector factory bound to backend settings."""

from functools import lru_cache

from sdk.connectors.edh import EdhConnector
from sdk.models.edh_models import EdhConnectorConfig

from app.core.config import Settings, get_settings


def create_edh_connector(
    settings: Settings | None = None,
    *,
    strategy: str = "data_lake",
    root_path: str = "./.data_lake",
    endpoint_url: str = "",
) -> EdhConnector:
    """Build an :class:`EdhConnector` configured from application settings."""
    resolved = settings or get_settings()
    namespace = resolved.gcs_document_bucket or resolved.gcp_project_id or "rag-default"
    config = EdhConnectorConfig(
        strategy=strategy,
        namespace=namespace,
        root_path=root_path,
        endpoint_url=endpoint_url,
        region=resolved.gcp_region,
    )
    return EdhConnector(config)


@lru_cache(maxsize=1)
def get_edh_connector() -> EdhConnector:
    """Return a cached default :class:`EdhConnector` instance."""
    return create_edh_connector()
