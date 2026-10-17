"""Property tests for the ledger verifier (pure, no database)."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import voiceledger
from voiceledger import append, load_rules_file, read_bundle, sign_head, snapshot_hash, write_bundle
from voiceledger.bundle import BundleInvalid
from voiceledger.chain import Event
from voiceledger.cli import main as _cli
from voiceledger.sign import SigningKeyMissing, new_private_key_hex, public_key_hex

# Ported from Slotback's ledger tests. Test bodies are unchanged; Slotback's two
# domain checks are supplied as configured rules instead of being built in.
RULES_FILE = Path(__file__).parent / "fixtures" / "slotback_rules.toml"
RULES = load_rules_file(RULES_FILE)


def verify(*args):
    return voiceledger.verify(*args, rules=RULES)


def cli(argv):
    return _cli([*argv, "--rules", str(RULES_FILE)])


CHAIN = "h1:2026-10-16"
CLOSERS = ["no_answer", "voicemail", "busy", "wrong_number", "declined", "expired", "skipped"]
KEY = new_private_key_hex()


def backfill(pids: list[str], closers: list[str], accept: bool, slot="S-CAR-1-0600"):
    """A valid backfill: ranks 1..k closed with logged reasons, then accept or run out."""
    ev: list[Event] = []
    t = iter(f"2026-10-16T10:{i // 60:02d}:{i % 60:02d}" for i in range(10_000))
    append(
        ev,
        next(t),
        "slot_freed",
        "system",
        chain_id=CHAIN,
        slot_id=slot,
        payload={"reason": "cancellation"},
    )
    append(
        ev,
        next(t),
        "list_snapshot",
        "system",
        slot_id=slot,
        payload={"snapshot_id": "snap-1", "snapshot_hash": snapshot_hash(pids)},
    )
    for rank, closer in enumerate(closers, start=1):
        pid, call = pids[rank - 1], f"c{rank}"
        base = {"pid": pid, "rank": rank, "call_id": call}
        if closer in ("no_answer", "voicemail", "busy", "wrong_number"):
            append(
                ev,
                next(t),
                "call_attempt",
                "agent",
                slot_id=slot,
                payload={**base, "outcome": closer},
            )
        elif closer == "skipped":
            append(
                ev,
                next(t),
                "rank_skipped",
                "system",
                slot_id=slot,
                payload={**base, "reason": "opted_out"},
            )
        else:
            append(
                ev,
                next(t),
                "call_attempt",
                "agent",
                slot_id=slot,
                payload={**base, "outcome": "answered"},
            )
            append(ev, next(t), "identity_verified", "agent", slot_id=slot, payload=base)
            append(
                ev,
                next(t),
                "detail_disclosed",
                "agent",
                slot_id=slot,
                payload={**base, "fields": ["specialty", "slot_time"]},
            )
            append(
                ev,
                next(t),
                "offer_started",
                "agent",
                slot_id=slot,
                payload={**base, "hold_until": "10:10"},
            )
            append(ev, next(t), f"offer_{closer}", "agent", slot_id=slot, payload=base)
    rank = len(closers) + 1
    if accept and rank <= len(pids):
        base = {"pid": pids[rank - 1], "rank": rank, "call_id": f"c{rank}"}
        append(
            ev,
            next(t),
            "call_attempt",
            "agent",
            slot_id=slot,
            payload={**base, "outcome": "answered"},
        )
        append(ev, next(t), "identity_verified", "agent", slot_id=slot, payload=base)
        append(
            ev,
            next(t),
            "detail_disclosed",
            "agent",
            slot_id=slot,
            payload={**base, "fields": ["specialty", "slot_time"]},
        )
        append(ev, next(t), "offer_started", "agent", slot_id=slot, payload=base)
        append(ev, next(t), "offer_accepted", "agent", slot_id=slot, payload=base)
        append(ev, next(t), "read_back", "agent", slot_id=slot, payload=base)
        append(ev, next(t), "slot_filled", "system", slot_id=slot, payload=base)
    else:
        append(
            ev,
            next(t),
            "slot_unfilled",
            "system",
            slot_id=slot,
            payload={"reason": "list exhausted"},
        )
    return ev, {"snap-1": pids}


scenarios = st.integers(min_value=2, max_value=8).flatmap(
    lambda n: st.tuples(
        st.just([f"P-{i:05d}" for i in range(1, n + 1)]),
        st.lists(st.sampled_from(CLOSERS), max_size=n - 1),
        st.booleans(),
    )
)


@settings(max_examples=200)
@given(scenarios)
def test_valid_backfills_always_verify(s):
    events, snaps = backfill(*s)
    head = sign_head(KEY, CHAIN, events[-1].seq, events[-1].hash, "2026-10-16T20:00:00")
    report = verify(events, snaps, head, public_key_hex(KEY))
    assert report.ok, report.to_dict()
    assert all(c.status == "pass" for c in report.checks.values())


@settings(max_examples=200)
@given(scenarios, st.data())
def test_any_tampered_event_fails_at_that_seq(s, data):
    events, snaps = backfill(*s)
    i = data.draw(st.integers(min_value=0, max_value=len(events) - 1))
    events[i] = replace(events[i], payload={**events[i].payload, "tampered": True})
    report = verify(events, snaps)
    assert report.checks["chain"].status == "fail"
    assert report.checks["chain"].seq == events[i].seq


@settings(max_examples=100)
@given(scenarios, st.data())
def test_deleting_an_event_breaks_the_chain(s, data):
    events, snaps = backfill(*s)
    i = data.draw(st.integers(min_value=0, max_value=len(events) - 2))
    del events[i]
    assert verify(events, snaps).checks["chain"].status == "fail"


def test_out_of_order_offer_fails_list_order():
    pids = ["P-00001", "P-00002", "P-00003"]
    events, snaps = backfill(pids, [], accept=False)
    events = events[:2]  # freed + snapshot
    # Skip rank 1 and call rank 2 directly.
    append(
        events,
        "2026-10-16T11:00:00",
        "call_attempt",
        "agent",
        slot_id="S-CAR-1-0600",
        payload={"pid": "P-00002", "rank": 2, "call_id": "x", "outcome": "answered"},
    )
    report = verify(events, snaps)
    assert report.checks["list_order"].status == "fail"
    assert report.checks["chain"].status == "pass"


def test_wrong_patient_at_rank_fails_list_order():
    pids = ["P-00001", "P-00002"]
    events, snaps = backfill(pids, [], accept=False)
    events = events[:2]
    append(
        events,
        "2026-10-16T11:00:00",
        "call_attempt",
        "agent",
        slot_id="S-CAR-1-0600",
        payload={"pid": "P-00002", "rank": 1, "call_id": "x", "outcome": "answered"},
    )
    assert verify(events, snaps).checks["list_order"].status == "fail"


def test_snapshot_swapped_after_the_fact_fails():
    events, snaps = backfill(["P-00001", "P-00002"], ["no_answer"], accept=True)
    snaps["snap-1"] = ["P-00002", "P-00001"]
    assert verify(events, snaps).checks["list_order"].status == "fail"


def test_detail_before_identity_fails():
    events, snaps = backfill(["P-00001"], [], accept=False)
    events = events[:2]
    append(
        events,
        "2026-10-16T11:00:00",
        "detail_disclosed",
        "agent",
        slot_id="S-CAR-1-0600",
        payload={"pid": "P-00001", "rank": 1, "call_id": "c1", "fields": ["specialty"]},
    )
    report = verify(events, snaps)
    assert report.checks["identity_before_detail"].status == "fail"


def test_late_accept_is_logged_without_breaking_order():
    events, snaps = backfill(["P-00001", "P-00002"], ["expired"], accept=True)
    append(
        events,
        "2026-10-16T12:00:00",
        "late_accept_rejected",
        "staff",
        slot_id="S-CAR-1-0600",
        payload={"pid": "P-00001", "rank": 1},
    )
    assert verify(events, snaps).ok


def test_ledger_only_reports_list_order_not_checked():
    events, _ = backfill(["P-00001", "P-00002"], ["no_answer"], accept=True)
    report = verify(events)
    assert report.ok
    assert report.checks["list_order"].status == "not_checked"
    assert report.checks["head"].status == "not_checked"


def test_head_with_untrusted_key_fails():
    events, snaps = backfill(["P-00001"], [], accept=True)
    other = new_private_key_hex()
    head = sign_head(other, CHAIN, events[-1].seq, events[-1].hash, "t")
    assert verify(events, snaps, head, public_key_hex(KEY)).checks["head"].status == "fail"


def test_ledger_extended_after_signing_fails_head():
    events, snaps = backfill(["P-00001"], [], accept=True)
    head = sign_head(KEY, CHAIN, events[-1].seq, events[-1].hash, "t")
    append(
        events,
        "2026-10-16T23:00:00",
        "attended",
        "staff",
        slot_id="S-CAR-1-0600",
        payload={"pid": "P-00001"},
    )
    assert verify(events, snaps, head, public_key_hex(KEY)).checks["head"].status == "fail"


def test_forged_signature_fails():
    events, snaps = backfill(["P-00001"], [], accept=True)
    head = sign_head(KEY, CHAIN, events[-1].seq, events[-1].hash, "t")
    forged = replace(head, signature="00" * 64)
    assert verify(events, snaps, forged).checks["head"].status == "fail"


def test_signing_without_key_refuses():
    with pytest.raises(SigningKeyMissing):
        sign_head(None, CHAIN, 1, "x", "t")


def test_bundle_round_trip_and_cli(tmp_path, capsys):
    events, snaps = backfill(["P-00001", "P-00002", "P-00003"], ["voicemail"], accept=True)
    head = sign_head(KEY, CHAIN, events[-1].seq, events[-1].hash, "2026-10-16T20:00:00")
    data = write_bundle(events, snaps, head)
    e2, s2, h2 = read_bundle(data)
    assert [asdict(e) for e in e2] == [asdict(e) for e in events]
    assert s2 == snaps and h2 == head
    path = tmp_path / "bundle.zip"
    path.write_bytes(data)
    assert cli(["verify", str(path), "--trusted-key", public_key_hex(KEY)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_bad_uploads_are_rejected_cleanly():
    with pytest.raises(BundleInvalid, match="not a zip"):
        read_bundle(b"<script>alert(1)</script>")
    with pytest.raises(BundleInvalid, match="5 MB"):
        read_bundle(b"0" * (5 * 1024 * 1024 + 1))
