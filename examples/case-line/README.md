# case-line

Voice agent for Northwind Energy, built on voiceops: safety defaults, a release gate, an audit ledger, deploy checks and a real-call review loop from day one.

Everything here is a starting point: the domain (a caller asking for the status of their case) is a placeholder to replace with yours. The safeguards around it stay.

## Run it

```bash
uv sync
uv run voiceops build --check     # agent configs match fragments + tools
uv run voiceops gate              # tool-level scenarios + leak detector known answers
uv run pytest -q                  # API tests
cp .env.example .env              # then fill it in
uv run uvicorn app.api:app --port 8000
```

## Where things live

| Path | What | Change it when |
|---|---|---|
| `voiceops.toml` | The project's contract: agent, guard, ledger rules, evals, deploy checks, review | Always first |
| `agent/fragments/` | Your prompt fragments. The kit adds its safety fragments; reword one via `[agent.safety]`, drop one only with `[agent.allow_remove]` and a reason | The agent's job changes |
| `agent/build/` | Generated agent configs and `safety.json`. Commit them; CI fails on drift | Never by hand: `voiceops build` |
| `app/models.py` | Tool request models → tool schemas | A tool is added or changed |
| `app/service.py` | Safeguards in code: token binding, identity before detail, ledger events | The domain changes |
| `evals/` | Scenarios, the tool runner, the leak vocabulary and its known answers | A new risk is found |

## Ship it

1. Deploy the API (the `Dockerfile` runs on any Docker host; set the variables from `.env.example` there), then `uv run voiceops check` against the live URL.
2. `uv run voiceops push --profile inbound-demo --base-url <API_URL>`.
3. Add the conversation scenarios (`evals/scenarios.py`) and `[evals] conversation`; run `uv run voiceops gate`. ERRORED is not a pass.
4. `uv run voiceops call` (rings only the one allow-listed number), then `uv run voiceops fetch <conversation_id>` and fill in the findings table of the review sheet.

Every number in a report carries its provenance: SIMULATED (gate), MEASURED (real call).
