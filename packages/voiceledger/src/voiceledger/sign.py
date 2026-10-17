"""Signed head: Ed25519 over the last event of a chain.

The head is handed to the party that relies on the ledger (the customer, an auditor).
Once they hold it, any later rewrite of that chain is detectable. It proves the record
was not changed after signing; it does not prove the events were true.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from voiceledger.result import Check

# Domain-separation tag of the v1 head format. Changing it invalidates every signed head.
HEAD_TAG = "slotledger-head"


@dataclass(frozen=True)
class SignedHead:
    chain_id: str
    seq: int
    hash: str
    signed_at: str
    public_key: str  # hex, raw 32 bytes
    signature: str  # hex

    def message(self) -> bytes:
        return f"{HEAD_TAG}|{self.chain_id}|{self.seq}|{self.hash}|{self.signed_at}".encode()

    def to_dict(self) -> dict:
        return asdict(self)


def new_private_key_hex() -> str:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()


def public_key_hex(private_key_hex: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def sign_head(private_key_hex: str | None, chain_id: str, seq: int, hash: str, signed_at: str):
    """Refuses to sign without a key (the caller shows an UNSIGNED banner)."""
    if not private_key_hex:
        raise SigningKeyMissing("LEDGER_SIGNING_KEY is not set")
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    unsigned = SignedHead(chain_id, seq, hash, signed_at, pub, "")
    return SignedHead(**{**asdict(unsigned), "signature": key.sign(unsigned.message()).hex()})


class SigningKeyMissing(RuntimeError):
    pass


def verify_head(head: SignedHead | None, last_event, trusted_public_key: str | None = None):
    c = Check()
    if head is None:
        c.status, c.detail = "not_checked", "no signed head supplied"
        return c
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(head.public_key)).verify(
            bytes.fromhex(head.signature), head.message()
        )
    except (InvalidSignature, ValueError):
        c.fail(head.seq, "head signature is not valid")
        return c
    if trusted_public_key and head.public_key != trusted_public_key:
        c.fail(head.seq, "head was signed with a key that is not trusted")
    elif last_event is None or (head.seq, head.hash, head.chain_id) != (
        last_event.seq,
        last_event.hash,
        last_event.chain_id,
    ):
        c.fail(head.seq, "ledger does not end at the signed head")
    else:
        c.detail = "signed head matches" + (
            "" if trusted_public_key else " (no trusted key given: signature self-consistent only)"
        )
    return c
