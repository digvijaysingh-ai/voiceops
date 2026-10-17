# voiceops

Deployment discipline for voice agents, as a kit: safety defaults, a release gate, a verifiable audit ledger, deploy checks and a real-call review loop.

```bash
uvx voiceops init claims-line --organisation "Example Insurance"
cd claims-line && uv sync
uv run voiceops gate
```

Commands: `init`, `build [--check]`, `push`, `gate`, `check`, `call`, `fetch`, `review`. Extras: `voiceops[fastapi]` (guard dependencies), `voiceops[postgres]` (token store, ledger storage).

Guide: [https://github.com/digvijaysingh-ai/voiceops/blob/main/docs/guide.md](https://github.com/digvijaysingh-ai/voiceops/blob/main/docs/guide.md). MIT licence.
