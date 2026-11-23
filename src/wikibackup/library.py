"""What's on disk: the archive manifest, archive and hot-copy listings, and the update rule.

``cli status`` and ``cli check`` use this module; from Stage 3, ``update`` and ``prune``
will too. In Stage 2 everything here is **read-only**: it never creates, writes or deletes
files or folders.

Locations (see "Where things live" in ``docs/kiwix_backup_plan.md``):

- **Archive**, ``DATA_DIR`` on the HDD: ZIMs in ``zim/`` and ``manifest.json``.
- **Hot copy**, ``HOT_DIR`` on the SSD: the newest version's ZIM plus a
  ``<file>.zim.sha256`` file, which is written only after the copy's SHA-256 has been
  checked against the manifest.

**Manifest format** (``DATA_DIR/manifest.json``; Stage 3 writes it, this module reads it).
The manifest is the source of truth: a ZIM in ``zim/`` counts as a kept, verified version
only if the manifest lists it.

    {"format": 1,
     "zims": [{"file": "wikipedia_en_all_maxi_2026-08.zim",
               "name": "wikipedia_en_all", "flavour": "maxi", "version": "2026-08",
               "size": 127418087648, "sha256": "<64 hex>",
               "added": "2026-11-23T14:00:00+00:00"}]}

**Mounted or not:** a path under ``/Volumes/<drive>/`` is available only if
``/Volumes/<drive>`` is a mount point. This matters because a folder can exist under
``/Volumes`` while its drive is detached. Paths elsewhere are treated as always available.
"""

import json
import os
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from wikibackup.catalog import Release, version_from_file_name
from wikibackup.config import Policy, ZimSpec

MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 1
ZIM_SUBDIR = "zim"
SHA256_SUFFIX = ".sha256"


class ManifestError(Exception):
    """``manifest.json`` exists but can't be read or has an unexpected format."""


@dataclass(frozen=True)
class ManifestEntry:
    """One verified version recorded in the manifest. ``size`` is in bytes."""

    file: str
    name: str
    flavour: str
    version: str
    size: int
    sha256: str
    added: str


@dataclass(frozen=True)
class ArchivedVersion:
    """One version of a book in the archive, as found by ``list_archive``.

    ``entry`` is the manifest record, or ``None`` if the file is in ``zim/`` but not in the
    manifest (unverified, e.g. left by a crash). ``size_on_disk`` is ``None`` if the
    manifest lists the file but it's missing from ``zim/``.
    """

    version: str
    file_name: str
    entry: ManifestEntry | None
    size_on_disk: int | None

    @property
    def verified(self) -> bool:
        """Listed in the manifest, present, and the size on disk matches the manifest."""
        return self.entry is not None and self.size_on_disk == self.entry.size


@dataclass(frozen=True)
class HotCopyState:
    """The hot copy of one book, as found by ``hot_copy_state``.

    ``file_name`` is the ZIM found in ``HOT_DIR`` (the newest, if several are there), or
    ``None``. ``current`` is true when that file is the newest verified archive version,
    its size matches the manifest, and its ``.sha256`` file holds the manifest's hash.
    ``note`` explains in a few words why it isn't current (empty when it is).
    """

    file_name: str | None
    current: bool
    note: str


@dataclass(frozen=True)
class Decision:
    """Whether ``update`` would download a release, and why (one line for humans)."""

    download: bool
    reason: str


def volume_root(path: Path) -> Path | None:
    """Return ``/Volumes/<drive>`` for a path on an external volume, else ``None``."""
    parts = path.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        return Path("/Volumes", parts[2])
    return None


def is_available(path: Path) -> bool:
    """True if ``path``'s drive is mounted (see the module docstring). Doesn't check that
    ``path`` itself exists."""
    root = volume_root(path)
    return root is None or os.path.ismount(root)


def disk_usage(path: Path) -> tuple[int, int, int] | None:
    """``(total, used, free)`` bytes of the drive holding ``path`` (``shutil.disk_usage``'s
    named tuple), or ``None`` if the drive isn't mounted. Uses the nearest existing parent,
    so ``path`` itself needn't exist."""
    if not is_available(path):
        return None
    root = volume_root(path) or path
    while not root.exists() and root != root.parent:
        root = root.parent
    return shutil.disk_usage(root)


def read_manifest(data_dir: Path) -> list[ManifestEntry]:
    """Read ``data_dir/manifest.json``. Returns ``[]`` if the file doesn't exist.

    Raises ``ManifestError`` if it exists but isn't valid JSON in the expected format.
    """
    path = data_dir / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"can't read {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("format") != MANIFEST_FORMAT:
        raise ManifestError(f"{path}: expected format {MANIFEST_FORMAT}")
    try:
        return [ManifestEntry(**item) for item in raw["zims"]]
    except (KeyError, TypeError) as exc:
        raise ManifestError(f"{path}: bad entry: {exc}") from exc


def list_archive(
    data_dir: Path, zim: ZimSpec, manifest: list[ManifestEntry]
) -> list[ArchivedVersion]:
    """List the archive's versions of ``zim``, newest first.

    Combines the manifest's entries for this book with the matching ``.zim`` files in
    ``data_dir/zim/``, so the result includes unverified files and manifest entries whose
    file is missing (see ``ArchivedVersion``). A missing ``zim/`` folder counts as empty.
    """
    entries = {
        e.file: e for e in manifest if e.name == zim.name and e.flavour == zim.flavour
    }
    on_disk = {}
    zim_dir = data_dir / ZIM_SUBDIR
    if zim_dir.is_dir():
        for path in zim_dir.iterdir():
            if path.is_file() and version_from_file_name(zim, path.name):
                on_disk[path.name] = path.stat().st_size

    versions = [
        ArchivedVersion(
            version=version_from_file_name(zim, name) or entries[name].version,
            file_name=name,
            entry=entries.get(name),
            size_on_disk=on_disk.get(name),
        )
        for name in entries.keys() | on_disk.keys()
    ]
    return sorted(versions, key=lambda v: v.version, reverse=True)


def newest_verified(versions: list[ArchivedVersion]) -> ArchivedVersion | None:
    """The newest verified version in a ``list_archive`` result, or ``None``."""
    return next((v for v in versions if v.verified), None)


def hot_copy_state(
    hot_dir: Path, zim: ZimSpec, newest: ArchivedVersion | None
) -> HotCopyState:
    """Describe the hot copy of ``zim`` in ``hot_dir`` against the newest verified
    archive version ``newest`` (from ``newest_verified``).

    Doesn't hash the file: it trusts the ``.sha256`` file, which is written only after a
    successful hash check. Callers check first that ``hot_dir``'s drive is mounted.
    """
    found = []
    if hot_dir.is_dir():
        found = sorted(
            (p for p in hot_dir.iterdir() if p.is_file() and version_from_file_name(zim, p.name)),
            key=lambda p: p.name,
            reverse=True,
        )
    if not found:
        return HotCopyState(None, False, "no hot copy")
    path = found[0]
    if newest is None:
        return HotCopyState(path.name, False, "no verified archive version to compare with")
    if path.name != newest.file_name:
        return HotCopyState(path.name, False, f"archive's newest is {newest.file_name}")
    if path.stat().st_size != newest.entry.size:
        return HotCopyState(path.name, False, "size differs from manifest")
    sidecar = path.with_name(path.name + SHA256_SUFFIX)
    try:
        recorded = sidecar.read_text().split()[0].lower()
    except (OSError, IndexError):
        return HotCopyState(path.name, False, "not verified (no .sha256 file)")
    if recorded != newest.entry.sha256:
        return HotCopyState(path.name, False, ".sha256 differs from manifest")
    return HotCopyState(path.name, True, "")


def months_old(version: str, today: date) -> int:
    """Whole calendar months from a ``YYYY-MM`` version tag to ``today``.

    Example: ``months_old("2026-05", date(2026, 11, 23))`` is 6. Ignores the day, so a
    version counts as one month older on the 1st of each month.
    """
    year, month = (int(x) for x in version.split("-"))
    return (today.year * 12 + today.month) - (year * 12 + month)


def decide_update(
    release: Release, newest: ArchivedVersion | None, policy: Policy, today: date
) -> Decision:
    """Apply the update rule from the plan's Design section.

    Download if there's no verified local version; otherwise only if the release is newer
    than the newest verified local version **and** that version is at least
    ``policy.min_age_months`` old (by ``months_old``). Unverified files are ignored.
    """
    if newest is None:
        return Decision(True, f"no verified local copy; would download {release.version}")
    if release.version <= newest.version:
        return Decision(False, f"up to date: newest local copy is {newest.version}")
    age = months_old(newest.version, today)
    if age < policy.min_age_months:
        return Decision(
            False,
            f"{release.version} is available, but local {newest.version} is {age} months "
            f"old (minimum {policy.min_age_months})",
        )
    return Decision(
        True, f"would replace {newest.version} ({age} months old) with {release.version}"
    )
