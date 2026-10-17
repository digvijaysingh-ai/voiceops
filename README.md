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

## Results

Measured against the plan's success criteria ([`docs/RESULTS.md`](docs/RESULTS.md)):

- **Parity:** Slotback runs on the kit. The ledger verifier gives the same result per check on 403 mutated copies of Slotback's signed evidence bundle; its agent configs rebuild byte-identical; its tests, its 12 tool-level eval scenarios and its live deploy checks all pass, with identical eval reports.
- **Thin use cases:** a second deployment (an insurer's claims line) started with `voiceops init` is 302 lines of domain Python on 2,841 lines of kit, with no kit code copied.
- **Independent verification:** `pip install voiceledger` is 4 packages and 12 MB, with no web framework or database driver.
- **Self-tested:** known-answer sets for the ledger and the leak detector, and `init` output that must pass its own gate, tests and lint.

## Develop

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

Postgres tests use `TEST_DATABASE_URL`, or an embedded server via `pgserver`.
