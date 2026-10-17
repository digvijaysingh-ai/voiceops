"""Declared deploy checks, as they appear in `[[deploy.checks]]`. Values may be "env:NAME"."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionSpec(_Strict):
    """How to open a test session on the project's API and read the call token from it."""

    path: str
    body: dict[str, Any] = {}
    token_field: str = "call_token"
    call_id_field: str = "call_id"


class Health(_Strict):
    check: Literal["health"]
    path: str = "/health"
    expect: dict[str, Any] = {}  # JSON fields that must equal these values
    at_least: dict[str, float] = {}  # JSON fields that must be >= these values


class UnsignedRefused(_Strict):
    check: Literal["unsigned_refused"]
    tool: str  # a tool called without the tool secret must get 401


class SignedRoundtrip(_Strict):
    check: Literal["signed_roundtrip"]
    tool: str
    session: SessionSpec
    body: dict[str, Any] = {}
    expect: dict[str, Any] = {}  # JSON fields the tool response must have
    samples: int = 20  # repeated calls; their latencies feed the p95 check


class LedgerVerifies(_Strict):
    check: Literal["ledger_verifies"]
    path: str = "/api/verify"
    bundle: Path
    trusted_key: Path  # file holding the hex key


class Pages(_Strict):
    check: Literal["pages"]
    paths: list[str]


class P95(_Strict):
    check: Literal["p95"]
    budget_ms: float


Check = Annotated[
    Health | UnsignedRefused | SignedRoundtrip | LedgerVerifies | Pages | P95,
    Field(discriminator="check"),
]
