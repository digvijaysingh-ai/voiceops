"""`voiceops.toml`: a project's contract. Every command reads the same declared setup.

Relative paths resolve from the directory holding the file. Unknown keys in the
sections read here are errors, so a typo can't silently fall back to a default.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

CONFIG_FILE = "voiceops.toml"


class ConfigError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectSection(_Strict):
    name: str
    locales: list[str] = []


class Direction(_Strict):
    first_message: str
    phone_audio: bool = False  # telephone codec (ulaw_8000) in and out, no resampling
    exclude_tools: list[str] = []


class AgentSection(_Strict):
    fragments: Path = Path("agent/fragments")
    profiles: Path = Path("agent/profiles.json")
    build_dir: Path = Path("agent/build")
    tools: str  # "package.module:ATTR", a dict of name -> (Pydantic model, description)
    language: str
    llm: str
    temperature: float = 0.2
    voice_id: str
    tts_model: str = "eleven_flash_v2_5"
    tool_timeout_secs: int = 10
    end_call_description: str = "End the call after logging the outcome and saying goodbye."
    directions: dict[str, Direction]
    # Kit safety slot -> the project fragment that rewords it (see voiceops.config.safety).
    safety: dict[str, str] = {}
    # Kit safety slot -> reason for dropping it. Recorded in the build's safety.json.
    allow_remove: dict[str, str] = {}
    # Project fragments every profile must include (domain safety rules).
    required_fragments: list[str] = []
    # Values for {{placeholders}} in the kit's default safety fragments.
    vars: dict[str, str] = {}


class GuardSection(_Strict):
    secret_header: str = "X-Tool-Secret"
    token_header: str = "X-Call-Token"
    token_variable: str = "secret__call_token"  # platform dynamic variable holding the token


class PushSection(_Strict):
    secret_name: str  # name of the tool secret stored on the platform
    state: Path = Path("agent/state.json")
    api_base: str = "https://api.elevenlabs.io"
    api_key_env: str = "ELEVENLABS_API_KEY"
    tool_secret_env: str = "TOOL_SECRET"


class VoiceopsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")  # sections for other modules ([ledger], ...)

    project: ProjectSection
    agent: AgentSection
    guard: GuardSection = GuardSection()
    push: PushSection | None = None
    root: Path = Path(".")

    def path(self, p: Path) -> Path:
        return p if p.is_absolute() else self.root / p


def load_config(path: str | Path = CONFIG_FILE) -> VoiceopsConfig:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"{path} not found") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    try:
        return VoiceopsConfig(**data, root=path.resolve().parent)
    except ValidationError as e:
        raise ConfigError(f"{path}: {e}") from e
