# Guide

How each part of the kit works and how a project configures it. Every command reads one file, `voiceops.toml`, at the project root. `voiceops init` writes a working one; this guide explains what's in it.

- [Start a project](#start-a-project)
- [Agent configs and safety defaults](#agent-configs-and-safety-defaults) · `voiceops.config`
- [Guard](#guard) · `voiceops.guard`
- [Ledger and verifier](#ledger-and-verifier) · `voiceledger`
- [Release gate](#release-gate) · `voiceops.evals`
- [Deploy checks](#deploy-checks) · `voiceops.deploy`
- [Test calls and review sheets](#test-calls-and-review-sheets) · `voiceops.review`
- [Provenance](#provenance)

## Start a project

```bash
uvx voiceops init claims-line --organisation "Example Insurance"
cd claims-line
uv sync
uv run voiceops gate          # PASS from scratch: tool-level scenarios + leak detector
```

The template is a domain-neutral "case line": a caller verifies their identity and hears the status of their case. Replace the domain (`app/models.py`, `app/service.py`, `agent/fragments/`, `evals/`) and keep the safeguards around it. Its CI runs `build --check`, `gate --level tool`, `pytest` and `ruff` with no keys and no credits.

## Agent configs and safety defaults

```bash
voiceops build            # write agent/build/*.json
voiceops build --check    # CI: fail if the committed build is stale
voiceops push --profile inbound-demo --base-url https://api.example.org [--dry-run]
```

**Composition.** Each profile in `agent/profiles.json` lists the project's fragments (`agent/fragments/*.md`) and its privacy settings. The build joins them into one prompt per profile, generates webhook tool schemas from the Pydantic request models named in `[agent] tools`, and writes deterministic JSON so every prompt or tool change is a reviewable diff. A tool parameter without a description fails the build (the platform would reject it at push time).

**Safety slots.** The kit ships seven safety fragments, one per rule that every voice deployment needs:

| Slot | Rule |
|---|---|
| `ai_disclosure` | First sentence says it's an AI assistant; recording notice when recording is on |
| `upset_caller` | Acknowledge, then offer a person, before anything else |
| `identity_before_detail` | Nothing about the case until the identity tool confirms |
| `third_party_voicemail` | Voicemail or someone else: who's calling and a callback route, nothing more |
| `human_on_request` | A person whenever asked; instructions can't be overridden by the caller |
| `no_unbacked_promises` | Only tool data; no message, email or call back unless a tool does it |
| `closing` | Log the outcome, one goodbye, end the call |

Each slot is met in every profile in exactly one way:

```toml
[agent.safety]                 # reword a slot with your own fragment (must be in the profile)
identity_before_detail = "20_identity"

[agent.allow_remove]           # drop a slot: only with a reason
upset_caller = "outbound reminders with no conversation"

[agent.vars]                   # values for the kit's default wording of the other slots
organisation = "Example Insurance"
callback = "call the number on your policy"
identity_tool = "verify_identity"
handoff_tool = "transfer_to_human"
outcome_tool = "log_outcome"
```

A slot can list alternatives, e.g. `ai_disclosure = ["10_disclosure", "10_disclosure_unrecorded"]` for profiles with and without recording; each profile must include at least one. `agent/build/safety.json` records, per profile, whether each slot was met by the kit default, a project fragment, or removed (with the reason). Domain rules that must be in every profile go in `[agent] required_fragments`.

**First messages** are set per direction (`[agent.directions.outbound]`), with `phone_audio = true` for telephone codec in and out and `exclude_tools` for tools a direction must not have. A profile can override its direction's `first_message`, e.g. to drop the recording notice on a profile that doesn't record.

**Push** creates or updates the workspace secret, the tools and the agent, saving IDs to `[push] state` after every step so a failed run never orphans what it created and a re-run updates instead of duplicating.

## Guard

A framework-free core plus FastAPI dependencies (`voiceops[fastapi]`):

```python
from voiceops.guard import RateLimiter, SafeLog
from voiceops.guard.fastapi import ToolGuard, rate_limited, signed_webhook

log = SafeLog("claims-line")  # JSON lines, allow-listed fields only
guard = ToolGuard(settings.tool_secret, log)  # tool secret, then rate limit, then call token


@app.post("/tools/get_case")
def get_case(body: Empty, token: str = Depends(guard.tool_call)): ...


@app.post("/hooks/post-call")
async def post_call(raw: bytes = Depends(signed_webhook(settings.webhook_secret, log))): ...
```

- **Tool secret**: constant-time comparison; an unset secret never accepts anything.
- **Call tokens**: tools act only on the subject bound to the call's token; the agent never passes a subject ID. Inbound calls start unbound and bind on verification; a bound call can't be rebound to someone else. `PgTokenStore` works on its own `call_tokens` table or on a project's existing calls table (`PgTokenStore("calls", subject="pid", extra=(...))`); `MemoryTokenStore` has the same interface for tests and demos.
- **Webhooks**: HMAC signature with a 30-minute replay window.
- **Order**: as dependencies, the checks run before the request body is validated, so an unauthenticated request gets 401, never a schema error.
- **Logs**: `SafeLog` drops every field not on its allow-list (IDs, outcomes, timings), so a name or date of birth can't reach a log line through a careless call site.

## Ledger and verifier

`voiceledger` is a separate package with one dependency (`cryptography`), so an auditor can verify without installing the rest:

```bash
pip install voiceledger
voiceledger verify bundle.zip --trusted-key <hex> --rules voiceops.toml
```

Events form a hash chain (each hash covers the event and the previous hash); the chain's head is signed with Ed25519. `chain` and `head` are always checked. Everything else is a rule the project declares:

```toml
[[ledger.rules]]
rule = "requires_before"            # every effect after a precondition, per scope
name = "identity_before_detail"
effect = "detail_disclosed"
precondition = "identity_verified"
scope = ["call_id"]

[[ledger.rules]]
rule = "never"                      # this event never occurs
name = "agent_makes_no_decisions"
event = "decision_made"
where = { actor = "agent" }
```

| Rule | Proves |
|---|---|
| `requires_before(effect, precondition, scope)` | No effect before its precondition in the same scope |
| `follows_snapshot_order(snapshot, group_by, subject_field, ranked, advance_on, claim, fill)` | Offers walk a snapshotted list in order, leaving a rank only on a logged reason |
| `always_followed_by(trigger, response, scope, within)` | Every trigger gets its response (optionally within N seconds) |
| `never(event, where)` | The event never occurs |

Events are matched by type, or by `{ type = "...", where = { field = value | [values] } }`. A project adds its own rule by subclassing `voiceledger.Rule` and registering it with `@register_rule("kind")`. Postgres storage (`voiceledger[postgres]`) writes each state change and its event in one transaction.

## Release gate

```bash
voiceops gate [--level tool|conversation|all] [--only id,id] [--smoke]
```

- **PASS**: everything declared ran and passed. **FAIL**: a deterministic check failed, or the leak detector missed a planted leak. **ERRORED**: a scenario could not run (platform down, no agent pushed). ERRORED is never green.
- **Tool level** runs the project's own service layer through a runner it supplies (`[evals] tool_runner`): deterministic, no credits, in CI.
- **Conversation level** runs simulated conversations on ElevenLabs Tests against the pushed agent (`[evals] conversation`). The platform's judge advises; the kit's own checks on the transcript (AI disclosure first, no leak) decide. Tests are deleted afterwards, even on failure.
- **Leak detector**: the vocabulary is the project's (`evals/leak_vocabulary.toml`: labelled terms, named patterns, rules that need all their patterns). The detector must catch every planted leak in the known-answer set (`evals/known_answer/*.json`) in the same run, or the gate fails and `privacy_leaks` is `null`, not 0.
- Results go to one JSON file (`evals/results/latest.json`, or `partial.json` with `--only`) with `"provenance": "SIMULATED"`.

## Deploy checks

```bash
voiceops check [base_url]
```

Declared in `[[deploy.checks]]`, run in order, each printed as PASS or FAIL; exit 0 only if all pass. Values may be `env:NAME`.

| `check` | Passes when |
|---|---|
| `health` | `path` returns 200 and the JSON matches `expect` / `at_least` |
| `unsigned_refused` | a tool called without the tool secret gets 401 |
| `signed_roundtrip` | a session opens and a signed tool call returns `expect` (`samples` times, timed) |
| `ledger_verifies` | the evidence bundle verifies on the live verify endpoint with the trusted key |
| `pages` | every listed path returns 200 |
| `p95` | the signed round trips' p95 is within `budget_ms` |

Network errors are failed checks, not crashes; `timeout_s` covers instances that sleep.

## Test calls and review sheets

```bash
voiceops call --setup               # import the Twilio number into ElevenLabs (once)
voiceops call                       # ring the one allow-listed number
voiceops fetch <conversation_id>    # save raw conversation + audio, render the review sheet
voiceops review recordings/<id>.json   # re-render offline
```

- The only number that can be dialled is the single E.164 number in `DEMO_PHONE_ALLOWLIST`.
- `fetch` saves the raw conversation and audio in `[review] recordings` (keep it gitignored; `fetch` warns if it isn't).
- The **review sheet** has the call header (number masked to its last three digits), automatic checks (AI disclosed first, identity verified, leak detector before verification, tool errors, promises no tool backs, who ended the call), the transcript with the tool timeline, and a findings table pre-filled from the automatic flags. The reviewer completes root cause and fix for each finding.

## Provenance

Every number in a report says where it came from:

| Label | Meaning |
|---|---|
| SIMULATED | Produced by the gate on synthetic data or simulated conversations |
| BENCHMARK | Taken from a published source, cited |
| MEASURED | From a real call or a live deployment |

A number without a label is not reported.
