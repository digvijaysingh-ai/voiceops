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


@app.callback()
def main() -> None:
    """Deployment discipline for voice agents."""


@app.command()
def version() -> None:
    """Print the kit version."""
    typer.echo(__version__)


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


if __name__ == "__main__":
    app()
