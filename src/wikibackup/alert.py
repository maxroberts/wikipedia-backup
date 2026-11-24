"""Desktop alert for a failed ``update``, so a failure isn't missed between rare downloads.

A real download happens only about every 6 months, and the logs are easy to forget. So
when ``wikibackup update`` fails (whether it's the monthly launchd run or a manual run),
``cli.run_writer`` calls ``write_alert``. That writes a plain-text file, ``ALERT_NAME``,
to ``~/Desktop``, holding:
- the time and the error message
- what to do next
- the paths to the repo, the archive, the hot copy, and every log

The next successful ``update`` calls ``clear_alert``, which deletes the file, so a file on
the Desktop always means the most recent update failed. Each failure overwrites the
file; the history is in ``DATA_DIR/logs/wikibackup.log``.

What counts as a failure (all decided in ``cli``):
- any error that makes ``update`` exit 1, e.g. a network, checksum, free-space or lock
  error, or a bad config
- an unexpected exception (a bug), which is reported with its type
- the archive drive not being mounted. The run still exits 0, as before, but nothing was
  checked, so it gets an alert too: otherwise a detached drive could silently skip months
  of checks.

Warnings that don't fail the run, such as a skipped hot copy or a kiwix-tools backup
problem, don't make an alert.

**Limits:**
- If the T7 isn't attached, launchd can't start ``wikibackup`` at all (the code and
  ``.venv`` live there), so no alert can be written.
- If writing the file fails, e.g. because macOS denies access to the Desktop, that's
  logged as an error, and the update's own result is unchanged.

``DESKTOP`` is a module variable so tests can point it at a temporary folder
(``tests/conftest.py`` does this for every test).
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from wikibackup.config import REPO_ROOT, Config

log = logging.getLogger(__name__)

DESKTOP = Path.home() / "Desktop"
ALERT_NAME = "wikipedia-backup UPDATE FAILED.txt"
LAUNCHD_LOG_DIR = Path.home() / "Library" / "Logs" / "wikibackup"
UPDATE_LABEL = "com.local.wikibackup-update"


def alert_path() -> Path:
    """Where the alert file goes: ``DESKTOP/ALERT_NAME``."""
    return DESKTOP / ALERT_NAME


def alert_text(error: str, config: Config | None, now: datetime) -> str:
    """The alert file's contents. ``config`` is ``None`` if the config couldn't be loaded;
    the data paths are then shown as unknown."""

    def path_or(value: Path | None, missing: str) -> str:
        return str(value) if value is not None else missing

    data_dir = config.data_dir if config else None
    hot_dir = config.hot_dir if config else None
    update_log = data_dir / "logs" / "wikibackup.log" if data_dir else None
    return f"""wikipedia-backup: the Wikipedia update FAILED

When:   {now:%Y-%m-%d %H:%M:%S} (this Mac's time)
Error:  {error}

What to do:
  1. Read the full log (below) for details, and fix the cause. The Troubleshooting
     section of the README lists common errors.
  2. Run the update again, from Terminal:
       launchctl kickstart gui/{os.getuid()}/{UPDATE_LABEL}
     or, to watch it run:
       cd "{REPO_ROOT}" && uv run wikibackup update
  3. The next successful update deletes this file. If it's still here after that run,
     the update failed again (this file then shows the newer error).

Paths:
  Repo:                     {REPO_ROOT}
  README (troubleshooting): {REPO_ROOT / "README.md"}
  Archive (DATA_DIR):       {path_or(data_dir, "unknown (config didn't load)")}
  Hot copy (HOT_DIR):       {path_or(hot_dir, "off or unknown")}
  Full update log:          {path_or(update_log, "unknown (config didn't load)")}
                            (on the archive drive; readable only while it's mounted)
  launchd's update output:  {LAUNCHD_LOG_DIR / "update.log"}
  Server log:               {LAUNCHD_LOG_DIR / "serve.log"}
"""


def write_alert(error: str, config: Config | None, now: datetime | None = None) -> Path | None:
    """Write (or overwrite) the Desktop alert for a failed update; return its path.

    Never raises. If the file can't be written, it logs an error and returns ``None``.
    """
    path = alert_path()
    try:
        path.write_text(alert_text(error, config, now or datetime.now()))
    except OSError as exc:
        log.error("couldn't write the Desktop alert %s: %s", path, exc)
        return None
    log.error("wrote a Desktop alert: %s", path)
    return path


def clear_alert() -> bool:
    """Delete the Desktop alert after a successful update. Returns ``True`` if there was one.

    Never raises: a failure to delete is logged as a warning.
    """
    path = alert_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("couldn't delete the old Desktop alert %s: %s", path, exc)
        return False
    log.info("update succeeded; deleted the old Desktop alert %s", path)
    return True
