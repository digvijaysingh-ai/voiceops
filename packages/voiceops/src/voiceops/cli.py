"""The `voiceops` command. Subcommands land with their modules (PLAN §4.5)."""

import typer

from voiceops import __version__

app = typer.Typer(help="Deployment discipline for voice agents.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Deployment discipline for voice agents."""


@app.command()
def version() -> None:
    """Print the kit version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
