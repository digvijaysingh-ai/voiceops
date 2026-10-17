"""Compose agent configs from fragments + profiles, and tool schemas from the request models.

Output is deterministic and committed, so every prompt or tool change shows up as a
reviewable diff; `check` fails on drift (for CI). Files written to [agent] build_dir:

  tools.json        webhook tool definitions
  <profile>.json    one agent config per profile
  safety.json       how each safety slot was satisfied in each profile
"""

from __future__ import annotations

import json

from voiceops.config.fragments import check_safety_config, compose
from voiceops.config.project import ConfigError, VoiceopsConfig
from voiceops.config.tools import Tools, load_tools, tool_schema


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def agent_config(key: str, p: dict, cfg: VoiceopsConfig, tools: Tools) -> tuple[dict, dict]:
    a = cfg.agent
    direction = a.directions.get(p["direction"])
    if direction is None:
        raise ConfigError(f"profile {key}: no [agent.directions.{p['direction']}]")
    prompt, safety = compose(
        key, p["fragments"], a, cfg.path(a.fragments), recording=p["record_voice"]
    )
    phone = direction.phone_audio
    config = {
        "name": p["name"],
        "conversation_config": {
            "agent": {
                "first_message": direction.first_message,
                "language": a.language,
                "prompt": {
                    "llm": a.llm,
                    "temperature": a.temperature,
                    "prompt": prompt,
                    "tool_names": [n for n in tools if n not in direction.exclude_tools],
                    # The agent hangs up itself after the goodbye, instead of looping on
                    # goodbyes until the caller hangs up.
                    "built_in_tools": {
                        "end_call": {
                            "name": "end_call",
                            "description": a.end_call_description,
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                },
            },
            "tts": {
                "model_id": a.tts_model,
                "voice_id": a.voice_id,
                **({"agent_output_audio_format": "ulaw_8000"} if phone else {}),
            },
            **({"asr": {"user_input_audio_format": "ulaw_8000"}} if phone else {}),
        },
        "platform_settings": {
            "privacy": {
                "record_voice": p["record_voice"],
                "zero_retention_mode": p["zero_retention_mode"],
            }
        },
    }
    return config, safety


def build(cfg: VoiceopsConfig) -> dict[str, str]:
    """Returns {filename: text} for the build directory. Raises ConfigError."""
    check_safety_config(cfg.agent)
    tools = load_tools(cfg.agent.tools, cfg.root)
    for direction, d in cfg.agent.directions.items():
        unknown = sorted(set(d.exclude_tools) - set(tools))
        if unknown:
            raise ConfigError(f"[agent.directions.{direction}] excludes unknown tools {unknown}")
    profiles = json.loads(cfg.path(cfg.agent.profiles).read_text())
    files = {"tools.json": _dump([tool_schema(n, tools, cfg) for n in tools])}
    manifest = {}
    for key, p in profiles.items():
        if key.startswith("_"):
            continue
        files[f"{key}.json"], manifest[key] = agent_config(key, p, cfg, tools)
        files[f"{key}.json"] = _dump(files[f"{key}.json"])
    files["safety.json"] = _dump(manifest)
    return files


def stale_files(cfg: VoiceopsConfig, files: dict[str, str]) -> list[str]:
    out = cfg.path(cfg.agent.build_dir)
    return [n for n, t in files.items() if not (out / n).exists() or (out / n).read_text() != t]


def write(cfg: VoiceopsConfig, files: dict[str, str]) -> None:
    out = cfg.path(cfg.agent.build_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
