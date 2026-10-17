"""Agent build: composition, safety defaults that can't be dropped silently, tools, drift."""

from __future__ import annotations

import json

import pytest
from conftest import edit_config
from typer.testing import CliRunner

from voiceops.cli import app
from voiceops.config import SAFETY_SLOTS, ConfigError, build, kit_fragment, load_config


def built(project):
    return build(load_config(project))


def agent(files, profile):
    return json.loads(files[f"{profile}.json"])["conversation_config"]


def test_build_writes_tools_profiles_and_safety_manifest(project):
    files = built(project)
    assert set(files) == {"tools.json", "outbound-demo.json", "inbound-private.json", "safety.json"}
    assert all(t.endswith("\n") for t in files.values())


def test_kit_safety_defaults_are_added_and_filled(project):
    prompt = agent(built(project), "outbound-demo")["agent"]["prompt"]["prompt"]
    assert prompt.startswith("# Role\n")
    assert "Call `verify_account` before any detail." in prompt  # project rewording
    assert "# SAFETY: identity before any detail" not in prompt  # so no kit default for it
    for slot in set(SAFETY_SLOTS) - {"identity_before_detail"}:
        heading = kit_fragment(slot).splitlines()[0]
        assert heading in prompt, slot
    assert "{{" not in prompt
    assert "`transfer_to_human`" in prompt and "Northwind Energy" in prompt


def test_recording_notice_follows_the_profile(project):
    files = built(project)
    recorded = agent(files, "outbound-demo")["agent"]["prompt"]["prompt"]
    private = agent(files, "inbound-private")["agent"]["prompt"]["prompt"]
    assert "the call is recorded" in recorded
    assert "the call is recorded" not in private


def test_safety_manifest_records_how_each_slot_is_met(project):
    manifest = json.loads(built(project)["safety.json"])
    for profile in ("outbound-demo", "inbound-private"):
        assert set(manifest[profile]) == set(SAFETY_SLOTS)
        assert manifest[profile]["identity_before_detail"] == "project: 20_identity"
        assert manifest[profile]["closing"] == "kit default"


def test_dropping_a_reworded_safety_fragment_fails(project):
    profiles = project.parent / "agent" / "profiles.json"
    data = json.loads(profiles.read_text())
    data["inbound-private"]["fragments"].remove("20_identity")
    profiles.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="inbound-private drops safety slot 'identity_before"):
        built(project)


def test_removal_needs_a_reason_and_is_recorded(project):
    edit_config(project, "[agent.vars]", '[agent.allow_remove]\nupset_caller = ""\n\n[agent.vars]')
    with pytest.raises(ConfigError, match="needs a reason"):
        built(project)
    edit_config(project, 'upset_caller = ""', 'upset_caller = "outbound reminders only"')
    files = built(project)
    assert json.loads(files["safety.json"])["outbound-demo"]["upset_caller"] == (
        "removed: outbound reminders only"
    )
    heading = kit_fragment("upset_caller").splitlines()[0]
    assert heading not in agent(files, "outbound-demo")["agent"]["prompt"]["prompt"]


def test_missing_required_fragment_fails(project):
    profiles = project.parent / "agent" / "profiles.json"
    data = json.loads(profiles.read_text())
    data["outbound-demo"]["fragments"].remove("40_no_debt_advice")
    profiles.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="missing required fragments"):
        built(project)


def test_kit_default_without_its_vars_fails(project):
    edit_config(project, 'handoff_tool = "transfer_to_human"\n', "")
    with pytest.raises(ConfigError, match="needs \\[agent.vars\\] handoff_tool"):
        built(project)


@pytest.mark.parametrize(
    "old, new, match",
    [
        ('llm = "test-llm"', 'llm = "test-llm"\nlmm = "typo"', "lmm"),
        ("[agent.safety]", '[agent.safety]\nno_such_slot = "x"', "unknown safety slots"),
        ('exclude_tools = ["arrange_payment_plan"]', 'exclude_tools = ["nope"]', "unknown tools"),
        ('tools = "northwind_tools:TOOLS"', 'tools = "northwind_tools"', "package.module:ATTR"),
    ],
)
def test_bad_config_is_rejected(project, old, new, match):
    edit_config(project, old, new)
    with pytest.raises(ConfigError, match=match):
        built(project)


def test_directions_set_tools_and_audio(project):
    files = built(project)
    out, inb = agent(files, "outbound-demo"), agent(files, "inbound-private")
    assert "arrange_payment_plan" in out["agent"]["prompt"]["tool_names"]
    assert "arrange_payment_plan" not in inb["agent"]["prompt"]["tool_names"]
    assert out["tts"]["agent_output_audio_format"] == "ulaw_8000"
    assert out["asr"] == {"user_input_audio_format": "ulaw_8000"}
    assert "agent_output_audio_format" not in inb["tts"] and "asr" not in inb


def test_tool_schemas_from_models(project):
    tools = {t["name"]: t for t in json.loads(built(project)["tools.json"])}
    log = tools["log_outcome"]["api_schema"]
    assert log["url"] == "{{TOOL_BASE_URL}}/tools/log_outcome"
    props = log["request_body_schema"]["properties"]
    assert props["outcome"]["enum"] == ["resolved", "handoff", "voicemail"]
    assert props["attempts"]["type"] == "integer"
    assert log["request_body_schema"]["required"] == ["outcome"]
    assert log["request_headers"] == {
        "X-Tool-Secret": {"secret_id": "{{TOOL_SECRET_ID}}"},
        "X-Call-Token": {"variable_name": "secret__call_token"},
    }


def test_undocumented_tool_parameter_fails(project):
    tools = project.parent / "northwind_tools.py"
    edit_config(
        tools,
        'postcode: str = Field(description="Postcode of the supply address")',
        "postcode: str",
    )
    with pytest.raises(ConfigError, match="without a description: \\['postcode'\\]"):
        built(project)


def test_cli_build_and_drift_check(project):
    runner = CliRunner()
    cfg = ["--config", str(project)]
    stale = runner.invoke(app, ["build", "--check", *cfg])
    assert stale.exit_code == 1
    assert runner.invoke(app, ["build", *cfg]).exit_code == 0
    assert runner.invoke(app, ["build", "--check", *cfg]).exit_code == 0
    (project.parent / "agent" / "fragments" / "00_role.md").write_text("# Role\nChanged.\n")
    assert runner.invoke(app, ["build", "--check", *cfg]).exit_code == 1


def test_cli_reports_config_errors(project):
    edit_config(project, 'llm = "test-llm"', "")
    result = CliRunner().invoke(app, ["build", "--config", str(project)])
    assert result.exit_code == 2 and "llm" in result.stderr
