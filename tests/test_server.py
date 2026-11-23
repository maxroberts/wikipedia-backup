"""Tests for ``wikibackup.server``: choosing the served file, building ``library.xml``, the
``serve`` loop, and the health helpers.

The kiwix-tools here are the fakes from ``conftest.py``. The real kiwix-serve and
kiwix-manage were tested by hand on 2026-11-23 (see Stage 4 in the plan). Archive and hot
folders are under ``tmp_path``; unmounted drives are simulated by replacing
``library.is_available``.
"""

import hashlib
import threading
import time
from pathlib import Path

import pytest

from wikibackup import library, server, update
from wikibackup.config import Config, KiwixTools, Policy, ZimSpec

BOOK = ZimSpec("wikipedia_en_all", "maxi")


def name(version: str) -> str:
    return f"wikipedia_en_all_maxi_{version}.zim"


@pytest.fixture
def config(tmp_path):
    return Config(
        policy=Policy(min_age_months=6, keep_versions=3), zims=(BOOK,),
        kiwix_tools=KiwixTools("3.8.2", ("macos-arm64",)),
        data_dir=tmp_path / "archive", hot_dir=tmp_path / "hot", state_dir=tmp_path / "state",
        bind_address="all", port=18099, log_level="INFO",
    )


def archive(cfg: Config, version: str, data: bytes = b"zim data") -> None:
    """Add a verified version to the archive."""
    update.prepare_archive(cfg.data_dir)
    (cfg.data_dir / "zim" / name(version)).write_bytes(data)
    entries = library.read_manifest(cfg.data_dir) + [library.ManifestEntry(
        name(version), BOOK.name, BOOK.flavour, version, len(data),
        hashlib.sha256(data).hexdigest(), "2026-01-01T00:00:00+00:00",
    )]
    library.write_manifest(cfg.data_dir, entries)


def hot(cfg: Config, version: str, data: bytes = b"zim data") -> Path:
    """Put a verified hot copy of ``data`` in HOT_DIR."""
    cfg.hot_dir.mkdir(exist_ok=True)
    path = cfg.hot_dir / name(version)
    path.write_bytes(data)
    (cfg.hot_dir / (name(version) + ".sha256")).write_text(
        f"{hashlib.sha256(data).hexdigest()}  {name(version)}\n")
    return path


def unmounted(monkeypatch, *paths: Path) -> None:
    """Make library.is_available report ``paths`` as on unmounted drives."""
    monkeypatch.setattr(library, "is_available", lambda p: p not in paths)


# --- served_file ---------------------------------------------------------------------

def test_serves_current_hot_copy(config):
    archive(config, "2026-02")
    archive(config, "2026-08")
    hot(config, "2026-08")
    assert server.served_file(config, BOOK) == config.hot_dir / name("2026-08")


def test_falls_back_to_archive_when_hot_copy_is_stale(config):
    archive(config, "2026-08")
    hot(config, "2026-02")
    assert server.served_file(config, BOOK) == config.data_dir / "zim" / name("2026-08")


def test_falls_back_to_archive_when_ssd_unmounted(config, monkeypatch):
    archive(config, "2026-08")
    hot(config, "2026-08")
    unmounted(monkeypatch, config.hot_dir)
    assert server.served_file(config, BOOK) == config.data_dir / "zim" / name("2026-08")


def test_hdd_unmounted_serves_newest_verified_hot_file(config, monkeypatch):
    hot(config, "2026-02")
    hot(config, "2026-08")
    (config.hot_dir / name("2027-02")).write_bytes(b"unverified: no .sha256")
    unmounted(monkeypatch, config.data_dir)
    assert server.served_file(config, BOOK) == config.hot_dir / name("2026-08")


def test_nothing_to_serve(config, monkeypatch):
    assert server.served_file(config, BOOK) is None
    unmounted(monkeypatch, config.data_dir, config.hot_dir)
    assert server.served_file(config, BOOK) is None


# --- build_library -------------------------------------------------------------------

def test_build_library_lists_the_served_file(config):
    archive(config, "2026-08")
    hot(config, "2026-08")
    assert server.build_library(config) == [config.hot_dir / name("2026-08")]
    assert server.library_books(config) == {(config.hot_dir / name("2026-08")).resolve(): True}
    assert [p.name for p in config.state_dir.iterdir()] == ["library.xml"]  # no temp files


def test_build_library_empty(config):
    assert server.build_library(config) == []
    assert server.library_path(config).read_text() == server.EMPTY_LIBRARY


def test_kiwix_manage_failure_keeps_old_library(config, fake_kiwix_tools):
    archive(config, "2026-08")
    server.build_library(config)
    before = server.library_path(config).read_text()
    (fake_kiwix_tools / "kiwix-manage").write_text("#!/bin/sh\necho broken >&2\nexit 1\n")
    archive(config, "2027-02")
    with pytest.raises(server.ServerError, match="broken"):
        server.build_library(config)
    assert server.library_path(config).read_text() == before


def test_missing_kiwix_manage(config, fake_kiwix_tools):
    archive(config, "2026-08")
    (fake_kiwix_tools / "kiwix-manage").unlink()
    with pytest.raises(server.ServerError, match="install-tools"):
        server.build_library(config)


def test_rebuild_guard_refuses_to_delete_a_served_file(config):
    archive(config, "2026-08")
    served = config.data_dir / "zim" / name("2026-08")
    with pytest.raises(server.ServerError, match="still serves"):
        update.rebuild_library(config, exclude={served})


def test_update_hot_copy_switch_rebuilds_library(config):
    """After a new hot copy replaces the old one, library.xml points at the new file."""
    archive(config, "2026-02")
    update.run_prune(config)
    archive(config, "2026-08")
    update.run_prune(config)
    assert list(server.library_books(config)) == [(config.hot_dir / name("2026-08")).resolve()]
    assert not (config.hot_dir / name("2026-02")).exists()


# --- serve loop ----------------------------------------------------------------------

def test_serve_command(config, fake_kiwix_tools):
    assert server.serve_command(config) == [
        str(fake_kiwix_tools / "kiwix-serve"), "--library", "--monitorLibrary",
        "--nodatealiases", "--address", "all", "--port", "18099",
        str(config.state_dir / "library.xml"),
    ]


def test_run_serve_rebuilds_when_a_hot_copy_appears(config, fake_kiwix_tools):
    archive(config, "2026-08")

    def later():
        """While serve runs: add a hot copy, wait for the rebuild, then stop kiwix-serve."""
        deadline = time.monotonic() + 10
        while not (fake_kiwix_tools / "serve-args.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        hot(config, "2026-08")
        target = (config.hot_dir / name("2026-08")).resolve()
        while target not in server.library_books(config) and time.monotonic() < deadline:
            time.sleep(0.02)
        (fake_kiwix_tools / "stop").touch()

    thread = threading.Thread(target=later)
    thread.start()
    status = server.run_serve(config, poll_s=0.05)
    thread.join()
    assert status == 3  # the fake's "stop file" exit status, passed through
    assert list(server.library_books(config)) == [(config.hot_dir / name("2026-08")).resolve()]
    args = (fake_kiwix_tools / "serve-args.txt").read_text().splitlines()
    assert args[:3] == ["--library", "--monitorLibrary", "--nodatealiases"]


def test_run_serve_without_kiwix_serve(config, fake_kiwix_tools):
    (fake_kiwix_tools / "kiwix-serve").unlink()
    with pytest.raises(server.ServerError, match="can't start kiwix-serve"):
        server.run_serve(config, poll_s=0.05)


# --- health helpers ------------------------------------------------------------------

def test_library_books_reports_missing_files(config):
    config.state_dir.mkdir()
    server.library_path(config).write_text(
        '<library version="20110515">\n  <book id="a" path="../gone.zim" />\n</library>\n')
    assert server.library_books(config) == {(config.state_dir.parent / "gone.zim").resolve(): False}


def test_http_status(file_server):
    url = file_server.add("/", b"home")
    assert server.http_status(url) == "200"
    assert server.http_status(file_server.url + "/nope") == "404"
    assert server.http_status("http://127.0.0.1:9/").startswith("no answer")
