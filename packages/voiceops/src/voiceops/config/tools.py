"""Webhook tool schemas from the project's Pydantic request models.

The project exposes `TOOLS: dict[name, (model, description)]`; `[agent] tools` points at it.
Deployment-specific values stay as placeholders filled at push time:
{{TOOL_BASE_URL}} and {{TOOL_SECRET_ID}}.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import get_args

from pydantic import BaseModel

from voiceops.config.project import ConfigError, VoiceopsConfig

Tools = dict[str, tuple[type[BaseModel], str]]


def load_tools(spec: str, root: Path) -> Tools:
    module_name, _, attr = spec.partition(":")
    if not attr:
        raise ConfigError(f"[agent] tools must look like 'package.module:ATTR', got {spec!r}")
    added = str(root) not in sys.path
    if added:
        sys.path.insert(0, str(root))
    try:
        return getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as e:
        raise ConfigError(f"cannot load tools from {spec!r}: {e}") from e
    finally:
        if added:
            sys.path.remove(str(root))


def _property(schema: dict) -> dict:
    out = {"description": schema.get("description", "")}
    if "enum" in schema or "const" in schema:
        out |= {"type": "string", "enum": schema.get("enum") or [schema["const"]]}
    else:
        out["type"] = {"boolean": "boolean", "integer": "integer"}.get(schema.get("type"), "string")
    return out


def tool_schema(name: str, tools: Tools, cfg: VoiceopsConfig) -> dict:
    model, description = tools[name]
    js = model.model_json_schema()
    props = {k: _property(v) for k, v in js.get("properties", {}).items()}
    for field_name, field in model.model_fields.items():  # Literal -> enum
        args = get_args(field.annotation)
        if args and all(isinstance(a, str) for a in args):
            props[field_name] = {
                "type": "string",
                "enum": list(args),
                "description": field.description or "",
            }
    undocumented = sorted(k for k, p in props.items() if not p["description"])
    if undocumented:  # the platform rejects parameters without a description
        raise ConfigError(f"tool {name}: parameters without a description: {undocumented}")
    guard = cfg.guard
    return {
        "type": "webhook",
        "name": name,
        "description": description,
        "response_timeout_secs": cfg.agent.tool_timeout_secs,
        "api_schema": {
            "url": "{{TOOL_BASE_URL}}/tools/" + name,
            "method": "POST",
            "request_headers": {
                guard.secret_header: {"secret_id": "{{TOOL_SECRET_ID}}"},
                guard.token_header: {"variable_name": guard.token_variable},
            },
            "request_body_schema": {
                "type": "object",
                "description": description,
                "properties": props,
                "required": sorted(js.get("required", [])),
            },
        },
    }
