"""Pluggable ledger rules.

A rule is a small object with a `name` and `check(events, snapshots) -> Check`. The
verifier always runs `chain` and `head`; everything else is a rule the project declares,
usually in its config file:

    [ledger]
    rules = [
      { rule = "requires_before", name = "identity_before_detail",
        effect = "detail_disclosed", precondition = "identity_verified",
        scope = ["call_id", "pid"] },
    ]

Generic rules shipped here:

  requires_before        every `effect` comes after a `precondition` with the same scope.
  follows_snapshot_order per group, ranked events walk a snapshotted list 1, 2, 3... with
                         the right subject at each rank, leaving a rank only on a logged
                         `advance_on` event; a `fill` needs a prior `claim` by that subject.
  always_followed_by     every `trigger` is followed by a `response` with the same scope,
                         optionally within `within` seconds.
  never                  `event` does not occur.

Events are selected by a matcher: an event type ("offer_started"), or a table with a
type and payload conditions ({ type = "call_attempt", where = { outcome = ["busy"] } }).
Field names resolve to the event's own fields (chain_id, seq, ts, type, actor, slot_id)
first, then to its payload.

Projects add their own rules by subclassing `Rule`, and make them loadable from config
with `@register_rule("kind")`.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from voiceledger.chain import Event, snapshot_hash
from voiceledger.result import Check

EVENT_FIELDS = frozenset({"chain_id", "seq", "ts", "type", "actor", "slot_id"})
RESERVED_NAMES = frozenset({"chain", "head"})  # always-on checks


class RuleConfigError(ValueError):
    pass


def field_value(ev: Event, name: str, default: Any = None) -> Any:
    if name in EVENT_FIELDS:
        return getattr(ev, name)
    return ev.payload.get(name, default)


@dataclass(frozen=True)
class Matcher:
    type: str
    where: Mapping[str, Any] = field(default_factory=dict)

    def matches(self, ev: Event) -> bool:
        if ev.type != self.type:
            return False
        for name, want in self.where.items():
            allowed = want if isinstance(want, list | tuple | set | frozenset) else [want]
            if field_value(ev, name) not in allowed:
                return False
        return True

    def __str__(self) -> str:
        if not self.where:
            return self.type
        conds = ", ".join(f"{k}={v}" for k, v in self.where.items())
        return f"{self.type}[{conds}]"

    @classmethod
    def parse(cls, spec: str | Mapping | Matcher) -> Matcher:
        if isinstance(spec, Matcher):
            return spec
        if isinstance(spec, str):
            return cls(spec)
        if isinstance(spec, Mapping) and "type" in spec and set(spec) <= {"type", "where"}:
            return cls(spec["type"], dict(spec.get("where", {})))
        raise RuleConfigError(f"not an event matcher: {spec!r}")


class Rule:
    """Base class. Subclasses set `kind` and implement `check`."""

    kind: ClassVar[str] = ""
    name: str

    def check(self, events: list[Event], snapshots: dict[str, list[str]] | None) -> Check:
        raise NotImplementedError


RULE_TYPES: dict[str, type[Rule]] = {}


def register_rule(kind: str):
    def deco(cls: type[Rule]) -> type[Rule]:
        cls.kind = kind
        RULE_TYPES[kind] = cls
        return cls

    return deco


def _scope_key(ev: Event, scope: Sequence[str]) -> tuple:
    return tuple(field_value(ev, f, "") for f in scope)


def _describe(scope: Sequence[str], key: tuple) -> str:
    return ", ".join(f"{f}={v}" for f, v in zip(scope, key, strict=True))


@register_rule("requires_before")
@dataclass
class RequiresBefore(Rule):
    effect: Any
    precondition: Any
    scope: Sequence[str] = ()
    name: str | None = None

    def __post_init__(self):
        self.effect = Matcher.parse(self.effect)
        self.precondition = Matcher.parse(self.precondition)
        self.scope = tuple(self.scope)
        self.name = self.name or self.kind

    def check(self, events, snapshots):
        c = Check()
        satisfied: set[tuple] = set()
        effects = 0
        for ev in events:
            key = _scope_key(ev, self.scope)
            if self.precondition.matches(ev):
                satisfied.add(key)
            elif self.effect.matches(ev):
                effects += 1
                if key not in satisfied:
                    where = f" ({_describe(self.scope, key)})" if self.scope else ""
                    c.fail(ev.seq, f"{self.effect} before {self.precondition}{where}")
        if c.status == "pass":
            c.detail = f"{effects} {self.effect} events, all after {self.precondition}"
        return c


@register_rule("follows_snapshot_order")
@dataclass
class FollowsSnapshotOrder(Rule):
    snapshot: Any
    group_by: str
    subject_field: str
    ranked: Sequence[str]
    advance_on: Sequence[Any]
    rank_field: str = "rank"
    claim: Any = None
    fill: Any = None
    id_field: str = "snapshot_id"
    hash_field: str = "snapshot_hash"
    name: str | None = None

    def __post_init__(self):
        self.snapshot = Matcher.parse(self.snapshot)
        self.ranked = frozenset(self.ranked)
        self.advance_on = tuple(Matcher.parse(m) for m in self.advance_on)
        self.claim = Matcher.parse(self.claim) if self.claim else None
        self.fill = Matcher.parse(self.fill) if self.fill else None
        self.name = self.name or self.kind

    def check(self, events, snapshots):
        c = Check()
        if snapshots is None:
            c.status, c.detail = "not_checked", "no snapshots supplied (ledger-only upload)"
            return c
        state: dict[Any, dict] = {}  # group -> {"ids": [...], "cur": int, "claimed": id}
        groups = 0
        subj = self.subject_field
        for ev in events:
            group = field_value(ev, self.group_by)
            if group is None:
                continue
            if self.snapshot.matches(ev):
                sid = ev.payload.get(self.id_field)
                ids = snapshots.get(sid)
                if ids is None:
                    c.fail(ev.seq, f"snapshot {sid} missing from bundle")
                    continue
                if snapshot_hash(ids) != ev.payload.get(self.hash_field):
                    c.fail(ev.seq, f"snapshot {sid} does not match its recorded hash")
                    continue
                state[group] = {"ids": ids, "cur": 1, "claimed": None}
                groups += 1
                continue
            st = state.get(group)
            if st is None or ev.type not in self.ranked or self.rank_field not in ev.payload:
                continue
            rank, sid = ev.payload[self.rank_field], ev.payload.get(subj)
            if rank != st["cur"]:
                c.fail(ev.seq, f"rank {rank} acted on while rank {st['cur']} was still open")
                continue
            if rank > len(st["ids"]) or st["ids"][rank - 1] != sid:
                c.fail(ev.seq, f"{subj} at rank {rank} does not match the snapshot")
                continue
            if self.claim and self.claim.matches(ev):
                st["claimed"] = sid
            elif self.fill and self.fill.matches(ev) and st["claimed"] != sid:
                c.fail(ev.seq, f"{self.fill} without a prior {self.claim} from this {subj}")
            elif any(m.matches(ev) for m in self.advance_on):
                st["cur"] += 1
        if c.status == "pass":
            c.detail = f"{groups} snapshots followed in order (per {self.group_by})"
        return c


@register_rule("always_followed_by")
@dataclass
class AlwaysFollowedBy(Rule):
    trigger: Any
    response: Any
    scope: Sequence[str] = ()
    within: float | None = None  # seconds, compared on event `ts` (ISO 8601)
    name: str | None = None

    def __post_init__(self):
        self.trigger = Matcher.parse(self.trigger)
        self.response = Matcher.parse(self.response)
        self.scope = tuple(self.scope)
        self.name = self.name or self.kind

    def check(self, events, snapshots):
        c = Check()
        failures: list[tuple[int, str]] = []
        pending: dict[tuple, list[Event]] = {}
        triggers = 0
        for ev in events:
            key = _scope_key(ev, self.scope)
            if self.response.matches(ev):
                for t in pending.pop(key, []):
                    late = self._late(t, ev)
                    if late:
                        failures.append((t.seq, late))
            elif self.trigger.matches(ev):
                triggers += 1
                pending.setdefault(key, []).append(ev)
        for opened in pending.values():
            for t in opened:
                failures.append((t.seq, f"{self.trigger} never followed by {self.response}"))
        for seq, detail in sorted(failures):
            c.fail(seq, detail)
        if c.status == "pass":
            limit = f" within {self.within:g}s" if self.within is not None else ""
            c.detail = f"{triggers} {self.trigger} events, each followed by {self.response}{limit}"
        return c

    def _late(self, trigger: Event, response: Event) -> str | None:
        if self.within is None:
            return None
        try:
            gap = (
                datetime.fromisoformat(response.ts) - datetime.fromisoformat(trigger.ts)
            ).total_seconds()
        except (TypeError, ValueError):
            return f"cannot compare timestamps {trigger.ts!r} and {response.ts!r}"
        if gap > self.within:
            return f"{self.response} came {gap:g}s after {self.trigger} (limit {self.within:g}s)"
        return None


@register_rule("never")
@dataclass
class Never(Rule):
    event: Any
    where: Mapping[str, Any] = field(default_factory=dict)
    name: str | None = None

    def __post_init__(self):
        m = Matcher.parse(self.event)
        self.event = Matcher(m.type, {**m.where, **self.where}) if self.where else m
        self.name = self.name or self.kind

    def check(self, events, snapshots):
        c = Check()
        for ev in events:
            if self.event.matches(ev):
                c.fail(ev.seq, f"{self.event} occurred")
                break
        if c.status == "pass":
            c.detail = f"no {self.event} in {len(events)} events"
        return c


def load_rules(config: Iterable[Mapping[str, Any]]) -> list[Rule]:
    """Build rules from config tables: each has `rule = "<kind>"` plus that rule's fields."""
    rules: list[Rule] = []
    for i, entry in enumerate(config, start=1):
        cfg = dict(entry)
        kind = cfg.pop("rule", None)
        cls = RULE_TYPES.get(kind)
        if cls is None:
            known = ", ".join(sorted(RULE_TYPES))
            raise RuleConfigError(f"rule #{i}: unknown rule {kind!r} (known: {known})")
        try:
            rules.append(cls(**cfg))
        except TypeError as e:
            raise RuleConfigError(f"rule #{i} ({kind}): {e}") from e
    check_names(rules)
    return rules


def check_names(rules: Sequence[Rule]) -> None:
    seen: set[str] = set()
    for r in rules:
        if r.name in RESERVED_NAMES or r.name in seen:
            raise RuleConfigError(f"rule name {r.name!r} is reserved or used twice")
        seen.add(r.name)


def load_rules_file(path: str | Path) -> list[Rule]:
    """Read rules from a TOML file: top-level `rules = [...]` or `[ledger] rules = [...]`."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    entries = data.get("rules", data.get("ledger", {}).get("rules"))
    if entries is None:
        raise RuleConfigError(f"{path}: no `rules` or `[ledger] rules` found")
    return load_rules(entries)
