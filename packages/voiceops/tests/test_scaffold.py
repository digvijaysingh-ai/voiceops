"""`voiceops init`: a new project passes its own gate, tests and lint from scratch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from voiceops.cli import app
from voiceops.config import ConfigError, build, load_config, stale_files
from voiceops.evals import gate_from_config
from voiceops.scaffold import init

REPO = Path(__file__).resolve().parents[3]
CACHES = {"__pycache__", ".ruff_cache", ".pytest_cache", "results"}


def project_files(root: Path) -> dict[Path, bytes]:
    return {
        p.relative_to(root): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not CACHES & set(p.relative_to(root).parts)
    }


EXAMPLE = REPO / "examples" / "case-line"


@pytest.fixture
def fresh_modules(monkeypatch):
    """Generated projects all use the package names `app` and `evals`."""
    for name in list(sys.modules):
        if name.split(".")[0] in ("app", "evals"):
            monkeypatch.delitem(sys.modules, name)


@pytest.fixture
def project(tmp_path, fresh_modules) -> Path:
    init(tmp_path / "demo", "demo", "Northwind Energy")
    return tmp_path / "demo"


def test_files_placeholders_and_dotfiles(project):
    files = project_files(project)
    names = {p.as_posix() for p in files}
    for expected in (
        ".gitignore",
        ".env.example",
        ".github/workflows/ci.yml",
        "voiceops.toml",
        "agent/build/safety.json",
        "agent/build/tools.json",
        "app/service.py",
    ):
        assert expected in names
    assert not [n for n in names if "dot_" in n]
    for p, data in files.items():
        text = data.decode()
        assert "__PROJECT" not in text and "__ORGANISATION__" not in text, p
    assert "Northwind Energy" in (project / "agent" / "build" / "outbound-demo.json").read_text()


def test_build_is_committed_and_up_to_date(project):
    cfg = load_config(project / "voiceops.toml")
    assert stale_files(cfg, build(cfg)) == []


def test_every_safety_slot_is_met_and_no_recording_profile_says_so(project):
    import json

    safety = json.loads((project / "agent" / "build" / "safety.json").read_text())
    assert all(v == "kit default" for p in safety.values() for v in p.values())
    prod = json.loads(
        (project / "agent" / "build" / "inbound-production-reference.json").read_text()
    )
    agent = prod["conversation_config"]["agent"]
    assert "recorded" not in agent["first_message"]
    assert "the call is recorded" not in agent["prompt"]["prompt"]


def test_gate_passes_from_scratch(project):
    report, _ = gate_from_config(load_config(project / "voiceops.toml"))
    assert report["gate"]["status"] == "PASS", report["gate"]["summary"]
    assert report["counts"] == {"passed": 5, "failed": 0, "errored": 0}
    assert report["leak_detector"]["ok"] and report["privacy_leaks"] == 0


def test_generated_tests_and_lint_pass(project):
    def run(*args):
        return subprocess.run(args, cwd=project, capture_output=True, text=True)

    tests = run(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")
    assert tests.returncode == 0, tests.stdout[-2000:]
    lint = run(sys.executable, "-m", "ruff", "check", ".")
    assert lint.returncode == 0, lint.stdout
    fmt = run(sys.executable, "-m", "ruff", "format", "--check", ".")
    assert fmt.returncode == 0, fmt.stdout


def test_refuses_a_non_empty_directory_and_bad_names(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "keep.txt").write_text("mine")
    with pytest.raises(ConfigError, match="not empty"):
        init(tmp_path / "x", "demo", "Org")
    with pytest.raises(ConfigError, match="name"):
        init(tmp_path / "y", "Bad Name", "Org")
    assert (tmp_path / "x" / "keep.txt").read_text() == "mine"


def test_kit_source_points_uv_at_a_local_checkout(tmp_path, fresh_modules):
    init(tmp_path / "d", "demo", "Org", kit_source=REPO / "packages")
    text = (tmp_path / "d" / "pyproject.toml").read_text()
    assert "[tool.uv.sources]" in text and str(REPO / "packages" / "voiceops") in text


def test_cli_init(tmp_path, fresh_modules):
    r = CliRunner().invoke(app, ["init", "demo", "--directory", str(tmp_path / "d")])
    assert r.exit_code == 0 and "voiceops gate" in r.stdout


def test_committed_example_matches_a_fresh_init(tmp_path, fresh_modules):
    """examples/case-line is `voiceops init case-line --organisation "Northwind Energy"`."""
    init(tmp_path / "case-line", "case-line", "Northwind Energy")
    assert project_files(EXAMPLE) == project_files(tmp_path / "case-line"), (
        "regenerate: rm -rf examples/case-line && voiceops init case-line"
        ' --organisation "Northwind Energy" --directory examples/case-line'
    )
