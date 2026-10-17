"""Check results and the verification report."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Check:
    status: str = "pass"  # pass | fail | not_checked
    detail: str = ""
    seq: int | None = None

    def fail(self, seq: int | None, detail: str) -> None:
        if self.status != "fail":  # keep the first failure
            self.status, self.seq, self.detail = "fail", seq, detail


@dataclass
class Report:
    events: int
    checks: dict[str, Check] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks.values())

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "events": self.events,
            "checks": {k: vars(v) for k, v in self.checks.items()},
        }
