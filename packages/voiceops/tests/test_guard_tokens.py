"""Per-call tokens on real Postgres: the default table and a project's own calls table."""

from __future__ import annotations

from datetime import datetime

import pytest

from voiceops.guard import (
    BindRefused,
    NotVerified,
    PgTokenStore,
    UnknownToken,
    require_subject,
)

NOW = datetime(2026, 3, 2, 9, 0)


@pytest.fixture
def store(pg):
    s = PgTokenStore()
    s.apply_schema(pg)
    return s


def test_unbound_token_binds_on_verification(store, pg):
    call_id, token = store.issue(pg, NOW)
    tok = store.resolve(pg, token)
    assert (tok.call_id, tok.subject, tok.verified) == (call_id, None, False)
    with pytest.raises(NotVerified):
        require_subject(tok)
    store.bind(pg, call_id, "ACC-1")
    assert require_subject(store.resolve(pg, token)) == "ACC-1"


def test_bound_token_still_needs_verification(store, pg):
    call_id, token = store.issue(pg, NOW, subject="ACC-1")
    with pytest.raises(NotVerified):
        require_subject(store.resolve(pg, token))
    store.bind(pg, call_id, "ACC-1")
    assert require_subject(store.resolve(pg, token)) == "ACC-1"


def test_bound_token_cannot_be_rebound_to_someone_else(store, pg):
    call_id, token = store.issue(pg, NOW, subject="ACC-1")
    with pytest.raises(BindRefused):
        store.bind(pg, call_id, "ACC-2")
    tok = store.resolve(pg, token)
    assert (tok.subject, tok.verified) == ("ACC-1", False)


def test_unknown_and_ended_tokens_do_not_resolve(store, pg):
    with pytest.raises(UnknownToken) as e:
        store.resolve(pg, "not-a-token")
    assert e.value.code == "unauthorized"
    call_id, token = store.issue(pg, NOW)
    store.end(pg, call_id, NOW)
    with pytest.raises(UnknownToken):
        store.resolve(pg, token)


def test_bind_to_missing_call_is_refused(store, pg):
    with pytest.raises(BindRefused):
        store.bind(pg, "call_nope", "ACC-1")


def test_project_table_with_its_own_columns(pg):
    pg.execute(
        "CREATE TABLE calls (call_id text PRIMARY KEY, token text UNIQUE, account text,"
        " verified boolean NOT NULL DEFAULT false, finished_at timestamp, purpose text)"
    )
    pg.execute("INSERT INTO calls VALUES ('c1', 't1', NULL, false, NULL, 'billing')")
    store = PgTokenStore("calls", subject="account", ended_at="finished_at", extra=("purpose",))
    tok = store.resolve(pg, "t1")
    assert tok.extra == {"purpose": "billing"} and tok.subject is None
    store.bind(pg, "c1", "ACC-9")
    assert require_subject(store.resolve(pg, "t1")) == "ACC-9"
