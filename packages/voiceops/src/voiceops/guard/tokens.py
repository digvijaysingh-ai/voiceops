"""Per-call tokens: tools act only on the subject bound to the call's token.

    start a call ──▶ token (subject bound now, or NULL for inbound / widget sessions)
    tool request ──▶ resolve(token) ──▶ CallToken(call_id, subject, verified)
    identity verified ──▶ bind(call_id, subject)   unbound: binds; bound: must be the same

The agent never passes a subject ID: it only holds the token, so a jailbroken agent
can't point a tool at someone else. Tokens of ended calls no longer resolve.

PgTokenStore works on its own `call_tokens` table, or on a project's existing calls
table by naming the columns (and `extra` columns to return alongside).
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class TokenError(Exception):
    code = "token_error"


class UnknownToken(TokenError):
    code = "unauthorized"


class NotVerified(TokenError):
    code = "identity_not_verified"


class BindRefused(TokenError):
    code = "bind_refused"


@dataclass(frozen=True)
class CallToken:
    call_id: str
    subject: str | None
    verified: bool
    extra: dict[str, Any] = field(default_factory=dict)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def new_call_id() -> str:
    return f"call_{secrets.token_hex(8)}"


def require_subject(tok: CallToken) -> str:
    if not tok.verified or tok.subject is None:
        raise NotVerified("verify identity first")
    return tok.subject


DEFAULT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS call_tokens (
    call_id     text PRIMARY KEY,
    token       text NOT NULL UNIQUE,
    subject     text,                  -- NULL until bound (inbound / widget)
    verified    boolean NOT NULL DEFAULT false,
    started_at  timestamp NOT NULL,
    ended_at    timestamp
);
"""


class PgTokenStore:
    def __init__(
        self,
        table: str = "call_tokens",
        *,
        call_id: str = "call_id",
        token: str = "token",
        subject: str = "subject",
        verified: str = "verified",
        ended_at: str = "ended_at",
        extra: Sequence[str] = (),
    ):
        from psycopg import sql  # optional extra: voiceops[postgres]

        self._sql, ident = sql, sql.Identifier
        self.table = ident(table)
        self.cols = {
            k: ident(v)
            for k, v in dict(
                call_id=call_id, token=token, subject=subject, verified=verified, ended_at=ended_at
            ).items()
        }
        self.extra = tuple(extra)

    def apply_schema(self, conn) -> None:
        """Creates the default table. A project using its own table manages its schema."""
        conn.execute(DEFAULT_SCHEMA_SQL)

    def issue(self, conn, now: datetime, subject: str | None = None) -> tuple[str, str]:
        """Inserts a call into the store's table. Returns (call_id, token)."""
        call_id, token = new_call_id(), new_token()
        c = self.cols
        conn.execute(
            self._sql.SQL("INSERT INTO {} ({}, {}, {}, started_at) VALUES (%s, %s, %s, %s)").format(
                self.table, c["call_id"], c["token"], c["subject"]
            ),
            (call_id, token, subject, now),
        )
        return call_id, token

    def resolve(self, conn, token: str) -> CallToken:
        c, sql = self.cols, self._sql
        cols = [c["call_id"], c["subject"], c["verified"], *map(sql.Identifier, self.extra)]
        row = conn.execute(
            sql.SQL("SELECT {} FROM {} WHERE {} = %s AND {} IS NULL").format(
                sql.SQL(", ").join(cols), self.table, c["token"], c["ended_at"]
            ),
            (token,),
        ).fetchone()
        if row is None:
            raise UnknownToken("unknown or finished call")
        return CallToken(row[0], row[1], row[2], dict(zip(self.extra, row[3:], strict=True)))

    def bind(self, conn, call_id: str, subject: str) -> None:
        """Marks the call verified for `subject`. Refuses to rebind to a different subject."""
        c = self.cols
        cur = conn.execute(
            self._sql.SQL(
                "UPDATE {} SET {} = true, {} = %s WHERE {} = %s AND ({} IS NULL OR {} = %s)"
            ).format(
                self.table, c["verified"], c["subject"], c["call_id"], c["subject"], c["subject"]
            ),
            (subject, call_id, subject),
        )
        if cur.rowcount != 1:
            raise BindRefused(f"call {call_id} is bound to another subject or does not exist")

    def end(self, conn, call_id: str, now: datetime) -> None:
        c = self.cols
        conn.execute(
            self._sql.SQL("UPDATE {} SET {} = %s WHERE {} = %s").format(
                self.table, c["ended_at"], c["call_id"]
            ),
            (now, call_id),
        )
