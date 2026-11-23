"""Tests for ``wikibackup.catalog`` against responses saved from Kiwix on 2026-11-23.

Fixtures in ``tests/fixtures/``:

- ``catalog_wikipedia_en_all_2026-11-23.xml``: the OPDS response for
  ``name=wikipedia_en_all`` (3 entries: nopic, maxi, mini), unmodified.
- ``wikipedia_en_all_maxi_2026-08.zim.meta4``: the maxi metalink with all but 2 of its
  4 MiB piece hashes removed (the code doesn't read pieces); otherwise unmodified.

No test touches the network: ``_fetch`` is monkeypatched where needed.
"""

from datetime import date
from pathlib import Path

import pytest

from wikibackup import catalog
from wikibackup.catalog import CatalogError, parse_catalog, parse_metalink, version_from_file_name
from wikibackup.config import ZimSpec

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG_XML = (FIXTURES / "catalog_wikipedia_en_all_2026-11-23.xml").read_bytes()
META4_XML = (FIXTURES / "wikipedia_en_all_maxi_2026-08.zim.meta4").read_bytes()
MAXI = ZimSpec("wikipedia_en_all", "maxi")
SHA = "34162d18b9f96f494eac48a73a309c72e0bc95d85f76be405d0f5de795854aa6"


def test_parse_catalog_picks_the_maxi_entry():
    release = parse_catalog(CATALOG_XML, MAXI)
    assert release.version == "2026-08"
    assert release.issued == date(2026, 8, 25)
    assert release.file_name == "wikipedia_en_all_maxi_2026-08.zim"
    assert release.meta4_url == (
        "https://lb.download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim.meta4"
    )
    assert release.approx_size == 127418088448


@pytest.mark.parametrize("flavour,version", [("nopic", "2026-06"), ("mini", "2026-09")])
def test_parse_catalog_filters_on_flavour(flavour, version):
    assert parse_catalog(CATALOG_XML, ZimSpec("wikipedia_en_all", flavour)).version == version


def test_old_style_name_finds_nothing():
    """The name the design session assumed doesn't exist in the catalog."""
    with pytest.raises(CatalogError, match="no catalog entry"):
        parse_catalog(CATALOG_XML, ZimSpec("wikipedia_en_all_maxi", "maxi"))


def test_parse_catalog_rejects_non_xml():
    with pytest.raises(CatalogError, match="valid XML"):
        parse_catalog(b"<html>301 Moved", MAXI)


def test_parse_catalog_rejects_entry_without_metalink():
    xml = CATALOG_XML.replace(b".zim.meta4", b".zim")
    with pytest.raises(CatalogError, match="meta4"):
        parse_catalog(xml, MAXI)


def test_parse_metalink():
    meta = parse_metalink(META4_XML)
    assert meta.file_name == "wikipedia_en_all_maxi_2026-08.zim"
    assert meta.size == 127418087648  # exact; the catalog's length is 800 bytes more
    assert meta.sha256 == SHA
    assert len(meta.mirrors) == 7
    assert meta.mirrors[0].startswith("https://dumps.wikimedia.org/")  # priority 1
    assert meta.mirrors[-1].startswith("https://mirrors.dotsrc.org/")  # priority 7


def test_parse_metalink_rejects_missing_sha256():
    xml = META4_XML.replace(b'type="sha-256"', b'type="sha-512"')
    with pytest.raises(CatalogError, match="sha-256"):
        parse_metalink(xml)


def test_parse_metalink_rejects_bad_size():
    xml = META4_XML.replace(b"<size>127418087648</size>", b"<size>big</size>")
    with pytest.raises(CatalogError, match="size"):
        parse_metalink(xml)


def test_fetch_metalink_rejects_a_different_file(monkeypatch):
    release = parse_catalog(CATALOG_XML, ZimSpec("wikipedia_en_all", "mini"))
    monkeypatch.setattr(catalog, "_fetch", lambda url: META4_XML)  # maxi's metalink
    with pytest.raises(CatalogError, match="expected wikipedia_en_all_mini"):
        catalog.fetch_metalink(release)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("wikipedia_en_all_maxi_2026-08.zim", "2026-08"),
        ("wikipedia_en_all_nopic_2026-08.zim", None),
        ("wikipedia_en_all_maxi_2026-08.zim.sha256", None),
        ("wikipedia_en_all_maxi_latest.zim", None),
    ],
)
def test_version_from_file_name(name, expected):
    assert version_from_file_name(MAXI, name) == expected
