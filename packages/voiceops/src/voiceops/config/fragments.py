"""Prompt composition with the kit's safety defaults (PLAN §4.4).

Each safety slot is satisfied in every profile in exactly one way:

  reworded   the project maps the slot to its own fragment ([agent.safety]); that
             fragment must then be in the profile, or the build fails.
  default    the kit's wording (voiceops/config/safety/<slot>.md) is appended.
  removed    only with a reason in [agent.allow_remove]; recorded in safety.json.

So safety can be reworded, but never dropped silently.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

from voiceops.config.project import AgentSection, ConfigError

SAFETY_SLOTS = (
    "ai_disclosure",
    "upset_caller",
    "identity_before_detail",
    "third_party_voicemail",
    "human_on_request",
    "no_unbacked_promises",
    "closing",
)
_VAR = re.compile(r"\{\{(\w+)\}\}")


def kit_fragment(slot: str) -> str:
    return files("voiceops.config").joinpath("safety", f"{slot}.md").read_text().strip()


def _fill(slot: str, text: str, values: dict[str, str]) -> str:
    missing = sorted({v for v in _VAR.findall(text) if v not in values})
    if missing:
        raise ConfigError(
            f"kit safety fragment {slot!r} needs [agent.vars] {', '.join(missing)}"
            f' (or reword it with [agent.safety] {slot} = "<your fragment>")'
        )
    return _VAR.sub(lambda m: values[m.group(1)], text)


def check_safety_config(agent: AgentSection) -> None:
    unknown = sorted((set(agent.safety) | set(agent.allow_remove)) - set(SAFETY_SLOTS))
    if unknown:
        raise ConfigError(f"unknown safety slots {unknown} (known: {', '.join(SAFETY_SLOTS)})")
    both = sorted(set(agent.safety) & set(agent.allow_remove))
    if both:
        raise ConfigError(f"safety slots both reworded and removed: {both}")
    blank = sorted(k for k, why in agent.allow_remove.items() if not why.strip())
    if blank:
        raise ConfigError(f"allow_remove needs a reason for: {blank}")


def compose(
    key: str, fragments: list[str], agent: AgentSection, fragment_dir: Path, recording: bool
) -> tuple[str, dict[str, str]]:
    """Returns (prompt, how each safety slot was satisfied) for one profile."""
    missing = [f for f in agent.required_fragments if f not in fragments]
    if missing:
        raise ConfigError(f"profile {key} is missing required fragments: {missing}")
    parts = [(fragment_dir / f"{f}.md").read_text().strip() for f in fragments]
    values = {"recording_notice": ", and that the call is recorded" if recording else ""}
    values |= agent.vars
    manifest: dict[str, str] = {}
    for slot in SAFETY_SLOTS:
        if slot in agent.allow_remove:
            manifest[slot] = f"removed: {agent.allow_remove[slot]}"
        elif slot in agent.safety:
            frag = agent.safety[slot]
            if frag not in fragments:
                raise ConfigError(
                    f"profile {key} drops safety slot {slot!r}: its fragment {frag!r} is not"
                    " in the profile (add it, or [agent.allow_remove] with a reason)"
                )
            manifest[slot] = f"project: {frag}"
        else:
            parts.append(_fill(slot, kit_fragment(slot), values))
            manifest[slot] = "kit default"
    return "\n\n".join(parts) + "\n", manifest
