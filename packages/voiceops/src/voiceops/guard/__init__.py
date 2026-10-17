"""Tool secret, per-call tokens bound to one subject, webhook signatures, rate limits and
safe logs (PLAN §2, §4.4). FastAPI dependencies live in voiceops.guard.fastapi."""

from voiceops.guard.logs import SAFE_FIELDS, SafeLog
from voiceops.guard.ratelimit import RateLimiter
from voiceops.guard.secrets import new_secret, secret_matches, sign_webhook, verify_webhook
from voiceops.guard.tokens import (
    BindRefused,
    CallToken,
    NotVerified,
    PgTokenStore,
    TokenError,
    UnknownToken,
    new_call_id,
    new_token,
    require_subject,
)

__all__ = [
    "SAFE_FIELDS",
    "BindRefused",
    "CallToken",
    "NotVerified",
    "PgTokenStore",
    "RateLimiter",
    "SafeLog",
    "TokenError",
    "UnknownToken",
    "new_call_id",
    "new_secret",
    "new_token",
    "require_subject",
    "secret_matches",
    "sign_webhook",
    "verify_webhook",
]
