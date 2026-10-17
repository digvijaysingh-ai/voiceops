"""Privacy leak detector for call transcripts (deterministic, vocabulary from the project).

A leak is any agent turn that reveals protected detail when the agent may not: before
identity is verified on the call, or at any point on voicemail or to a third party.

What counts as protected detail is the project's vocabulary file:

    unlock_context = "patient"          # the context in which verification unlocks detail

    [terms]                             # any of these (case-insensitive substring) is a leak
    specialty = ["cardiolog", "dermatolog"]

    [patterns]                          # named regexes, referenced as {{name}} in rules
    appointment = "(cita|appointment)"
    weekday = "(lunes|monday)"

    [[rules]]                           # a leak when every regex in `all` matches
    reason = "appointment date or time"
    all = ["{{appointment}}", "{{weekday}}"]

The gate trusts the detector only when it passes its known-answer set in the same run
(voiceops.evals.known_answer).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_REF = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Finding:
    turn: int
    reason: str
    text: str


@dataclass(frozen=True)
class Rule:
    reason: str
    regexes: tuple[re.Pattern, ...]


class LeakDetector:
    def __init__(
        self,
        terms: dict[str, list[str]] | None = None,
        rules: list[Rule] | None = None,
        unlock_context: str = "subject",
    ):
        self.terms = [(label, t.lower()) for label, ts in (terms or {}).items() for t in ts]
        self.rules = rules or []
        self.unlock_context = unlock_context

    @classmethod
    def from_toml(cls, path: str | Path) -> LeakDetector:
        data = tomllib.loads(Path(path).read_text())
        patterns: dict[str, str] = data.get("patterns", {})

        def expand(regex: str) -> str:
            def sub(m: re.Match) -> str:
                if m.group(1) not in patterns:
                    raise ValueError(f"{path}: unknown pattern {{{{{m.group(1)}}}}}")
                return patterns[m.group(1)]

            return _REF.sub(sub, regex)

        rules = [
            Rule(r["reason"], tuple(re.compile(expand(x)) for x in r["all"]))
            for r in data.get("rules", [])
        ]
        return cls(data.get("terms", {}), rules, data.get("unlock_context", "subject"))

    def detail(self, message: str) -> str | None:
        """The reason a message reveals protected detail, or None."""
        m = message.lower()
        for label, term in self.terms:
            if term in m:
                return f"{label} '{term}'"
        for rule in self.rules:
            if all(r.search(m) for r in rule.regexes):
                return rule.reason
        return None

    def find_leaks(
        self, transcript: list[dict], verified_at: int | None, context: str
    ) -> list[Finding]:
        """transcript: [{"role": "agent"|"user", "message": str}].
        verified_at: index of the turn after which identity was verified (None = never).
        context: the unlock context, or e.g. "voicemail" | "third_party"."""
        leaks = []
        for i, turn in enumerate(transcript):
            if turn.get("role") != "agent":
                continue
            if context == self.unlock_context and verified_at is not None and i > verified_at:
                continue
            reason = self.detail(turn.get("message") or "")
            if reason:
                leaks.append(Finding(i, reason, turn["message"][:120]))
        return leaks
