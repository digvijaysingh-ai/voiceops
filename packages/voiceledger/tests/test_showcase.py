"""Known-answer test: Slotback's signed showcase bundle verifies with its checks as rules.

The bundle (synthetic, simulated day; pseudonymous IDs) and its trusted key are copied
from Slotback's evidence folder. Expected results match Slotback's own verifier.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from voiceledger import load_rules_file, read_bundle, verify
from voiceledger.cli import main as cli

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLE = FIXTURES / "showcase-2026-10-16.zip"
RULES_FILE = FIXTURES / "slotback_rules.toml"
TRUSTED_KEY = (FIXTURES / "trusted_key.txt").read_text().strip()


def outcome(report):
    return {name: (c.status, c.seq) for name, c in report.checks.items()}


def test_showcase_bundle_verifies_with_rules():
    events, snaps, head = read_bundle(BUNDLE.read_bytes())
    report = verify(events, snaps, head, TRUSTED_KEY, load_rules_file(RULES_FILE))
    assert report.ok
    assert outcome(report) == {
        "chain": ("pass", None),
        "identity_before_detail": ("pass", None),
        "list_order": ("pass", None),
        "head": ("pass", None),
    }
    assert report.events == 1707
    assert report.checks["identity_before_detail"].detail.startswith("300 ")
    assert report.checks["list_order"].detail.startswith("14 ")


def test_showcase_tamper_known_answer():
    """Change the patient on event #11 (an accepted offer): chain and list_order fail there."""
    data = BUNDLE.read_bytes()
    with zipfile.ZipFile(BytesIO(data)) as z:
        members = {n: z.read(n) for n in z.namelist()}
    lines = members["ledger.jsonl"].decode().splitlines()
    ev = json.loads(lines[10])
    assert ev["seq"] == 11 and ev["type"] == "offer_accepted"
    ev["payload"]["pid"] = "P-99999"
    lines[10] = json.dumps(ev)
    members["ledger.jsonl"] = ("\n".join(lines) + "\n").encode()
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)

    events, snaps, head = read_bundle(buf.getvalue())
    report = verify(events, snaps, head, TRUSTED_KEY, load_rules_file(RULES_FILE))
    assert outcome(report) == {
        "chain": ("fail", 11),
        "identity_before_detail": ("pass", None),
        "list_order": ("fail", 11),
        "head": ("pass", None),
    }


def test_cli_with_project_config(tmp_path, capsys):
    """The CLI reads `[ledger] rules` straight from a project's config file."""
    args = ["verify", str(BUNDLE), "--trusted-key", TRUSTED_KEY, "--rules", str(RULES_FILE)]
    assert cli(args) == 0
    assert capsys.readouterr().out.startswith("PASS  1707 events")


def test_without_rules_only_chain_and_head_run(capsys):
    assert cli(["verify", str(BUNDLE), "--trusted-key", TRUSTED_KEY]) == 0
    out = capsys.readouterr().out
    assert "list_order" not in out and "no rules configured" in out
