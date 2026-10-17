"""Scenarios, the PASS / FAIL / ERRORED gate, the leak detector with its known-answer set,
and the ElevenLabs Tests adapter (PLAN §2, §4.3)."""

from voiceops.evals.elevenlabs import (
    ConversationSetup,
    ConversationUnavailable,
    run_conversation_scenarios,
    verified_index,
)
from voiceops.evals.gate import EXIT, gate_from_config, iter_failures, run_gate, select
from voiceops.evals.known_answer import KnownAnswerReport, check, load_cases
from voiceops.evals.leak import Finding, LeakDetector
from voiceops.evals.scenarios import Scenario

__all__ = [
    "EXIT",
    "ConversationSetup",
    "ConversationUnavailable",
    "Finding",
    "KnownAnswerReport",
    "LeakDetector",
    "Scenario",
    "check",
    "gate_from_config",
    "iter_failures",
    "load_cases",
    "run_conversation_scenarios",
    "run_gate",
    "select",
    "verified_index",
]
