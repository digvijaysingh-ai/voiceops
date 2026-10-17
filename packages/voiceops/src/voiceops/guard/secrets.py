"""Shared-secret checks and webhook signatures. Constant-time comparisons only."""

from __future__ import annotations

import hashlib
import hmac
import secrets

SIGNATURE_TOLERANCE_S = 30 * 60


def secret_matches(given: str | None, expected: str | None) -> bool:
    """False when either side is empty: an unset secret never accepts anything."""
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def sign_webhook(secret: str, body: bytes, t: int) -> str:
    """The header a platform sends: t=<unix>,v0=<hex hmac_sha256(secret, f"{t}.{body}")>."""
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v0={sig}"


def verify_webhook(
    secret: str,
    header: str | None,
    body: bytes,
    now: float,
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
) -> bool:
    """Checks an ElevenLabs-style signature header; refuses stale or future timestamps."""
    if not secret or not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        t = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(now - t) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v0", ""))
