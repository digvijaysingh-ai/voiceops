"""Safe logs: one JSON object per line, allow-listed fields only.

Callers can pass anything; only IDs, outcomes and timings reach the log line, so a
name, a date of birth or a transcript can't leak in through a careless call site.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

SAFE_FIELDS = frozenset({"event", "tool", "call_id", "outcome", "latency_ms", "status", "path"})


class SafeLog:
    def __init__(self, logger: logging.Logger | str, extra_fields: Iterable[str] = ()):
        self.logger = logging.getLogger(logger) if isinstance(logger, str) else logger
        self.allowed = SAFE_FIELDS | frozenset(extra_fields)

    def line(self, **fields) -> str:
        return json.dumps({k: v for k, v in fields.items() if k in self.allowed and v is not None})

    def __call__(self, **fields) -> None:
        self.logger.info(self.line(**fields))
