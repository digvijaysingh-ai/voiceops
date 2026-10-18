"""Review: allow-listed calls, fetch and the review sheet, on a synthetic conversation."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from voiceops.cli import app
from voiceops.config import ConfigError, load_config
from voiceops.evals import LeakDetector
from voiceops.review import (
    SheetOptions,
    allowed_number,
    automatic_checks,
    fetch_conversation,
    place_call,
    render,
)
from voiceops.review.sheet import _cell

REVIEW = """
[review]
profile = "outbound-demo"
api_url = "env:NORTHWIND_API_URL"
session = { path = "/api/sessions", body = { code = "env:NORTHWIND_CODE" } }
disclosure_markers = ["ai assistant"]
verify_tool = "verify_account"
leak_context = "account_holder"
unbacked_promises = ["by email", "text message"]
"""

NUMBER = "+447700900123"  # a drama-reserved UK number, never a real line


def result(tool, body, error=False):
    return {"tool_name": tool, "is_error": error, "result_value": json.dumps(body)}


def conversation(leak=False, promise=False, ended_by_agent=True, disclosure=True):
    first = (
        "Hello, I'm Northwind Energy's AI assistant." if disclosure else "Hello, Northwind here."
    )
    turns = [
        {"role": "agent", "message": first, "time_in_call_secs": 0},
        {"role": "user", "message": "Hi, it's me.", "time_in_call_secs": 3},
        {
            "role": "agent",
            "message": "Your balance is 40." if leak else "Postcode, please?",
            "time_in_call_secs": 5,
        },
        {
            "role": "agent",
            "message": "",
            "time_in_call_secs": 9,
            "tool_calls": [{"tool_name": "verify_account", "params_as_json": '{"postcode": "X1"}'}],
        },
        {
            "role": "agent",
            "message": "",
            "time_in_call_secs": 9,
            "tool_results": [result("verify_account", {"status": "verified"})],
        },
        {
            "role": "agent",
            "message": "You owe £40. I'll confirm by email." if promise else "You owe £40.",
            "time_in_call_secs": 11,
        },
        {
            "role": "agent",
            "message": "",
            "time_in_call_secs": 12,
            "tool_results": [result("log_outcome", {"status": "error"}, error=True)],
        },
    ]
    return {
        "conversation_id": "conv_test1",
        "agent_name": "Northwind outbound demo",
        "status": "done",
        "metadata": {
            "call_duration_secs": 75,
            "start_time_unix_secs": 1_772_445_600,
            "main_language": "en",
            "termination_reason": "end_call tool was called."
            if ended_by_agent
            else "Client disconnected",
            "phone_call": {"direction": "outbound", "external_number": NUMBER},
        },
        "transcript": turns,
    }


@pytest.fixture
def opts(project):
    return SheetOptions(
        disclosure_markers=["ai assistant"],
        verify_tool="verify_account",
        leak_context="account_holder",
        unbacked_promises=["by email"],
        detector=LeakDetector.from_toml(project.parent / "evals" / "leak_vocabulary.toml"),
    )


def checks(conv, opts):
    rows, flags = automatic_checks(conv, opts)
    return {c: r for c, r, _ in rows}, flags


def test_clean_call(opts):
    rows, flags = checks(conversation(), opts)
    assert rows["AI disclosed in the first agent turn"] == "PASS"
    assert rows["Identity verified"] == "yes"
    assert rows["No protected detail before verification"] == "PASS"
    assert rows["Call ended by the agent"] == "yes"
    assert [f.what for f in flags] == ['`log_outcome` returned an error: status: "error"']


def test_each_problem_is_flagged(opts):
    conv = conversation(leak=True, promise=True, ended_by_agent=False, disclosure=False)
    rows, flags = checks(conv, opts)
    assert rows["AI disclosed in the first agent turn"] == "FAIL"
    assert rows["No protected detail before verification"] == "FAIL"
    assert rows["Promises no tool backs"] == "1"
    assert rows["Call ended by the agent"] == "no"
    what = " ".join(f.what for f in flags)
    for expected in ("not disclosed", "Possible leak", "by email", "did not end with end_call"):
        assert expected in what


def test_sheet_masks_the_phone_number_and_shows_the_timeline(opts):
    sheet = render(conversation(), opts, "Call 01 · Balance")
    assert NUMBER not in sheet and "0900123" not in sheet and "…123" in sheet
    assert "# Call 01 · Balance · `conv_test1`" in sheet
    assert '→ `verify_account` {"postcode": "X1"}' in sheet
    assert '← `verify_account`: status: "verified"' in sheet
    assert "⚠ error" in sheet and "MEASURED" in sheet
    assert "| # | Turn | What happened | Root cause | Fix |" in sheet


def test_pipes_in_messages_do_not_break_the_table(opts):
    conv = conversation()
    conv["transcript"][1]["message"] = "a | b"
    assert "a \\| b" in render(conv, opts)


def test_sheet_without_vocabulary_says_not_checked():
    rows, _ = checks(conversation(leak=True), SheetOptions(disclosure_markers=["ai assistant"]))
    assert rows["No protected detail before verification"] == "NOT CHECKED"


# ── calls and fetch ──


@pytest.fixture
def cfg(project, monkeypatch):
    monkeypatch.setenv("NORTHWIND_API_URL", "https://api.test")
    monkeypatch.setenv("NORTHWIND_CODE", "letmein")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("DEMO_PHONE_ALLOWLIST", NUMBER)
    project.write_text(project.read_text() + REVIEW)
    state = project.parent / "agent" / "state.json"
    state.write_text(
        json.dumps({"agents": {"outbound-demo": "agent_1"}, "phone_number_id": "pn_1"})
    )
    return load_config(project)


@pytest.mark.parametrize("value", ["", "+44 7700 900123", "07700900123", f"{NUMBER},+34600000000"])
def test_allowlist_must_be_exactly_one_e164_number(cfg, monkeypatch, value):
    monkeypatch.setenv("DEMO_PHONE_ALLOWLIST", value)
    with pytest.raises(ConfigError):
        allowed_number(cfg)


def mock(handler):
    return httpx.Client(base_url="https://x.test", transport=httpx.MockTransport(handler))


def test_place_call_dials_only_the_allow_listed_number(cfg):
    sent = {}

    def api(req):
        return httpx.Response(200, json={"call_token": "tok", "call_id": "c9"})

    def el(req):
        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"conversation_id": "conv_new"})

    placed = place_call(cfg, client=mock(el), api_client=mock(api))
    assert placed == {"conversation_id": "conv_new", "to": "…123", "call_id": "c9"}
    assert sent["to_number"] == NUMBER and sent["agent_id"] == "agent_1"
    dyn = sent["conversation_initiation_client_data"]["dynamic_variables"]
    assert dyn == {"secret__call_token": "tok", "call_id": "c9"}


def test_place_call_refuses_an_unpushed_profile(cfg):
    (cfg.root / "agent" / "state.json").write_text(json.dumps({"agents": {}}))
    with pytest.raises(ConfigError, match="not pushed"):
        place_call(cfg, client=mock(lambda r: None), api_client=mock(lambda r: None))


def test_fetch_saves_raw_and_audio_and_cli_renders(cfg, project):
    def el(req):
        if req.url.path.endswith("/audio"):
            return httpx.Response(200, content=b"ID3audio")
        return httpx.Response(200, json=conversation())

    conv, folder = fetch_conversation(cfg, "conv_test1", client=mock(el))
    assert (folder / "conv_test1.json").exists() and (
        folder / "conv_test1.mp3"
    ).read_bytes() == b"ID3audio"
    result = CliRunner().invoke(
        app, ["review", str(folder / "conv_test1.json"), "--config", str(project), "--title", "T"]
    )
    assert result.exit_code == 0
    sheet = (project.parent / "review" / "conv_test1.md").read_text()
    assert sheet.startswith("# T · `conv_test1`") and NUMBER not in sheet


def test_fetch_refuses_odd_conversation_ids(cfg):
    with pytest.raises(ConfigError, match="bad conversation id"):
        fetch_conversation(cfg, "../../etc/passwd", client=mock(lambda r: None))


def test_an_error_body_counts_as_a_tool_error_even_unflagged(opts):
    # A guarded API refuses with a normal response; the platform doesn't mark it is_error.
    # The first real claims-surge call: an unknown call token, read out as "details don't match".
    conv = conversation()
    refused = result("verify_account", {"ok": False, "error": "unauthorized"})
    mismatch = result("verify_account", {"status": "failed", "attempts_left": 1})
    conv["transcript"][1]["tool_results"] = [refused, mismatch]
    rows, flags = checks(conv, opts)
    assert rows["Tool errors"] == "2"  # the refusal and log_outcome's flagged error
    assert any('error: "unauthorized"' in f.what for f in flags)
    assert not any("attempts_left" in f.what for f in flags)


@pytest.mark.parametrize(
    "said, kept, gone",
    [
        ("This is the same number, 07700 900 461.", "…461", "0900"),
        ("Call me on +44 7700 900123 please", "…123", "7700"),
        ('{"callback_number": "07700-900-123"}', "…123", "900-"),
        ("Your reference is CLM-2026-0502, born 1958-07-21.", "CLM-2026-0502", None),
        ("It happened on 2026-03-01 10 am", "2026-03-01 10 am", None),
    ],
)
def test_numbers_said_on_the_call_are_masked(said, kept, gone):
    # The second real claims-surge call: the caller said their callback number in full.
    cell = _cell(said)
    assert kept in cell
    assert gone is None or gone not in cell
