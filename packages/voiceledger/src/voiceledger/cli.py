"""`voiceledger verify <bundle.zip | ledger.jsonl> [--trusted-key HEX] [--rules FILE]`

`--rules` takes a TOML file with `rules = [...]` or a `[ledger] rules` table (so a
project's voiceops.toml works as is). Without it only `chain` and `head` are checked.

Exit code 0 when every supplied invariant holds, 1 otherwise, 2 for unreadable input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from voiceledger.bundle import BundleInvalid, read_bundle, read_ledger_jsonl
from voiceledger.rules import RuleConfigError, load_rules_file
from voiceledger.verify import verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voiceledger")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="verify a bundle or a bare ledger.jsonl")
    v.add_argument("path", type=Path)
    v.add_argument("--trusted-key", help="trusted public key of the head signer (hex)")
    v.add_argument("--rules", type=Path, help="TOML file declaring the ledger rules")
    v.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        rules = load_rules_file(args.rules) if args.rules else []
    except (OSError, RuleConfigError, ValueError) as e:
        print(f"invalid rules: {e}", file=sys.stderr)
        return 2
    data = args.path.read_bytes()
    try:
        if args.path.suffix == ".jsonl":
            events, snapshots, head = read_ledger_jsonl(data.decode("utf-8")), None, None
        else:
            events, snapshots, head = read_bundle(data)
    except BundleInvalid as e:
        print(f"invalid: {e}", file=sys.stderr)
        return 2
    report = verify(events, snapshots, head, args.trusted_key, rules)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(("PASS" if report.ok else "FAIL") + f"  {report.events} events")
        for name, c in report.checks.items():
            where = f" at #{c.seq}" if c.seq else ""
            print(f"  {c.status.upper():12} {name}{where}: {c.detail}")
        if not rules:
            print("  (no rules configured: only chain and head were checked)")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
