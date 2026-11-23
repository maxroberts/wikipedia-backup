"""Smoke tests: the package imports and the CLI's subcommands parse.

These guard the Stage 1 scaffold. Each later stage adds its own test module (catalog,
download, library, server) with saved responses and no real downloads.
"""

import pytest

import wikibackup
from wikibackup import cli


def test_package_imports():
    """The package imports and has a module docstring."""
    assert wikibackup.__doc__


@pytest.mark.parametrize("command", list(cli.SUBCOMMANDS))
def test_each_subcommand_parses_and_reports_not_implemented(command, capsys):
    """Every planned subcommand is accepted and, for now, exits with the stub status."""
    assert cli.main([command]) == cli.EXIT_NOT_IMPLEMENTED
    assert "not implemented yet" in capsys.readouterr().err


def test_no_subcommand_is_an_error():
    """Running with no subcommand is a usage error (argparse exits with status 2)."""
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
