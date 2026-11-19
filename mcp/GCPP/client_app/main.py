#!/usr/bin/env python3
"""Main entry point for the Multi-MCP Web Dashboard & REST Client Application."""

from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from client_app.config.client_settings import ClientSettings
from client_app.presentation.api import router as api_router
from core.logging import configure_logging, get_logger

settings = ClientSettings()
configure_logging(level="INFO", format="TEXT")
logger = get_logger("client_app")

STATIC_DIR = Path(__file__).parent / "presentation" / "static"

app = FastAPI(
    title="Enterprise Multi-MCP Client Console",
    description="Interactive diagnostic and testing platform for CustomerMCP, MachineMCP, and TelemetryMCP",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def serve_dashboard() -> FileResponse:
    index_file = STATIC_DIR / "index.html"
    return FileResponse(index_file)


@app.get("/healthz", tags=["Observability"])
@app.get("/health", tags=["Observability"])
async def health_check() -> dict[str, str]:
    """Liveness probe endpoint (OBS-02)."""
    return {"status": "healthy", "service": "mcp-client-console"}


@app.get("/ready", tags=["Observability"])
@app.get("/readiness", tags=["Observability"])
async def readiness_check() -> dict[str, Any]:
    """Readiness probe endpoint checking MCP subsystems and gateways (OBS-02)."""
    from core.health import check_readiness
    from config.settings import Settings
    app_settings = Settings()
    return check_readiness(app_settings)


@app.get("/metrics", tags=["Observability"])
async def metrics_endpoint() -> PlainTextResponse:
    """Prometheus metrics exposition endpoint (OBS-03)."""
    from core.metrics import get_metrics_registry
    return PlainTextResponse(
        get_metrics_registry().generate_metrics_text(),
        media_type="text/plain; version=0.0.4",
    )


def run_app() -> None:
    print("\n" + "=" * 80)
    print("  🚀 ENTERPRISE MULTI-MCP CLIENT CONSOLE STARTING")
    print(f"  👉 Web Dashboard: http://localhost:{settings.CLIENT_PORT}")
    print(f"  👉 REST API Docs: http://localhost:{settings.CLIENT_PORT}/docs")
    print(f"  👉 Mode: DATA_PROVIDER_MODE={settings.DATA_PROVIDER_MODE}")
    print("=" * 80 + "\n")
    uvicorn.run(
        app,
        host=settings.CLIENT_HOST,
        port=settings.CLIENT_PORT,
        log_level="info" if settings.DEBUG else "warning",
    )


if __name__ == "__main__":
    run_app()
