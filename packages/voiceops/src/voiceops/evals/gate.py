"""The release gate: run scenarios, check the leak detector, write one results JSON.

  FAIL     any deterministic check fails, or the leak detector misses a planted leak
  ERRORED  a scenario could not run (e.g. the platform is unavailable): never green
  PASS     everything ran and passed
Exit code 0 = PASS, 1 = FAIL, 2 = ERRORED.

"0 leaks" is only reported when the detector passed its known-answer set in this same
run; otherwise `privacy_leaks` is null (unproven), not 0.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

from voiceops._imports import ImportSpecError, load_object
from voiceops.config.project import ConfigError, VoiceopsConfig
from voiceops.evals import known_answer
from voiceops.evals.elevenlabs import ConversationSetup, run_conversation_scenarios
from voiceops.evals.leak import LeakDetector
from voiceops.evals.scenarios import Scenario

EXIT = {"PASS": 0, "FAIL": 1, "ERRORED": 2}
ToolRunnerFactory = Callable[[str], AbstractContextManager[Callable[[str], dict[str, bool]]]]


def select(
    scenarios: list[Scenario], level: str = "all", only: list[str] | None = None, smoke=False
) -> list[Scenario]:
    if only:
        unknown = sorted(set(only) - {s.id for s in scenarios})
        if unknown:
            raise ConfigError(f"unknown scenarios: {unknown}")
        picked = [s for s in scenarios if s.id in set(only)]
    else:
        picked = [s for s in scenarios if s.smoke] if smoke else list(scenarios)
    return [s for s in picked if level == "all" or s.level == level]


def _row(s: Scenario) -> dict:
    return {"id": s.id, "persona": s.persona, "language": s.language, "level": s.level}


def run_tool_scenarios(scenarios: list[Scenario], factory: ToolRunnerFactory, run_id: str):
    rows = []
    with factory(run_id) as run:
        for s in scenarios:
            row = {**_row(s), "checks": {}}
            try:
                row["checks"] = run(s.id)
                row["status"] = "pass" if all(row["checks"].values()) else "fail"
            except Exception as e:  # a crash is a failed scenario, reported as such
                row["status"], row["error"] = "fail", f"{type(e).__name__}: {e}"
            rows.append(row)
    return rows


def run_gate(
    scenarios: list[Scenario],
    detector: LeakDetector,
    cases: list[dict],
    *,
    tool_runner: ToolRunnerFactory | None = None,
    conversation: Callable[[], ConversationSetup] | None = None,
    max_false_alarms: int = 2,
    leak_checks: tuple[str, ...] = ("nothing_disclosed", "no_privacy_leak"),
    mode: str = "full",
    run_id: str | None = None,
) -> dict:
    run_id = run_id or uuid.uuid4().hex[:8]
    by_id: dict[str, dict] = {}
    reasons: list[str] = []

    tools = [s for s in scenarios if s.level == "tool"]
    if tools:
        if tool_runner is None:
            reasons.append("no tool runner configured")
        else:
            by_id |= {r["id"]: r for r in run_tool_scenarios(tools, tool_runner, run_id)}

    convo = [s for s in scenarios if s.level == "conversation"]
    if convo:
        try:
            if conversation is None:
                raise RuntimeError("no conversation setup configured")
            for r in run_conversation_scenarios(convo, conversation(), detector):
                by_id[r["id"]] = r
        except Exception as e:  # outage, quota, no agent pushed: ERRORED, never a pass
            reasons.append(f"{type(e).__name__}: {str(e)[:200]}")

    results = []
    for s in scenarios:
        row = by_id.get(s.id)
        if row is None:
            row = {**_row(s), "checks": {}, "status": "errored"}
            row["error"] = "; ".join(reasons) or "did not run"
        results.append(row)

    leak = known_answer.check(detector, cases, max_false_alarms)
    counts = {
        "passed": sum(r["status"] == "pass" for r in results),
        "failed": sum(r["status"] == "fail" for r in results),
        "errored": sum(r["status"] == "errored" for r in results),
    }
    if counts["failed"] or not leak.ok:
        status = "FAIL"
    elif counts["errored"]:
        status = "ERRORED"
    else:
        status = "PASS"
    summary = (
        f"{counts['passed']} passed, {counts['failed']} failed, {counts['errored']} did "
        f"not run. Leak detector {leak.caught}/{leak.planted} caught, "
        f"{leak.false_alarms} false alarms."
    )
    if not leak.ok:
        summary += f" Leak counts unproven: missed {list(leak.missed) or 'none'}."
    if counts["errored"]:
        summary += " Not green until every scenario runs: " + ("; ".join(reasons) or "see errors")
        summary += "."
    leaks = sum(1 for r in results if any(r["checks"].get(name) is False for name in leak_checks))
    return {
        "run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": mode,
        "gate": {"status": status, "summary": summary},
        "counts": counts,
        "privacy_leaks": leaks if leak.ok else None,
        "leak_detector": leak.to_dict(),
        "scenarios": results,
        "credits_used": sum(r.get("credits_used") or 0 for r in results),
        "provenance": "SIMULATED",
    }


def _load(spec: str, root: Path, what: str):
    try:
        return load_object(spec, root, what)
    except ImportSpecError as e:
        raise ConfigError(str(e)) from e


def gate_from_config(
    cfg: VoiceopsConfig,
    level: str = "all",
    only: list[str] | None = None,
    smoke: bool = False,
) -> tuple[dict, Path]:
    """Runs the gate as the project declares it. Returns (report, path it was written to)."""
    if cfg.evals is None:
        raise ConfigError("voiceops.toml has no [evals] section")
    ev = cfg.evals
    scenarios = select(_load(ev.scenarios, cfg.root, "[evals] scenarios"), level, only, smoke)
    detector = LeakDetector.from_toml(cfg.path(ev.leak_vocabulary))
    cases = known_answer.load_cases(cfg.path(ev.known_answers))
    tool_runner = _load(ev.tool_runner, cfg.root, "[evals] tool_runner") if ev.tool_runner else None
    conversation = None
    if ev.conversation:
        factory = _load(ev.conversation, cfg.root, "[evals] conversation")

        def conversation() -> ConversationSetup:
            return factory(cfg)

    report = run_gate(
        scenarios,
        detector,
        cases,
        tool_runner=tool_runner,
        conversation=conversation,
        max_false_alarms=ev.max_false_alarms,
        leak_checks=tuple(ev.leak_checks),
        mode="partial" if only else ("smoke" if smoke else "full"),
    )
    out_dir = cfg.path(ev.results)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("partial.json" if only else "latest.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report, out


def iter_failures(report: dict) -> Iterator[str]:
    for r in report["scenarios"]:
        if r["status"] != "pass":
            bad = [k for k, v in r.get("checks", {}).items() if v is False]
            yield f"{r['status'].upper():8} {r['id']}: {r.get('error') or ', '.join(bad)}"
