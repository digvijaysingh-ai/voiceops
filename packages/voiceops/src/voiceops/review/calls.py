"""Allow-listed test calls and call fetch (ElevenLabs + Twilio).

    voiceops call --setup        import the Twilio number into ElevenLabs (once)
    voiceops call                ring the ONE allow-listed number with the review profile's agent
    voiceops fetch <conv_id>     save the raw conversation + audio, render a review sheet

The only number that can ever be dialled is the single E.164 number in the allow-list
environment variable. Secrets come from the environment and are never printed; phone
numbers are printed masked. Raw conversations and audio stay in the recordings folder,
which must be gitignored.
API shapes: elevenlabs.io/docs/api-reference (phone-numbers, twilio/outbound-call,
conversations), checked 2026-10-16.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import httpx

from voiceops.config.project import ConfigError, VoiceopsConfig, resolve_env, resolve_env_tree

API = "https://api.elevenlabs.io"
E164 = re.compile(r"^\+[1-9]\d{6,14}$")
CONV_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise ConfigError(f"{name} is not set (see .env.example)")
    return v


def allowed_number(cfg: VoiceopsConfig) -> str:
    env_name = _review(cfg).allowlist_env
    nums = [n.strip() for n in _env(env_name).split(",") if n.strip()]
    if len(nums) != 1 or not E164.match(nums[0]):
        raise ConfigError(f"{env_name} must be exactly one E.164 number, e.g. +34600000000")
    return nums[0]


def _review(cfg: VoiceopsConfig):
    if cfg.review is None:
        raise ConfigError("voiceops.toml has no [review] section")
    return cfg.review


def _el(cfg: VoiceopsConfig, client: httpx.Client | None) -> httpx.Client:
    key_env = cfg.push.api_key_env if cfg.push else "ELEVENLABS_API_KEY"
    return client or httpx.Client(base_url=API, headers={"xi-api-key": _env(key_env)}, timeout=60)


def _state_file(cfg: VoiceopsConfig) -> Path:
    if cfg.push is None:
        raise ConfigError("voiceops.toml has no [push] section (agent IDs live in its state)")
    return cfg.path(cfg.push.state)


def import_number(cfg: VoiceopsConfig, client: httpx.Client | None = None) -> str:
    """Import the Twilio number into ElevenLabs (idempotent); stores its ID in the push state."""
    state_file = _state_file(cfg)
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    from_number = _env("TWILIO_FROM_NUMBER")
    with _el(cfg, client) as c:
        r = c.get("/v1/convai/phone-numbers")
        r.raise_for_status()
        existing = [p for p in r.json() if p.get("phone_number") == from_number]
        if existing:
            pn_id = existing[0]["phone_number_id"]
        else:
            r = c.post(
                "/v1/convai/phone-numbers",
                json={
                    "provider": "twilio",
                    "phone_number": from_number,
                    "label": f"{cfg.project.name} test calls (allow-listed number only)",
                    "sid": _env("TWILIO_ACCOUNT_SID"),
                    "token": _env("TWILIO_AUTH_TOKEN"),
                },
            )
            if r.status_code >= 400:
                raise ConfigError(f"import failed: {r.status_code} {r.text[:300]}")
            pn_id = r.json()["phone_number_id"]
    state["phone_number_id"] = pn_id
    state_file.write_text(json.dumps(state, indent=2) + "\n")
    return pn_id


def place_call(
    cfg: VoiceopsConfig,
    client: httpx.Client | None = None,
    api_client: httpx.Client | None = None,
) -> dict:
    """Ring the allow-listed number. Returns {conversation_id, to (masked), call_id}."""
    rv = _review(cfg)
    to = allowed_number(cfg)
    state_file = _state_file(cfg)
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    agent_id = (state.get("agents") or {}).get(rv.profile)
    if not agent_id:
        raise ConfigError(f"profile {rv.profile!r} is not pushed (run `voiceops push`)")
    if "phone_number_id" not in state:
        raise ConfigError("no phone number imported (run `voiceops call --setup`)")
    s = rv.session
    api = api_client or httpx.Client(base_url=resolve_env(rv.api_url).rstrip("/"), timeout=90)
    with api:
        api.get("/health")  # wake a sleeping instance
        r = api.post(s.path, json=resolve_env_tree(s.body))
        r.raise_for_status()
        sess = r.json()
    with _el(cfg, client) as c:
        r = c.post(
            "/v1/convai/twilio/outbound-call",
            json={
                "agent_id": agent_id,
                "agent_phone_number_id": state["phone_number_id"],
                "to_number": to,
                "conversation_initiation_client_data": {
                    "dynamic_variables": {
                        "secret__call_token": sess[s.token_field],
                        "call_id": sess[s.call_id_field],
                    }
                },
            },
        )
        if r.status_code >= 400:
            raise ConfigError(f"call failed: {r.status_code} {r.text[:300]}")
        body = r.json()
    return {
        "conversation_id": body.get("conversation_id"),
        "to": f"…{to[-3:]}",
        "call_id": sess[s.call_id_field],
    }


def recordings_ignored(path: Path) -> bool | None:
    """True if git ignores the folder, False if it would be committed, None outside git."""
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-q", str(path)], cwd=path.parent, capture_output=True
        )
    except FileNotFoundError:
        return None
    return {0: True, 1: False}.get(r.returncode)


def fetch_conversation(
    cfg: VoiceopsConfig, conv_id: str, client: httpx.Client | None = None
) -> tuple[dict, Path]:
    """Save the raw conversation (JSON) and audio (mp3) locally. Returns (conversation, dir)."""
    if not CONV_ID.match(conv_id):
        raise ConfigError("bad conversation id")
    out = cfg.path(_review(cfg).recordings)
    out.mkdir(parents=True, exist_ok=True)
    with _el(cfg, client) as c:
        r = c.get(f"/v1/convai/conversations/{conv_id}")
        if r.status_code >= 400:
            raise ConfigError(f"fetch failed: {r.status_code} {r.text[:200]}")
        conv = r.json()
        (out / f"{conv_id}.json").write_text(json.dumps(conv, indent=1, ensure_ascii=False))
        r = c.get(f"/v1/convai/conversations/{conv_id}/audio")
        if r.status_code < 400:
            (out / f"{conv_id}.mp3").write_bytes(r.content)
    return conv, out
