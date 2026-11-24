"""Command-line entry point for wikibackup: the ``wikibackup`` command.

``pyproject.toml`` maps the ``wikibackup`` script to ``main``, so it runs as
``uv run wikibackup <subcommand>``, or as ``.venv/bin/wikibackup <subcommand>``, which is
how the launchd agent runs ``serve``.

Subcommands (see ``docs/kiwix_backup_plan.md`` for the design):

- ``check``: fetch the newest release of each tracked book from Kiwix's catalog and its
  metalink. Print the release (version, size, SHA-256) and whether ``update`` would
  download it, by the rule in ``library.decide_update``. Read-only; needs network.
- ``status``: for each location (archive, hot copy, state), show whether its drive is
  mounted, whether the folder exists, and free space. For each tracked book, list the
  archive's versions and the hot copy's state. Then the server: what ``library.xml``
  serves, HTTP answers on ``127.0.0.1`` and the LAN address, the firewall state, and
  whether kiwix-tools is installed (``server.health``). Read-only; no internet access.
- ``update``: refresh the kiwix-tools backups; then, for each book, download a new
  release if the policy allows, verify it, make sure the hot copy is current, prune, and
  rebuild ``library.xml`` (``update.run_update``).
- ``prune``: the same without the network steps (``update.run_prune``).
- ``serve``: run kiwix-serve and keep ``library.xml`` current until stopped
  (``server.run_serve``). Used by the launchd agent.
- ``install-tools``: back up the configured kiwix-tools builds (if the archive drive is
  mounted), then install the macOS build in ``~/.local/opt`` (``software.install_tools``).
  Run by ``install.sh``.

Logging: ``update`` and ``prune`` log timestamped progress to stderr and append it to
``DATA_DIR/logs/wikibackup.log``. ``serve`` and ``install-tools`` log to stderr only;
under launchd, ``serve``'s stderr goes to ``~/Library/Logs/wikibackup/serve.log``.
``check`` and ``status`` print plain reports to stdout.

If the archive drive isn't mounted, ``update`` and ``prune`` say so and exit 0 without
doing anything, so the monthly launchd job doesn't report a failure while the drive is
attached elsewhere.

**Desktop alert** (``alert.py``): when ``update`` fails, a text file with the error and
all the relevant paths is written to ``~/Desktop``, and the next successful ``update``
deletes it. Real downloads happen only about every 6 months, so this is how a failure
gets noticed. "Fails" here means:
- any error that makes ``update`` exit 1
- an unexpected exception, which is logged with its traceback and exits 1
- the archive drive not being mounted, which still exits 0

``prune`` and the other commands never write an alert.

The global option ``--config PATH`` reads a different ``config.toml`` (the default is
the one in the repo root). The end-to-end tests use it with a small book.

Exit statuses: 0 success (or nothing to do); 1 an error such as a bad config, an
unreadable manifest, a catalog, network or checksum failure, too little free space,
another run holding the lock, or a library or kiwix-tools failure, reported on stderr;
for ``update`` and ``prune``, also an unexpected exception (logged with its traceback);
2 a usage error. ``serve`` exits with kiwix-serve's own status.
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from wikibackup import alert, download, library, server, software, update
from wikibackup.catalog import CatalogError, fetch_metalink, latest_release
from wikibackup.config import Config, ConfigError, load_config

log = logging.getLogger("wikibackup")

# Errors reported as "error: <message>" with exit status 1.
EXPECTED_ERRORS = (
    ConfigError, CatalogError, library.ManifestError, download.DownloadError,
    download.VerifyError, update.SpaceError, update.LockedError, server.ServerError,
    software.SoftwareError,
)
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

# Subcommand name -> one-line help text. The order sets the order in --help.
SUBCOMMANDS = {
    "check": "Show the newest catalog release and whether update would download it.",
    "status": "Show installed versions, the hot copy, disk space and the server.",
    "update": "Download, verify and install a new release if policy allows.",
    "prune": "Bring the hot copy up to date and delete versions beyond keep_versions.",
    "serve": "Run kiwix-serve in the foreground (used by launchd).",
    "install-tools": "Back up kiwix-tools builds and install the macOS one.",
}

EXIT_OK = 0
EXIT_ERROR = 1

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
    the folder exists (and, if not, whether this user can create it), and free space on
    the drive.

    ``path`` is ``None`` for a turned-off location (only ``HOT_DIR`` can be off).
    """
    if path is None:
        return f"{label}: off (HOT_DIR is empty)"
    if not library.is_available(path):
        return f"{label}: {path} (drive not mounted)"
    usage = library.disk_usage(path)
    if path.is_dir():
        exists = "exists"
    else:
        parent = next(p for p in path.parents if p.exists())
        exists = "folder not created yet" + (
            "" if os.access(parent, os.W_OK) else f"; CAN'T be created: {parent} isn't writable"
        )
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
    print_server_status(config)
    return EXIT_OK


def print_server_status(config: Config) -> None:
    """The server part of ``status`` (see ``server.health``)."""
    h = server.health(config)
    print("\nserver")
    if not h.library_exists:
        print(f"  library:   {server.library_path(config)} not created yet")
    elif not h.books:
        print(f"  library:   {server.library_path(config)} (serves nothing)")
    for path, exists in h.books.items():
        print(f"  serves:    {path}{'' if exists else '  MISSING (HTTP 500 for this book)'}")
    print(f"  local:     http://127.0.0.1:{config.port}/ -> {h.local}")
    print(f"  LAN:       {h.lan_url or '-'} -> {h.lan}")
    if h.host_url:
        print(f"  name:      {h.host_url}")
    print(f"  firewall:  {h.firewall}")
    kiwix_serve = software.tool_path(config, "kiwix-serve")
    state = "installed" if kiwix_serve.is_file() else "NOT installed (run install-tools)"
    print(f"  kiwix-tools {config.kiwix_tools.version}: {state} ({kiwix_serve.parent})")


def setup_logging(config: Config, to_file: bool) -> None:
    """Send the ``wikibackup`` loggers' messages at ``LOG_LEVEL`` and above to stderr, and
    if ``to_file`` also to ``DATA_DIR/logs/wikibackup.log`` (appending; the caller has
    created ``logs/``). Replaces handlers from an earlier call, so tests can call ``main``
    repeatedly."""
    logger = logging.getLogger("wikibackup")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(config.log_level.upper())
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if to_file:
        handlers.append(logging.FileHandler(config.data_dir / update.LOG_SUBDIR / "wikibackup.log"))
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)


def run_writer(command: str, config: Config, today: date) -> int:
    """Run ``update`` or ``prune``: check the archive drive, prepare the folders, set up
    logging, and call ``update.run_update`` or ``update.run_prune``.

    For ``update`` only, it manages the Desktop alert (see the module docstring). It
    writes the alert on an unmounted archive drive, an expected error or an unexpected
    exception, and clears it on success. An unexpected exception is logged with its
    traceback and returns ``EXIT_ERROR``, rather than propagating.
    """
    if not library.is_available(config.data_dir):
        print(
            f"wikibackup {command}: archive drive not mounted ({config.data_dir}); nothing to do",
            file=sys.stderr,
        )
        if command == "update":
            setup_logging(config, to_file=False)  # the archive's log file isn't reachable
            alert.write_alert(
                f"the archive drive isn't mounted ({config.data_dir}), so the update "
                "couldn't run. Attach the drive and run the update again.",
                config,
            )
        return EXIT_OK
    try:
        update.prepare_archive(config.data_dir)
        setup_logging(config, to_file=True)
        log.info("wikibackup %s: start", command)
        if command == "update":
            update.run_update(config, today)
        else:
            update.run_prune(config)
    except EXPECTED_ERRORS as exc:
        log.error("wikibackup %s: %s", command, exc)
        if command == "update":
            alert.write_alert(str(exc), config)
        return EXIT_ERROR
    except Exception as exc:  # a bug: log the traceback, alert, and exit 1
        log.exception("wikibackup %s: unexpected error", command)
        if command == "update":
            alert.write_alert(
                f"unexpected {type(exc).__name__}: {exc} (probably a bug; the traceback is "
                "in the update log)",
                config,
            )
        return EXIT_ERROR
    log.info("wikibackup %s: done", command)
    if command == "update":
        alert.clear_alert()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``) and run the chosen subcommand.

    Returns the process exit status (see the module docstring). Expected errors
    (``EXPECTED_ERRORS``) are reported on stderr instead of as a traceback.
    """
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "check":
            return run_check(config, date.today())
        if args.command == "status":
            return run_status(config, date.today())
        if args.command == "serve":
            setup_logging(config, to_file=False)
            return server.run_serve(config)
        if args.command == "install-tools":
            setup_logging(config, to_file=False)
            path = software.install_tools(config)
            print(f"kiwix-tools {config.kiwix_tools.version} installed in {path}")
            return EXIT_OK
        return run_writer(args.command, config, date.today())
    except EXPECTED_ERRORS as exc:
        print(f"wikibackup {args.command}: error: {exc}", file=sys.stderr)
        if args.command == "update":  # e.g. a bad config: alert without its paths
            alert.write_alert(str(exc), None)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
