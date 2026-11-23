"""Tests for ``wikibackup.software``: kiwix-tools backups and installation.

``software._fetch`` is replaced with a dict lookup that serves a small tarball built in
the test, plus its ``.md5``. ``OPT_DIR`` and ``BIN_DIR`` point at temporary folders
(``conftest.py``); each install test empties the fake install first, so the real
unpacking code runs. Nothing touches the network or the real ``~/.local``.
"""

import hashlib
import io
import shutil
import tarfile

import pytest

from wikibackup import download, library, software
from wikibackup.config import Config, KiwixTools, Policy, ZimSpec

VERSION = "3.8.2"


def make_tarball(platform: str, files=("kiwix-serve", "kiwix-manage", "kiwix-search")) -> bytes:
    """A tarball shaped like Kiwix's: one top folder holding the executables."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in files:
            body = f"#!/bin/sh\necho {f}\n".encode()
            info = tarfile.TarInfo(f"kiwix-tools_{platform}-{VERSION}/{f}")
            info.size, info.mode = len(body), 0o755
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture
def config(tmp_path):
    return Config(
        policy=Policy(6, 3), zims=(ZimSpec("wikipedia_en_all", "maxi"),),
        kiwix_tools=KiwixTools(VERSION, ("macos-arm64", "linux-x86_64")),
        data_dir=tmp_path / "archive", hot_dir=tmp_path / "hot", state_dir=tmp_path / "state",
        bind_address="all", port=8080, log_level="INFO",
    )


@pytest.fixture
def kiwix_site(monkeypatch):
    """Serve tarballs and .md5 files for both platforms; returns the dict to tamper with."""
    site = {}
    for platform in ("macos-arm64", "linux-x86_64"):
        name = software.tarball_name(platform, VERSION)
        data = make_tarball(platform)
        site[software.RELEASE_URL + name] = data
        site[software.RELEASE_URL + name + ".md5"] = f"{hashlib.md5(data).hexdigest()}  {name}\n".encode()

    def fetch(url):
        if url not in site:
            raise software.SoftwareError(f"404 {url}")
        return site[url]

    monkeypatch.setattr(software, "_fetch", fetch)
    return site


def test_backups_go_to_archive_and_hot_copy(config, kiwix_site):
    software.ensure_backups(config)
    for platform in ("macos-arm64", "linux-x86_64"):
        name = software.tarball_name(platform, VERSION)
        archived = config.data_dir / software.SOFTWARE_SUBDIR / name
        hot = config.hot_dir / software.SOFTWARE_SUBDIR / name
        assert archived.read_bytes() == hot.read_bytes() == kiwix_site[software.RELEASE_URL + name]
        assert library.has_sha256_file(archived) and library.has_sha256_file(hot)
        assert archived.with_name(name + ".sha256").read_text().split()[0] == download.sha256_file(archived)


def test_md5_mismatch_is_a_warning_and_leaves_nothing(config, kiwix_site, caplog):
    name = software.tarball_name("linux-x86_64", VERSION)
    kiwix_site[software.RELEASE_URL + name] += b"corrupted"
    software.ensure_backups(config)
    assert "MD5 doesn't match" in caplog.text
    assert not (config.data_dir / software.SOFTWARE_SUBDIR / name).exists()
    assert (config.data_dir / software.SOFTWARE_SUBDIR /
            software.tarball_name("macos-arm64", VERSION)).exists()  # the other one still worked


def test_existing_backup_is_not_downloaded_again(config, kiwix_site, monkeypatch):
    software.ensure_backups(config)
    monkeypatch.setattr(software, "_fetch", lambda url: pytest.fail(f"fetched {url}"))
    software.ensure_backups(config)


def test_install_tools(config, kiwix_site, fake_kiwix_tools):
    shutil.rmtree(fake_kiwix_tools)
    target = software.install_tools(config)
    assert target == software.install_dir(VERSION)
    assert sorted(p.name for p in target.iterdir()) == ["kiwix-manage", "kiwix-search", "kiwix-serve"]
    assert (target / "kiwix-serve").stat().st_mode & 0o100  # still executable
    for tool in ("kiwix-serve", "kiwix-manage"):
        assert (software.BIN_DIR / tool).resolve() == (target / tool).resolve()
    assert software.install_tools(config) == target  # running again is fine


def test_install_from_hot_copy_when_archive_unmounted(config, kiwix_site, fake_kiwix_tools, monkeypatch):
    software.ensure_backups(config)
    shutil.rmtree(fake_kiwix_tools)
    monkeypatch.setattr(library, "is_available", lambda p: config.data_dir not in (p, *p.parents))
    assert (software.install_tools(config) / "kiwix-serve").is_file()


def test_install_refuses_tampered_tarball(config, kiwix_site, fake_kiwix_tools):
    software.ensure_backups(config)
    shutil.rmtree(fake_kiwix_tools)
    name = software.tarball_name("macos-arm64", VERSION)
    for folder in (config.data_dir, config.hot_dir):
        (folder / software.SOFTWARE_SUBDIR / name).write_bytes(make_tarball("macos-arm64", ("evil",)))
    with pytest.raises(software.SoftwareError, match="no verified"):
        software.install_tools(config)


def test_install_refuses_to_replace_a_regular_file(config, kiwix_site):
    software.BIN_DIR.mkdir(parents=True)
    (software.BIN_DIR / "kiwix-serve").write_text("someone else's file")
    with pytest.raises(software.SoftwareError, match="isn't a symlink"):
        software.install_tools(config)


def test_tarball_without_kiwix_serve(config, kiwix_site, fake_kiwix_tools):
    shutil.rmtree(fake_kiwix_tools)
    name = software.tarball_name("macos-arm64", VERSION)
    data = make_tarball("macos-arm64", ("kiwix-manage",))
    kiwix_site[software.RELEASE_URL + name] = data
    kiwix_site[software.RELEASE_URL + name + ".md5"] = hashlib.md5(data).hexdigest().encode()
    with pytest.raises(software.SoftwareError, match="doesn't contain kiwix-serve"):
        software.install_tools(config)
    assert not software.install_dir(VERSION).exists()
