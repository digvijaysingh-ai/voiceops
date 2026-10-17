"""Load a project object named as "package.module:ATTR", relative to the project root."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


class ImportSpecError(ValueError):
    pass


def load_object(spec: str, root: Path, what: str = "object") -> Any:
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ImportSpecError(f"{what} must look like 'package.module:ATTR', got {spec!r}")
    added = str(root) not in sys.path
    if added:
        sys.path.insert(0, str(root))
    try:
        return getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as e:
        raise ImportSpecError(f"cannot load {what} from {spec!r}: {e}") from e
    finally:
        if added:
            sys.path.remove(str(root))
