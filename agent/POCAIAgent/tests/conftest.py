import pathlib

import pytest
from fastapi.testclient import TestClient

from container import build_container
from presentation.api import create_app
from settings import Settings

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def settings() -> Settings:
    return Settings(agents_config=str(ROOT / "config" / "agents.yaml"))


@pytest.fixture
def client(settings):
    with TestClient(create_app(build_container(settings))) as c:
        yield c


def auth(roles: str, user: str = "u1") -> dict:
    return {"Authorization": f"Bearer dev:{user}:{roles}"}
