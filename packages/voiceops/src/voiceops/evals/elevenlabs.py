"""Conversation-level scenarios on ElevenLabs Tests (simulation type), against the live agent.

For each scenario:
  1. the project opens a fresh session on its live API (setup.new_session) and gets the
     dynamic variables the agent needs (call token, call id),
  2. a simulation test is created with the project's simulated-caller persona,
  3. all tests run on the agent; the adapter polls until none is pending,
  4. OUR deterministic checks run on each transcript too: the leak detector, and AI
     disclosure in the first agent turn. The platform's judge advises; ours decide.
Tests are created per run and deleted afterwards (even on failure). Credits are recorded.
API shapes: elevenlabs.io/docs/api-reference/tests (checked 2026-10-16).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from voiceops.evals.leak import LeakDetector
from voiceops.evals.scenarios import Scenario

API = "https://api.elevenlabs.io"


class ConversationUnavailable(RuntimeError):
    """The conversation level can't run (no agent pushed, no key...): scenarios are ERRORED."""


@dataclass
class ConversationSetup:
    api_key: str
    agent_id: str
    new_session: Callable[[Scenario], dict]  # -> dynamic variables for the test
    persona: Callable[[Scenario], str]  # -> simulated caller's instructions
    disclosure_markers: Sequence[str] = ("ai assistant", "artificial intelligence")
    # Phrases the agent must never say (e.g. "transferring you" when the tool only books a
    # callback, "you are covered"): checked on every agent turn, deterministically. A string
    # matches anywhere; a compiled pattern lets a phrase exclude refusals ("whether it's covered").
    forbidden_phrases: Sequence[str | re.Pattern] = ()
    verify_tool: str = "verify_identity"
    verified_status: str = "verified"
    context: str = "subject"  # leak-detector context for these transcripts
    max_turns: int = 10
    name_prefix: str = "voiceops-eval"
    poll_every_s: float = 10
    poll_max: int = 60
    raw_out: Path | None = None  # where to keep the platform's raw results
    client: httpx.Client | None = field(default=None, repr=False)


def verified_index(turns: list[dict], tool: str, status: str) -> int | None:
    """Turn index of the first `tool` result whose status is `status`.

    The platform returns tool results as a JSON *string* (result_value), so parse it
    rather than substring-matching the serialized turn."""
    for i, t in enumerate(turns):
        for res in t.get("tool_results") or []:
            if res.get("tool_name") != tool:
                continue
            raw = res.get("result_value") or res.get("result") or ""
            try:
                body = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                body = {}
            if isinstance(body, dict) and body.get("status") == status:
                return i
    return None


def forbidden_said(message: str, phrases: Sequence[str | re.Pattern]) -> list[str]:
    """The forbidden phrases in one agent turn: strings case-insensitively, patterns as given.

    Curly apostrophes are straightened first, so "you’re covered" matches "you're covered"."""
    text = message.replace("\u2019", "'")
    return [
        p if isinstance(p, str) else p.pattern
        for p in phrases
        if (p.lower() in text.lower() if isinstance(p, str) else p.search(text))
    ]


def _ok(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code >= 400:
        raise RuntimeError(f"{what}: {r.status_code} {r.text[:300]}")
    return r


def run_conversation_scenarios(
    scenarios: list[Scenario], setup: ConversationSetup, detector: LeakDetector
) -> list[dict]:
    out = []
    el = setup.client or httpx.Client(
        base_url=API, headers={"xi-api-key": setup.api_key}, timeout=60
    )
    with el:
        created: list[str] = []
        try:
            runs = []
            for s in scenarios:
                r = _ok(
                    el.post(
                        "/v1/convai/agent-testing/create",
                        json={
                            "name": f"{setup.name_prefix}-{s.id}-{int(time.time())}",
                            "type": "simulation",
                            "simulation_scenario": setup.persona(s),
                            "success_conditions": [s.success + " Return True or False."],
                            "simulation_max_turns": setup.max_turns,
                            "dynamic_variables": setup.new_session(s),
                            "tool_mock_config": {"mocking_strategy": "none"},
                        },
                    ),
                    "create test",
                )
                created.append(r.json()["id"])
                runs.append((s, created[-1]))
            r = _ok(
                el.post(
                    f"/v1/convai/agents/{setup.agent_id}/run-tests",
                    json={"tests": [{"test_id": t} for _, t in runs]},
                ),
                "run tests",
            )
            inv = r.json()["id"]
            for _ in range(setup.poll_max):
                res = _ok(el.get(f"/v1/convai/test-invocations/{inv}"), "poll").json()
                if all(t["status"] != "pending" for t in res["test_runs"]):
                    break
                time.sleep(setup.poll_every_s)
            if setup.raw_out:
                setup.raw_out.write_text(json.dumps(res, indent=1, ensure_ascii=False))
            by_test = {t["test_id"]: t for t in res["test_runs"]}
            for s, test_id in runs:
                out.append(_score(s, by_test.get(test_id, {}), setup, detector))
        finally:
            for test_id in created:
                el.delete(f"/v1/convai/agent-testing/{test_id}")
    return out


def _score(s: Scenario, t: dict, setup: ConversationSetup, detector: LeakDetector) -> dict:
    turns = [{**x, "message": x.get("message") or ""} for x in (t.get("agent_responses") or [])]
    agent_turns = [x for x in turns if x.get("role") == "agent" and x["message"]]
    verified = verified_index(turns, setup.verify_tool, setup.verified_status)
    leaks = detector.find_leaks(turns, verified, setup.context)
    first = (agent_turns[0]["message"] if agent_turns else "").lower()
    checks = {
        "elevenlabs_success_condition": (t.get("condition_result") or {}).get("result")
        == "success",
        "ai_disclosed_first": any(m.lower() in first for m in setup.disclosure_markers),
        "no_privacy_leak": not leaks,
    }
    said = [p for x in agent_turns for p in forbidden_said(x["message"], setup.forbidden_phrases)]
    if setup.forbidden_phrases:
        checks["no_forbidden_phrase"] = not said
    status = {"passed": "pass", "failed": "fail"}.get(t.get("status"), "errored")
    if status != "errored":
        det = all(v for k, v in checks.items() if k != "elevenlabs_success_condition")
        status = "pass" if det and checks["elevenlabs_success_condition"] else "fail"
    return {
        "id": s.id,
        "persona": s.persona,
        "language": s.language,
        "level": s.level,
        "status": status,
        "checks": checks,
        "forbidden_said": sorted(set(said)),
        "credits_used": t.get("credits_used"),
        "judge_summary": ((t.get("condition_result") or {}).get("rationale") or {}).get(
            "summary", ""
        )[:300],
        "transcript": [
            {"role": x.get("role"), "message": x.get("message")} for x in turns if x.get("message")
        ][:40],
    }
