"""Deploy checks against a fake API: each check passes on a good deployment and fails alone."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from voiceops.cli import app
from voiceops.config import ConfigError, load_config
from voiceops.deploy.run import p95, run_checks

RealClient = httpx.Client  # the CLI test patches httpx.Client inside the runner

DEPLOY = """
[deploy]
base_url = "env:NORTHWIND_API_URL"

[[deploy.checks]]
check = "health"
at_least = { accounts = 1 }
expect = { ok = true }

[[deploy.checks]]
check = "unsigned_refused"
tool = "get_balance"

[[deploy.checks]]
check = "signed_roundtrip"
tool = "get_balance"
session = { path = "/api/sessions", body = { code = "env:NORTHWIND_CODE" } }
expect = { error = "identity_not_verified" }
samples = 5

[[deploy.checks]]
check = "ledger_verifies"
bundle = "evidence/bundle.zip"
trusted_key = "evidence/key.txt"

[[deploy.checks]]
check = "pages"
paths = ["/", "/status"]

[[deploy.checks]]
check = "p95"
budget_ms = 5000
"""


class FakeAPI:
    def __init__(self, **broken):
        self.broken = broken
        self.sessions = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, h = request.url.path, request.headers
        if path == "/health":
            return httpx.Response(
                200, json={"ok": True, "accounts": 0 if "health" in self.broken else 3}
            )
        if path.startswith("/tools/"):
            if h.get("X-Tool-Secret") != "s3cret" and "unsigned" not in self.broken:
                return httpx.Response(401, json={"detail": "missing or invalid tool secret"})
            leaked = "signed" in self.broken
            return httpx.Response(
                200, json={"ok": leaked} | ({} if leaked else {"error": "identity_not_verified"})
            )
        if path == "/api/sessions":
            self.sessions.append(json.loads(request.content))
            return httpx.Response(200, json={"call_token": "tok", "call_id": "c1"})
        if path == "/api/verify":
            ok = "ledger" not in self.broken and request.url.params.get("trusted_key") == "abc"
            return httpx.Response(
                200, json={"ok": ok, "checks": {"head": {"detail": "signed head matches"}}}
            )
        if path in ("/", "/status"):
            return httpx.Response(500 if path == self.broken.get("page") else 200, text="ok")
        return httpx.Response(404)

    def client(self):
        return RealClient(base_url="https://api.test", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def cfg(project, monkeypatch):
    monkeypatch.setenv("NORTHWIND_API_URL", "https://api.test")
    monkeypatch.setenv("NORTHWIND_CODE", "letmein")
    monkeypatch.setenv("TOOL_SECRET", "s3cret")
    (project.parent / "evidence").mkdir()
    (project.parent / "evidence" / "bundle.zip").write_bytes(b"zip")
    (project.parent / "evidence" / "key.txt").write_text("abc\n")
    project.write_text(project.read_text() + DEPLOY)
    return load_config(project)


def outcome(results):
    return [(r.name, r.ok) for r in results]


def test_healthy_deployment_passes_every_check(cfg):
    fake = FakeAPI()
    results = run_checks(cfg, client=fake.client())
    assert all(r.ok for r in results), [r.line() for r in results]
    assert len(results) == 6
    assert fake.sessions == [{"code": "letmein"}]  # env: values resolved
    assert results[4].detail == "2 pages"


@pytest.mark.parametrize(
    "broken, failing",
    [
        ({"health": 1}, "health"),
        ({"unsigned": 1}, "unsigned tool call refused"),
        ({"signed": 1}, "signed tool call"),
        ({"ledger": 1}, "evidence bundle verifies"),
        ({"page": "/status"}, "all pages render"),
    ],
)
def test_each_check_fails_on_its_own(cfg, broken, failing):
    results = run_checks(cfg, client=FakeAPI(**broken).client())
    assert [r.name for r in results if not r.ok] == [failing]


def test_p95_over_budget_fails(cfg):
    cfg.deploy.checks[-1].budget_ms = 0
    results = run_checks(cfg, client=FakeAPI().client())
    assert not results[-1].ok


def test_p95_without_samples_fails(cfg):
    cfg.deploy.checks = [c for c in cfg.deploy.checks if c.check != "signed_roundtrip"]
    assert "no samples" in run_checks(cfg, client=FakeAPI().client())[-1].detail


def test_network_error_is_a_failed_check_not_a_crash(cfg):
    def down(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(down))
    results = run_checks(cfg, client=client)
    assert not any(r.ok for r in results) and "ConnectTimeout" in results[0].detail


def test_missing_env_value_fails_cleanly(cfg, monkeypatch):
    monkeypatch.delenv("NORTHWIND_API_URL")
    with pytest.raises(ConfigError, match="NORTHWIND_API_URL is not set"):
        run_checks(cfg)


def test_p95_index():
    assert p95([float(i) for i in range(1, 21)]) == 19.0
    assert p95([7.0]) == 7.0


def test_unknown_check_is_a_config_error(project):
    project.write_text(
        project.read_text() + '\n[deploy]\nbase_url = "x"\n[[deploy.checks]]\ncheck = "nope"\n'
    )
    with pytest.raises(ConfigError, match="nope"):
        load_config(project)


def test_cli_check_reports_and_exits(cfg, project, monkeypatch):
    monkeypatch.setattr(
        "voiceops.deploy.run.httpx.Client",
        lambda **kw: FakeAPI(page="/").client(),
    )
    result = CliRunner().invoke(app, ["check", "--config", str(project)])
    assert result.exit_code == 1
    assert "FAIL  all pages render  (/)" in result.stdout and "PASS  health" in result.stdout
