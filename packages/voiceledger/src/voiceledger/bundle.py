"""Evidence bundle: a zip with ledger.jsonl, list_snapshots.jsonl and head.json.

Reading is defensive: bundles are uploaded by anyone, so we cap the size, read only
the three known members, and parse JSON (never execute anything).
"""

from __future__ import annotations

import io
import json
import zipfile

from voiceledger.chain import Event, canonical_json
from voiceledger.sign import SignedHead

MAX_BUNDLE_BYTES = 5 * 1024 * 1024
MAX_MEMBER_BYTES = 20 * 1024 * 1024  # uncompressed, guards against zip bombs
LEDGER, SNAPSHOTS, HEAD = "ledger.jsonl", "list_snapshots.jsonl", "head.json"


class BundleInvalid(ValueError):
    pass


def write_bundle(events: list[Event], snapshots: dict[str, list[str]], head: SignedHead | None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(LEDGER, "\n".join(e.to_json() for e in events) + "\n")
        z.writestr(
            SNAPSHOTS,
            "".join(
                canonical_json({"snapshot_id": k, "ordered_pids": v}) + "\n"
                for k, v in sorted(snapshots.items())
            ),
        )
        if head is not None:
            z.writestr(HEAD, canonical_json(head.to_dict()))
    return buf.getvalue()


def _read_member(z: zipfile.ZipFile, name: str) -> str | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_MEMBER_BYTES:
        raise BundleInvalid(f"{name} is too large")
    return z.read(name).decode("utf-8")


def read_bundle(data: bytes):
    """Returns (events, snapshots | None, head | None). Raises BundleInvalid."""
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleInvalid("file over 5 MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise BundleInvalid("not a ledger bundle (not a zip file)") from e
    with z:
        ledger = _read_member(z, LEDGER)
        if ledger is None:
            raise BundleInvalid("not a ledger bundle (no ledger.jsonl)")
        try:
            events = [Event.from_dict(json.loads(line)) for line in ledger.splitlines() if line]
            snap_text = _read_member(z, SNAPSHOTS)
            snapshots = None
            if snap_text is not None:
                snapshots = {
                    row["snapshot_id"]: list(row["ordered_pids"])
                    for row in map(json.loads, filter(None, snap_text.splitlines()))
                }
            head_text = _read_member(z, HEAD)
            head = SignedHead(**json.loads(head_text)) if head_text else None
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            raise BundleInvalid(f"malformed bundle: {e}") from e
    return events, snapshots, head


def read_ledger_jsonl(text: str) -> list[Event]:
    try:
        return [Event.from_dict(json.loads(line)) for line in text.splitlines() if line]
    except (json.JSONDecodeError, TypeError) as e:
        raise BundleInvalid(f"malformed ledger: {e}") from e
