"""Known-answer set for the leak detector: "0 leaks" counts only if it catches every planted leak.

Cases are JSON files (a list per file) in the project's known-answer directory:

    {"id": "...", "context": "patient", "verified_at": null, "expect_leak": true,
     "transcript": [{"role": "agent", "message": "..."}]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from voiceops.evals.leak import LeakDetector

REQUIRED = {"id", "context", "verified_at", "expect_leak", "transcript"}


def load_cases(path: str | Path) -> list[dict]:
    path = Path(path)
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    cases = [c for f in files for c in json.loads(f.read_text())]
    for c in cases:
        missing = REQUIRED - set(c)
        if missing:
            raise ValueError(f"known-answer case {c.get('id', '?')} lacks {sorted(missing)}")
    return cases


@dataclass(frozen=True)
class KnownAnswerReport:
    planted: int
    caught: int
    clean: int
    false_alarms: int
    max_false_alarms: int
    missed: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Every planted leak caught, few enough false alarms, and something was planted."""
        return (
            self.planted > 0
            and self.caught == self.planted
            and self.false_alarms <= self.max_false_alarms
        )

    def to_dict(self) -> dict:
        return {
            "planted": self.planted,
            "caught": self.caught,
            "clean": self.clean,
            "false_alarms": self.false_alarms,
            "ok": self.ok,
        }


def check(detector: LeakDetector, cases: list[dict], max_false_alarms: int = 2):
    def hit(c):
        return bool(detector.find_leaks(c["transcript"], c["verified_at"], c["context"]))

    planted = [c for c in cases if c["expect_leak"]]
    clean = [c for c in cases if not c["expect_leak"]]
    missed = tuple(c["id"] for c in planted if not hit(c))
    return KnownAnswerReport(
        planted=len(planted),
        caught=len(planted) - len(missed),
        clean=len(clean),
        false_alarms=sum(hit(c) for c in clean),
        max_false_alarms=max_false_alarms,
        missed=missed,
    )
