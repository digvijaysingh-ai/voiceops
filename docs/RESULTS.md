# Results

The success criteria from [`PLAN.md`](PLAN.md) §1, measured. Every number is MEASURED (local runs on 2026-10-17 against real Slotback code and data, or against Slotback's live API where stated) unless labelled otherwise.

| # | Criterion | Result | Status |
|---|---|---|---|
| S1 | Slotback runs on the kit with no behaviour change | Same ledger results, byte-identical agent configs, same eval reports, all tests and live deploy checks pass | **Met** (merged; Slotback's CI green on GitHub; live API redeployed, 6/6 deploy checks PASS) |
| S2 | `voiceops init` → live agent passing its gate in ≤ 3 working days | Live and pushed at 1 h 07 min; conversation gate PASS at 1 h 17 min (fourth run); real call pending | **Met to the gate**; the reviewed real call is the last step |
| S3 | The second deployment is mostly domain code, no copied kit code | 302 domain lines on 2,841 kit lines; 0 copied | **Met** |
| S4 | The kit tests itself the way it tests agents | 156 tests; known-answer sets for the ledger and the leak detector; `init` output gated in CI | **Met** (GitHub CI green) |
| S5 | An auditor verifies a ledger with one small install | 4 packages, 12 MB, no web framework or database driver | **Met** |

## S1 · Parity with Slotback

Each module was compared with Slotback's original on Slotback's own code and data before Slotback moved onto it, one commit per module.

| Module | Comparison | Result |
|---|---|---|
| Ledger | Slotback's verifier vs `voiceledger` with Slotback's two checks as rules, on the signed showcase bundle and 403 mutated copies (edited, deleted, swapped, retyped events; a reversed waiting list) | Same status and same first failing event for every check, in all 403 cases (281 chain failures, 67 identity-before-detail, 20 list-order) |
| Ledger tests | Slotback's ledger test suite, test bodies unchanged, checks supplied as rules | 15/15 pass (now in the kit) |
| Agent configs | Slotback's four agent configs and `tools.json`, rebuilt by `voiceops build` | 5/5 byte-identical; `safety.json` added |
| Guard | Slotback's full suite with its guard code replaced by `voiceops.guard` | 117/117 pass |
| Leak detector | Slotback's detector vs the kit's with Slotback's vocabulary as TOML, on 26 known-answer cases and 276 recorded agent turns in three contexts | 0 differences (51 flagged in both) |
| Gate | Slotback's 12 tool-level scenarios through its own runner and through `voiceops gate` | Identical reports: 12/12 PASS, leak detector 16/16, 0 false alarms |
| Conversation adapter | Slotback's recorded ElevenLabs Tests response (3 real simulated conversations) replayed through the kit adapter | 3/3 rows identical to the recorded scores |
| Deploy checks | `voiceops check` against Slotback's live API, from the migration branch | 6/6 PASS (tool p95 90 ms, client-side) |
| Review | Call 02's raw conversation through `voiceops fetch`/`review` | The automatic checks flag the invented email confirmation that the manual review found |

After the migration, Slotback's CI steps all pass, locally and on GitHub: lint, format, `voiceops build --check`, metrics check, 103 tests (the 15 ledger tests now run in the kit), and the tool-level gate. Two intended changes, each in its own commit: unauthenticated requests get 401 before any body validation, and the production-reference profiles (which don't record) no longer say "this call is recorded".

## S2 · Speed

The second deployment, `claims-surge` (a fictional insurer's claims line after a storm: claim status, new claims, emergencies passed to a person before any identity check), started from `voiceops init`. Its time log:

| Step | Elapsed |
|---|---|
| `voiceops init claims-surge` | 0 |
| Claims domain in; first passing tool-level gate (8/8, leak detector 9/9); tests and lint green | 2 min 22 s |
| Deploy-ready: API runs; deploy checks 4/4 PASS locally; push dry-run builds 7 tools and the agent | 3 min 10 s |
| API live on Render, deploy checks 4/4 PASS live, agents pushed | 1 h 06 min 55 s |
| Conversation gate PASS: 11/11 (8 tool-level, 3 simulated conversations), on the fourth run | 1 h 17 min 17 s |
| First real call reviewed | pending: a call to the owner's phone |

These times come from a single focused build session. The conversation runs to the first pass cost 6,108 credits in four runs (MEASURED); each failing run found something real or tightened a check. A fifth run the next day failed on a false alarm in a phrase check (1,563 credits), fixed in 0.1.4. The time log in the `claims-surge` repository lists every finding and its fix.

## What the second deployment changed in the kit

Running a second, different deployment through the kit found what Slotback alone couldn't. Each became a release:

| Release | Found by | Change |
|---|---|---|
| 0.1.1 | Pushing a second project into the same ElevenLabs workspace | `push` adopted tools by name, so it would have repointed or deleted Slotback's live tools. It now touches only tools calling its own endpoint (checked against the real workspace: Slotback's 13 tools unchanged) |
| 0.1.2 | The platform refusing the first English agent | The build fails when an English agent uses a non-English TTS model; `push` refuses a placeholder voice |
| 0.1.3 | The first conversation run | `forbidden_phrases` check on every agent turn (the agent said "I'm connecting you" and the platform's judge passed it); the handoff default says it's a call back; the template matches dates and references as callers say them |
| 0.1.4 | A later conversation run | A correct refusal ("I can't tell you whether it's covered") failed the gate on a substring match. A forbidden phrase can now be a pattern that skips refusals, and the gate and the project's tests share one matcher |
| 0.1.5 | The first real call | The API restarted mid-call and refused the call's token; the agent told a caller with the right details that they didn't match, and the review sheet showed 0 tool errors. The identity default now says a tool error is a technical problem, and the sheet counts error replies |

## S3 · Thin use cases

On the deploy-ready commit of `claims-surge`:

| | Lines |
|---|---|
| Python written for the claims domain | 302 |
| Python from `voiceops init`, unchanged | 287 |
| Config, prompt fragments, leak vocabulary, known answers | 199 |
| Kit Python it runs on (`voiceops` + `voiceledger`) | 2,841 |
| Kit definitions copied into the project | 0 |

It uses three of the four generic ledger rules, two of which Slotback never needed: `requires_before` (no claim detail before the policy is verified), `always_followed_by` (every emergency handed to a person within 120 s, with a known-answer scenario proving the rule fails when the handoff is missing) and `never` (the agent records no payout decision).

## S4 · The kit tests itself

- 156 tests, Postgres tests on a real server; CI green on GitHub.
- Known-answer sets: the signed showcase bundle and its tamper case for the ledger; planted-leak sets for the leak detector (Slotback 16/16, claims-surge 9/9, the `init` template 4/4); the gate refuses to report leak counts when its detector misses one.
- `voiceops init` output passes its own gate, tests and lint inside the kit's test suite, and the committed example is checked against a fresh `init`.
- The built wheels, installed in a clean environment, run `init`, `gate`, `build --check` and the generated tests.

## S5 · Independent verification

`voiceledger` installed alone from its wheel into an empty Python 3.12 environment pulls in `cryptography` (and its `cffi` and `pycparser`): 4 packages, 12 MB. It verifies Slotback's showcase bundle with Slotback's rules file:

```
PASS  1707 events
  PASS         chain: 1707 events linked
  PASS         identity_before_detail: 300 detail_disclosed events, all after identity_verified
  PASS         list_order: 14 snapshots followed in order (per slot_id)
  PASS         head: signed head matches
```
