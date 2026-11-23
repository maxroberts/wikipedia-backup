"""The ``update`` and ``prune`` commands: download, verify, hot-copy and retain ZIMs.

``cli`` calls ``run_update`` and ``run_prune``. Both take the archive lock, then work
through each tracked book in ``config.toml``. This module decides what to do and in which
order; ``download.py`` moves the bytes, and ``library.py`` reads the archive and writes
the manifest.

**update**, for each book (Design section of ``docs/kiwix_backup_plan.md``):

1. Read the catalog and apply ``library.decide_update``.
2. If it says download: fetch the metalink; check free space; download into
   ``partial/<file>.part`` (mirrors in priority order, then the redirector); check the
   SHA-256; rename into ``zim/``; add a manifest entry. If ``zim/`` already holds that file
   without a manifest entry (e.g. after a crash between the rename and the manifest
   write), it's hashed and adopted if it matches, or deleted if it doesn't.
3. Ensure the hot copy (``ensure_hot_copy``).
4. Prune (``prune_book``).

**prune**, for each book: steps 3 and 4 only, with no network access.

**Safety rules:**

- Nothing is deleted until its replacement is verified. Only verified versions count
  towards ``keep_versions``, so a failed download never causes a prune.
- Order for deleting an archived version: rebuild ``library.xml`` without it, delete the
  file, then remove its manifest entry. A crash in between leaves a manifest entry whose
  file is missing, and the next prune cleans that up.
- Files in ``zim/`` that the manifest doesn't list are never deleted automatically,
  except as in step 2. They're only logged.
- **Stage 3 limitation:** ``rebuild_library`` is a no-op until Stage 4 adds
  ``library.xml``. That's safe for now because nothing serves the files yet.

**Locking:** ``DATA_DIR/.wikibackup.lock`` is held with ``flock`` for the whole run, so a
manual run and the monthly launchd run can't overlap. A second run fails at once with
``LockedError``. The lock is released automatically if the process dies.

**Free space:** a download needs its remaining bytes, and a hot copy needs the file's
size, each plus ``FREE_SPACE_MARGIN`` (5 GiB). If there's too little, the download raises
``SpaceError``. The hot copy is skipped with a warning instead, so the archive side of an
update still succeeds when the SSD is full.
"""

import fcntl
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from wikibackup import download, library
from wikibackup.catalog import Release, fetch_metalink, latest_release
from wikibackup.config import Config, ZimSpec

log = logging.getLogger(__name__)

FREE_SPACE_MARGIN = 5 * 1024**3
LOCK_NAME = ".wikibackup.lock"
PARTIAL_SUBDIR = "partial"
PART_SUFFIX = ".part"
LOG_SUBDIR = "logs"


class LockedError(Exception):
    """Another ``update`` or ``prune`` holds the archive lock."""


class SpaceError(Exception):
    """Not enough free space for a download."""


@contextmanager
def archive_lock(data_dir: Path) -> Iterator[None]:
    """Hold an exclusive ``flock`` on ``data_dir/.wikibackup.lock`` for the ``with``
    block. Raises ``LockedError`` at once if another process holds it."""
    with (data_dir / LOCK_NAME).open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockedError(f"another update or prune is running ({data_dir / LOCK_NAME})") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def prepare_archive(data_dir: Path) -> None:
    """Create ``DATA_DIR`` and its ``zim/``, ``partial/`` and ``logs/`` folders if missing.
    The caller must have checked that the drive is mounted."""
    for sub in (library.ZIM_SUBDIR, PARTIAL_SUBDIR, LOG_SUBDIR):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)


def free_bytes(path: Path) -> int:
    """Free bytes on the drive holding ``path`` (or its nearest existing parent)."""
    usage = library.disk_usage(path)
    if usage is None:
        raise SpaceError(f"drive for {path} isn't mounted")
    return usage[2]


def rebuild_library(config: Config, exclude: set[Path] = frozenset()) -> None:
    """Rebuild ``library.xml`` from the files available now, leaving out ``exclude``.

    **Stage 3: a no-op** (it only logs at DEBUG level). Stage 4 implements it with
    kiwix-manage. It's called before any file is deleted, so that kiwix-serve stops
    serving a file before it disappears.
    """
    log.debug("rebuild_library: not implemented until Stage 4 (exclude=%s)", sorted(exclude))


def _add_manifest_entry(config: Config, release: Release, size: int, sha256: str) -> None:
    """Record a newly verified version in the manifest, replacing any entry with the same
    file name."""
    entries = [e for e in library.read_manifest(config.data_dir) if e.file != release.file_name]
    entries.append(
        library.ManifestEntry(
            file=release.file_name, name=release.zim.name, flavour=release.zim.flavour,
            version=release.version, size=size, sha256=sha256,
            added=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    )
    library.write_manifest(config.data_dir, entries)


def fetch_and_install(config: Config, release: Release) -> None:
    """Download ``release`` into the archive and record it (step 2 in the module
    docstring).

    Raises ``SpaceError``, ``download.DownloadError`` (``.part`` is kept for the next
    run), ``download.VerifyError`` (``.part`` is deleted, because a full-length file with
    the wrong hash can't be repaired by resuming), or ``catalog.CatalogError``.
    """
    meta = fetch_metalink(release)
    final = config.data_dir / library.ZIM_SUBDIR / release.file_name
    part = config.data_dir / PARTIAL_SUBDIR / (release.file_name + PART_SUFFIX)

    if final.exists():
        log.info("%s is in zim/ but not in the manifest; checking its hash", final.name)
        if final.stat().st_size == meta.size and download.sha256_file(final) == meta.sha256:
            _add_manifest_entry(config, release, meta.size, meta.sha256)
            log.info("adopted %s (hash matches the metalink)", final.name)
            return
        log.warning("deleting %s: its size or hash doesn't match the metalink", final.name)
        final.unlink()

    have = part.stat().st_size if part.exists() else 0
    need = max(meta.size - have, 0) + FREE_SPACE_MARGIN
    free = free_bytes(config.data_dir)
    if free < need:
        raise SpaceError(
            f"archive needs {need / 1024**3:.1f} GiB free for {release.file_name}, "
            f"has {free / 1024**3:.1f} GiB"
        )

    redirector = release.meta4_url.removesuffix(".meta4")
    urls = [*meta.mirrors, redirector] if redirector not in meta.mirrors else list(meta.mirrors)
    log.info("downloading %s (%.1f GiB, %d already present)", release.file_name,
             meta.size / 1024**3, have)
    download.download_resumable(urls, part, meta.size)

    log.info("checking SHA-256 of %s", part.name)
    actual = download.sha256_file(part)
    if actual != meta.sha256:
        part.unlink()
        raise download.VerifyError(
            f"{release.file_name}: SHA-256 {actual} doesn't match the metalink's "
            f"{meta.sha256}; deleted the download"
        )
    os.replace(part, final)
    _add_manifest_entry(config, release, meta.size, meta.sha256)
    log.info("installed %s in the archive", release.file_name)


def ensure_hot_copy(config: Config, zim: ZimSpec) -> None:
    """Make ``HOT_DIR`` hold a verified copy of the newest verified archive version of
    ``zim``, and nothing older.

    Does nothing if ``HOT_DIR`` is off or the archive has no verified version, and only
    logs a warning if ``HOT_DIR``'s drive isn't mounted or lacks free space. After the
    copy is current, other ZIMs of this book in ``HOT_DIR`` (with their ``.sha256`` files)
    and leftover ``.tmp`` files are deleted, once ``rebuild_library`` has run. Raises
    ``download.VerifyError`` if the copy's hash is wrong.
    """
    hot_dir = config.hot_dir
    if hot_dir is None:
        return
    if not library.is_available(hot_dir):
        log.warning("hot copy skipped: drive for %s isn't mounted", hot_dir)
        return
    versions = library.list_archive(config.data_dir, zim, library.read_manifest(config.data_dir))
    newest = library.newest_verified(versions)
    if newest is None:
        return

    state = library.hot_copy_state(hot_dir, zim, newest)
    if not state.current:
        existing = hot_dir / newest.file_name
        reclaim = existing.stat().st_size if existing.exists() else 0
        free = free_bytes(hot_dir) + reclaim
        if free < newest.entry.size + FREE_SPACE_MARGIN:
            log.warning(
                "hot copy skipped: %s needs %.1f GiB free, has %.1f GiB", hot_dir,
                (newest.entry.size + FREE_SPACE_MARGIN) / 1024**3, free / 1024**3,
            )
            return
        log.info("making hot copy of %s (before: %s)", newest.file_name, state.note)
        download.copy_verified(
            config.data_dir / library.ZIM_SUBDIR / newest.file_name, hot_dir, newest.entry.sha256
        )
        log.info("hot copy of %s verified", newest.file_name)

    stale = [
        p for p in hot_dir.iterdir()
        if p.is_file() and p.name not in (newest.file_name, newest.file_name + download.SHA256_SUFFIX)
        and p.name.lstrip(".").startswith(zim.file_prefix)
    ]
    if stale:
        rebuild_library(config, exclude=set(stale))
        for p in stale:
            log.info("deleting old hot-copy file %s", p.name)
            p.unlink()


def prune_book(config: Config, zim: ZimSpec) -> None:
    """Keep the newest ``keep_versions`` verified versions of ``zim`` in the archive and
    delete older verified ones (see the safety rules in the module docstring).

    Also removes manifest entries whose file is missing, and logs, but keeps, files that
    the manifest doesn't list.
    """
    manifest = library.read_manifest(config.data_dir)
    versions = library.list_archive(config.data_dir, zim, manifest)
    verified = [v for v in versions if v.verified]
    doomed = verified[config.policy.keep_versions:]
    missing = [v for v in versions if v.entry is not None and v.size_on_disk is None]
    for v in versions:
        if v.entry is None:
            log.warning("%s isn't in the manifest; leaving it alone", v.file_name)
        elif v.size_on_disk is not None and not v.verified:
            log.warning("%s size differs from the manifest; leaving it alone", v.file_name)

    zim_dir = config.data_dir / library.ZIM_SUBDIR
    if doomed:
        rebuild_library(config, exclude={zim_dir / v.file_name for v in doomed})
    for v in doomed:
        log.info("pruning %s (keeping %d newer)", v.file_name, config.policy.keep_versions)
        (zim_dir / v.file_name).unlink()
    for v in missing:
        log.warning("%s is in the manifest but missing from zim/; removing the entry", v.file_name)
    gone = {v.file_name for v in doomed + missing}
    if gone:
        library.write_manifest(config.data_dir, [e for e in manifest if e.file not in gone])


def run_update(config: Config, today: date) -> None:
    """``wikibackup update``: steps 1–4 for each tracked book, under the archive lock.

    The caller has checked that the archive drive is mounted. Errors propagate after
    being logged by the caller; a failure on one book stops the run.
    """
    prepare_archive(config.data_dir)
    with archive_lock(config.data_dir):
        for zim in config.zims:
            manifest = library.read_manifest(config.data_dir)
            newest = library.newest_verified(library.list_archive(config.data_dir, zim, manifest))
            release = latest_release(zim)
            decision = library.decide_update(release, newest, config.policy, today)
            log.info("%s: %s", zim.file_prefix.rstrip("_"), decision.reason)
            if decision.download:
                fetch_and_install(config, release)
            ensure_hot_copy(config, zim)
            prune_book(config, zim)


def run_prune(config: Config) -> None:
    """``wikibackup prune``: ensure the hot copy, then prune, for each tracked book, under
    the archive lock. No network access. The caller has checked the archive is mounted."""
    prepare_archive(config.data_dir)
    with archive_lock(config.data_dir):
        for zim in config.zims:
            ensure_hot_copy(config, zim)
            prune_book(config, zim)
