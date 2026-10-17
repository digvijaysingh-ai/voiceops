# voiceops: plan

**One line:** the deployment discipline from Slotback, packaged so the next voice-agent deployment starts with a release gate, an audit ledger and safe defaults on day one, instead of rebuilding them.

**Status:** days 1–8 done. Slotback runs on the kit on its `voiceops-migration` branch (S1 met; merges once voiceops is published, since Slotback's CI and image install the kit from a public source). Next: the second deployment (S2, S3).

---

## 1. Goal and how we'll know it worked

A voice agent is easy to demo and hard to put in front of customers. Slotback showed what "deployable" takes: safeguards enforced in code, a gate that fails loudly, evidence a buyer can verify, and a loop that turns real calls into fixes. Most of that wasn't specific to hospitals. This kit extracts it.

| # | Success criterion | How it's measured |
|---|---|---|
| S1 | **Parity:** Slotback runs on the kit with no behaviour change | Slotback's full test suite and its 15-scenario gate pass after the migration; the ledger verifier gives the same result on the same bundle |
| S2 | **Speed:** a new use case goes from `voiceops init` to a live agent passing its gate in **≤ 3 working days** | Timestamped log in the second deployment's repo: init → first passing tool-level gate → agent pushed → conversation gate PASS → first real call reviewed |
| S3 | **Thin use cases:** the second deployment is mostly domain code | Lines of use-case code vs kit code, reported (target: no copied kit code at all) |
| S4 | **Trustworthy kit:** the kit tests itself the way it tests agents | Known-answer sets for the leak detector and ledger rules; the kit's own CI is green |
| S5 | **Independent verification:** an auditor can verify a ledger with one small install | `pip install voiceledger` (no web framework, no database driver) verifies any bundle |

**Out of scope for v1:** a hosted service, a UI builder, platforms other than ElevenLabs (the interfaces leave room for one), voice-quality metrics (latency in audio, speech-recognition accuracy).

---

## 2. What gets extracted (from Slotback)

| Kit module | Slotback source | What changes |
|---|---|---|
| `voiceledger` (own package) | `packages/slotledger` | Hash chain, signing, bundles, CLI unchanged. **Rules become pluggable**: Slotback's `identity_before_detail` and `list_order` become two instances of generic rules (§4.2). Postgres storage moves to an optional extra. |
| `voiceops.config` | `agent/build.py`, `agent/push.py`, `agent/fragments/`, `agent/profiles.json` | Paths, first messages, voice, LLM and tool registry come from `voiceops.toml` instead of constants. Safety fragments become shipped defaults a project can extend but not silently drop. |
| `voiceops.guard` | tool-secret check, per-call token, HMAC webhook check, rate limiter, JSON logs in `slotback/api/app.py` and `services/scheduling.py` | A small FastAPI-facing library. The token store is an interface (Postgres implementation included). |
| `voiceops.evals` | `evals/` | Scenario model, PASS/FAIL/ERRORED gate, ElevenLabs Tests adapter, results JSON. The leak detector takes vocabulary and patterns from the project; the **known-answer rule** (the detector must catch every planted leak before "0 leaks" counts) is built in. |
| `voiceops.deploy` | `scripts/deploy_check.py` | Declarative checks: health, unsigned call refused, signed round trip, ledger verifies, p95 latency, pages render. |
| `voiceops.review` | `scripts/demo_call.py` and the manual call review | Allow-listed test calls, fetch recording and transcript, render a review sheet (transcript + tool timeline + automatic checks + a findings table: what happened / root cause / fix). |
| `voiceops.scaffold` | Slotback's layout | `voiceops init` creates a new use-case project with all of the above wired in. |

What stays in Slotback: the scheduling domain, the backfill state machine, the simulator, the dashboard, the docs.

---

## 3. Repository layout

```
voiceops/
├── packages/
│   ├── voiceledger/          standalone: chain, sign, bundle, rules, verify, CLI   (deps: cryptography)
│   └── voiceops/             the kit                                              (deps: pydantic, httpx, typer)
│       └── src/voiceops/
│           ├── config/       fragments → profiles → built agent configs; tool schemas; drift check; push
│           ├── guard/        tool secret, per-call tokens, webhook signatures, rate limits, safe logs
│           ├── evals/        scenarios, gate, leak detector + known-answer sets, ElevenLabs Tests adapter
│           ├── deploy/       declarative deploy checks
│           ├── review/       allow-listed test calls, call fetch, review sheet
│           ├── scaffold/     `voiceops init` template
│           └── cli.py        `voiceops` command
├── examples/                 a minimal end-to-end example (used by the kit's own CI)
├── docs/                     PLAN.md (this), DECISIONS.md, then a guide per module
└── .github/workflows/        lint, tests, example gate
```

Two packages, not one, because the verifier must be installable by an auditor without the rest (S5).

---

## 4. Design

### 4.1 A project's contract: `voiceops.toml`

Every use case declares itself in one file. Sketch (final shape decided in the build):

```toml
[project]
name = "claims-surge"
locales = ["es", "en"]

[agent]
fragments = "agent/fragments"            # project fragments; kit safety fragments are added automatically
profiles  = "agent/profiles.json"
tools     = "app.models:TOOLS"           # Pydantic models → tool schemas
voice_id  = "…"
llm       = "…"

[guard]
token_header = "X-Call-Token"

[[ledger.rules]]                          # full Slotback config: packages/voiceledger/tests/fixtures/slotback_rules.toml
rule = "requires_before"
name = "identity_before_detail"
effect = "detail_disclosed"
precondition = "identity_verified"
scope = ["call_id", "pid"]

[evals]
scenarios = "evals/scenarios.py"
leak_vocabulary = "evals/leak_vocabulary.toml"
known_answers = "evals/known_answer"

[deploy]
base_url = "env:API_URL"
checks = ["health", "unsigned_refused", "signed_roundtrip", "ledger_verifies", "p95_ms<=300"]

[review]
allowlist = "env:DEMO_PHONE_ALLOWLIST"
```

### 4.2 Ledger rules, generalised

| Generic rule | Slotback instance | Other uses |
|---|---|---|
| `chain` (always on) | chain intact | — |
| `signed_head` (always on) | head signed with the trusted key | — |
| `requires_before(effect, precondition, scope)` | no appointment detail before identity verified, per call | no balance disclosed before identity check (energy); no claim reference before policy verified (insurance) |
| `follows_snapshot_order(snapshot, group_by, subject_field, ranked, advance_on, claim, fill)` | offers follow the waiting-list order | any "offer in the customer's order" process |
| `always_followed_by(trigger, response, within, scope)` | — | vulnerability flagged → handoff (energy); emergency phrase → transfer (insurance) |
| `never(event, where)` | — | no payout decision event ever emitted by the agent |

A rule is a small class with `name`, config, and `check(events, snapshots) -> Check`. Events are picked by a matcher: a type, or `{ type, where = { field = value | [values] } }`. Projects add their own rules with `@register_rule("kind")`, without forking. `voiceledger verify --rules <file>` reads `[ledger] rules` straight from `voiceops.toml`.

### 4.3 The gate

- Every scenario ends **PASS, FAIL or ERRORED**. A scenario that didn't run is never a pass.
- **Deterministic checks decide; platform judges advise.**
- Three levels, each optional per project:
  - **tool-level:** calls the project's API directly, no credits, runs in CI;
  - **conversation:** ElevenLabs Tests against the live agent, costs credits, on demand;
  - **real call:** a reviewed recording, logged as evidence, never automated.
- The leak detector refuses to report "0 leaks" unless its known-answer set passes in the same run.
- Results are one JSON file, versioned in the repo, that a dashboard or README can read.

### 4.4 Safe defaults shipped by the kit

Prompt fragments (overridable wording, not removable without an explicit `allow_remove` with a reason, logged in the build):
- AI disclosure first, with a recording notice when recording is on;
- identity before detail;
- third party or voicemail: callback number only;
- a person whenever asked; upset callers offered a person early;
- never promise what the tools don't do;
- closing: log the outcome, one goodbye, end the call.

Guard defaults: the tool secret is required; tools act only on the subject bound to the call token; tokens bind on verification for inbound and widget sessions; webhooks need a valid HMAC; logs carry IDs and outcomes only.

### 4.5 CLI

```
voiceops init <name>            scaffold a project
voiceops build [--check]        compose agent configs; --check fails on drift (CI)
voiceops push --profile <p>     idempotent push to the platform
voiceops gate [--level tool|conversation|all] [--only …]
voiceops verify <bundle> --trusted-key <hex>
voiceops check <url>            deploy checks
voiceops call / fetch <id>      allow-listed test call; fetch recording + review sheet
```

---

## 5. Build plan

| Day | Work | Done when |
|---|---|---|
| 1 | **Structure** (this), decisions, skeleton packages, CI | Repo builds; plan agreed |
| 2 | `voiceledger`: port chain, sign, bundle, CLI; pluggable rules; four generic rules | Port of Slotback's ledger tests passes; the Slotback showcase bundle verifies with rules configured |
| 3 | `voiceops.config`: `voiceops.toml` loader, fragments + safe defaults, tool schemas, drift check, push | Rebuilding Slotback's agents from the kit gives byte-identical JSON (or a reviewed diff) |
| 4 | `voiceops.guard`: tokens, tool secret, webhook HMAC, rate limits, logs | Unit tests; Slotback's API tests pass using the guard |
| 5 | `voiceops.evals`: scenario model, gate, leak detector + known answers, ElevenLabs Tests adapter | Slotback's 12 tool-level scenarios pass through the kit gate |
| 6 | `voiceops.deploy` + `voiceops.review` | Deploy check on the live Slotback API passes; a review sheet renders for call 02 |
| 7 | `voiceops init` template, CLI, docs, the example project | `voiceops init demo && cd demo && voiceops gate` passes from scratch |
| 8 | **Migrate Slotback onto the kit** (S1) | Slotback CI green; gate PASS; no behaviour change |
| 9–11 | **Second deployment** with `voiceops init` (S2, S3), the time log running from minute one | Live agent, gate PASS, one real call reviewed |
| 12 | Write-up, publish (with the owner's OK) | README with the S1–S5 results |

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Over-generalising (a framework nobody needs) | Extract only what Slotback already uses; the second deployment decides what stays |
| The platform changes its API | One adapter module per platform call; contract tests against recorded responses |
| Migration breaks Slotback | Do it on a branch; Slotback's existing suite and gate are the acceptance test |
| Generic leak detection gives false confidence | Vocabulary is per project, and the known-answer rule blocks a "0 leaks" claim without proof |
