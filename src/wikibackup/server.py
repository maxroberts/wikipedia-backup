"""Serve the newest version of each book with kiwix-serve, and check that it's reachable.

Only the **newest** version of each book is served (the user's decision of 2026-11-23).
Older archived versions are backups and aren't browsable. ``served_file`` picks the copy:

1. the hot copy in ``HOT_DIR``, if it's current (``library.hot_copy_state``)
2. otherwise the newest verified version in the archive, if the archive drive is mounted
3. otherwise, with the archive drive unmounted, the newest hot-copy file that has a
   ``.sha256`` file (it was verified when it was copied)
4. otherwise nothing

**library.xml** (``build_library``) lives in ``STATE_DIR`` on the internal disk. It's
built with ``kiwix-manage add`` into a temporary file in the same folder, then renamed
into place. kiwix-manage stores paths *relative to the library file's folder* (even with
``--zimPathToSave``), so ``library.xml`` must not be moved. Callers are
``update.rebuild_library`` (before deleting files), and the ``serve`` loop below.

**serve** (``run_serve``, run by the launchd agent):

- It builds the library, then starts
  ``kiwix-serve --library --monitorLibrary --nodatealiases --address A --port P library.xml``
  as a child process.
- Every ``poll_s`` seconds (60) it recomputes the served files, and rebuilds the library
  if they've changed (a drive plugged in or unplugged, a new hot copy). kiwix-serve
  reloads a library replaced by rename within about 2 s (tested 2026-11-23).
- This matters because when a served file disappears, kiwix-serve keeps running but
  answers HTTP 500 for that book (tested); it doesn't exit, so launchd wouldn't restart it.
- SIGTERM or SIGINT (e.g. ``launchctl bootout``) is passed on to kiwix-serve, and the loop
  exits with its status. If kiwix-serve exits by itself, so does ``run_serve``, with the
  same status, and launchd restarts it.
- An empty library is fine: kiwix-serve starts and shows an empty home page (tested).

``--nodatealiases`` (``-z``), despite its name, *adds* an undated address for each book,
e.g. ``/content/wikipedia_en_all_maxi/`` for ``wikipedia_en_all_maxi_2026-08.zim``. With
one version per book there's no name clash, so bookmarks keep working across updates.

**Health** (``health``, used by ``cli status``): what ``library.xml`` lists and whether
those files exist; an HTTP request to ``127.0.0.1`` and to this Mac's LAN address; the
``.local`` host name; and whether the macOS application firewall is on.

**Known limit:** the code and its ``.venv`` live on the T7. If the T7 is unplugged while
``serve`` is running, whether the already-running Python process carries on (and so falls
back to the HDD copy) hasn't been tested. After a restart without the T7, ``serve``
can't start at all.
"""

import logging
import os
import re
import signal
import socket
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from wikibackup import library, software
from wikibackup.catalog import version_from_file_name
from wikibackup.config import Config, ZimSpec

log = logging.getLogger(__name__)

LIBRARY_NAME = "library.xml"
EMPTY_LIBRARY = '<library version="20110515">\n</library>\n'
FIREWALL_CMD = "/usr/libexec/ApplicationFirewall/socketfilterfw"


class ServerError(Exception):
    """library.xml couldn't be built, or kiwix-serve couldn't be started."""


def library_path(config: Config) -> Path:
    """``STATE_DIR/library.xml``."""
    return config.state_dir / LIBRARY_NAME


def served_file(config: Config, zim: ZimSpec) -> Path | None:
    """The file to serve for ``zim``, chosen by the rules in the module docstring."""
    hot_ok = config.hot_dir is not None and library.is_available(config.hot_dir)
    if library.is_available(config.data_dir):
        manifest = library.read_manifest(config.data_dir)
        newest = library.newest_verified(library.list_archive(config.data_dir, zim, manifest))
        if newest is None:
            return None
        if hot_ok and library.hot_copy_state(config.hot_dir, zim, newest).current:
            return config.hot_dir / newest.file_name
        return config.data_dir / library.ZIM_SUBDIR / newest.file_name
    if hot_ok and config.hot_dir.is_dir():
        candidates = sorted(
            (p for p in config.hot_dir.iterdir()
             if version_from_file_name(zim, p.name) and library.has_sha256_file(p)),
            key=lambda p: p.name,
        )
        return candidates[-1] if candidates else None
    return None


def served_files(config: Config) -> list[Path]:
    """``served_file`` for every tracked book, leaving out books with nothing to serve."""
    return [p for zim in config.zims if (p := served_file(config, zim)) is not None]


def build_library(config: Config) -> list[Path]:
    """Write ``library.xml`` listing ``served_files``, and return that list.

    It writes ``library.xml.<pid>.tmp`` with kiwix-manage, or a fixed empty library if
    nothing can be served, then renames it over ``library.xml``. Two processes (the
    ``serve`` loop and an ``update``) may rebuild at the same moment; each uses its own
    temporary file, and both compute the same contents. Creates ``STATE_DIR`` if needed.

    Raises ``ServerError`` if kiwix-manage is missing or fails. The old ``library.xml``
    is left unchanged in that case.
    """
    files = served_files(config)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    final = library_path(config)
    tmp = final.with_name(f"{LIBRARY_NAME}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    if files:
        manage = software.tool_path(config, "kiwix-manage")
        try:
            result = subprocess.run(
                [str(manage), str(tmp), "add", *map(str, files)],
                capture_output=True, text=True, timeout=120,
            )
        except OSError as exc:
            raise ServerError(f"can't run {manage}: {exc}; run `wikibackup install-tools`") from exc
        if result.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise ServerError(f"kiwix-manage failed: {(result.stderr or result.stdout).strip()}")
    else:
        tmp.write_text(EMPTY_LIBRARY)
    tmp.replace(final)
    log.info("library.xml now serves: %s", ", ".join(map(str, files)) or "nothing")
    return files


def serve_command(config: Config) -> list[str]:
    """The kiwix-serve command line used by ``run_serve``."""
    return [
        str(software.tool_path(config, "kiwix-serve")),
        "--library", "--monitorLibrary", "--nodatealiases",
        "--address", config.bind_address, "--port", str(config.port),
        str(library_path(config)),
    ]


def run_serve(config: Config, poll_s: float = 60) -> int:
    """``wikibackup serve``: run kiwix-serve and keep its library current (see the module
    docstring). Returns kiwix-serve's exit status. Raises ``ServerError`` if the first
    library build fails or kiwix-serve can't be started. Later rebuild failures are logged,
    and the previous library stays in use."""
    current = build_library(config)
    command = serve_command(config)
    log.info("starting: %s", " ".join(command))
    try:
        child = subprocess.Popen(command)
    except OSError as exc:
        raise ServerError(f"can't start kiwix-serve: {exc}; run `wikibackup install-tools`") from exc

    def forward(signum, frame):
        log.info("received signal %d; stopping kiwix-serve", signum)
        child.send_signal(signum)

    old_handlers = {s: signal.signal(s, forward) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        while True:
            try:
                status = child.wait(timeout=poll_s)
                log.info("kiwix-serve exited with status %d", status)
                return status
            except subprocess.TimeoutExpired:
                pass
            try:
                if served_files(config) != current:
                    current = build_library(config)
            except (ServerError, library.ManifestError, OSError) as exc:
                log.warning("library rebuild failed; keeping the previous one: %s", exc)
    finally:
        for s, handler in old_handlers.items():
            signal.signal(s, handler)


# --- health -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Health:
    """What ``health`` found. ``books`` maps each path in ``library.xml`` to whether it
    exists. ``local`` and ``lan`` are HTTP status codes, or an error message. ``lan_url``
    is ``None`` if no LAN address was found. ``firewall`` is ``socketfilterfw``'s state
    line, or an error message."""

    library_exists: bool
    books: dict[Path, bool]
    local: str
    lan_url: str | None
    lan: str
    host_url: str | None
    firewall: str


def library_books(config: Config) -> dict[Path, bool]:
    """Paths listed in ``library.xml`` (resolved against its folder) -> whether they
    exist. Empty if the file is missing or unreadable."""
    path = library_path(config)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    books = {}
    for book in root.iter("book"):
        if book.get("path"):
            resolved = (path.parent / book.get("path")).resolve()
            books[resolved] = resolved.is_file()
    return books


def lan_address() -> str | None:
    """This Mac's IPv4 address on the network its default route uses, or ``None``.

    Uses a UDP socket ``connect``, which picks a route but sends no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never actually contacted
            address = s.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("0.") else address


def local_host_name() -> str | None:
    """The Bonjour name (``scutil --get LocalHostName``), without ``.local``, or ``None``."""
    try:
        result = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True,
                                text=True, timeout=5)
    except OSError:
        return None
    return result.stdout.strip() or None


def http_status(url: str, timeout_s: float = 3) -> str:
    """GET ``url``. Returns the status code as text, or a short error message."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return str(response.status)
    except urllib.error.HTTPError as exc:
        return str(exc.code)
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return f"no answer ({reason})"


def firewall_state() -> str:
    """The macOS application firewall's global state, e.g. ``Firewall is disabled.``"""
    try:
        result = subprocess.run([FIREWALL_CMD, "--getglobalstate"], capture_output=True,
                                text=True, timeout=5)
    except OSError as exc:
        return f"unknown ({exc})"
    return re.sub(r"\s*\(State = \d+\)", "", result.stdout.strip()) or "unknown"


def health(config: Config) -> Health:
    """Collect the server checks shown by ``wikibackup status``. Read-only."""
    lan = lan_address()
    lan_url = f"http://{lan}:{config.port}/" if lan else None
    host = local_host_name()
    return Health(
        library_exists=library_path(config).is_file(),
        books=library_books(config),
        local=http_status(f"http://127.0.0.1:{config.port}/"),
        lan_url=lan_url,
        lan=http_status(lan_url) if lan_url else "no LAN address",
        host_url=f"http://{host}.local:{config.port}/" if host else None,
        firewall=firewall_state(),
    )
