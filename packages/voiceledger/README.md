# voiceledger

A hash-chained, signed event ledger with pluggable rules and a standalone verifier. One dependency (`cryptography`): an auditor can verify a ledger without installing the system that wrote it.

```bash
pip install voiceledger
voiceledger verify bundle.zip --trusted-key <hex> --rules rules.toml
```

```
PASS  1707 events
  PASS         chain: 1707 events linked
  PASS         identity_before_detail: 300 detail_disclosed events, all after identity_verified
  PASS         list_order: 14 snapshots followed in order (per slot_id)
  PASS         head: signed head matches
```

- **chain**: each event's hash covers the event and the previous hash, so editing, inserting, deleting or reordering any event breaks every later link.
- **head**: the last hash is signed with Ed25519; with the trusted key, the whole chain can't be regenerated and re-signed by anyone else.
- **rules**: what the project declares, e.g. `requires_before` (no detail before identity, per call), `follows_snapshot_order` (offers in the customer's list order), `always_followed_by`, `never`. Custom rules subclass `voiceledger.Rule`.

A bundle is a zip with `ledger.jsonl`, `list_snapshots.jsonl` and `head.json`. Reading is defensive (size caps, known members only, JSON only). Exit code 0 when every check holds, 1 otherwise, 2 for unreadable input.

What it proves: the record wasn't changed after the head was signed, and the declared rules hold over it. What it doesn't: that the events were true. Reconcile against the system of record for that.

Part of voiceops; rules reference in its `docs/guide.md`.
