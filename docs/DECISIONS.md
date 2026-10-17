# Decisions

| Date | Decision | Why | Alternative not taken |
|---|---|---|---|
| 2026-10-17 | Extract the deployment discipline from Slotback into a kit, then prove it with a second deployment | One deployment shows it can be done; a repeatable one shows it scales | Building a second project from scratch |
| 2026-10-17 | Two packages: `voiceledger` (standalone verifier) and `voiceops` (the kit) | An auditor must verify a ledger without installing a web stack | One package with extras |
| 2026-10-17 | Ledger checks become pluggable generic rules (`requires_before`, `follows_snapshot_order`, `always_followed_by`, `never`) | Slotback's two domain checks are instances of general patterns other use cases need | Hard-coded checks per project |
| 2026-10-17 | One project contract file, `voiceops.toml` | Every command reads the same declared setup; nothing hidden in constants | Per-command flags |
| 2026-10-17 | Safety prompt fragments ship as defaults that can be reworded but not dropped without a logged reason | Safety shouldn't depend on remembering to copy it | Optional examples |
| 2026-10-17 | Scope: ElevenLabs only in v1, behind adapter modules | Depth over breadth; adapters keep the door open | Multi-platform from the start |
| 2026-10-17 | Acceptance = Slotback migrated with no behaviour change, then a new use case live in ≤ 3 working days | Measurable, not "it feels reusable" | Feature checklist |
| 2026-10-17 | Keep the ledger's v1 wire format byte-identical: event fields (incl. `slot_id` as the subject key), the `slotledger-head` signing tag, bundle member names | Any rename changes every hash and signature, so existing signed bundles (Slotback's evidence) would stop verifying | Generic names with a migration tool |
| 2026-10-17 | Event-type allow-list is optional (`types=` on `seal` / `record_and_apply`), not built into the ledger | The set of event types is domain knowledge; Slotback passes its own list | A kit-wide event vocabulary |
| 2026-10-17 | `follows_snapshot_order` takes explicit `ranked`, `advance_on`, `claim`, `fill` (with payload matchers) rather than a single `event` | Slotback's list-order check depends on which events close a rank (e.g. `call_attempt` with outcome `busy`); a shorter config couldn't reproduce it | Hard-coding Slotback's rank lifecycle |
| 2026-10-17 | Rule check details are generic wording; status and failing seq are the parity contract | Same result on 403 mutated copies of the showcase bundle (status + seq per check) vs Slotback's verifier | Per-rule message templates to copy Slotback's wording |
