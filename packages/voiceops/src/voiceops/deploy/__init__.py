"""Declarative checks against a live deployment (PLAN §2): health, unsigned call refused,
signed round trip, evidence bundle verifies, pages render, tool p95 within budget.

Declared in `[[deploy.checks]]` (voiceops.deploy.spec); run with `voiceops check`
(voiceops.deploy.run). This package imports nothing so the config model can use the spec.
"""
