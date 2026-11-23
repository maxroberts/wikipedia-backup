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
- ``update`` (Stage 3): for each book, download a new release if the policy allows,
  verify it, make sure the hot copy is current, and prune (``update.run_update``).
- ``prune`` (Stage 3): make sure the hot copy is current and prune, with no network access
  (``update.run_prune``).
- ``serve`` (Stage 4): run kiwix-serve in the foreground for launchd. Still a stub that
  prints "not implemented yet" and exits with status 2.

``update`` and ``prune`` log progress (timestamped) to stderr and append the same lines to
``DATA_DIR/logs/wikibackup.log``. If the archive drive isn't mounted, they log a warning
and exit 0 without doing anything, so the monthly launchd job doesn't report a failure
while the drive is attached elsewhere. ``check`` and ``status`` print plain reports to
stdout.

The global option ``--config PATH`` reads a different ``config.toml`` (the default is
the one in the repo root). The end-to-end test uses it with a small book.

Exit statuses: 0 success (or nothing to do); 1 an error such as a bad config, an
unreadable manifest, a catalog, network or checksum failure, too little free space, or
another run holding the lock, reported on stderr; 2 a usage error or a stub subcommand.
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from wikibackup import download, library, update
from wikibackup.catalog import CatalogError, fetch_metalink, latest_release
from wikibackup.config import Config, ConfigError, load_config

log = logging.getLogger("wikibackup")

# Errors reported as "error: <message>" with exit status 1.
EXPECTED_ERRORS = (
    ConfigError, CatalogError, library.ManifestError, download.DownloadError,
    download.VerifyError, update.SpaceError, update.LockedError,
)
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

# Subcommand name -> one-line help text. The order sets the order in --help.
SUBCOMMANDS = {
    "check": "Show the newest catalog release and whether update would download it.",
    "status": "Show installed versions, the hot copy and disk space.",
    "update": "Download, verify and install a new release if policy allows.",
    "prune": "Bring the hot copy up to date and delete versions beyond keep_versions.",
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
    parser.add_argument(
        "--config", type=Path, default=None, metavar="PATH",
        help="config.toml to use (default: the one in the repo root)",
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


def setup_logging(config: Config) -> None:
    """Send the ``wikibackup`` loggers' messages at ``LOG_LEVEL`` and above to stderr and
    to ``DATA_DIR/logs/wikibackup.log`` (appending). Used by ``update`` and ``prune`` only;
    the caller has created ``logs/``. Replaces handlers from an earlier call, so tests can
    call ``main`` repeatedly."""
    logger = logging.getLogger("wikibackup")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(config.log_level.upper())
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT)
    log_file = config.data_dir / update.LOG_SUBDIR / "wikibackup.log"
    for handler in (logging.StreamHandler(sys.stderr), logging.FileHandler(log_file)):
        handler.setFormatter(formatter)
        logger.addHandler(handler)


def run_writer(command: str, config: Config, today: date) -> int:
    """Run ``update`` or ``prune``: check the archive drive, prepare the folders, set up
    logging, and call ``update.run_update`` or ``update.run_prune``."""
    if not library.is_available(config.data_dir):
        print(
            f"wikibackup {command}: archive drive not mounted ({config.data_dir}); nothing to do",
            file=sys.stderr,
        )
        return EXIT_OK
    update.prepare_archive(config.data_dir)
    setup_logging(config)
    log.info("wikibackup %s: start", command)
    try:
        if command == "update":
            update.run_update(config, today)
        else:
            update.run_prune(config)
    except EXPECTED_ERRORS as exc:
        log.error("wikibackup %s: %s", command, exc)
        return EXIT_ERROR
    log.info("wikibackup %s: done", command)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``) and run the chosen subcommand.

    Returns the process exit status (see the module docstring). Expected errors
    (``EXPECTED_ERRORS``) are reported on stderr instead of as a traceback.
    """
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        print(f"wikibackup {args.command}: not implemented yet", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    try:
        config = load_config(args.config)
        if args.command == "check":
            return run_check(config, date.today())
        if args.command == "status":
            return run_status(config, date.today())
        return run_writer(args.command, config, date.today())
    except EXPECTED_ERRORS as exc:
        print(f"wikibackup {args.command}: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
