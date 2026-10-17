"""Replay a ledger and prove its invariants.

Always-on checks (each reports pass | fail | not_checked, plus the first failing seq):

  chain   seq contiguous from 1, one chain_id, every prev_hash links, every hash recomputes.
  head    (needs a signed head) Ed25519 signature valid, head matches the last event;
          key matches the trusted key if one is given.

Everything in between comes from the project's rules (see voiceledger.rules), reported
under each rule's name, in the order given.
"""

from __future__ import annotations

from collections.abc import Sequence

from voiceledger.chain import GENESIS, Event, event_hash
from voiceledger.result import Check, Report
from voiceledger.rules import Rule, check_names
from voiceledger.sign import SignedHead, verify_head


def check_chain(events: list[Event]) -> Check:
    c = Check()
    if not events:
        c.status, c.detail = "not_checked", "empty ledger"
        return c
    chain_id = events[0].chain_id
    prev = GENESIS
    for i, ev in enumerate(events, start=1):
        if ev.chain_id != chain_id:
            c.fail(ev.seq, f"chain_id changed to {ev.chain_id}")
        elif ev.seq != i:
            c.fail(ev.seq, f"expected seq {i}")
        elif ev.prev_hash != prev:
            c.fail(ev.seq, "prev_hash does not link to the previous event")
        elif event_hash(ev.body()) != ev.hash:
            c.fail(ev.seq, "event was changed after it was recorded")
        prev = ev.hash
    if c.status == "pass":
        c.detail = f"{len(events)} events linked"
    return c


def verify(
    events: list[Event],
    snapshots: dict[str, list[str]] | None = None,
    head: SignedHead | None = None,
    trusted_public_key: str | None = None,
    rules: Sequence[Rule] = (),
) -> Report:
    check_names(rules)
    report = Report(events=len(events))
    report.checks["chain"] = check_chain(events)
    for rule in rules:
        report.checks[rule.name] = rule.check(events, snapshots)
    report.checks["head"] = verify_head(head, events[-1] if events else None, trusted_public_key)
    return report
