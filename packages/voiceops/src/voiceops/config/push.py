"""Push the built agent configs to ElevenLabs (create or update), idempotently.

    voiceops push --profile inbound-demo --base-url https://<api-host>

Prerequisites (checked first, nothing is half-written):
  - the platform API key ([push] api_key_env) with the Agents permission
  - the tool secret ([push] tool_secret_env), stored on the platform as a workspace
    secret and sent in the [guard] secret_header
  - --base-url: a public HTTPS URL of the API that passed the deploy checks

IDs are written to [push] state after every step (not secret), so a failed run never
orphans what it created and a re-run updates instead of duplicating.
"""

from __future__ import annotations

import json
import os

import httpx

from voiceops.config.project import ConfigError, VoiceopsConfig


class PushError(RuntimeError):
    pass


def fill(obj, base_url: str, secret_id: str):
    text = json.dumps(obj)
    return json.loads(
        text.replace("{{TOOL_BASE_URL}}", base_url.rstrip("/")).replace(
            "{{TOOL_SECRET_ID}}", secret_id
        )
    )


def preflight(client: httpx.Client) -> str | None:
    r = client.get("/v1/convai/agents", params={"page_size": 1})
    if r.status_code == 401 and "convai" in r.text:
        return "API key lacks the Agents permission (convai_read/convai_write)"
    if r.status_code >= 400:
        return f"ElevenLabs API error {r.status_code}: {r.text[:120]}"
    return None


def _ok(r: httpx.Response) -> httpx.Response:
    if r.status_code >= 400:
        raise PushError(
            f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:600]}"
        )
    return r


def load_payloads(cfg: VoiceopsConfig, profile: str) -> tuple[list[dict], dict]:
    build_dir = cfg.path(cfg.agent.build_dir)
    agent_file = build_dir / f"{profile}.json"
    if not agent_file.exists():
        raise ConfigError(f"no built profile {profile!r} in {build_dir} (run `voiceops build`)")
    return json.loads((build_dir / "tools.json").read_text()), json.loads(agent_file.read_text())


def dry_run(cfg: VoiceopsConfig, profile: str, base_url: str) -> str:
    tools, agent = load_payloads(cfg, profile)
    payload = fill({"tools": tools, "agent": agent}, base_url, "<secret_id>")
    return json.dumps(payload, indent=2, ensure_ascii=False)


def push(
    cfg: VoiceopsConfig, profile: str, base_url: str, client: httpx.Client | None = None
) -> str:
    """Create or update the profile's tools and agent. Returns a one-line summary."""
    if cfg.push is None:
        raise ConfigError("voiceops.toml has no [push] section")
    pc = cfg.push
    key, tool_secret = os.environ.get(pc.api_key_env), os.environ.get(pc.tool_secret_env)
    if not key or not tool_secret:
        raise ConfigError(f"{pc.api_key_env} and {pc.tool_secret_env} must be set")
    tools, agent = load_payloads(cfg, profile)
    state_file = cfg.path(pc.state)
    state = (
        json.loads(state_file.read_text()) if state_file.exists() else {"tools": {}, "agents": {}}
    )

    def save() -> None:  # after every step, so a failure never orphans what was created
        state_file.write_text(json.dumps(state, indent=2) + "\n")

    c = client or httpx.Client(base_url=pc.api_base, headers={"xi-api-key": key}, timeout=60)
    with c:
        problem = preflight(c)
        if problem:
            raise PushError(f"not pushing: {problem}")
        if "secret_id" not in state:  # reuse an existing secret with our name
            existing = _ok(c.get("/v1/convai/secrets")).json().get("secrets", [])
            match = [s for s in existing if s.get("name") == pc.secret_name]
            if match:
                state["secret_id"] = match[0]["secret_id"]
            else:
                r = _ok(
                    c.post(
                        "/v1/convai/secrets",
                        json={"type": "new", "name": pc.secret_name, "value": tool_secret},
                    )
                )
                state["secret_id"] = r.json()["secret_id"]
            save()
        # Tools already in the workspace, by the endpoint they call. Only a tool calling this
        # deployment's own endpoint is ours (left by an earlier, interrupted run): other
        # projects in the same workspace may use the same tool names, and are never touched.
        remote: dict[str, list[str]] = {}
        for t in _ok(c.get("/v1/convai/tools")).json().get("tools", []):
            url = (t["tool_config"].get("api_schema") or {}).get("url")
            if url:
                remote.setdefault(url, []).append(t["id"])
        tool_ids = {}
        for tool in fill(tools, base_url, state["secret_id"]):
            name = tool["name"]
            ours = remote.get(tool["api_schema"]["url"], [])
            known = state["tools"].get(name) or (ours or [None])[0]
            for dup in [i for i in ours if i != known]:
                _ok(c.delete(f"/v1/convai/tools/{dup}"))  # duplicates from failed runs
            if known:
                _ok(c.patch(f"/v1/convai/tools/{known}", json={"tool_config": tool}))
                state["tools"][name] = known
            else:
                r = _ok(c.post("/v1/convai/tools", json={"tool_config": tool}))
                state["tools"][name] = r.json()["id"]
            tool_ids[name] = state["tools"][name]
            save()
        prompt = agent["conversation_config"]["agent"]["prompt"]
        prompt["tool_ids"] = [tool_ids[n] for n in prompt.pop("tool_names")]
        if profile in state["agents"]:
            _ok(c.patch(f"/v1/convai/agents/{state['agents'][profile]}", json=agent))
        else:
            r = _ok(c.post("/v1/convai/agents/create", json=agent))
            state["agents"][profile] = r.json()["agent_id"]
        save()
    return f"pushed {profile}: agent {state['agents'][profile]}, {len(tool_ids)} tools"
