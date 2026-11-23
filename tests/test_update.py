"""Tests for ``wikibackup.update`` (``update`` and ``prune``) and their CLI wiring.

Each test gets an archive, hot and state folder under ``tmp_path``. The catalog and
metalink lookups are replaced with fakes that point at the local ``file_server``
(``conftest.py``), so real downloads run, over HTTP on 127.0.0.1, with no internet access.
Fake ZIMs are small random byte strings; only names, sizes and hashes matter.

Scenarios: first download (archive + manifest + hot copy + log), nothing to do, a newer
release replacing the hot copy and pruning the oldest, a bad checksum, an SSD that's
unmounted or full, adopting an unlisted file, the lock, prune housekeeping, and an
unmounted archive.
"""

import dataclasses
import hashlib
import json
import os
from datetime import date
from pathlib import Path

import pytest

from wikibackup import catalog, cli, download, library, update
from wikibackup.catalog import Metalink, Release
from wikibackup.config import Config, KiwixTools, Policy, ZimSpec

BOOK = ZimSpec("wikipedia_en_all", "maxi")
TODAY = date(2027, 3, 1)


def name(version: str) -> str:
    return f"wikipedia_en_all_maxi_{version}.zim"


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Config with folders under tmp_path; installed for cli.main as well."""
    cfg = Config(
        policy=Policy(min_age_months=6, keep_versions=3), zims=(BOOK,),
        kiwix_tools=KiwixTools("3.8.2", ("macos-arm64",)),
        data_dir=tmp_path / "archive", hot_dir=tmp_path / "hot", state_dir=tmp_path / "state",
        bind_address="all", port=8080, log_level="INFO",
    )
    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)
    return cfg


class FakeKiwix:
    """Publishes one 'latest' release on the file server and answers the catalog and
    metalink lookups for it. ``publish`` can be called again to simulate a new release;
    ``sha_override`` makes the metalink claim a wrong hash."""

    def __init__(self, server, monkeypatch):
        self.server = server
        self.release: Release | None = None
        self.meta: Metalink | None = None
        self.sha_override: str | None = None
        monkeypatch.setattr(update, "latest_release", lambda zim: self.release)
        monkeypatch.setattr(update, "fetch_metalink", lambda r: self.meta)

    def publish(self, version: str, size: int = 200_000) -> bytes:
        data = os.urandom(size)
        url = self.server.add("/" + name(version), data)
        self.release = Release(BOOK, version, date(2026, 1, 1), name(version), url + ".meta4", size)
        self.meta = Metalink(name(version), size, self.sha_override or hashlib.sha256(data).hexdigest(), ())
        return data


@pytest.fixture
def kiwix(file_server, monkeypatch):
    return FakeKiwix(file_server, monkeypatch)


def archive_version(cfg: Config, version: str, data: bytes | None = None) -> bytes:
    """Put a verified version straight into the archive (file + manifest entry)."""
    data = data or os.urandom(1000)
    update.prepare_archive(cfg.data_dir)
    (cfg.data_dir / "zim" / name(version)).write_bytes(data)
    entries = library.read_manifest(cfg.data_dir) + [library.ManifestEntry(
        name(version), BOOK.name, BOOK.flavour, version, len(data),
        hashlib.sha256(data).hexdigest(), "2026-01-01T00:00:00+00:00",
    )]
    library.write_manifest(cfg.data_dir, entries)
    return data


def archive_files(cfg: Config) -> list[str]:
    return sorted(p.name for p in (cfg.data_dir / "zim").iterdir())


def hot_files(cfg: Config) -> list[str]:
    return sorted(p.name for p in cfg.hot_dir.iterdir())


def test_first_update_downloads_verifies_and_hot_copies(config, kiwix):
    data = kiwix.publish("2027-02")
    assert cli.main(["update"]) == cli.EXIT_OK
    assert (config.data_dir / "zim" / name("2027-02")).read_bytes() == data
    [entry] = library.read_manifest(config.data_dir)
    assert (entry.version, entry.size, entry.sha256) == ("2027-02", len(data), kiwix.meta.sha256)
    assert hot_files(config) == [name("2027-02"), name("2027-02") + ".sha256"]
    assert list((config.data_dir / "partial").iterdir()) == []
    log_text = (config.data_dir / "logs" / "wikibackup.log").read_text()
    assert "installed wikipedia_en_all_maxi_2027-02.zim" in log_text
    assert "hot copy of wikipedia_en_all_maxi_2027-02.zim verified" in log_text


def test_second_update_does_nothing(config, kiwix, file_server):
    kiwix.publish("2027-02")
    assert cli.main(["update"]) == cli.EXIT_OK
    n_requests = len(file_server.requests)
    assert cli.main(["update"]) == cli.EXIT_OK
    assert len(file_server.requests) == n_requests
    assert "up to date" in (config.data_dir / "logs" / "wikibackup.log").read_text()


def test_new_release_replaces_hot_copy_and_prunes_oldest(config, kiwix):
    for v in ("2025-02", "2025-08", "2026-02"):
        archive_version(config, v)
    assert update.run_prune(config) is None  # makes the hot copy of 2026-02
    assert hot_files(config) == [name("2026-02"), name("2026-02") + ".sha256"]

    kiwix.publish("2026-08")  # 2026-02 is 13 months old on TODAY, so this downloads
    update.run_update(config, TODAY)
    assert archive_files(config) == [name("2025-08"), name("2026-02"), name("2026-08")]
    assert [e.version for e in library.read_manifest(config.data_dir)] == [
        "2025-08", "2026-02", "2026-08",
    ]
    assert hot_files(config) == [name("2026-08"), name("2026-08") + ".sha256"]


def test_young_local_copy_blocks_download(config, kiwix, file_server):
    archive_version(config, "2027-01")
    kiwix.publish("2027-02")
    update.run_update(config, TODAY)
    assert archive_files(config) == [name("2027-01")]
    assert all(not p.endswith(".zim") or "2027-02" not in p for p, _ in file_server.requests)


def test_bad_checksum_deletes_download_and_keeps_old_versions(config, kiwix):
    for v in ("2025-02", "2025-08", "2026-02"):
        archive_version(config, v)
    kiwix.sha_override = "0" * 64
    kiwix.publish("2026-08")
    assert cli.main(["update"]) == cli.EXIT_ERROR
    assert archive_files(config) == [name("2025-02"), name("2025-08"), name("2026-02")]
    assert list((config.data_dir / "partial").iterdir()) == []
    assert len(library.read_manifest(config.data_dir)) == 3
    assert "doesn't match the metalink" in (config.data_dir / "logs" / "wikibackup.log").read_text()


def test_unmounted_ssd_skips_hot_copy(config, kiwix, monkeypatch):
    kiwix.publish("2027-02")
    real = library.is_available
    monkeypatch.setattr(library, "is_available", lambda p: p != config.hot_dir and real(p))
    update.run_update(config, TODAY)
    assert archive_files(config) == [name("2027-02")]
    assert not config.hot_dir.exists()
    # Next run with the SSD back repairs the hot copy without downloading again.
    monkeypatch.setattr(library, "is_available", real)
    update.run_update(config, TODAY)
    assert hot_files(config) == [name("2027-02"), name("2027-02") + ".sha256"]


def test_full_ssd_skips_hot_copy(config, kiwix, monkeypatch, caplog):
    kiwix.publish("2027-02")
    real_free = update.free_bytes
    monkeypatch.setattr(update, "free_bytes", lambda p: 0 if p == config.hot_dir else real_free(p))
    update.run_update(config, TODAY)
    assert archive_files(config) == [name("2027-02")]
    assert not config.hot_dir.exists()
    assert "hot copy skipped" in caplog.text


def test_unwritable_ssd_skips_hot_copy(config, kiwix, monkeypatch, caplog):
    """E.g. HOT_DIR's parent is owned by root (the T7's top level, found 2026-11-23)."""
    kiwix.publish("2027-02")

    def denied(src, dest_dir, sha256):
        raise PermissionError(13, "Permission denied", str(dest_dir))

    monkeypatch.setattr(update.download, "copy_verified", denied)
    update.run_update(config, TODAY)
    assert archive_files(config) == [name("2027-02")]
    assert "hot copy skipped: [Errno 13] Permission denied" in caplog.text


def test_full_archive_raises_space_error(config, kiwix, monkeypatch):
    kiwix.publish("2027-02")
    monkeypatch.setattr(update, "free_bytes", lambda p: 0)
    with pytest.raises(update.SpaceError, match="archive needs"):
        update.run_update(config, TODAY)


def test_unlisted_file_with_right_hash_is_adopted(config, kiwix, file_server):
    data = kiwix.publish("2027-02")
    update.prepare_archive(config.data_dir)
    (config.data_dir / "zim" / name("2027-02")).write_bytes(data)
    update.run_update(config, TODAY)
    assert [e.version for e in library.read_manifest(config.data_dir)] == ["2027-02"]
    assert ("/" + name("2027-02"), "bytes=0-") not in file_server.requests


def test_unlisted_file_with_wrong_hash_is_replaced(config, kiwix):
    data = kiwix.publish("2027-02")
    update.prepare_archive(config.data_dir)
    (config.data_dir / "zim" / name("2027-02")).write_bytes(b"corrupt")
    update.run_update(config, TODAY)
    assert (config.data_dir / "zim" / name("2027-02")).read_bytes() == data


def test_lock_blocks_a_second_run(config):
    update.prepare_archive(config.data_dir)
    with update.archive_lock(config.data_dir):
        assert cli.main(["prune"]) == cli.EXIT_ERROR
    assert cli.main(["prune"]) == cli.EXIT_OK


def test_prune_housekeeping(config):
    archive_version(config, "2026-02")
    archive_version(config, "2026-08")
    (config.data_dir / "zim" / name("2026-02")).unlink()          # listed, file missing
    (config.data_dir / "zim" / name("2025-08")).write_bytes(b"?")  # on disk, not listed
    config.hot_dir.mkdir()
    (config.hot_dir / name("2025-08")).write_bytes(b"old")         # stale hot copy
    (config.hot_dir / f".{name('2026-08')}.tmp").write_bytes(b"")  # leftover temp file
    (config.hot_dir / "unrelated.txt").write_bytes(b"keep me")
    update.run_prune(config)
    assert [e.version for e in library.read_manifest(config.data_dir)] == ["2026-08"]
    assert archive_files(config) == [name("2025-08"), name("2026-08")]  # unlisted file kept
    assert hot_files(config) == ["unrelated.txt", name("2026-08"), name("2026-08") + ".sha256"]


def test_archive_unmounted_is_not_an_error(config, monkeypatch, capsys):
    monkeypatch.setattr(library, "is_available", lambda p: False)
    assert cli.main(["update"]) == cli.EXIT_OK
    assert "archive drive not mounted" in capsys.readouterr().err
    assert not config.data_dir.exists()
