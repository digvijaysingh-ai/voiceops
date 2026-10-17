# voiceops

**Deployment discipline for voice agents, as a kit.** Safety defaults, a release gate, a verifiable audit ledger, deploy checks and a real-call review loop, so a new voice-agent deployment starts trustworthy instead of becoming trustworthy later.

Extracted from [Slotback](https://github.com/digvijaysingh-ai/slotback), a full deployment of an ElevenLabs agent for a (fictional) public hospital.

```bash
uvx voiceops init claims-line --organisation "Example Insurance"
cd claims-line && uv sync
uv run voiceops gate      # PASS from scratch
```

## What's in it

| Package / module | Does | Command |
|---|---|---|
| `voiceledger` | Hash-chained, Ed25519-signed event ledger; pluggable rules; standalone verifier (one dependency) | `voiceledger verify` |
| `voiceops.config` | Agent configs from prompt fragments; seven safety slots that can be reworded, never dropped silently; tool schemas from Pydantic models; drift check; idempotent push | `voiceops build`, `push` |
| `voiceops.guard` | Tool secret, per-call tokens bound to one subject, webhook HMAC, rate limits, allow-listed JSON logs | (library) |
| `voiceops.evals` | PASS / FAIL / ERRORED gate; tool-level and conversation scenarios; leak detector that must pass its known-answer set first | `voiceops gate` |
| `voiceops.deploy` | Declarative checks against a live deployment | `voiceops check` |
| `voiceops.review` | Allow-listed test calls; review sheets with automatic checks and a findings table | `voiceops call`, `fetch`, `review` |
| `voiceops init` | A new use-case project with all of the above wired in | `voiceops init` |

How each part works and how to configure it: [`docs/guide.md`](docs/guide.md). A generated project, as `init` writes it: [`examples/case-line`](examples/case-line).

## How it's tested

The kit tests itself the way it tests agents:

- Ported Slotback's own test suites and checks against the original on real Slotback data: the ledger verifier gives the same result per check on 403 mutated copies of Slotback's signed evidence bundle; agent configs rebuild byte-identical; Slotback's 117 tests pass with its guard code replaced; its 12 tool-level scenarios give identical reports through the kit gate; its recorded ElevenLabs Tests results score identically through the kit adapter.
- The leak detector's known-answer rule is enforced in the gate itself.
- `voiceops init` output passes its own gate, tests and lint in the kit's CI, from scratch.

## Status

Built: every module above. Next: Slotback migrated onto the kit, then a second deployment started with `voiceops init`, timed. Plan and success criteria: [`docs/PLAN.md`](docs/PLAN.md); decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Develop

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

Postgres tests use `TEST_DATABASE_URL`, or an embedded server via `pgserver`.
