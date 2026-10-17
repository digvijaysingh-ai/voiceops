"""Guard: secrets, webhook signatures, rate limits, safe logs, FastAPI dependencies."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from voiceops.guard import RateLimiter, SafeLog, secret_matches, sign_webhook, verify_webhook
from voiceops.guard.fastapi import ToolGuard, rate_limited, signed_webhook

SECRET, HOOK, NOW = "tool-secret", "hook-secret", 1_800_000_000


def test_secret_matches_refuses_empty_on_either_side():
    assert secret_matches("abc", "abc")
    assert not secret_matches("abc", "abd")
    assert not secret_matches("", "")
    assert not secret_matches(None, "abc")
    assert not secret_matches("abc", "")  # an unset secret never accepts anything
    assert not secret_matches("ñ", "abc")  # non-ASCII input is refused, not a crash


@pytest.mark.parametrize(
    "header_t, secret, body, ok",
    [
        (NOW, HOOK, b'{"a":1}', True),
        (NOW - 29 * 60, HOOK, b'{"a":1}', True),
        (NOW - 31 * 60, HOOK, b'{"a":1}', False),  # stale: replay window closed
        (NOW + 31 * 60, HOOK, b'{"a":1}', False),  # from the future
        (NOW, "other", b'{"a":1}', False),
        (NOW, HOOK, b'{"a":2}', False),  # body changed after signing
    ],
)
def test_webhook_signature(header_t, secret, body, ok):
    header = sign_webhook(secret, b'{"a":1}', header_t)
    assert verify_webhook(HOOK, header, body, NOW) is ok


@pytest.mark.parametrize("header", [None, "", "garbage", "t=abc,v0=00", "v0=00"])
def test_malformed_signature_headers_are_refused(header):
    assert not verify_webhook(HOOK, header, b"{}", NOW)


def test_webhook_without_configured_secret_refuses():
    assert not verify_webhook("", sign_webhook("", b"{}", NOW), b"{}", NOW)


def test_rate_limiter_sliding_window():
    t = [0.0]
    limiter = RateLimiter(per_minute=2, clock=lambda: t[0])
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")  # per key
    t[0] = 61.0
    assert limiter.allow("a")


def test_safe_log_keeps_only_allowed_fields(caplog):
    log = SafeLog("voiceops.test", extra_fields={"pid"})
    with caplog.at_level(logging.INFO, logger="voiceops.test"):
        log(event="tool", call_id="c1", pid="P-1", name="Ana", dob="1950-01-01", status=None)
    assert json.loads(caplog.records[0].message) == {"event": "tool", "call_id": "c1", "pid": "P-1"}


class Body(BaseModel):
    amount: int


@pytest.fixture
def client():
    log = SafeLog("voiceops.test")
    guard = ToolGuard(SECRET, log, limiter=RateLimiter(per_minute=3))
    app = FastAPI()

    @app.post("/tools/pay")
    def pay(body: Body, token: str = Depends(guard.tool_call)):
        return {"token": token, "amount": body.amount}

    @app.post("/hooks/sweep", dependencies=[Depends(guard.require_secret)])
    def sweep():
        return {"ok": True}

    @app.post("/hooks/post-call")
    async def post_call(raw: bytes = Depends(signed_webhook(HOOK, log, clock=lambda: NOW))):
        return json.loads(raw)

    @app.post("/upload", dependencies=[Depends(rate_limited(RateLimiter(per_minute=1)))])
    def upload():
        return {"ok": True}

    return TestClient(app)


def call(client, secret=SECRET, token="tok", body=None):
    headers = {k: v for k, v in {"X-Tool-Secret": secret, "X-Call-Token": token}.items() if v}
    return client.post("/tools/pay", json=body or {"amount": 5}, headers=headers)


def test_tool_call_happy_path(client):
    r = call(client)
    assert r.status_code == 200 and r.json() == {"token": "tok", "amount": 5}


def test_tool_call_refusals(client):
    assert call(client, secret="wrong").json() == {"detail": "missing or invalid tool secret"}
    assert call(client, secret=None).status_code == 401
    r = call(client, token=None)
    assert (r.status_code, r.json()["detail"]) == (401, "missing call token")


def test_unauthenticated_bad_body_gets_401_not_a_schema_error(client):
    assert call(client, secret="wrong", body={"amount": "lots"}).status_code == 401
    assert call(client, body={"amount": "lots"}).status_code == 422  # authenticated: 422


def test_tool_rate_limit(client):
    assert [call(client).status_code for _ in range(4)] == [200, 200, 200, 429]


def test_secret_only_endpoint(client):
    assert client.post("/hooks/sweep").status_code == 401
    assert client.post("/hooks/sweep", headers={"X-Tool-Secret": SECRET}).status_code == 200


def test_signed_webhook(client):
    raw = b'{"type":"post_call"}'
    good = {"ElevenLabs-Signature": sign_webhook(HOOK, raw, NOW)}
    assert client.post("/hooks/post-call", content=raw, headers=good).json() == {
        "type": "post_call"
    }
    bad = {"ElevenLabs-Signature": sign_webhook(HOOK, raw, NOW - 3600)}
    r = client.post("/hooks/post-call", content=raw, headers=bad)
    assert (r.status_code, r.json()["detail"]) == (401, "bad signature")


def test_rate_limited_dependency(client):
    assert client.post("/upload").status_code == 200
    assert client.post("/upload").status_code == 429
