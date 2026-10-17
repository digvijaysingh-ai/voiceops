"""HTTP layer: the agent's webhook tools, a signed post-call webhook, demo sessions.

    agent ──POST /tools/<name>──▶ tool secret ─▶ rate limit ─▶ call token ─▶ service
    platform ──POST /hooks/post-call (HMAC signature)──▶ logged
    browser / test call ──POST /api/sessions (access code)──▶ call token

Logs are JSON with IDs and outcomes only (voiceops.guard.SafeLog).

    uv run uvicorn app.api:app --port 8000

No `from __future__ import annotations` here: FastAPI must see the real request model
classes on the generated tool endpoints, not strings.
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from voiceops.guard import RateLimiter, SafeLog, secret_matches
from voiceops.guard.fastapi import ToolGuard, rate_limited, signed_webhook

from app.models import TOOLS
from app.service import Service, ServiceError

log = SafeLog("case-line")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class Settings:
    tool_secret: str
    webhook_secret: str = ""
    access_code: str = ""
    clock: Callable[[], datetime] = utcnow
    service: Service = field(default_factory=Service)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            tool_secret=os.environ["TOOL_SECRET"],
            webhook_secret=os.environ.get("WEBHOOK_SECRET", ""),
            access_code=os.environ.get("DEMO_ACCESS_CODE", ""),
        )


class SessionRequest(BaseModel):
    access_code: str
    purpose: str = "inbound"  # "inbound" (unbound, widget) | "outbound" (test call)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    svc = settings.service
    guard = ToolGuard(settings.tool_secret, log)
    app = FastAPI(title="case-line")

    for tool_name, (model, _description) in TOOLS.items():

        def make(name=tool_name, Model=model):
            def endpoint(body: Model, token: str = Depends(guard.tool_call)):  # type: ignore[valid-type]
                started = time.perf_counter()
                try:
                    result = svc.call(name, token, body.model_dump(), settings.clock())
                    payload = {"ok": True, **result}
                except ServiceError as e:
                    payload = {"ok": False, "error": e.code, "message": str(e)}
                ms = round((time.perf_counter() - started) * 1000, 1)
                log(event="tool", tool=name, latency_ms=ms, status=payload.get("error", "ok"))
                return payload

            endpoint.__name__ = f"tool_{name}"
            app.post(f"/tools/{name}", tags=["tools"])(endpoint)

        make()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/hooks/post-call")
    async def post_call(raw: bytes = Depends(signed_webhook(settings.webhook_secret, log))):
        event = json.loads(raw)
        log(event="post_call", status=event.get("type"))
        return {"ok": True}

    @app.post("/api/sessions", dependencies=[Depends(rate_limited(RateLimiter(per_minute=5)))])
    def new_session(req: SessionRequest):
        if not secret_matches(req.access_code, settings.access_code):
            raise HTTPException(403, "wrong access code")
        if req.purpose not in ("inbound", "outbound"):
            raise HTTPException(400, "purpose is inbound or outbound")
        # A test call is bound to the first synthetic subject; the caller still has to
        # pass verify_identity as that subject.
        subject = next(iter(svc.subjects)) if req.purpose == "outbound" else None
        call_id, token = svc.start_call(settings.clock(), subject)
        return {"call_id": call_id, "call_token": token}

    return app


def __getattr__(name: str):  # `uvicorn app.api:app` builds the app from the environment
    if name == "app":
        return create_app()
    raise AttributeError(name)
