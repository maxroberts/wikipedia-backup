"""Back up kiwix-tools builds like ZIMs, and install the macOS build on this Mac.

Two jobs, both driven by ``[kiwix_tools]`` in ``config.toml`` (a version and a list of
platforms):

- **Backup** (``ensure_backups``, called by ``update.run_update``). For each platform,
  keep ``kiwix-tools_<platform>-<version>.tar.gz`` in ``DATA_DIR/software/kiwix-tools/``
  on the HDD, and a copy in ``HOT_DIR/software/kiwix-tools/`` on the SSD. Kiwix publishes
  only an **MD5** for these files, so a download is checked against that MD5. Its SHA-256
  is then computed and written to ``<file>.sha256``. That file marks the backup as
  verified, and later copies are checked against it, the same convention as the ZIM hot
  copy. Every version ever kept stays; the files are 10–22 MB each.
- **Install** (``install_tools``, the ``wikibackup install-tools`` command, run by
  ``install.sh``). Unpack the ``macos-arm64`` build from a verified backup into
  ``~/.local/opt/kiwix-tools-<version>/``, and symlink ``kiwix-serve`` and
  ``kiwix-manage`` into ``~/.local/bin/``. ``server.py`` runs the binaries from the
  versioned folder (``tool_path``), not through the symlinks or ``PATH``, so launchd's
  minimal environment doesn't matter.

Checked on 2026-11-23: the 3.8.2 macOS build is ad-hoc signed and has no quarantine
attribute when fetched this way. Both Linux glibc builds are statically linked (``file``
output), so they should run on Ubuntu 24.04 unchanged; that wasn't tested on Ubuntu.
"""

import hashlib
import logging
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path

from wikibackup import download, library
from wikibackup.config import Config

log = logging.getLogger(__name__)

RELEASE_URL = "https://download.kiwix.org/release/kiwix-tools/"
SOFTWARE_SUBDIR = Path("software") / "kiwix-tools"
HOST_PLATFORM = "macos-arm64"
OPT_DIR = Path.home() / ".local" / "opt"
BIN_DIR = Path.home() / ".local" / "bin"
LINKED_TOOLS = ("kiwix-serve", "kiwix-manage")
TIMEOUT_S = 120


class SoftwareError(Exception):
    """A kiwix-tools build couldn't be fetched, verified or installed."""


def tarball_name(platform: str, version: str) -> str:
    """Kiwix's file name for a build, e.g. ``kiwix-tools_macos-arm64-3.8.2.tar.gz``."""
    return f"kiwix-tools_{platform}-{version}.tar.gz"


def install_dir(version: str) -> Path:
    """Where ``install_tools`` unpacks a version: ``~/.local/opt/kiwix-tools-<version>``."""
    return OPT_DIR / f"kiwix-tools-{version}"


def tool_path(config: Config, tool: str) -> Path:
    """Absolute path of an installed tool (``kiwix-serve`` or ``kiwix-manage``) for the
    configured version. Doesn't check that it exists."""
    return install_dir(config.kiwix_tools.version) / tool


def _fetch(url: str) -> bytes:
    """GET ``url`` into memory (fine for files of a few tens of MB). Raises
    ``SoftwareError`` on network errors."""
    request = urllib.request.Request(url, headers={"User-Agent": download.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read()
    except OSError as exc:
        raise SoftwareError(f"couldn't fetch {url}: {exc}") from exc


def _recorded_sha256(path: Path) -> str | None:
    """The hash in ``<path>.sha256``, or ``None`` if that file is missing or empty."""
    try:
        return (path.with_name(path.name + download.SHA256_SUFFIX).read_text().split() or [None])[0]
    except OSError:
        return None


def archive_tarball(config: Config, platform: str) -> Path:
    """Fetch one build into the archive unless a verified copy is already there.

    Downloads the tarball and its ``.md5`` from ``RELEASE_URL`` and compares the MD5. It
    then writes the file under a temporary name, renames it into place, and writes a
    ``.sha256`` file. Returns the archive path. Raises ``SoftwareError`` on a network error
    or an MD5 mismatch; nothing is left at the final name in that case.
    """
    name = tarball_name(platform, config.kiwix_tools.version)
    dest_dir = config.data_dir / SOFTWARE_SUBDIR
    dest = dest_dir / name
    if library.has_sha256_file(dest):
        return dest
    data = _fetch(RELEASE_URL + name)
    published_md5 = _fetch(RELEASE_URL + name + ".md5").decode().split()[0].lower()
    if hashlib.md5(data).hexdigest() != published_md5:
        raise SoftwareError(f"{name}: MD5 doesn't match Kiwix's published {published_md5}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".{name}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    sha256 = hashlib.sha256(data).hexdigest()
    dest.with_name(name + download.SHA256_SUFFIX).write_text(f"{sha256}  {name}\n")
    log.info("archived %s (%d bytes, MD5 matches Kiwix)", name, len(data))
    return dest


def ensure_backups(config: Config) -> None:
    """Make sure every configured build is in the archive and, if ``HOT_DIR`` is on and
    mounted, in the hot copy (``download.copy_verified`` against the archive's
    ``.sha256``).

    Failures are logged as warnings, not raised: a missing tools backup shouldn't stop a
    Wikipedia update. The caller has checked that the archive drive is mounted.
    """
    hot_ok = config.hot_dir is not None and library.is_available(config.hot_dir)
    for platform in config.kiwix_tools.platforms:
        try:
            archived = archive_tarball(config, platform)
            if hot_ok:
                hot = config.hot_dir / SOFTWARE_SUBDIR / archived.name
                if _recorded_sha256(hot) != _recorded_sha256(archived):
                    download.copy_verified(archived, hot.parent, _recorded_sha256(archived))
                    log.info("hot copy of %s verified", archived.name)
        except (SoftwareError, download.VerifyError, OSError) as exc:
            log.warning("kiwix-tools backup of %s: %s", platform, exc)


def _source_tarball(config: Config) -> Path:
    """A verified local tarball of the host build: the archive's, else the hot copy's.
    Its SHA-256 is re-checked here, because it's about to become an executable."""
    name = tarball_name(HOST_PLATFORM, config.kiwix_tools.version)
    candidates = [config.data_dir / SOFTWARE_SUBDIR / name]
    if config.hot_dir is not None:
        candidates.append(config.hot_dir / SOFTWARE_SUBDIR / name)
    for path in candidates:
        if library.is_available(path) and library.has_sha256_file(path):
            if download.sha256_file(path) == _recorded_sha256(path):
                return path
            log.warning("%s doesn't match its .sha256; not using it", path)
    raise SoftwareError(
        f"no verified {name} in the archive or hot copy; run `wikibackup install-tools` "
        "with the archive drive mounted so it can be downloaded"
    )


def install_tools(config: Config) -> Path:
    """Install the configured macOS build and return the install folder.

    If the archive drive is mounted, it first runs ``archive_tarball`` and the hot-copy step
    for every configured platform (``ensure_backups``). It then unpacks the ``macos-arm64``
    tarball, taking only regular files by base name, into a temporary folder, which it
    renames to ``~/.local/opt/kiwix-tools-<version>/``. Finally it points
    ``~/.local/bin/kiwix-serve`` and ``kiwix-manage`` at that folder, replacing existing
    symlinks but never a regular file. Running it again when the folder exists only
    refreshes the symlinks.

    Raises ``SoftwareError`` if no verified tarball is available, if the tarball lacks
    ``kiwix-serve`` or ``kiwix-manage``, or if a regular file is in the way in
    ``~/.local/bin``.
    """
    version = config.kiwix_tools.version
    if library.is_available(config.data_dir):
        ensure_backups(config)
    target = install_dir(version)
    if not all((target / tool).is_file() for tool in LINKED_TOOLS):
        source = _source_tarball(config)
        tmp = OPT_DIR / f".kiwix-tools-{version}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        with tarfile.open(source) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    member.name = Path(member.name).name  # flatten; no paths from the archive
                    tar.extract(member, tmp, filter="data")
        missing = [tool for tool in LINKED_TOOLS if not (tmp / tool).is_file()]
        if missing:
            shutil.rmtree(tmp)
            raise SoftwareError(f"{source.name} doesn't contain {', '.join(missing)}")
        shutil.rmtree(target, ignore_errors=True)
        os.replace(tmp, target)
        log.info("installed kiwix-tools %s in %s", version, target)

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for tool in LINKED_TOOLS:
        link = BIN_DIR / tool
        if link.exists() and not link.is_symlink():
            raise SoftwareError(f"{link} exists and isn't a symlink; not replacing it")
        link.unlink(missing_ok=True)
        link.symlink_to(target / tool)
    return target
