"""Eval scenarios.

level="tool": the safeguard is enforced in code, so the scenario runs deterministically
through the project's own service layer and needs no LLM and no credits.
level="conversation": the behaviour lives in the agent's speech (tone, language switch,
refusals), so it runs as a simulated conversation against the pushed agent.
"""

from __future__ import annotations

from dataclasses import dataclass

LEVELS = ("tool", "conversation")


@dataclass(frozen=True)
class Scenario:
    id: str
    persona: str
    language: str
    level: str  # "tool" | "conversation"
    smoke: bool
    prompt: str = ""  # simulated-user instructions (conversation level)
    success: str = ""  # success criterion for the platform's judge (conversation level)

    def __post_init__(self):
        if self.level not in LEVELS:
            raise ValueError(f"scenario {self.id}: level must be one of {LEVELS}")
        if self.level == "conversation" and not (self.prompt and self.success):
            raise ValueError(f"scenario {self.id}: conversation scenarios need prompt and success")
