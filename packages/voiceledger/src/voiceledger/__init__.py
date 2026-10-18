"""voiceledger: hash-chained, signed event ledger with pluggable rules and a standalone verifier.

No web framework or database driver needed to verify (Postgres storage is the optional
`voiceledger[postgres]` extra). Rules: see voiceledger.rules and docs/PLAN.md §4.2.
"""

from voiceledger.bundle import BundleInvalid, read_bundle, write_bundle
from voiceledger.chain import GENESIS, Event, append, seal, snapshot_hash
from voiceledger.result import Check, Report
from voiceledger.rules import (
    AlwaysFollowedBy,
    FollowsSnapshotOrder,
    Matcher,
    Never,
    RequiresBefore,
    Rule,
    RuleConfigError,
    load_rules,
    load_rules_file,
    register_rule,
)
from voiceledger.sign import SignedHead, SigningKeyMissing, sign_head
from voiceledger.verify import verify

__version__ = "0.1.4"
__all__ = [
    "GENESIS",
    "AlwaysFollowedBy",
    "BundleInvalid",
    "Check",
    "Event",
    "FollowsSnapshotOrder",
    "Matcher",
    "Never",
    "Report",
    "RequiresBefore",
    "Rule",
    "RuleConfigError",
    "SignedHead",
    "SigningKeyMissing",
    "append",
    "load_rules",
    "load_rules_file",
    "read_bundle",
    "register_rule",
    "seal",
    "sign_head",
    "snapshot_hash",
    "verify",
    "write_bundle",
]
