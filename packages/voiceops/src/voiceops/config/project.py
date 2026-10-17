"""`voiceops.toml`: a project's contract. Every command reads the same declared setup.

Relative paths resolve from the directory holding the file. Unknown keys in the
sections read here are errors, so a typo can't silently fall back to a default.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from voiceops.deploy.spec import Check, SessionSpec

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


class EvalsSection(_Strict):
    scenarios: str  # "package.module:ATTR", a list of voiceops.evals.Scenario
    # "package.module:ATTR", a context manager factory: run_id -> run(scenario_id) -> checks
    tool_runner: str | None = None
    # "package.module:ATTR", a factory: config -> voiceops.evals.ConversationSetup
    conversation: str | None = None
    leak_vocabulary: Path = Path("evals/leak_vocabulary.toml")
    known_answers: Path = Path("evals/known_answer")
    max_false_alarms: int = 2
    # Scenario checks that, when False, count as a privacy leak in the report.
    leak_checks: list[str] = ["nothing_disclosed", "no_privacy_leak"]
    results: Path = Path("evals/results")


class DeploySection(_Strict):
    base_url: str  # or "env:NAME"
    timeout_s: float = 30
    checks: list[Check]


class ReviewSection(_Strict):
    allowlist_env: str = "DEMO_PHONE_ALLOWLIST"  # exactly one E.164 number may be dialled
    profile: str  # the built profile whose pushed agent places test calls
    api_url: str  # the project's API, or "env:NAME"
    session: SessionSpec  # opens the call's session; the agent gets its token
    instructions: str | None = None  # "package.module:ATTR" -> str printed before a call
    recordings: Path = Path("recordings")  # raw conversations and audio (keep gitignored)
    sheets: Path = Path("review")  # rendered review sheets
    disclosure_markers: list[str] = ["ai assistant", "artificial intelligence"]
    verify_tool: str = "verify_identity"
    verified_status: str = "verified"
    leak_context: str = "subject"
    # Phrases that promise something no tool does (e.g. "by email"): flagged for review.
    unbacked_promises: list[str] = []


class VoiceopsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")  # sections for other modules ([ledger], ...)

    project: ProjectSection
    agent: AgentSection
    guard: GuardSection = GuardSection()
    push: PushSection | None = None
    evals: EvalsSection | None = None
    deploy: DeploySection | None = None
    review: ReviewSection | None = None
    root: Path = Path(".")

    def path(self, p: Path) -> Path:
        return p if p.is_absolute() else self.root / p


def resolve_env(value: str) -> str:
    """ "env:NAME" -> the environment variable (must be set); anything else as is."""
    if isinstance(value, str) and value.startswith("env:"):
        name = value[4:]
        v = os.environ.get(name, "").strip()
        if not v:
            raise ConfigError(f"{name} is not set (see .env.example)")
        return v
    return value


def resolve_env_tree(obj):
    if isinstance(obj, dict):
        return {k: resolve_env_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_tree(v) for v in obj]
    return resolve_env(obj)


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
