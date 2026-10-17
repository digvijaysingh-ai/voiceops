from typer.testing import CliRunner

import voiceledger
import voiceops
from voiceops.cli import app


def test_packages_import_and_cli_runs():
    assert voiceops.__version__ == voiceledger.__version__ == "0.1.1"
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0 and result.stdout.strip() == "0.1.1"
