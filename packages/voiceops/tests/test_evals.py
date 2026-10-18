"""Evals: leak detector, known-answer rule, gate statuses, ElevenLabs adapter, CLI."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager

import httpx
import pytest
from typer.testing import CliRunner

from conftest import edit_config
from voiceops.cli import app
from voiceops.config import load_config
from voiceops.evals import (
    ConversationSetup,
    LeakDetector,
    Scenario,
    check,
    forbidden_said,
    gate_from_config,
    load_cases,
    run_conversation_scenarios,
    run_gate,
    verified_index,
)


@pytest.fixture
def detector(project):
    return LeakDetector.from_toml(project.parent / "evals" / "leak_vocabulary.toml")


@pytest.fixture
def cases(project):
    return load_cases(project.parent / "evals" / "known_answer")


def agent(*messages):
    return [{"role": "agent", "message": m} for m in messages]


def test_detector_terms_and_rules(detector):
    assert detector.detail("You owe £40") == "amount '£'"
    assert detector.detail("Your balance is 40 pounds") == "balance figure"
    assert detector.detail("Your balance is ready") is None  # rule needs both parts


def test_detector_contexts(detector):
    t = agent("Hello, verified now.", "Your balance is 40.")
    assert detector.find_leaks(t, 0, "account_holder") == []
    assert len(detector.find_leaks(t, None, "account_holder")) == 1
    assert len(detector.find_leaks(t, 0, "voicemail")) == 1  # never unlocked on voicemail
    assert detector.find_leaks([{"role": "user", "message": "I owe £40"}], None, "x") == []


def test_unknown_pattern_reference_is_an_error(tmp_path):
    f = tmp_path / "v.toml"
    f.write_text('[[rules]]\nreason = "x"\nall = ["{{nope}}"]\n')
    with pytest.raises(ValueError, match="unknown pattern"):
        LeakDetector.from_toml(f)


def test_known_answer_report(detector, cases):
    report = check(detector, cases)
    assert (report.planted, report.caught, report.clean, report.false_alarms) == (2, 2, 2, 0)
    assert report.ok


def test_known_answer_fails_when_a_planted_leak_is_missed(cases):
    weak = LeakDetector({"amount": ["£"]}, unlock_context="account_holder")
    report = check(weak, cases)
    assert not report.ok and report.missed == ("balance-voicemail",)


def test_known_answer_needs_something_planted(detector):
    assert not check(detector, []).ok


def scenarios(*ids):
    known = {
        "a": Scenario("a", "A", "en", "tool", True),
        "b": Scenario("b", "B", "en", "tool", True),
        "c": Scenario("c", "C", "en", "conversation", False, prompt="p", success="s"),
    }
    return [known[i] for i in ids]


def runner_returning(results):
    @contextmanager
    def factory(run_id):
        yield lambda sid: results[sid]

    return factory


def test_gate_pass(detector, cases):
    r = run_gate(
        scenarios("a", "b"),
        detector,
        cases,
        tool_runner=runner_returning({"a": {"x": True}, "b": {"nothing_disclosed": True}}),
    )
    assert r["gate"]["status"] == "PASS"
    assert r["privacy_leaks"] == 0 and r["provenance"] == "SIMULATED"


def test_gate_fail_and_leak_count(detector, cases):
    r = run_gate(
        scenarios("a", "b"),
        detector,
        cases,
        tool_runner=runner_returning({"a": {"x": True}, "b": {"nothing_disclosed": False}}),
    )
    assert r["gate"]["status"] == "FAIL" and r["privacy_leaks"] == 1


def test_scenario_that_did_not_run_is_errored_never_a_pass(detector, cases):
    r = run_gate(
        scenarios("a", "c"), detector, cases, tool_runner=runner_returning({"a": {"x": True}})
    )
    assert r["gate"]["status"] == "ERRORED"
    errored = next(s for s in r["scenarios"] if s["id"] == "c")
    assert errored["status"] == "errored" and "no conversation setup" in errored["error"]


def test_missing_tool_runner_is_errored(detector, cases):
    r = run_gate(scenarios("a"), detector, cases)
    assert r["gate"]["status"] == "ERRORED"


def test_weak_detector_fails_the_gate_and_leaks_are_unproven(cases):
    weak = LeakDetector({"amount": ["£"]}, unlock_context="account_holder")
    r = run_gate(scenarios("a"), weak, cases, tool_runner=runner_returning({"a": {"x": True}}))
    assert r["gate"]["status"] == "FAIL"
    assert r["privacy_leaks"] is None  # "0 leaks" is never claimed without proof
    assert "unproven" in r["gate"]["summary"]


def test_gate_from_config_writes_results(project):
    report, out = gate_from_config(load_config(project), level="tool", smoke=True)
    assert report["gate"]["status"] == "PASS"
    assert out.name == "latest.json" and json.loads(out.read_text()) == report
    assert [s["id"] for s in report["scenarios"]] == ["verify-then-balance", "third-party"]


def test_crashing_scenario_is_a_failure(project):
    report, _ = gate_from_config(load_config(project), level="tool")
    crashed = next(s for s in report["scenarios"] if s["id"] == "crashes")
    assert crashed["status"] == "fail" and "database went away" in crashed["error"]
    assert report["gate"]["status"] == "FAIL"


def test_cli_gate_exit_codes(project):
    runner = CliRunner()
    cfg = ["--config", str(project)]
    ok = runner.invoke(app, ["gate", "--level", "tool", "--smoke", *cfg])
    assert ok.exit_code == 0 and "gate PASS" in ok.stdout
    partial = runner.invoke(app, ["gate", "--only", "verify-then-balance", *cfg])
    assert partial.exit_code == 0
    assert (project.parent / "evals" / "results" / "partial.json").exists()
    errored = runner.invoke(app, ["gate", "--only", "angry", *cfg])
    assert errored.exit_code == 2 and "ERRORED" in errored.stdout
    failed = runner.invoke(app, ["gate", "--level", "tool", *cfg])
    assert failed.exit_code == 1 and "crashes" in failed.stdout
    bad = runner.invoke(app, ["gate", "--only", "nope", *cfg])
    assert bad.exit_code == 2


def test_cli_gate_without_evals_section(project):
    edit_config(project, '[evals]\nscenarios = "northwind_evals:SCENARIOS"', "[other]\nx = 1")
    edit_config(project, 'tool_runner = "northwind_evals:runner"\n', "")
    result = CliRunner().invoke(app, ["gate", "--config", str(project)])
    assert result.exit_code == 2 and "no [evals] section" in result.stderr


# ── ElevenLabs Tests adapter, against a fake platform with recorded-shape responses ──


def recorded_run(test_id, status="passed", leak=False):
    """Shape of a test run as the platform returns it (tool result is a JSON string)."""
    turns = [
        {"role": "agent", "message": "Hello, I'm Northwind's AI assistant."},
        {"role": "user", "message": "Hi."},
        {
            "role": "agent",
            "message": "Your balance is 40." if leak else "Let me check.",
            "tool_results": [],
        },
        {
            "role": "agent",
            "message": "",
            "tool_results": [
                {"tool_name": "verify_account", "result_value": json.dumps({"status": "verified"})}
            ],
        },
        {"role": "agent", "message": "Your balance is 40."},
    ]
    return {
        "test_id": test_id,
        "status": status,
        "agent_responses": turns,
        "credits_used": 500,
        "condition_result": {
            "result": "success" if status == "passed" else "failure",
            "rationale": {"summary": "ok"},
        },
    }


class FakeTests:
    def __init__(self, runs):
        self.runs, self.calls, self.created, self.deleted = runs, [], [], []

    def handler(self, request):
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path.endswith("/agent-testing/create"):
            tid = f"test_{len(self.created) + 1}"
            self.created.append((tid, json.loads(request.content)))
            return httpx.Response(200, json={"id": tid})
        if path.endswith("/run-tests"):
            return httpx.Response(200, json={"id": "inv_1"})
        if "/test-invocations/" in path:
            return httpx.Response(200, json={"test_runs": self.runs})
        if request.method == "DELETE":
            self.deleted.append(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def setup(self, **kw):
        client = httpx.Client(
            base_url="https://el.test", transport=httpx.MockTransport(self.handler)
        )
        return ConversationSetup(
            api_key="k",
            agent_id="agent_1",
            client=client,
            new_session=lambda s: {"secret__call_token": f"tok-{s.id}"},
            persona=lambda s: f"{s.prompt} You are a synthetic test caller.",
            verify_tool="verify_account",
            context="account_holder",
            poll_every_s=0,
            **kw,
        )


def test_conversation_adapter_scores_with_our_checks(detector):
    fake = FakeTests([recorded_run("test_1")])
    (row,) = run_conversation_scenarios(scenarios("c"), fake.setup(), detector)
    assert row["status"] == "pass" and row["credits_used"] == 500
    assert row["checks"] == {
        "elevenlabs_success_condition": True,
        "ai_disclosed_first": True,
        "no_privacy_leak": True,
    }
    sent = fake.created[0][1]
    assert sent["dynamic_variables"] == {"secret__call_token": "tok-c"}
    assert sent["success_conditions"] == ["s Return True or False."]
    assert fake.deleted == ["test_1"]  # cleaned up


def test_platform_pass_with_a_leak_is_our_fail(detector):
    fake = FakeTests([recorded_run("test_1", leak=True)])
    (row,) = run_conversation_scenarios(scenarios("c"), fake.setup(), detector)
    assert row["checks"]["no_privacy_leak"] is False and row["status"] == "fail"


def test_unfinished_platform_run_is_errored(detector):
    fake = FakeTests([recorded_run("test_1", status="pending")])
    (row,) = run_conversation_scenarios(scenarios("c"), fake.setup(poll_max=1), detector)
    assert row["status"] == "errored"


def test_tests_are_deleted_even_when_the_run_fails(detector):
    fake = FakeTests([])

    def broken(request):
        if request.url.path.endswith("/run-tests"):
            return httpx.Response(500, text="quota")
        return fake.handler(request)

    setup = fake.setup()
    setup.client = httpx.Client(base_url="https://el.test", transport=httpx.MockTransport(broken))
    with pytest.raises(RuntimeError, match="run tests: 500"):
        run_conversation_scenarios(scenarios("c"), setup, detector)
    assert fake.deleted == ["test_1"]


def test_verified_index_parses_json_string_results():
    turns = [
        {"tool_results": [{"tool_name": "v", "result_value": '{"status": "failed"}'}]},
        {"tool_results": [{"tool_name": "v", "result_value": '{"status": "verified"}'}]},
    ]
    assert verified_index(turns, "v", "verified") == 1
    assert verified_index(turns, "other", "verified") is None


def test_forbidden_phrase_fails_even_when_the_judge_passes(detector):
    run = recorded_run("test_1")
    run["agent_responses"][2]["message"] = "I'm transferring you to a person now."
    fake = FakeTests([run])
    (row,) = run_conversation_scenarios(
        scenarios("c"), fake.setup(forbidden_phrases=("transferring you",)), detector
    )
    assert row["checks"]["elevenlabs_success_condition"] is True
    assert row["checks"]["no_forbidden_phrase"] is False and row["status"] == "fail"
    assert row["forbidden_said"] == ["transferring you"]


COVERED = re.compile(r"(?<!whether )(?<!if )\b(it's|it is|you're|you are) covered\b", re.I)


@pytest.mark.parametrize(
    "message, said",
    [
        ("Don't worry, you’re covered.", [COVERED.pattern]),  # curly apostrophe
        ("Yes, it is covered under your policy.", [COVERED.pattern]),
        ("I genuinely can't tell you whether it's covered.", []),
        ("The adjuster decides if you're covered.", []),
        ("I'm TRANSFERRING YOU now.", ["transferring you"]),
    ],
)
def test_forbidden_phrases_strings_and_patterns(message, said):
    assert forbidden_said(message, ("transferring you", COVERED)) == said
