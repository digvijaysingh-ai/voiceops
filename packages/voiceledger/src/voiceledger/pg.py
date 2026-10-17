"""Postgres storage for the ledger (optional extra: `voiceledger[postgres]`).

record_and_apply() writes a state change and its ledger event in ONE transaction:

    BEGIN
      SELECT ... FROM ledger_heads WHERE chain_id = $1 FOR UPDATE   -- only this chain waits
      apply(conn)                                                  -- caller's state change
      INSERT INTO ledger_events (seq = head.seq + 1, prev_hash = head.hash, ...)
      UPDATE ledger_heads SET seq, hash
    COMMIT

A crash anywhere rolls back both, so the ledger and the state can never disagree.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection

from voiceledger.chain import GENESIS, Event, seal, snapshot_hash

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger_heads (
    chain_id  text PRIMARY KEY,
    seq       integer NOT NULL,
    hash      text NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_events (
    chain_id   text NOT NULL,
    seq        integer NOT NULL,
    ts         text NOT NULL,
    type       text NOT NULL,
    actor      text NOT NULL,
    slot_id    text,
    payload    jsonb NOT NULL,
    prev_hash  text NOT NULL,
    hash       text NOT NULL,
    PRIMARY KEY (chain_id, seq)
);
CREATE INDEX IF NOT EXISTS ledger_events_slot ON ledger_events (chain_id, slot_id);
CREATE TABLE IF NOT EXISTS list_snapshots (
    chain_id       text NOT NULL,
    snapshot_id    text NOT NULL,
    ordered_pids   jsonb NOT NULL,
    snapshot_hash  text NOT NULL,
    PRIMARY KEY (chain_id, snapshot_id)
);
CREATE TABLE IF NOT EXISTS ledger_signed_heads (
    chain_id   text NOT NULL,
    seq        integer NOT NULL,
    head       jsonb NOT NULL,
    PRIMARY KEY (chain_id, seq)
);
"""


def apply_schema(conn) -> None:
    conn.execute(SCHEMA_SQL)


def record_and_apply(
    conn,
    chain_id: str,
    ts: str,
    type: str,
    actor: str,
    payload: dict | None = None,
    slot_id: str | None = None,
    apply: Callable[[object], None] | None = None,
    types: Collection[str] | None = None,
) -> Event:
    """Append one event and run `apply(conn)` atomically. Raises whatever apply raises.

    `types`, when given, is the project's allow-list of event types (see chain.seal).
    """
    with conn.transaction():
        conn.execute(
            "INSERT INTO ledger_heads (chain_id, seq, hash) VALUES (%s, 0, %s)"
            " ON CONFLICT (chain_id) DO NOTHING",
            (chain_id, GENESIS),
        )
        seq, prev_hash = conn.execute(
            "SELECT seq, hash FROM ledger_heads WHERE chain_id = %s FOR UPDATE", (chain_id,)
        ).fetchone()
        if apply is not None:
            apply(conn)
        ev = seal(chain_id, seq + 1, ts, type, actor, payload, slot_id, prev_hash, types)
        conn.execute(
            "INSERT INTO ledger_events (chain_id, seq, ts, type, actor, slot_id, payload,"
            " prev_hash, hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                ev.chain_id,
                ev.seq,
                ev.ts,
                ev.type,
                ev.actor,
                ev.slot_id,
                json.dumps(ev.payload),
                ev.prev_hash,
                ev.hash,
            ),
        )
        conn.execute(
            "UPDATE ledger_heads SET seq = %s, hash = %s WHERE chain_id = %s",
            (ev.seq, ev.hash, chain_id),
        )
    return ev


def save_snapshot(conn, chain_id: str, snapshot_id: str, ordered_pids: list[str]) -> str:
    h = snapshot_hash(ordered_pids)
    conn.execute(
        "INSERT INTO list_snapshots (chain_id, snapshot_id, ordered_pids, snapshot_hash)"
        " VALUES (%s, %s, %s, %s)",
        (chain_id, snapshot_id, json.dumps(ordered_pids), h),
    )
    return h


def load_events(conn, chain_id: str) -> list[Event]:
    rows = conn.execute(
        "SELECT chain_id, seq, ts, type, actor, payload, slot_id, prev_hash, hash"
        " FROM ledger_events WHERE chain_id = %s ORDER BY seq",
        (chain_id,),
    ).fetchall()
    return [Event(*r) for r in rows]


def load_snapshots(conn, chain_id: str) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT snapshot_id, ordered_pids FROM list_snapshots WHERE chain_id = %s", (chain_id,)
    ).fetchall()
    return {sid: list(pids) for sid, pids in rows}
