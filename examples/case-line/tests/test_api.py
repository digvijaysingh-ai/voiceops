"""HTTP layer: tool auth, token binding, signed webhooks."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from voiceops.guard import sign_webhook

from app.api import Settings, create_app
from app.service import SUBJECTS

SECRET, HOOK, CODE = "tool-secret", "hook-secret", "letmein"


@pytest.fixture
def client():
    return TestClient(create_app(Settings(SECRET, HOOK, CODE)))


def tool(client, name, token, body=None, secret=SECRET):
    headers = {"X-Tool-Secret": secret, "X-Call-Token": token}
    return client.post(f"/tools/{name}", json=body or {}, headers=headers)


def session(client, purpose="inbound"):
    r = client.post("/api/sessions", json={"access_code": CODE, "purpose": purpose})
    return r.json()["call_token"]


def test_tools_refuse_a_missing_or_wrong_secret(client):
    assert tool(client, "get_case", "x", secret="nope").status_code == 401
    assert client.post("/tools/get_case", json={}).status_code == 401


def test_sessions_need_the_access_code(client):
    assert client.post("/api/sessions", json={"access_code": "wrong"}).status_code == 403


def test_unbound_session_binds_on_verification(client):
    token = session(client)
    assert tool(client, "get_case", token).json()["error"] == "identity_not_verified"
    s = SUBJECTS[0]
    body = {"date_of_birth": s.date_of_birth, "reference_last4": s.last4}
    assert tool(client, "verify_identity", token, body).json()["status"] == "verified"
    assert tool(client, "get_case", token).json()["case_status"] == s.case_status


def test_unknown_token_is_unauthorized_not_a_crash(client):
    r = tool(client, "get_case", "not-a-token")
    assert r.status_code == 200 and r.json()["error"] == "unauthorized"


def test_post_call_webhook_needs_a_fresh_signature(client):
    raw = b'{"type": "post_call_transcription"}'
    good = sign_webhook(HOOK, raw, int(time.time()))
    stale = sign_webhook(HOOK, raw, int(time.time()) - 3600)

    def post(sig):
        return client.post("/hooks/post-call", content=raw, headers={"ElevenLabs-Signature": sig})

    assert post(good).status_code == 200
    assert post(stale).status_code == 401
