"""Tests for ``wikibackup.library``: manifest reading, archive and hot-copy listing, the
mount check, and the update rule.

Archives and hot copies are built in pytest's ``tmp_path`` with small fake ZIM files (the
content doesn't matter; only names and sizes are read). ``tmp_path`` isn't under
``/Volumes``, so it always counts as available.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from wikibackup import library
from wikibackup.catalog import Release
from wikibackup.config import Policy, ZimSpec

MAXI = ZimSpec("wikipedia_en_all", "maxi")
POLICY = Policy(min_age_months=6, keep_versions=3)
TODAY = date(2026, 11, 23)


def entry(version: str, size: int = 10, sha: str = "a" * 64, flavour: str = "maxi") -> dict:
    """A manifest entry dict for a fake version."""
    return {
        "file": f"wikipedia_en_all_{flavour}_{version}.zim", "name": "wikipedia_en_all",
        "flavour": flavour, "version": version, "size": size, "sha256": sha,
        "added": "2026-11-23T00:00:00+00:00",
    }


def make_archive(data_dir: Path, entries: list[dict], files: dict[str, int]) -> None:
    """Write manifest.json with ``entries`` and create ``files`` (name -> size) in zim/."""
    (data_dir / "zim").mkdir(parents=True)
    (data_dir / "manifest.json").write_text(json.dumps({"format": 1, "zims": entries}))
    for name, size in files.items():
        (data_dir / "zim" / name).write_bytes(b"x" * size)


def release(version: str) -> Release:
    """A catalog release of MAXI at ``version``."""
    return Release(MAXI, version, date(2026, 8, 25), f"wikipedia_en_all_maxi_{version}.zim",
                   "https://example.invalid/x.meta4", None)


# --- mount check ---------------------------------------------------------------------

def test_volume_root():
    assert library.volume_root(Path("/Volumes/Starfish_Prime/kiwix-data")) == Path(
        "/Volumes/Starfish_Prime"
    )
    assert library.volume_root(Path("/Users/x/.local/state")) is None


def test_unmounted_volume_is_unavailable():
    path = Path("/Volumes/wikibackup-test-not-a-drive/kiwix-data")
    assert not library.is_available(path)
    assert library.disk_usage(path) is None


def test_disk_usage_of_missing_folder_uses_parent(tmp_path):
    usage = library.disk_usage(tmp_path / "not" / "created")
    assert usage is not None and usage[0] > 0


# --- manifest ------------------------------------------------------------------------

def test_missing_manifest_is_empty(tmp_path):
    assert library.read_manifest(tmp_path) == []


@pytest.mark.parametrize(
    "content", ["not json", '{"format": 2, "zims": []}', '{"format": 1, "zims": [{"file": 1}]}']
)
def test_bad_manifest_raises(tmp_path, content):
    (tmp_path / "manifest.json").write_text(content)
    with pytest.raises(library.ManifestError):
        library.read_manifest(tmp_path)


# --- archive listing -----------------------------------------------------------------

def test_list_archive_combines_manifest_and_disk(tmp_path):
    make_archive(
        tmp_path,
        entries=[entry("2026-02"), entry("2025-08"), entry("2026-08", size=99), entry("2026-06", flavour="nopic")],
        files={
            "wikipedia_en_all_maxi_2026-02.zim": 10,  # verified
            "wikipedia_en_all_maxi_2026-08.zim": 10,  # size differs from manifest
            "wikipedia_en_all_maxi_2027-02.zim": 5,   # not in manifest
            "wikipedia_en_all_nopic_2026-06.zim": 10,  # another flavour: ignored
            "notes.txt": 1,                            # not a ZIM: ignored
        },
    )
    versions = library.list_archive(tmp_path, MAXI, library.read_manifest(tmp_path))
    assert [(v.version, v.verified) for v in versions] == [
        ("2027-02", False), ("2026-08", False), ("2026-02", True), ("2025-08", False),
    ]
    assert versions[3].size_on_disk is None  # in manifest, file missing
    assert library.newest_verified(versions).version == "2026-02"


def test_list_archive_without_zim_folder(tmp_path):
    assert library.list_archive(tmp_path, MAXI, []) == []


# --- hot copy ------------------------------------------------------------------------

@pytest.fixture
def newest(tmp_path):
    """A verified archive version 2026-08 (size 10, sha 'a'*64) in tmp_path/archive."""
    data_dir = tmp_path / "archive"
    make_archive(data_dir, [entry("2026-08")], {"wikipedia_en_all_maxi_2026-08.zim": 10})
    versions = library.list_archive(data_dir, MAXI, library.read_manifest(data_dir))
    return library.newest_verified(versions)


def hot(tmp_path: Path, name: str, size: int = 10, sha: str | None = "a" * 64) -> Path:
    """Create a hot copy file (and its .sha256 unless sha is None); return HOT_DIR."""
    hot_dir = tmp_path / "hot"
    hot_dir.mkdir(exist_ok=True)
    (hot_dir / name).write_bytes(b"x" * size)
    if sha is not None:
        (hot_dir / (name + ".sha256")).write_text(f"{sha}  {name}\n")
    return hot_dir


def test_hot_copy_current(tmp_path, newest):
    hot_dir = hot(tmp_path, "wikipedia_en_all_maxi_2026-08.zim")
    assert library.hot_copy_state(hot_dir, MAXI, newest) == library.HotCopyState(
        "wikipedia_en_all_maxi_2026-08.zim", True, ""
    )


@pytest.mark.parametrize(
    "name,size,sha,note",
    [
        ("wikipedia_en_all_maxi_2026-02.zim", 10, "a" * 64, "archive's newest is"),
        ("wikipedia_en_all_maxi_2026-08.zim", 9, "a" * 64, "size differs"),
        ("wikipedia_en_all_maxi_2026-08.zim", 10, None, "no .sha256"),
        ("wikipedia_en_all_maxi_2026-08.zim", 10, "b" * 64, ".sha256 differs"),
    ],
)
def test_hot_copy_not_current(tmp_path, newest, name, size, sha, note):
    hot_dir = hot(tmp_path, name, size, sha)
    state = library.hot_copy_state(hot_dir, MAXI, newest)
    assert not state.current and note in state.note


def test_no_hot_copy(tmp_path, newest):
    state = library.hot_copy_state(tmp_path / "hot-missing", MAXI, newest)
    assert state == library.HotCopyState(None, False, "no hot copy")


# --- update rule ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "version,today,expected",
    [("2026-05", TODAY, 6), ("2026-11", TODAY, 0), ("2025-12", date(2026, 1, 1), 1)],
)
def test_months_old(version, today, expected):
    assert library.months_old(version, today) == expected


def local(version: str) -> library.ArchivedVersion:
    """A verified local version."""
    e = library.ManifestEntry(**entry(version))
    return library.ArchivedVersion(version, e.file, e, e.size)


@pytest.mark.parametrize(
    "newest_local,catalog,download,reason",
    [
        (None, "2026-08", True, "no verified local copy"),
        ("2026-08", "2026-08", False, "up to date"),
        ("2026-08", "2027-02", False, "3 months old (minimum 6)"),
        ("2026-05", "2026-08", True, "would replace 2026-05 (6 months old)"),
    ],
)
def test_decide_update(newest_local, catalog, download, reason):
    decision = library.decide_update(
        release(catalog), local(newest_local) if newest_local else None, POLICY, TODAY
    )
    assert decision.download is download
    assert reason in decision.reason
