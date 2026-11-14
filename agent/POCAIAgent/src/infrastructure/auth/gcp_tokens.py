"""Google identity tokens for service-to-service calls (ADC / metadata server; no keys)."""
from __future__ import annotations

import anyio
import google.auth.transport.requests
import google.oauth2.id_token


def _fetch(audience: str) -> str:
    return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)


async def google_id_token(audience: str) -> str:
    return await anyio.to_thread.run_sync(_fetch, audience)


def verify_google_id_token(token: str, audience: str) -> dict:
    """Used by MCP servers when MCP_AUTH=google (Cloud Run IAM already enforces it at the edge)."""
    return google.oauth2.id_token.verify_oauth2_token(token, google.auth.transport.requests.Request(), audience)
