"""Smoke tests: the package imports and the CLI's subcommands parse.

Each subcommand's behaviour is tested elsewhere: ``check`` and ``status`` in
``test_cli.py``, ``update`` and ``prune`` in ``test_update.py``, ``serve`` in
``test_server.py``, ``install-tools`` in ``test_software.py``.
"""

import pytest

import wikibackup
from wikibackup import cli

def test_package_imports():
    """The package imports and has a module docstring."""
    assert wikibackup.__doc__


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
