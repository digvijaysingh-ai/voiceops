"""The `voiceops` command. Subcommands land with their modules (PLAN §4.5)."""

from pathlib import Path

import typer
from dotenv import load_dotenv

from voiceops import __version__
from voiceops.config import ConfigError, PushError, load_config, stale_files, write
from voiceops.config import build as build_files
from voiceops.config import dry_run as push_dry_run
from voiceops.config import push as push_profile

app = typer.Typer(help="Deployment discipline for voice agents.", no_args_is_help=True)
CONFIG = typer.Option(Path("voiceops.toml"), "--config", "-c", help="Project config file.")
DIRECTORY = typer.Option(None, help="Where to create it (default: ./<name>).")
KIT_SOURCE = typer.Option(None, help="Use a local kit checkout (its packages/ folder), not PyPI.")


@app.callback()
def main() -> None:
    """Deployment discipline for voice agents."""


@app.command()
def version() -> None:
    """Print the kit version."""
    typer.echo(__version__)


@app.command()
def init(
    name: str = typer.Argument(..., help="Project name: lowercase, digits, dashes."),
    organisation: str = typer.Option("Example Organisation", help="Who the agent speaks for."),
    directory: Path = DIRECTORY,
    kit_source: Path = KIT_SOURCE,
) -> None:
    """Create a new use-case project with the kit wired in."""
    from voiceops.scaffold import init as scaffold

    target = directory or Path(name)
    try:
        written = scaffold(target, name, organisation, kit_source)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    typer.echo(f"created {target} ({len(written)} files)")
    typer.echo(f"next: cd {target} && uv sync && uv run voiceops gate")


@app.command()
def build(
    check: bool = typer.Option(False, help="Fail if the committed build is stale (for CI)."),
    config: Path = CONFIG,
) -> None:
    """Compose agent configs and tool schemas into the build directory."""
    try:
        cfg = load_config(config)
        files = build_files(cfg)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    if check:
        stale = stale_files(cfg, files)
        if stale:
            typer.echo(f"stale agent build files (run `voiceops build`): {stale}", err=True)
            raise typer.Exit(1)
        typer.echo("agent build up to date")
        return
    write(cfg, files)
    typer.echo(f"wrote {len(files)} files to {cfg.path(cfg.agent.build_dir)}")


@app.command()
def push(
    profile: str = typer.Option(..., help="Profile to push, as named in the profiles file."),
    base_url: str = typer.Option(..., help="Public HTTPS URL of the project's tool API."),
    dry_run: bool = typer.Option(False, help="Print the payloads, call nothing."),
    config: Path = CONFIG,
) -> None:
    """Create or update the profile's tools and agent on the platform, idempotently."""
    try:
        cfg = load_config(config)
        if dry_run:
            typer.echo(push_dry_run(cfg, profile, base_url))
            return
        load_dotenv(cfg.root / ".env")
        typer.echo(push_profile(cfg, profile, base_url))
    except (ConfigError, PushError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e


@app.command()
def gate(
    level: str = typer.Option("all", help="tool | conversation | all"),
    only: str = typer.Option("", help="Comma-separated scenario ids (writes partial.json)."),
    smoke: bool = typer.Option(False, help="Only scenarios marked smoke."),
    config: Path = CONFIG,
) -> None:
    """Run the release gate. Exit 0 = PASS, 1 = FAIL, 2 = ERRORED."""
    from voiceops.evals import EXIT, gate_from_config, iter_failures

    if level not in ("tool", "conversation", "all"):
        typer.echo("error: --level must be tool, conversation or all", err=True)
        raise typer.Exit(2)
    try:
        cfg = load_config(config)
        load_dotenv(cfg.root / ".env")
        report, out = gate_from_config(cfg, level, [s for s in only.split(",") if s], smoke)
    except (ConfigError, ValueError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    status = report["gate"]["status"]
    typer.echo(f"gate {status}: {report['gate']['summary']}")
    for line in iter_failures(report):
        typer.echo(f"  {line}")
    typer.echo(f"report: {out}")
    raise typer.Exit(EXIT[status])


@app.command()
def check(
    base_url: str = typer.Argument(None, help="Override [deploy] base_url."),
    config: Path = CONFIG,
) -> None:
    """Run the declared deploy checks against a live deployment."""
    from voiceops.deploy.run import run_checks

    try:
        cfg = load_config(config)
        load_dotenv(cfg.root / ".env")
        results = run_checks(cfg, base_url)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    for r in results:
        typer.echo(r.line())
    raise typer.Exit(0 if all(r.ok for r in results) else 1)


@app.command()
def call(
    setup: bool = typer.Option(False, help="Import the Twilio number into ElevenLabs first."),
    config: Path = CONFIG,
) -> None:
    """Place one test call to the single allow-listed number."""
    from voiceops._imports import load_object
    from voiceops.review import import_number, place_call

    try:
        cfg = load_config(config)
        load_dotenv(cfg.root / ".env")
        if setup:
            typer.echo(f"phone number imported: {import_number(cfg)}")
            return
        placed = place_call(cfg)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    typer.echo(f"calling your allow-listed number ending {placed['to']}")
    typer.echo(f"conversation_id: {placed['conversation_id']}")
    if cfg.review and cfg.review.instructions:
        typer.echo(load_object(cfg.review.instructions, cfg.root, "[review] instructions")())
    typer.echo("afterwards: voiceops fetch <conversation_id>")


def _sheet_options(cfg):
    from voiceops.evals import LeakDetector
    from voiceops.review import SheetOptions

    rv = cfg.review
    vocab = cfg.path(cfg.evals.leak_vocabulary) if cfg.evals else None
    return SheetOptions(
        disclosure_markers=rv.disclosure_markers,
        verify_tool=rv.verify_tool,
        verified_status=rv.verified_status,
        leak_context=rv.leak_context,
        unbacked_promises=rv.unbacked_promises,
        detector=LeakDetector.from_toml(vocab) if vocab and vocab.exists() else None,
    )


def _write_sheet(cfg, conv: dict, title: str | None) -> Path:
    from voiceops.review import render

    out = cfg.path(cfg.review.sheets)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{conv.get('conversation_id', 'call')}.md"
    path.write_text(render(conv, _sheet_options(cfg), title))
    return path


@app.command()
def fetch(
    conversation_id: str,
    title: str = typer.Option(None, help="Sheet title, e.g. 'Call 02 · Confirm and reschedule'."),
    config: Path = CONFIG,
) -> None:
    """Save a call's raw conversation and audio locally, and render its review sheet."""
    from voiceops.review import fetch_conversation, recordings_ignored

    try:
        cfg = load_config(config)
        load_dotenv(cfg.root / ".env")
        conv, folder = fetch_conversation(cfg, conversation_id)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    if recordings_ignored(folder) is False:
        typer.echo(f"warning: {folder} is not gitignored; recordings must stay local", err=True)
    typer.echo(f"saved raw conversation and audio in {folder}")
    typer.echo(f"review sheet: {_write_sheet(cfg, conv, title)}")


@app.command()
def review(
    conversation_json: Path,
    title: str = typer.Option(None, help="Sheet title."),
    config: Path = CONFIG,
) -> None:
    """Render a review sheet from a saved raw conversation (no network)."""
    import json

    try:
        cfg = load_config(config)
        if cfg.review is None:
            raise ConfigError("voiceops.toml has no [review] section")
        conv = json.loads(conversation_json.read_text())
    except (ConfigError, OSError, ValueError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    typer.echo(f"review sheet: {_write_sheet(cfg, conv, title)}")


if __name__ == "__main__":
    app()
