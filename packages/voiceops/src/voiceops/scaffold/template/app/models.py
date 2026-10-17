"""Tool request models. The agent sees these as webhook tool schemas (`voiceops build`).

No model takes a subject ID: tools act on whoever the call's token is bound to, so the
agent can't be talked into looking up someone else.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Empty(BaseModel):
    pass


class VerifyIdentity(BaseModel):
    date_of_birth: str = Field(description="Caller's date of birth, YYYY-MM-DD")
    reference_last4: str = Field(description="Last four characters of the caller's reference")


class TransferToHuman(BaseModel):
    reason: str = Field(description="Why the caller wants a person, in a few words")


class LogOutcome(BaseModel):
    outcome: Literal[
        "status_given", "handoff", "identity_failed", "voicemail", "third_party", "wrong_number"
    ] = Field(description="How the call ended")


TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "record_disclosure": (Empty, "Record that you said you are an AI and the call is recorded."),
    "verify_identity": (VerifyIdentity, "Check the caller is the account holder."),
    "get_case": (Empty, "Read the verified caller's case status and next step."),
    "transfer_to_human": (TransferToHuman, "Ask a person to call the caller back."),
    "log_outcome": (LogOutcome, "Record how the call ended. Call before saying goodbye."),
}
