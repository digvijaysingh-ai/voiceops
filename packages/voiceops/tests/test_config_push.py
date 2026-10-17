"""Push against a fake platform: idempotent, saves state, refuses without prerequisites."""

from __future__ import annotations

import json

import httpx
import pytest

from voiceops.config import ConfigError, PushError, build, dry_run, load_config, push, write
from voiceops.config.push import fill

BASE = "https://api.northwind.example"


class FakePlatform:
    def __init__(self, preflight_status=200):
        self.preflight_status = preflight_status
        self.tools: dict[str, dict] = {}
        self.agents: dict[str, dict] = {}
        self.secrets: list[dict] = []
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        m, path = request.method, request.url.path
        self.calls.append(f"{m} {path}")
        body = json.loads(request.content) if request.content else None
        if path == "/v1/convai/agents" and m == "GET":
            if self.preflight_status != 200:
                return httpx.Response(self.preflight_status, text="missing convai_write")
            return httpx.Response(200, json={"agents": []})
        if path == "/v1/convai/secrets":
            if m == "GET":
                return httpx.Response(200, json={"secrets": self.secrets})
            self.secrets.append({"name": body["name"], "secret_id": "sec_1"})
            return httpx.Response(200, json={"secret_id": "sec_1"})
        if path == "/v1/convai/tools":
            if m == "GET":
                return httpx.Response(
                    200,
                    json={"tools": [{"id": i, "tool_config": t} for i, t in self.tools.items()]},
                )
            tid = f"tool_{len(self.tools) + 1}"
            self.tools[tid] = body["tool_config"]
            return httpx.Response(200, json={"id": tid})
        if path.startswith("/v1/convai/tools/"):
            tid = path.rsplit("/", 1)[1]
            if m == "DELETE":
                self.tools.pop(tid)
            else:
                self.tools[tid] = body["tool_config"]
            return httpx.Response(200, json={})
        if path == "/v1/convai/agents/create":
            aid = f"agent_{len(self.agents) + 1}"
            self.agents[aid] = body
            return httpx.Response(200, json={"agent_id": aid})
        if path.startswith("/v1/convai/agents/"):
            self.agents[path.rsplit("/", 1)[1]] = body
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url="https://platform.test", transport=httpx.MockTransport(self.handler)
        )


@pytest.fixture
def cfg(project, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("TOOL_SECRET", "test-secret")
    c = load_config(project)
    write(c, build(c))
    return c


def test_first_push_creates_then_second_updates(cfg):
    fake = FakePlatform()
    assert push(cfg, "outbound-demo", BASE, fake.client()).startswith("pushed outbound-demo")
    assert len(fake.tools) == 5 and len(fake.agents) == 1 and len(fake.secrets) == 1
    created = [c for c in fake.calls if c.startswith("POST")]
    state = json.loads((cfg.root / "agent" / "state.json").read_text())
    assert set(state["tools"]) == set(t["name"] for t in fake.tools.values())

    fake.calls.clear()
    push(cfg, "outbound-demo", BASE, fake.client())
    assert not [c for c in fake.calls if c.startswith("POST")], "re-run must not create"
    assert len(fake.tools) == 5 and len(fake.agents) == 1
    assert len(created) == 1 + 5 + 1  # secret, tools, agent


def test_pushed_payloads_are_filled(cfg):
    fake = FakePlatform()
    push(cfg, "inbound-private", BASE + "/", fake.client())
    tool = next(t for t in fake.tools.values() if t["name"] == "log_outcome")
    assert tool["api_schema"]["url"] == f"{BASE}/tools/log_outcome"
    assert tool["api_schema"]["request_headers"]["X-Tool-Secret"] == {"secret_id": "sec_1"}
    (agent,) = fake.agents.values()
    prompt = agent["conversation_config"]["agent"]["prompt"]
    assert "tool_names" not in prompt and len(prompt["tool_ids"]) == 4  # payment plan excluded


def test_duplicate_tools_from_failed_runs_are_removed(cfg):
    fake = FakePlatform()
    tools = json.loads((cfg.root / "agent" / "build" / "tools.json").read_text())
    dup = fill(next(t for t in tools if t["name"] == "get_balance"), BASE, "sec_1")
    fake.tools = {"tool_a": dup, "tool_b": dup}
    push(cfg, "outbound-demo", BASE, fake.client())
    assert "DELETE /v1/convai/tools/tool_b" in fake.calls
    assert [t["name"] for t in fake.tools.values()].count("get_balance") == 1


def test_missing_permission_stops_before_writing(cfg):
    fake = FakePlatform(preflight_status=401)
    with pytest.raises(PushError, match="Agents permission"):
        push(cfg, "outbound-demo", BASE, fake.client())
    assert fake.calls == ["GET /v1/convai/agents"]
    assert not (cfg.root / "agent" / "state.json").exists()


def test_missing_secrets_refuse(cfg, monkeypatch):
    monkeypatch.delenv("TOOL_SECRET")
    with pytest.raises(ConfigError, match="TOOL_SECRET"):
        push(cfg, "outbound-demo", BASE, FakePlatform().client())


def test_unknown_profile_refuses(cfg):
    with pytest.raises(ConfigError, match="no built profile"):
        push(cfg, "nope", BASE, FakePlatform().client())


def test_dry_run_calls_nothing(cfg):
    out = json.loads(dry_run(cfg, "outbound-demo", BASE))
    assert out["tools"][0]["api_schema"]["url"].startswith(BASE + "/tools/")
    assert "{{" not in json.dumps(out)


def test_other_projects_tools_with_the_same_names_are_never_touched(cfg):
    """Two deployments in one workspace: same tool names, different endpoints."""
    fake = FakePlatform()
    tools = json.loads((cfg.root / "agent" / "build" / "tools.json").read_text())
    other = {f"other_{t['name']}": fill(t, "https://other-project.example", "sec_x") for t in tools}
    fake.tools = dict(other)
    push(cfg, "outbound-demo", BASE, fake.client())
    for tid, tool in other.items():
        assert fake.tools[tid] == tool, f"{tid} was changed"
    assert not [c for c in fake.calls if c.startswith("DELETE")]
    assert len(fake.tools) == len(other) + 5  # ours created alongside
