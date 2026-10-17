# voiceops

**Deployment discipline for voice agents, as a kit.** Safety defaults, a release gate, a verifiable audit ledger, deploy checks and a real-call review loop, so a new voice-agent deployment starts trustworthy instead of becoming trustworthy later.

Extracted from [Slotback](https://github.com/digvijaysingh-ai/slotback), a full deployment of an ElevenLabs agent for a (fictional) public hospital.

> **Status: early build.** `voiceledger` (ledger, signing, bundles, pluggable rules, verifier CLI) is ported; the kit modules are next. The plan, scope and success criteria are in [`docs/PLAN.md`](docs/PLAN.md); decisions in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## What's in it (planned)

| Package / module | Does |
|---|---|
| `voiceledger` | Hash-chained, signed event ledger with pluggable rules and a standalone verifier |
| `voiceops.config` | Agent configs composed from prompt fragments, with safe defaults and a drift check; idempotent push |
| `voiceops.guard` | Tool secret, per-call tokens bound to one subject, webhook signatures, rate limits, safe logs |
| `voiceops.evals` | PASS / FAIL / ERRORED gate; tool-level and conversation scenarios; leak detector with known-answer sets |
| `voiceops.deploy` | Declarative checks against a live deployment |
| `voiceops.review` | Allow-listed test calls and review sheets for real recordings |
| `voiceops init` | A new use-case project with all of the above wired in |
