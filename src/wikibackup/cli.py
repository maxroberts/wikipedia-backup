"""Command-line entry point for wikibackup: the ``wikibackup`` command.

``pyproject.toml`` maps the ``wikibackup`` script to ``main``, so it runs as
``uv run wikibackup <subcommand>``. The launchd jobs (from Stage 4 on) call it the same way.

Subcommands, and the stage that implements each (see ``docs/kiwix_backup_plan.md``):

- ``check`` (Stage 2): fetch the newest release of each tracked book from Kiwix's catalog
  and its metalink. Print the release (version, size, SHA-256) and whether ``update``
  would download it, by the rule in ``library.decide_update``. Read-only; needs network.
- ``status`` (Stage 2): for each location (archive, hot copy, state), show whether its
  drive is mounted, whether the folder exists, and free space. For each tracked book, list
  the archive's versions and the hot copy's state. Read-only; no network. Server health is
  added in Stage 4.
- ``update`` (Stage 3): download, verify and install a new release if policy allows.
- ``prune`` (Stage 3): delete versions beyond ``keep_versions``.
- ``serve`` (Stage 4): run kiwix-serve in the foreground for launchd.

``update``, ``prune`` and ``serve`` are stubs: each prints that it isn't implemented yet
and exits with status 2.

Exit statuses: 0 success; 1 an error (bad config, unreadable manifest, catalog or network
failure), reported as one line on stderr; 2 a usage error or a stub subcommand.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from wikibackup import library
from wikibackup.catalog import CatalogError, fetch_metalink, latest_release
from wikibackup.config import Config, ConfigError, load_config

# Subcommand name -> one-line help text. The order sets the order in --help.
SUBCOMMANDS = {
    "check": "Show the newest catalog release and whether update would download it.",
    "status": "Show installed versions, the hot copy and disk space.",
    "update": "Download, verify and install a new release if policy allows.",
    "prune": "Delete versions beyond keep_versions.",
    "serve": "Run kiwix-serve in the foreground (used by launchd).",
}

EXIT_OK = 0
EXIT_ERROR = 1
# Exit status for subcommands that exist in the CLI but aren't implemented yet.
EXIT_NOT_IMPLEMENTED = 2

GIB = 1024**3


def gib(n_bytes: int) -> str:
    """Format a byte count as GiB with one decimal, e.g. ``118.7 GiB``."""
    return f"{n_bytes / GIB:.1f} GiB"


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


def location_line(label: str, path: Path | None) -> str:
    """One status line for a data location: path, whether the drive is mounted, whether
    the folder exists, and free space on the drive.

    ``path`` is ``None`` for a turned-off location (only ``HOT_DIR`` can be off).
    """
    if path is None:
        return f"{label}: off (HOT_DIR is empty)"
    if not library.is_available(path):
        return f"{label}: {path} (drive not mounted)"
    usage = library.disk_usage(path)
    exists = "exists" if path.is_dir() else "folder not created yet"
    return f"{label}: {path} ({exists}; {gib(usage[2])} free of {gib(usage[0])})"


def run_check(config: Config, today: date) -> int:
    """Implement ``wikibackup check`` (see the module docstring). Prints to stdout."""
    archive_ok = library.is_available(config.data_dir)
    manifest = library.read_manifest(config.data_dir) if archive_ok else []
    for zim in config.zims:
        release = latest_release(zim)
        meta = fetch_metalink(release)
        print(f"{zim.name} ({zim.flavour})")
        print(f"  catalog:  {release.version} (issued {release.issued}), {release.file_name}")
        print(f"            {gib(meta.size)} ({meta.size:,} bytes), {len(meta.mirrors)} mirrors")
        print(f"            sha256 {meta.sha256}")
        if not archive_ok:
            print(f"  decision: can't decide, archive drive not mounted ({config.data_dir})")
            continue
        newest = library.newest_verified(library.list_archive(config.data_dir, zim, manifest))
        print(f"  local:    {newest.version if newest else 'none'}")
        decision = library.decide_update(release, newest, config.policy, today)
        verb = "DOWNLOAD" if decision.download else "skip"
        print(f"  decision: {verb}: {decision.reason}")
    return EXIT_OK


def run_status(config: Config, today: date) -> int:
    """Implement ``wikibackup status`` (see the module docstring). Prints to stdout."""
    print(location_line("archive (DATA_DIR)", config.data_dir))
    print(location_line("hot copy (HOT_DIR)", config.hot_dir))
    print(location_line("state (STATE_DIR)", config.state_dir))

    archive_ok = library.is_available(config.data_dir)
    hot_ok = config.hot_dir is not None and library.is_available(config.hot_dir)
    manifest = library.read_manifest(config.data_dir) if archive_ok else []

    for zim in config.zims:
        print(f"\n{zim.name} ({zim.flavour})")
        newest = None
        if not archive_ok:
            print("  archive:  not available (drive not mounted)")
        else:
            versions = library.list_archive(config.data_dir, zim, manifest)
            newest = library.newest_verified(versions)
            if not versions:
                print("  archive:  no versions")
            for v in versions:
                size = gib(v.size_on_disk) if v.size_on_disk is not None else "file missing"
                state = "verified" if v.verified else "NOT verified"
                age = library.months_old(v.version, today)
                print(f"  archive:  {v.version}  {size}  {age} months old  {state}")
        if config.hot_dir is None:
            print("  hot copy: off")
        elif not hot_ok:
            print("  hot copy: not available (drive not mounted)")
        elif not archive_ok:
            print("  hot copy: can't check without the archive's manifest")
        else:
            hot = library.hot_copy_state(config.hot_dir, zim, newest)
            label = hot.file_name or "none"
            print(f"  hot copy: {label}  {'current' if hot.current else hot.note}")
    print("\nserver: not checked yet (added in Stage 4)")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``) and run the chosen subcommand.

    Returns the process exit status (see the module docstring). Config, manifest and
    catalog errors are caught here and printed as one line on stderr.
    """
    args = build_parser().parse_args(argv)
    handlers = {"check": run_check, "status": run_status}
    if args.command not in handlers:
        print(f"wikibackup {args.command}: not implemented yet", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    try:
        return handlers[args.command](load_config(), date.today())
    except (ConfigError, CatalogError, library.ManifestError) as exc:
        print(f"wikibackup {args.command}: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
