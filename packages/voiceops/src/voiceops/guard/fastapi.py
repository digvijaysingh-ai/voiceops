"""FastAPI dependencies for the guard (optional extra: voiceops[fastapi]).

    @app.post("/tools/get_balance")
    def get_balance(body: Empty, token: str = Depends(guard.tool_call)): ...

    @app.post("/hooks/post-call")
    async def post_call(raw: bytes = Depends(signed)): ...

As dependencies, the checks run before the request body is validated, so an
unauthenticated caller gets 401, never a schema error.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import HTTPException, Request

from voiceops.guard.logs import SafeLog
from voiceops.guard.ratelimit import RateLimiter
from voiceops.guard.secrets import SIGNATURE_TOLERANCE_S, secret_matches, verify_webhook

DEFAULT_TOOL_LIMIT_PER_MINUTE = 120


def client_key(request: Request) -> str:
    return request.client.host if request.client else "?"


def rate_limited(limiter: RateLimiter) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        if not limiter.allow(client_key(request)):
            raise HTTPException(429, "too many requests")

    return dependency


class ToolGuard:
    """Tool requests: shared tool secret, then a per-client rate limit, then a call token."""

    def __init__(
        self,
        tool_secret: str,
        log: SafeLog,
        *,
        secret_header: str = "X-Tool-Secret",
        token_header: str = "X-Call-Token",
        limiter: RateLimiter | None = None,
    ):
        self.tool_secret = tool_secret
        self.log = log
        self.secret_header, self.token_header = secret_header, token_header
        self.limiter = limiter or RateLimiter(DEFAULT_TOOL_LIMIT_PER_MINUTE)

    def require_secret(self, request: Request) -> None:
        if not secret_matches(request.headers.get(self.secret_header), self.tool_secret):
            self.log(event="tool_rejected", status=401)
            raise HTTPException(401, "missing or invalid tool secret")

    def tool_call(self, request: Request) -> str:
        """Dependency for tool endpoints. Returns the call token."""
        self.require_secret(request)
        if not self.limiter.allow(client_key(request)):
            raise HTTPException(429, "too many requests")
        token = request.headers.get(self.token_header)
        if not token:
            raise HTTPException(401, "missing call token")
        return token


def signed_webhook(
    secret: str,
    log: SafeLog,
    header: str = "ElevenLabs-Signature",
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
    clock: Callable[[], float] = time.time,
):
    """Dependency that returns the raw body only if its HMAC signature is valid and fresh."""

    async def dependency(request: Request) -> bytes:
        raw = await request.body()
        if not verify_webhook(secret, request.headers.get(header), raw, clock(), tolerance_s):
            log(event="webhook_rejected", status=401)
            raise HTTPException(401, "bad signature")
        return raw

    return dependency
