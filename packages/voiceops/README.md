# voiceops

**Deployment discipline for voice agents, as a kit.** Safety defaults, a release gate, a verifiable audit ledger, deploy checks and a real-call review loop, so a new voice-agent deployment starts trustworthy instead of becoming trustworthy later.

Extracted from [Slotback](https://github.com/digvijaysingh-ai/slotback), a full deployment of an ElevenLabs agent for a fictional public hospital, and proven on a second, different deployment, [claims-surge](https://github.com/digvijaysingh-ai/claims-surge), a fictional insurer's claims line.

```bash
uvx voiceops init claims-line --organisation "Example Insurance"
cd claims-line && uv sync
uv run voiceops gate      # PASS from scratch
```

## What's in it

| Part | Does | Command |
|---|---|---|
| [`voiceledger`](https://pypi.org/project/voiceledger/) | Hash-chained, Ed25519-signed event ledger; pluggable rules; standalone verifier (one dependency) | `voiceledger verify` |
| `voiceops.config` | Agent configs from prompt fragments; seven safety slots that can be reworded, never dropped silently; tool schemas from Pydantic models; drift check; a push that only touches its own tools | `voiceops build`, `push` |
| `voiceops.guard` | Tool secret, per-call tokens bound to one subject, webhook HMAC, rate limits, allow-listed JSON logs; FastAPI dependencies that refuse before reading the body | (library) |
| `voiceops.evals` | PASS / FAIL / ERRORED gate; tool-level and conversation scenarios on ElevenLabs Tests; a leak detector that must pass its known-answer set first; forbidden phrases checked on every agent turn | `voiceops gate` |
| `voiceops.deploy` | Declarative checks against a live deployment | `voiceops check` |
| `voiceops.review` | Test calls to one allow-listed number; review sheets with automatic checks, masked numbers and a findings table | `voiceops call`, `fetch`, `review` |
| `voiceops init` | A new project with all of the above wired in | `voiceops init` |

Extras: `voiceops[fastapi]` (guard dependencies), `voiceops[postgres]` (token store, ledger storage).

## Results

Measured against the plan's success criteria ([RESULTS.md](https://github.com/digvijaysingh-ai/voiceops/blob/main/docs/RESULTS.md)):

- **Parity:** Slotback runs on the kit with no change in behaviour: the same verdict on 403 tampered copies of its signed ledger, byte-identical agent configs, identical eval reports, live deploy checks passing.
- **Second deployment:** claims-surge was live with its conversation gate passing 1 h 17 min after `voiceops init`: 302 lines of domain Python on 2,841 lines of kit, none copied.
- **Field findings:** going live with a second deployment, simulated conversations and real calls produced six kit releases (0.1.1 to 0.1.6), each with a test: a push that would have touched another project's tools, a platform constraint, a transfer the platform's judge passed, a check that flagged a correct refusal, a tool error read out as "your details don't match", and a phone number left unmasked on a review sheet.
- **Independent verification:** `pip install voiceledger` is 4 packages and 12 MB, with no web framework or database driver.

## Documentation

- Guide: [docs/guide.md](https://github.com/digvijaysingh-ai/voiceops/blob/main/docs/guide.md)
- Decisions: [docs/DECISIONS.md](https://github.com/digvijaysingh-ai/voiceops/blob/main/docs/DECISIONS.md)
- A generated project: [examples/case-line](https://github.com/digvijaysingh-ai/voiceops/tree/main/examples/case-line)

MIT licence.
