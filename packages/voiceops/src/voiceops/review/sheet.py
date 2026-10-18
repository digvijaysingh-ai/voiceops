"""Review sheet for a real call: header, automatic checks, transcript with the tool timeline,
and a findings table (what happened / root cause / fix) for the reviewer to complete.

Input is the platform's raw conversation (ElevenLabs GET /v1/convai/conversations/{id}).
Phone numbers never reach the sheet: only the last three digits are shown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from voiceops.evals.elevenlabs import verified_index
from voiceops.evals.leak import LeakDetector


@dataclass
class SheetOptions:
    disclosure_markers: list[str] = field(default_factory=lambda: ["ai assistant"])
    verify_tool: str = "verify_identity"
    verified_status: str = "verified"
    leak_context: str = "subject"
    unbacked_promises: list[str] = field(default_factory=list)
    detector: LeakDetector | None = None


@dataclass
class Flag:
    turn: int | None
    what: str


# Nine or more digits, however spaced or punctuated: a phone number said during the call or
# passed to a tool. Dates and short references (CLM-2026-0502) have fewer, or are dates.
PHONE_LIKE = re.compile(r"\+?\d(?:[\s\-.()]*\d){8,}")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _mask_numbers(text: str) -> str:
    def mask(m: re.Match) -> str:
        if ISO_DATE.search(m.group()):
            return m.group()
        return "…" + re.sub(r"\D", "", m.group())[-3:]

    return PHONE_LIKE.sub(mask, text)


def _cell(text: str, limit: int = 400) -> str:
    text = _mask_numbers(" ".join(str(text).split()))
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def _masked(number: str | None) -> str:
    return f"…{number[-3:]}" if number else "n/a"


def _result_text(res: dict) -> str:
    raw = res.get("result_value") or ""
    try:
        body = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if isinstance(body, dict):
        return ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in body.items())
    return raw


def _is_error(res: dict) -> bool:
    """The platform's flag, or a tool that answered with an error body (`{"error": ...}`):
    a guarded API refuses with a normal response, so the platform never marks it."""
    if res.get("is_error"):
        return True
    try:
        body = json.loads(res.get("result_value") or "")
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(body, dict) and bool(body.get("error"))


def _turns(conv: dict) -> list[dict]:
    return [{**t, "message": t.get("message") or ""} for t in conv.get("transcript") or []]


def automatic_checks(
    conv: dict, opts: SheetOptions
) -> tuple[list[tuple[str, str, str]], list[Flag]]:
    """Returns (rows of (check, result, detail), flags for the findings table)."""
    turns = _turns(conv)
    rows: list[tuple[str, str, str]] = []
    flags: list[Flag] = []
    spoken = [(i, t) for i, t in enumerate(turns) if t["role"] == "agent" and t["message"]]
    first = spoken[0][1]["message"].lower() if spoken else ""
    disclosed = any(m.lower() in first for m in opts.disclosure_markers)
    rows.append(
        ("AI disclosed in the first agent turn", "PASS" if disclosed else "FAIL", _cell(first, 120))
    )
    if not disclosed:
        flags.append(Flag(spoken[0][0] if spoken else None, "AI not disclosed in the first turn"))

    v = verified_index(turns, opts.verify_tool, opts.verified_status)
    rows.append(
        (
            "Identity verified",
            "yes" if v is not None else "no",
            f"`{opts.verify_tool}` at turn {v}" if v is not None else "never verified on this call",
        )
    )
    if opts.detector is None:
        rows.append(("No protected detail before verification", "NOT CHECKED", "no vocabulary"))
    else:
        leaks = opts.detector.find_leaks(turns, v, opts.leak_context)
        rows.append(
            (
                "No protected detail before verification",
                "PASS" if not leaks else "FAIL",
                "; ".join(f"turn {f.turn}: {f.reason}" for f in leaks) or "leak detector clean",
            )
        )
        flags += [Flag(f.turn, f"Possible leak: {f.reason}") for f in leaks]

    errors = [
        (i, r) for i, t in enumerate(turns) for r in t.get("tool_results") or [] if _is_error(r)
    ]
    rows.append(
        (
            "Tool errors",
            str(len(errors)),
            "; ".join(
                f"turn {i}: `{r.get('tool_name')}` {_cell(_result_text(r), 80)}" for i, r in errors
            )
            or "none",
        )
    )
    flags += [
        Flag(i, f"`{r.get('tool_name')}` returned an error: {_cell(_result_text(r), 120)}")
        for i, r in errors
    ]

    promised = [
        (i, p)
        for i, t in spoken
        for p in opts.unbacked_promises
        if p.lower() in t["message"].lower()
    ]
    rows.append(
        (
            "Promises no tool backs",
            str(len(promised)),
            "; ".join(f"turn {i}: “{p}”" for i, p in promised) or "none found",
        )
    )
    flags += [Flag(i, f"Agent mentioned “{p}”, which no tool does") for i, p in promised]

    reason = (conv.get("metadata") or {}).get("termination_reason") or ""
    by_agent = "end_call" in reason
    rows.append(("Call ended by the agent", "yes" if by_agent else "no", _cell(reason, 80)))
    if not by_agent:
        flags.append(Flag(None, f"Call did not end with end_call: {reason or 'unknown'}"))
    return rows, flags


def render(conv: dict, opts: SheetOptions, title: str | None = None) -> str:
    meta = conv.get("metadata") or {}
    phone = meta.get("phone_call") or {}
    started = meta.get("start_time_unix_secs")
    when = datetime.fromtimestamp(started, UTC).strftime("%Y-%m-%d %H:%M UTC") if started else "?"
    secs = meta.get("call_duration_secs") or 0
    rows, flags = automatic_checks(conv, opts)
    number = _masked(phone.get("external_number"))
    out = [
        f"# {title or 'Call review'} · `{conv.get('conversation_id', '?')}`",
        "",
        "**Provenance:** MEASURED (a real call, reviewed by a person). Recording and raw "
        "conversation stay local.",
        "",
        "| | |",
        "|---|---|",
        f"| Agent | {_cell(conv.get('agent_name') or conv.get('agent_id') or '?')} |",
        f"| When | {when} |",
        f"| Length | {secs // 60} min {secs % 60} s |",
        f"| Direction | {phone.get('direction', 'n/a')} · number {number} |",
        f"| Language | {meta.get('main_language', '?')} |",
        f"| Status | {conv.get('status', '?')} · {_cell(meta.get('termination_reason') or '?')} |",
        "",
        "## Automatic checks",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
        *[f"| {c} | {r} | {_cell(d)} |" for c, r, d in rows],
        "",
        "## Transcript and tool timeline",
        "",
        "| # | t (s) | Who | What |",
        "|---|---|---|---|",
    ]
    for i, t in enumerate(_turns(conv)):
        ts = t.get("time_in_call_secs", "")
        if t["message"]:
            who = "Agent" if t["role"] == "agent" else "Caller"
            out.append(f"| {i} | {ts} | {who} | {_cell(t['message'])} |")
        for c in t.get("tool_calls") or []:
            params = c.get("params_as_json") or "{}"
            params = "" if params.strip() in ("{}", "") else f" {_cell(params, 160)}"
            out.append(f"| {i} | {ts} | tool | → `{c.get('tool_name')}`{params} |")
        for r in t.get("tool_results") or []:
            mark = " ⚠ error" if _is_error(r) else ""
            out.append(
                f"| {i} | {ts} | tool | ← `{r.get('tool_name')}`{mark}: "
                f"{_cell(_result_text(r), 200)} |"
            )
    out += [
        "",
        "## Findings",
        "",
        "Automatic flags are pre-filled; the reviewer completes root cause and fix, and adds "
        "anything heard in the recording that the checks can't see.",
        "",
        "| # | Turn | What happened | Root cause | Fix |",
        "|---|---|---|---|---|",
        *[
            f"| {n} | {f.turn if f.turn is not None else '–'} | {_cell(f.what)} | | |"
            for n, f in enumerate(flags, start=1)
        ],
        f"| {len(flags) + 1} | | | | |",
        "",
    ]
    return "\n".join(out)
