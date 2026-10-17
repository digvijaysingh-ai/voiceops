"""Generic rules: each one passes a compliant ledger and fails a violating one at the right seq."""

from __future__ import annotations

import pytest

from voiceledger import (
    AlwaysFollowedBy,
    Matcher,
    Never,
    RequiresBefore,
    Rule,
    RuleConfigError,
    append,
    load_rules,
    register_rule,
    verify,
)
from voiceledger.chain import seal
from voiceledger.cli import main as cli
from voiceledger.result import Check

CHAIN = "acme:2026-03-02"


def ledger(*steps):
    """steps: (type, payload) or (type, payload, ts)."""
    ev = []
    for i, step in enumerate(steps):
        type_, payload = step[0], step[1]
        ts = step[2] if len(step) > 2 else f"2026-03-02T09:00:{i:02d}"
        append(ev, ts, type_, "agent", chain_id=CHAIN, payload=payload)
    return ev


# requires_before: no balance disclosed before the account holder is verified.
BALANCE = RequiresBefore("balance_disclosed", "identity_verified", scope=["call_id"])


def test_requires_before_passes_when_precondition_came_first():
    ev = ledger(
        ("identity_verified", {"call_id": "c1"}),
        ("balance_disclosed", {"call_id": "c1"}),
    )
    c = BALANCE.check(ev, None)
    assert c.status == "pass"
    assert c.detail == "1 balance_disclosed events, all after identity_verified"


def test_requires_before_is_scoped():
    ev = ledger(
        ("identity_verified", {"call_id": "c1"}),
        ("balance_disclosed", {"call_id": "c2"}),
    )
    c = BALANCE.check(ev, None)
    assert (c.status, c.seq) == ("fail", 2)
    assert "call_id=c2" in c.detail


def test_requires_before_with_payload_conditions():
    rule = RequiresBefore(
        {"type": "detail_disclosed", "where": {"field": "claim_ref"}},
        "policy_verified",
        scope=["call_id"],
    )
    ev = ledger(
        ("detail_disclosed", {"call_id": "c1", "field": "office_hours"}),
        ("detail_disclosed", {"call_id": "c1", "field": "claim_ref"}),
    )
    assert (rule.check(ev, None).status, rule.check(ev, None).seq) == ("fail", 2)


# always_followed_by: a vulnerability flag must lead to a handoff on the same call.
HANDOFF = AlwaysFollowedBy("vulnerability_flagged", "handoff_to_human", scope=["call_id"])


def test_always_followed_by_passes():
    ev = ledger(
        ("vulnerability_flagged", {"call_id": "c1"}),
        ("note", {"call_id": "c1"}),
        ("handoff_to_human", {"call_id": "c1"}),
    )
    assert HANDOFF.check(ev, None).status == "pass"


def test_always_followed_by_fails_at_the_unanswered_trigger():
    ev = ledger(
        ("vulnerability_flagged", {"call_id": "c1"}),
        ("vulnerability_flagged", {"call_id": "c2"}),
        ("handoff_to_human", {"call_id": "c2"}),
    )
    c = HANDOFF.check(ev, None)
    assert (c.status, c.seq) == ("fail", 1)


def test_response_on_another_call_does_not_count():
    ev = ledger(
        ("vulnerability_flagged", {"call_id": "c1"}),
        ("handoff_to_human", {"call_id": "c9"}),
    )
    assert HANDOFF.check(ev, None).status == "fail"


def test_always_followed_by_within_seconds():
    rule = AlwaysFollowedBy("emergency_phrase", "transferred", scope=["call_id"], within=30)
    on_time = ledger(
        ("emergency_phrase", {"call_id": "c1"}, "2026-03-02T09:00:00"),
        ("transferred", {"call_id": "c1"}, "2026-03-02T09:00:20"),
    )
    late = ledger(
        ("emergency_phrase", {"call_id": "c1"}, "2026-03-02T09:00:00"),
        ("transferred", {"call_id": "c1"}, "2026-03-02T09:01:00"),
    )
    assert rule.check(on_time, None).status == "pass"
    c = rule.check(late, None)
    assert (c.status, c.seq) == ("fail", 1)
    assert "60s" in c.detail


# never: the agent never emits a payout decision.
def test_never_fails_at_first_occurrence():
    rule = Never("payout_decided", where={"actor": "agent"})
    ev = ledger(("claim_logged", {}), ("payout_decided", {"amount": 10}))
    c = rule.check(ev, None)
    assert (c.status, c.seq) == ("fail", 2)


def test_never_respects_where():
    rule = Never("payout_decided", where={"actor": "staff"})
    ev = ledger(("payout_decided", {}))  # emitted by the agent, not staff
    assert rule.check(ev, None).status == "pass"


def test_matcher_accepts_lists_and_scalars():
    m = Matcher.parse({"type": "call_attempt", "where": {"outcome": ["busy", "no_answer"]}})
    (busy,) = ledger(("call_attempt", {"outcome": "busy"}))
    (answered,) = ledger(("call_attempt", {"outcome": "answered"}))
    assert m.matches(busy) and not m.matches(answered)
    assert Matcher.parse("x") == Matcher("x")


def test_load_rules_from_config():
    rules = load_rules(
        [
            {"rule": "requires_before", "effect": "a", "precondition": "b"},
            {"rule": "never", "name": "no_payouts", "event": "payout_decided"},
        ]
    )
    assert [r.name for r in rules] == ["requires_before", "no_payouts"]


@pytest.mark.parametrize(
    "config, match",
    [
        ([{"rule": "nope"}], "unknown rule"),
        ([{"rule": "never"}], "never"),  # missing field
        ([{"rule": "never", "event": "x", "typo": 1}], "typo"),
        ([{"rule": "never", "event": {"type": "x", "wher": {}}}], "not an event matcher"),
        ([{"rule": "never", "event": "x", "name": "chain"}], "reserved"),
        ([{"rule": "never", "event": "x"}, {"rule": "never", "event": "y"}], "used twice"),
    ],
)
def test_bad_config_is_rejected(config, match):
    with pytest.raises(RuleConfigError, match=match):
        load_rules(config)


def test_custom_rule_plugs_in():
    @register_rule("max_events_per_call")
    class MaxEvents(Rule):
        def __init__(self, limit: int, name: str | None = None):
            self.limit, self.name = limit, name or self.kind

        def check(self, events, snapshots):
            c = Check()
            counts: dict[str, int] = {}
            for ev in events:
                k = ev.payload.get("call_id")
                counts[k] = counts.get(k, 0) + 1
                if counts[k] > self.limit:
                    c.fail(ev.seq, f"call {k} has more than {self.limit} events")
            return c

    (rule,) = load_rules([{"rule": "max_events_per_call", "limit": 1}])
    ev = ledger(("a", {"call_id": "c1"}), ("b", {"call_id": "c1"}))
    report = verify(ev, rules=[rule])
    assert list(report.checks) == ["chain", "max_events_per_call", "head"]
    assert report.checks["max_events_per_call"].seq == 2


def test_event_type_allow_list_is_optional():
    assert seal(CHAIN, 1, "t", "anything", "agent").type == "anything"
    with pytest.raises(ValueError, match="unknown event type"):
        seal(CHAIN, 1, "t", "anything", "agent", types={"slot_freed"})


def test_cli_rejects_bad_rules_file(tmp_path, capsys):
    bad = tmp_path / "rules.toml"
    bad.write_text('rules = [{ rule = "nope" }]\n')
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text("\n".join(e.to_json() for e in ledger(("a", {}))) + "\n")
    assert cli(["verify", str(ledger_path), "--rules", str(bad)]) == 2
    assert "unknown rule" in capsys.readouterr().err
