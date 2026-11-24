"""Resumable HTTP download, SHA-256 hashing, and verified copying of large files.

``update.py`` uses these three functions for the steps that move bytes:

- ``download_resumable`` fetches a ZIM into a ``.part`` file, resuming from wherever the
  file ends. It tries the metalink's mirrors in priority order, then Kiwix's redirector.
- ``sha256_file`` hashes a file (used to verify downloads and copies).
- ``copy_verified`` makes the SSD hot copy: copy under a temporary name, hash the copy,
  rename it into place, then write a ``<file>.sha256`` file.

Nothing here knows about the manifest, the policy, or which drive is which. That's
``update.py``'s job.

**Resume rules** (``download_resumable``). Each attempt asks for
``Range: bytes=<current size>-``, and accepts the reply only if it continues exactly where
the file ends:

- ``206`` whose ``Content-Range`` is ``bytes <offset>-<size-1>/<size>``: append.
- ``200``: the server ignored the range and sent the whole file, so truncate and start
  from 0. Its ``Content-Length`` must equal the expected size.
- ``416`` when the file is already ``size`` bytes long: done.
- Anything else, including a ``Content-Range`` that names a different total size, a
  network error, or a stall longer than ``timeout_s``: the attempt fails, and the next
  attempt uses the next mirror (cycling), after a pause that doubles from 2 s up to 60 s.

A ``.part`` file larger than the expected size is deleted and the download restarts.
Hashing isn't done during the download, because a resumed download would have to re-hash
the existing prefix anyway; the caller hashes the finished file.

**Timing:** hashing reads the whole file once. For the 118.7 GiB maxi on Starfish_Prime
(HDD) it took 18 minutes, about 114 MB/s (Stage 5, 2026-11-23).
"""

import hashlib
import http.client
import logging
import os
import re
import shutil
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

log = logging.getLogger(__name__)

USER_AGENT = "wikibackup/0.1"
CHUNK = 8 * 1024 * 1024  # bytes per read/write
PROGRESS_EVERY = 1024**3  # log progress after each GiB
SHA256_SUFFIX = ".sha256"


class DownloadError(Exception):
    """A download couldn't be completed within the allowed attempts, or a server reply
    was unacceptable (raised inside an attempt, and then retried)."""


class VerifyError(Exception):
    """A file's size or SHA-256 doesn't match what was expected."""


# Errors that end one attempt and trigger a retry. HTTPError is a URLError; a stalled read
# raises TimeoutError; a connection dropped mid-body raises IncompleteRead (an
# HTTPException). Other OSErrors (e.g. ENOSPC, disk full) are local and propagate at once.
RETRYABLE = (
    urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError,
    http.client.HTTPException, DownloadError,
)


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of the file at ``path``. Reads it once in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _attempt(url: str, part: Path, size: int, timeout_s: float) -> None:
    """One download attempt from ``url`` into ``part`` (see the resume rules in the module
    docstring). Returns when ``part`` holds ``size`` bytes. Raises ``OSError`` (network or
    disk) or ``DownloadError`` (an unacceptable reply) otherwise."""
    offset = part.stat().st_size if part.exists() else 0
    if offset > size:
        log.warning("%s is larger than expected; starting over", part.name)
        part.unlink()
        offset = 0
    if offset == size:
        return

    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={offset}-"}
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_s)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset == size:
            return
        raise

    with response:
        if response.status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if not match or (int(match[1]), int(match[2]), int(match[3])) != (offset, size - 1, size):
                raise DownloadError(
                    f"unexpected Content-Range {response.headers.get('Content-Range')!r} "
                    f"(wanted bytes {offset}-{size - 1}/{size})"
                )
            mode = "ab"
        elif response.status == 200:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != size:
                raise DownloadError(f"server sent {length} bytes, expected {size}")
            if offset:
                log.info("server ignored the range request; restarting from 0")
            offset, mode = 0, "wb"
        else:
            raise DownloadError(f"unexpected HTTP status {response.status}")

        written = offset
        next_report = (written // PROGRESS_EVERY + 1) * PROGRESS_EVERY
        started, start_bytes = time.monotonic(), written
        with part.open(mode) as f:
            while chunk := response.read(CHUNK):
                f.write(chunk)
                written += len(chunk)
                if written > size:
                    raise DownloadError(f"server sent more than {size} bytes")
                if written >= next_report:
                    rate = (written - start_bytes) / max(time.monotonic() - started, 1e-6)
                    log.info(
                        "%s: %.1f / %.1f GiB (%.0f%%), %.1f MB/s",
                        part.name, written / 1024**3, size / 1024**3,
                        100 * written / size, rate / 1e6,
                    )
                    next_report += PROGRESS_EVERY
    if written != size:
        raise DownloadError(f"connection ended at {written} of {size} bytes")


def download_resumable(
    urls: Sequence[str],
    part: Path,
    size: int,
    *,
    max_attempts: int = 30,
    timeout_s: float = 60,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Download a file of ``size`` bytes into ``part``, resuming and switching mirrors as
    needed (see the module docstring).

    ``urls`` are tried in order, cycling, one per attempt. ``max_attempts`` counts all
    attempts in this call; ``sleep`` is replaceable so tests don't wait. On success,
    ``part`` holds exactly ``size`` bytes, which aren't verified yet. Raises
    ``DownloadError`` after ``max_attempts`` failures, leaving ``part`` in place for the
    next run to resume. Disk errors (e.g. a full disk) are raised immediately, not retried.
    """
    if not urls:
        raise DownloadError("no download URLs")
    part.parent.mkdir(parents=True, exist_ok=True)
    delay = 2.0
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        url = urls[attempt % len(urls)]
        try:
            log.info("download attempt %d from %s", attempt + 1, url)
            _attempt(url, part, size, timeout_s)
            return
        except RETRYABLE as exc:
            last_error = exc
        log.warning("attempt %d failed: %s; retrying in %.0f s", attempt + 1, last_error, delay)
        sleep(delay)
        delay = min(delay * 2, 60)
    raise DownloadError(f"gave up after {max_attempts} attempts; last error: {last_error}")


def copy_verified(src: Path, dest_dir: Path, sha256: str) -> Path:
    """Copy ``src`` into ``dest_dir`` and verify the copy, for the SSD hot copy.

    Steps: copy to ``dest_dir/.<name>.tmp``, hash the copy and compare with ``sha256``,
    rename it to ``dest_dir/<name>``, then write ``dest_dir/<name>.sha256`` (the
    ``sha256sum`` format, ``<hash>  <name>``). The ``.sha256`` file is written last, so its
    presence means the copy was verified (``library.hot_copy_state`` relies on this).

    Returns the final path. Raises ``VerifyError`` if the hash differs; the temporary file
    is deleted and nothing at the final name is touched. Disk errors propagate.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / src.name
    tmp = dest_dir / f".{src.name}.tmp"
    try:
        log.info("copying %s to %s", src, dest_dir)
        shutil.copyfile(src, tmp)
        actual = sha256_file(tmp)
        if actual != sha256:
            raise VerifyError(f"copy of {src.name} has SHA-256 {actual}, expected {sha256}")
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    final.with_name(final.name + SHA256_SUFFIX).write_text(f"{sha256}  {final.name}\n")
    return final
