"""Short-lived signed user context propagated chat-api -> MCP (defense in depth).

The MCP still requires the Google ID token (Cloud Run IAM); this header only carries *who* the caller is.
Swap HMAC for Cloud KMS asymmetric signing by replacing sign/verify (same signature).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from domain.errors import AuthenticationError
from domain.ports import UserContext

HEADER = "X-User-Context"
_TTL_S = 60


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(ctx: UserContext, secret: str, ttl_s: int = _TTL_S) -> str:
    payload = json.dumps(
        {"sub": ctx.user_id, "tid": ctx.tenant_id, "roles": sorted(ctx.roles), "exp": int(time.time()) + ttl_s},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def verify(value: str, secret: str) -> UserContext:
    try:
        p, s = value.split(".")
        payload = _unb64(p)
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(s)):
            raise ValueError("bad signature")
        data = json.loads(payload)
        if data["exp"] < time.time():
            raise ValueError("expired")
        return UserContext(data["sub"], data["tid"], frozenset(data["roles"]))
    except Exception as exc:
        raise AuthenticationError("Invalid user context") from exc
