"""`voiceops init`: a new use-case project with the kit wired in (PLAN §2, §5 day 7).

The template is a working, domain-neutral project: an agent that tells a verified caller
the status of their case. Its gate passes from scratch; the domain is the part to replace.
"""

from __future__ import annotations

import re
from importlib.resources import as_file, files
from pathlib import Path

from voiceops.config import build, load_config, write
from voiceops.config.project import ConfigError

NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
TEXT_SUFFIXES = {".py", ".toml", ".md", ".json", ".yml", ".example", ""}


def _target_name(rel: Path) -> Path:
    """dot_gitignore -> .gitignore (dotfiles are stored renamed so packaging keeps them)."""
    return Path(*[p.replace("dot_", ".", 1) if p.startswith("dot_") else p for p in rel.parts])


def init(target: Path, name: str, organisation: str, kit_source: Path | None = None) -> list[Path]:
    """Create the project in `target` (must not exist or be empty). Returns files written."""
    if not NAME.match(name):
        raise ConfigError("name: lowercase letters, digits and dashes, starting with a letter")
    if target.exists() and any(target.iterdir()):
        raise ConfigError(f"{target} exists and is not empty")
    values = {
        "__PROJECT_ID__": name.replace("-", "_"),
        "__PROJECT__": name,
        "__ORGANISATION__": organisation,
    }
    written = []
    with as_file(files("voiceops.scaffold").joinpath("template")) as src:
        for f in sorted(p for p in Path(src).rglob("*") if p.is_file()):
            if "__pycache__" in f.parts:
                continue
            rel = f.relative_to(src)
            out = target / _target_name(rel)
            out.parent.mkdir(parents=True, exist_ok=True)
            if f.suffix in TEXT_SUFFIXES:
                text = f.read_text()
                for k, v in values.items():
                    text = text.replace(k, v)
                out.write_text(text)
            else:
                out.write_bytes(f.read_bytes())
            written.append(out)
    if kit_source is not None:  # develop against a local kit checkout instead of PyPI
        pyproject = target / "pyproject.toml"
        kit = kit_source.resolve()
        pyproject.write_text(
            pyproject.read_text()
            + "\n[tool.uv.sources]\n"
            + f'voiceops = {{ path = "{kit / "voiceops"}", editable = true }}\n'
            + f'voiceledger = {{ path = "{kit / "voiceledger"}", editable = true }}\n'
        )
    cfg = load_config(target / "voiceops.toml")
    files_ = build(cfg)
    write(cfg, files_)
    written += [cfg.path(cfg.agent.build_dir) / n for n in files_]
    return written
