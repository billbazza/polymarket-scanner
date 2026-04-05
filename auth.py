from __future__ import annotations

"""Route-level authorization helpers for the FastAPI server."""
from fastapi import Header, HTTPException, Request, status

import runtime_config

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", None}


def _configured_keys():
    raw = runtime_config.get("SCANNER_API_KEYS", "")
    if raw:
        keys = {}
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" in chunk:
                scope, key = chunk.split(":", 1)
            else:
                scope, key = "admin", chunk
            keys[key.strip()] = scope.strip()
        return keys

    fallback = runtime_config.get("SCANNER_API_KEY", "")
    return {fallback: "admin"} if fallback else {}


def _scope_allows(actual_scope: str, required_scope: str) -> bool:
    order = {"operator": 1, "admin": 2}
    return order.get(actual_scope, 0) >= order.get(required_scope, 999)


def _authorize(request: Request, provided_key: str | None, required_scope: str) -> None:
    allowed_cf_emails = {
        email.strip().lower()
        for email in runtime_config.get("SCANNER_CF_ACCESS_EMAILS", "").split(",")
        if email.strip()
    }
    cf_email = (request.headers.get("CF-Access-Authenticated-User-Email") or "").strip().lower()
    if cf_email and cf_email in allowed_cf_emails:
        return

    keys = _configured_keys()
    if not keys:
        if request.client and request.client.host in _LOCAL_HOSTS:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mutating routes are localhost-only until SCANNER_API_KEY(S) is configured.",
        )

    scope = keys.get((provided_key or "").strip())
    if scope and _scope_allows(scope, required_scope):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"{required_scope} API key required.",
        headers={"WWW-Authenticate": "X-API-Key"},
    )


async def require_operator(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _authorize(request, x_api_key, "operator")


async def require_admin(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _authorize(request, x_api_key, "admin")
