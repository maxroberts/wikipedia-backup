"""Command-line entry point for wikibackup: the ``wikibackup`` command.

``pyproject.toml`` maps the ``wikibackup`` script to ``main``, so it runs as
``uv run wikibackup <subcommand>``. The launchd jobs (from Stage 4 on) call it the same way.

Subcommands, and the stage that implements each (see ``docs/kiwix_backup_plan.md``):

- ``check``: report the newest release in the catalog and whether ``update`` would
  download it (Stage 2).
- ``status``: list installed versions, disk use and server health (Stage 2, with server
  health added in Stage 4).
- ``update``: download, verify and install a new release if policy allows (Stage 3).
- ``prune``: delete versions beyond ``keep_versions`` (Stage 3).
- ``serve``: run kiwix-serve in the foreground for launchd (Stage 4).

Currently all subcommands are stubs: each prints that it isn't implemented yet and exits
with status 2.
"""

import argparse
import sys

# Subcommand name -> one-line help text. The order sets the order in --help.
SUBCOMMANDS = {
    "check": "Show the newest catalog release and whether update would download it.",
    "status": "Show installed versions, disk use and whether the server is up.",
    "update": "Download, verify and install a new release if policy allows.",
    "prune": "Delete versions beyond keep_versions.",
    "serve": "Run kiwix-serve in the foreground (used by launchd).",
}

# Exit status for subcommands that exist in the CLI but aren't implemented yet.
EXIT_NOT_IMPLEMENTED = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one subparser per entry in ``SUBCOMMANDS``.

    A subcommand is required: running ``wikibackup`` with none prints usage and exits
    with status 2 (argparse's standard error exit).
    """
    parser = argparse.ArgumentParser(
        prog="wikibackup",
        description="Keep an offline, LAN-served copy of English Wikipedia (Kiwix ZIM).",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name, help_text in SUBCOMMANDS.items():
        sub.add_parser(name, help=help_text, description=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``) and run the chosen subcommand.

    Returns the process exit status. Every subcommand is currently a stub that prints a
    "not implemented yet" message to stderr and returns ``EXIT_NOT_IMPLEMENTED``.
    """
    args = build_parser().parse_args(argv)
    print(f"wikibackup {args.command}: not implemented yet", file=sys.stderr)
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    sys.exit(main())
