"""record_and_apply on real Postgres: atomicity and concurrent appends."""

from __future__ import annotations

import threading

import psycopg
import pytest

from voiceledger.pg import apply_schema, load_events, record_and_apply
from voiceledger.verify import verify

CHAIN = "h1:2026-10-16"


@pytest.fixture
def ledger(pg):
    apply_schema(pg)
    pg.execute("CREATE TABLE counter (id int PRIMARY KEY, n int NOT NULL)")
    pg.execute("INSERT INTO counter VALUES (1, 0)")
    return pg


def bump(conn):
    conn.execute("UPDATE counter SET n = n + 1 WHERE id = 1")


def test_event_and_state_change_commit_together(ledger):
    for i in range(5):
        record_and_apply(ledger, CHAIN, f"t{i}", "slot_freed", "system", apply=bump)
    assert ledger.execute("SELECT n FROM counter").fetchone()[0] == 5
    events = load_events(ledger, CHAIN)
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert verify(events).checks["chain"].status == "pass"


def test_crash_in_apply_rolls_back_the_event_too(ledger):
    record_and_apply(ledger, CHAIN, "t0", "slot_freed", "system", apply=bump)

    def crash(conn):
        bump(conn)
        raise RuntimeError("process died mid-write")

    with pytest.raises(RuntimeError):
        record_and_apply(ledger, CHAIN, "t1", "slot_filled", "system", apply=crash)
    assert ledger.execute("SELECT n FROM counter").fetchone()[0] == 1
    assert len(load_events(ledger, CHAIN)) == 1


def test_concurrent_appends_stay_linked(ledger, pg_url):
    schema = ledger.execute("SELECT current_schema()").fetchone()[0]
    errors: list[Exception] = []

    def worker(k):
        try:
            with psycopg.connect(pg_url, autocommit=True) as c:
                c.execute(f'SET search_path TO "{schema}"')
                for i in range(10):
                    record_and_apply(c, CHAIN, f"w{k}-{i}", "call_attempt", "agent", apply=bump)
        except Exception as e:  # surfaced below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    events = load_events(ledger, CHAIN)
    assert len(events) == 40
    assert verify(events).checks["chain"].status == "pass"
    assert ledger.execute("SELECT n FROM counter").fetchone()[0] == 40


def test_separate_days_do_not_share_a_chain(ledger):
    record_and_apply(ledger, "h1:2026-10-16", "t", "slot_freed", "system")
    record_and_apply(ledger, "h1:2026-10-17", "t", "slot_freed", "system")
    assert load_events(ledger, "h1:2026-10-17")[0].seq == 1
