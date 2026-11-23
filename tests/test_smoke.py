"""Smoke tests: the package imports and the CLI's subcommands parse.

The stub subcommand (``serve``) must exit with the "not implemented" status until Stage 4
replaces it. ``check`` and ``status`` are covered in ``test_cli.py``, and ``update`` and
``prune`` in ``test_update.py``.
"""

import pytest

import wikibackup
from wikibackup import cli

STUBS = ["serve"]


def test_package_imports():
    """The package imports and has a module docstring."""
    assert wikibackup.__doc__


@pytest.mark.parametrize("command", STUBS)
def test_stub_subcommands_report_not_implemented(command, capsys):
    """Each stub subcommand is accepted and exits with the stub status."""
    assert cli.main([command]) == cli.EXIT_NOT_IMPLEMENTED
    assert "not implemented yet" in capsys.readouterr().err


def test_every_subcommand_is_in_help():
    """Every planned subcommand is registered with the parser."""
    help_text = cli.build_parser().format_help()
    for command in cli.SUBCOMMANDS:
        assert command in help_text


def test_no_subcommand_is_an_error():
    """Running with no subcommand is a usage error (argparse exits with status 2)."""
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
