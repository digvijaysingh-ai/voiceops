"""Run the declared deploy checks against a live deployment.

Run after every deploy and before pushing the agent config. Each check prints
PASS or FAIL with a short detail; the run passes only if every check does. Checks run
in the declared order; `p95` uses the latencies recorded by `signed_roundtrip`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from voiceops.config.project import ConfigError, VoiceopsConfig, resolve_env, resolve_env_tree
from voiceops.deploy import spec


@dataclass
class Result:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'}  {self.name}  ({self.detail})"


def _json(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _mismatch(body: dict, expect: dict) -> str | None:
    for k, v in expect.items():
        if body.get(k) != v:
            return f"{k}={body.get(k)!r}, expected {v!r}"
    return None


def p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, int(len(ordered) * 0.95) - 1)]


class Runner:
    def __init__(self, cfg: VoiceopsConfig, client: httpx.Client):
        self.cfg, self.c = cfg, client
        self.samples: list[float] = []
        g = cfg.guard
        self.secret_header, self.token_header = g.secret_header, g.token_header
        env_name = cfg.push.tool_secret_env if cfg.push else "TOOL_SECRET"
        self.tool_secret = os.environ.get(env_name, "")

    def run(self, check) -> Result:
        try:
            return getattr(self, f"_{check.check}")(check)
        except httpx.HTTPError as e:
            return Result(check.check, False, f"{type(e).__name__}: {e}"[:120])
        except ConfigError as e:
            return Result(check.check, False, str(e))

    def _health(self, ch: spec.Health) -> Result:
        r = self.c.get(ch.path)
        body = _json(r)
        bad = _mismatch(body, ch.expect)
        for k, minimum in ch.at_least.items():
            if not isinstance(body.get(k), int | float) or body[k] < minimum:
                bad = bad or f"{k}={body.get(k)!r}, expected >= {minimum:g}"
        ok = r.status_code == 200 and bad is None
        return Result("health", ok, bad or r.text[:80])

    def _unsigned_refused(self, ch: spec.UnsignedRefused) -> Result:
        r = self.c.post(f"/tools/{ch.tool}", json={}, headers={self.token_header: "x"})
        return Result("unsigned tool call refused", r.status_code == 401, str(r.status_code))

    def _signed_roundtrip(self, ch: spec.SignedRoundtrip) -> Result:
        if not self.tool_secret:
            return Result("signed tool call", False, "tool secret is not set")
        s = ch.session
        r = self.c.post(s.path, json=resolve_env_tree(s.body))
        token = _json(r).get(s.token_field)
        if r.status_code != 200 or not token:
            return Result("signed tool call", False, f"session: {r.status_code} {r.text[:60]}")
        headers = {self.secret_header: self.tool_secret, self.token_header: token}
        for _ in range(ch.samples):
            t0 = time.perf_counter()
            r = self.c.post(f"/tools/{ch.tool}", json=ch.body, headers=headers)
            self.samples.append((time.perf_counter() - t0) * 1000)
        bad = _mismatch(_json(r), ch.expect)
        return Result("signed tool call", r.status_code == 200 and bad is None, bad or r.text[:80])

    def _ledger_verifies(self, ch: spec.LedgerVerifies) -> Result:
        bundle = self.cfg.path(ch.bundle).read_bytes()
        key = self.cfg.path(ch.trusted_key).read_text().strip()
        r = self.c.post(ch.path, params={"trusted_key": key}, files={"file": ("b.zip", bundle)})
        body = _json(r)
        head = str(body.get("checks", {}).get("head", {}).get("detail", ""))[:60]
        ok = r.status_code == 200 and body.get("ok") is True
        return Result("evidence bundle verifies", ok, head or r.text[:60])

    def _pages(self, ch: spec.Pages) -> Result:
        bad = [p for p in ch.paths if self.c.get(p).status_code != 200]
        detail = ",".join(bad) or f"{len(ch.paths)} pages"
        return Result("all pages render", not bad, detail)

    def _p95(self, ch: spec.P95) -> Result:
        name = f"tool round trip p95 ≤ {ch.budget_ms:g} ms (client-side)"
        if not self.samples:
            return Result(name, False, "no samples: declare signed_roundtrip before p95")
        value = p95(self.samples)
        return Result(name, value <= ch.budget_ms, f"{value:.0f} ms")


def run_checks(
    cfg: VoiceopsConfig, base_url: str | None = None, client: httpx.Client | None = None
) -> list[Result]:
    if cfg.deploy is None:
        raise ConfigError("voiceops.toml has no [deploy] section")
    base = (base_url or resolve_env(cfg.deploy.base_url)).rstrip("/")
    c = client or httpx.Client(base_url=base, timeout=cfg.deploy.timeout_s)
    with c:
        runner = Runner(cfg, c)
        return [runner.run(ch) for ch in cfg.deploy.checks]
