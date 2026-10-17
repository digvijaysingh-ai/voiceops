"""Service layer: everything the agent's tools call. Safeguards are enforced here, not in prompts.

- Tools act only on the subject bound to the call token. Inbound calls start unbound and
  bind on a successful verify_identity; outbound calls are bound to who was called.
- Nothing about a case is returned before identity is verified, and every disclosure is
  written to the ledger (identity before detail, proven by `voiceledger verify`).
- Two failed identity checks end the call.

The store is in memory so the project runs from scratch; swap in Postgres
(voiceops.guard.PgTokenStore, voiceledger.pg) when it goes live.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

from voiceledger import Event, append
from voiceops.guard import MemoryTokenStore, NotVerified, UnknownToken, require_subject

MAX_FAILURES_PER_CALL = 2


@dataclass(frozen=True)
class Subject:
    subject_id: str
    date_of_birth: str
    reference: str
    case_status: str
    next_step: str

    @property
    def last4(self) -> str:
        """What the caller is asked for: the last four characters, ignoring dashes."""
        return self.reference.replace("-", "")[-4:]


# Synthetic account holders (fictional; no real people).
SUBJECTS = [
    Subject("ACC-0001", "1961-04-12", "NW-48213-QX", "open", "an adviser reviews it this week"),
    Subject("ACC-0002", "1975-09-30", "NW-77120-LB", "waiting for documents", "send your form"),
    Subject("ACC-0003", "1988-01-05", "NW-30981-TT", "closed", "nothing more is needed"),
]


class ServiceError(Exception):
    code = "error"


class Unauthorized(ServiceError):
    code = "unauthorized"


class IdentityNotVerified(ServiceError):
    code = "identity_not_verified"


class Service:
    def __init__(self, subjects: list[Subject] = SUBJECTS, chain_id: str = "case-line:demo"):
        self.subjects = {s.subject_id: s for s in subjects}
        self.tokens = MemoryTokenStore()
        self.events: list[Event] = []
        self.failures: dict[str, int] = {}
        self.chain_id = chain_id
        self._lock = threading.Lock()

    def _event(self, now: datetime, type: str, payload: dict, actor: str = "agent") -> None:
        append(self.events, now.isoformat(), type, actor, chain_id=self.chain_id, payload=payload)

    def _resolve(self, token: str):
        try:
            return self.tokens.resolve(None, token)
        except UnknownToken as e:
            raise Unauthorized("unknown or finished call") from e

    def start_call(self, now: datetime, subject_id: str | None = None) -> tuple[str, str]:
        with self._lock:
            return self.tokens.issue(None, now, subject_id)

    def record_disclosure(self, token: str, now: datetime) -> dict:
        with self._lock:
            tok = self._resolve(token)
            self._event(now, "ai_disclosed", {"call_id": tok.call_id})
            return {"status": "recorded"}

    def verify_identity(
        self, token: str, date_of_birth: str, reference_last4: str, now: datetime
    ) -> dict:
        with self._lock:
            tok = self._resolve(token)
            if tok.verified:
                return {"status": "verified"}
            last4 = reference_last4.strip().upper()
            candidates = [tok.subject] if tok.subject else list(self.subjects)
            match = [
                sid
                for sid in candidates
                if self.subjects[sid].date_of_birth == date_of_birth.strip()
                and self.subjects[sid].last4 == last4
            ]
            if len(match) != 1:
                n = self.failures[tok.call_id] = self.failures.get(tok.call_id, 0) + 1
                self._event(now, "identity_failed", {"call_id": tok.call_id, "failures": n})
                if n >= MAX_FAILURES_PER_CALL:
                    self.tokens.end(None, tok.call_id, now)
                    return {"status": "end_call"}
                return {"status": "failed", "attempts_left": MAX_FAILURES_PER_CALL - n}
            self.tokens.bind(None, tok.call_id, match[0])
            self._event(now, "identity_verified", {"call_id": tok.call_id, "subject": match[0]})
            return {"status": "verified"}

    def get_case(self, token: str, now: datetime) -> dict:
        with self._lock:
            tok = self._resolve(token)
            try:
                sid = require_subject(tok)
            except NotVerified as e:
                raise IdentityNotVerified("verify the caller's identity first") from e
            s = self.subjects[sid]
            self._event(
                now, "detail_disclosed", {"call_id": tok.call_id, "fields": ["case_status"]}
            )
            return {"case_status": s.case_status, "next_step": s.next_step}

    def transfer_to_human(self, token: str, reason: str, now: datetime) -> dict:
        with self._lock:
            tok = self._resolve(token)
            self._event(now, "handoff_to_human", {"call_id": tok.call_id, "reason": reason[:200]})
            # No live transfer in the template: a person calls back. Never claim a transfer.
            return {"status": "callback_requested"}

    def log_outcome(self, token: str, outcome: str, now: datetime) -> dict:
        with self._lock:
            tok = self._resolve(token)
            self._event(now, "outcome_logged", {"call_id": tok.call_id, "outcome": outcome})
            return {"status": "logged"}

    def call(self, name: str, token: str, body: dict, now: datetime) -> dict:
        """Dispatch a tool by name (the API and the evals use the same path)."""
        handlers = {
            "record_disclosure": lambda: self.record_disclosure(token, now),
            "verify_identity": lambda: self.verify_identity(token, **body, now=now),
            "get_case": lambda: self.get_case(token, now),
            "transfer_to_human": lambda: self.transfer_to_human(token, body["reason"], now),
            "log_outcome": lambda: self.log_outcome(token, body["outcome"], now),
        }
        return handlers[name]()
