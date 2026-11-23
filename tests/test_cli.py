"""Tests for the ``check`` and ``status`` subcommands in ``wikibackup.cli``.

``load_config`` is replaced with a config pointing at folders in ``tmp_path``, and the
catalog functions are replaced with ones that parse the saved fixtures, so no test reads
the real ``.env``, touches the data drives, or uses the network.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from wikibackup import catalog, cli
from wikibackup.config import Config, KiwixTools, Policy, ZimSpec

FIXTURES = Path(__file__).parent / "fixtures"
MAXI = ZimSpec("wikipedia_en_all", "maxi")
SHA = "34162d18b9f96f494eac48a73a309c72e0bc95d85f76be405d0f5de795854aa6"


@pytest.fixture
def config(tmp_path, monkeypatch):
    """A Config with archive, hot and state folders under tmp_path, installed in cli."""
    cfg = Config(
        policy=Policy(min_age_months=6, keep_versions=3), zims=(MAXI,),
        kiwix_tools=KiwixTools("3.8.2", ("macos-arm64",)),
        data_dir=tmp_path / "archive", hot_dir=tmp_path / "hot", state_dir=tmp_path / "state",
        bind_address="all", port=8080, log_level="INFO",
    )
    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)
    return cfg


@pytest.fixture
def offline_catalog(monkeypatch):
    """Serve the saved catalog and metalink fixtures instead of fetching them."""
    xml = (FIXTURES / "catalog_wikipedia_en_all_2026-11-23.xml").read_bytes()
    meta4 = (FIXTURES / "wikipedia_en_all_maxi_2026-08.zim.meta4").read_bytes()
    monkeypatch.setattr(cli, "latest_release", lambda zim: catalog.parse_catalog(xml, zim))
    monkeypatch.setattr(cli, "fetch_metalink", lambda r: catalog.parse_metalink(meta4))


def add_version(cfg: Config, version: str, size: int = 10) -> None:
    """Put a verified fake version in the archive (manifest entry + file)."""
    name = f"wikipedia_en_all_maxi_{version}.zim"
    (cfg.data_dir / "zim").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "zim" / name).write_bytes(b"x" * size)
    path = cfg.data_dir / "manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {"format": 1, "zims": []}
    manifest["zims"].append({
        "file": name, "name": "wikipedia_en_all", "flavour": "maxi", "version": version,
        "size": size, "sha256": SHA, "added": "2026-11-23T00:00:00+00:00",
    })
    path.write_text(json.dumps(manifest))


def test_check_with_empty_archive_would_download(config, offline_catalog, capsys):
    assert cli.main(["check"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "catalog:  2026-08 (issued 2026-08-25)" in out
    assert "118.7 GiB (127,418,087,648 bytes), 7 mirrors" in out
    assert SHA in out
    assert "local:    none" in out
    assert "decision: DOWNLOAD: no verified local copy" in out


def test_check_up_to_date(config, offline_catalog, capsys):
    add_version(config, "2026-08")
    assert cli.main(["check"]) == cli.EXIT_OK
    assert "decision: skip: up to date" in capsys.readouterr().out


def test_check_with_archive_unmounted(config, offline_catalog, monkeypatch, capsys):
    monkeypatch.setattr(cli.library, "is_available", lambda path: False)
    assert cli.main(["check"]) == cli.EXIT_OK
    assert "can't decide, archive drive not mounted" in capsys.readouterr().out


def test_check_catalog_error_exits_1(config, monkeypatch, capsys):
    def fail(zim):
        raise catalog.CatalogError("couldn't fetch x: timed out")
    monkeypatch.setattr(cli, "latest_release", fail)
    assert cli.main(["check"]) == cli.EXIT_ERROR
    assert "wikibackup check: error: couldn't fetch x" in capsys.readouterr().err


def test_status_fresh_install(config, capsys):
    assert cli.main(["status"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "archive (DATA_DIR): " in out and "folder not created yet" in out
    assert "archive:  no versions" in out
    assert "hot copy: none  no hot copy" in out
    assert "library.xml not created yet" in out
    assert "local:     http://127.0.0.1:8080/ -> " in out
    assert "firewall:  " in out
    assert "kiwix-tools 3.8.2: installed" in out  # the fake tools from conftest.py


def test_status_lists_versions_and_current_hot_copy(config, capsys):
    add_version(config, "2026-02")
    add_version(config, "2026-08")
    name = "wikipedia_en_all_maxi_2026-08.zim"
    config.hot_dir.mkdir()
    (config.hot_dir / name).write_bytes(b"x" * 10)
    (config.hot_dir / (name + ".sha256")).write_text(f"{SHA}  {name}\n")
    assert cli.main(["status"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.index("archive:  2026-08") < out.index("archive:  2026-02")
    assert "verified" in out and "NOT verified" not in out
    assert f"hot copy: {name}  current" in out


def test_status_hot_copy_off(config, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda path=None: dataclasses.replace(config, hot_dir=None))
    assert cli.main(["status"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "hot copy (HOT_DIR): off" in out and "hot copy: off" in out


def test_status_bad_manifest_exits_1(config, capsys):
    config.data_dir.mkdir()
    (config.data_dir / "manifest.json").write_text("{")
    assert cli.main(["status"]) == cli.EXIT_ERROR
    assert "wikibackup status: error: can't read" in capsys.readouterr().err
