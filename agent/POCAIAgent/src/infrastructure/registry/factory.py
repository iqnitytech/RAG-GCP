from __future__ import annotations

from domain.ports import RegistryStore
from settings import Settings

from .yaml_store import YamlRegistryStore


def build_registry_store(settings: Settings) -> RegistryStore:
    if settings.registry_store == "yaml":
        return YamlRegistryStore(settings.agents_config)
    if settings.registry_store == "firestore":
        from google.cloud import firestore  # pip install google-cloud-firestore

        from .firestore_store import FirestoreRegistryStore

        client = firestore.Client(project=settings.firestore_project or None)
        return FirestoreRegistryStore(client, settings.registry_prefix)
    raise ValueError(f"Unknown REGISTRY_STORE: {settings.registry_store}")
