"""Agent configs from prompt fragments and profiles, with shipped safety defaults, tool schemas
from Pydantic models, a drift check and an idempotent push (PLAN §2, §4.4)."""

from voiceops.config.build import build, stale_files, write
from voiceops.config.fragments import SAFETY_SLOTS, kit_fragment
from voiceops.config.project import ConfigError, VoiceopsConfig, load_config
from voiceops.config.push import PushError, dry_run, push

__all__ = [
    "SAFETY_SLOTS",
    "ConfigError",
    "PushError",
    "VoiceopsConfig",
    "build",
    "dry_run",
    "kit_fragment",
    "load_config",
    "push",
    "stale_files",
    "write",
]
