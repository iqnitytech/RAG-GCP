"""App package allowing both `uvicorn app.main:app` and `uvicorn app:app`."""
from .main import app

__all__ = ["app"]
