from __future__ import annotations

import jwt
from jwt import PyJWKClient

from domain.errors import AuthenticationError
from domain.ports import UserContext


class EntraTokenValidator:
    """Validates Microsoft Entra ID v2 access tokens (signature, iss, aud, exp, tid)."""

    def __init__(self, tenant_id: str, audience: str, jwks_client: PyJWKClient | None = None) -> None:
        self._tenant = tenant_id
        self._audience = audience
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._jwks = jwks_client or PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True
        )

    async def validate(self, token: str) -> UserContext:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "oid", "tid"]},
            )
        except Exception as exc:
            raise AuthenticationError("Invalid token") from exc
        if claims["tid"] != self._tenant:
            raise AuthenticationError("Wrong tenant")
        return UserContext(claims["oid"], claims["tid"], frozenset(claims.get("roles", [])))


class DevTokenValidator:
    """LOCAL ONLY. Token format: dev:<user>:<Role1,Role2>. Refused when APP_ENV=prod (see Settings)."""

    async def validate(self, token: str) -> UserContext:
        parts = token.split(":")
        if len(parts) != 3 or parts[0] != "dev":
            raise AuthenticationError("Invalid dev token")
        roles = frozenset(r for r in parts[2].split(",") if r)
        return UserContext(parts[1], "dev-tenant", roles)
