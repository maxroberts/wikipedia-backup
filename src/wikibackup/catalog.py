"""Find the newest Kiwix release of a tracked book, and its exact size, SHA-256 and mirrors.

``cli check`` and ``update.run_update`` call ``latest_release``; ``check`` and
``update.fetch_and_install`` then call ``fetch_metalink``. Nothing here writes to disk.

Two network sources, both checked on 2026-11-23 (see "How the parts work" in
``docs/kiwix_backup_plan.md``):

1. **OPDS catalog** (Atom XML) at ``CATALOG_URL``. Kiwix names the book
   ``wikipedia_en_all`` and gives the flavour (``maxi``, ``nopic``, ``mini``) in its own
   ``<flavour>`` element. The catalog lists only the latest release of each flavour and
   ignores a ``flavour=`` query parameter, so ``parse_catalog`` filters on flavour itself.
   Each entry's open-access acquisition link points at a ``.zim.meta4`` metalink; that
   link's ``length`` is only approximate (800 bytes off for the 2026-08 maxi).
2. **Metalink** (``.zim.meta4``, RFC 5854 XML). It gives the exact ``<size>``, the
   ``sha-256`` hash, and mirror URLs with priorities. ``parse_metalink`` reads those.

Fetching uses ``urllib``, which follows the catalog's 301 redirect from
``library.kiwix.org`` and the download server's redirects. The XML parsers are
``xml.etree``, which doesn't fetch external entities; the sources are Kiwix's own servers.
"""

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from wikibackup.config import ZimSpec

CATALOG_URL = "https://opds.library.kiwix.org/catalog/v2/entries"
USER_AGENT = "wikibackup/0.1"
TIMEOUT_S = 60

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/terms/}"
METALINK = "{urn:ietf:params:xml:ns:metalink}"
ACQUISITION_REL = "http://opds-spec.org/acquisition/open-access"


class CatalogError(Exception):
    """The catalog or metalink couldn't be fetched, or didn't contain what was expected."""


@dataclass(frozen=True)
class Release:
    """The newest catalog release of one book.

    ``version`` is the ``YYYY-MM`` tag from the file name (e.g. ``2026-08``), which is what
    local copies are compared on. ``issued`` is the catalog's ``<dc:issued>`` date, which
    may fall later in the month or in the following month. ``approx_size`` is the
    catalog's ``length`` in bytes; the exact size comes from the metalink.
    """

    zim: ZimSpec
    version: str
    issued: date
    file_name: str
    meta4_url: str
    approx_size: int | None


@dataclass(frozen=True)
class Metalink:
    """Exact facts about one ZIM file from its ``.meta4``.

    ``mirrors`` lists download URLs, highest priority (lowest number) first.
    """

    file_name: str
    size: int
    sha256: str
    mirrors: tuple[str, ...]


def version_from_file_name(zim: ZimSpec, file_name: str) -> str | None:
    """Return the ``YYYY-MM`` tag if ``file_name`` is ``<name>_<flavour>_YYYY-MM.zim``,
    else ``None``. Used for catalog entries and for files on disk (``library``)."""
    match = re.fullmatch(re.escape(zim.file_prefix) + r"(\d{4}-\d{2})\.zim", file_name)
    return match.group(1) if match else None


def _fetch(url: str) -> bytes:
    """GET ``url`` and return the body, raising ``CatalogError`` on any network error."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read()
    except OSError as exc:  # URLError and HTTPError are both OSErrors
        raise CatalogError(f"couldn't fetch {url}: {exc}") from exc


def catalog_query_url(zim: ZimSpec) -> str:
    """The catalog URL listing every flavour of ``zim.name`` (in practice 3 entries)."""
    return f"{CATALOG_URL}?name={zim.name}&count=50"


def parse_catalog(xml: bytes, zim: ZimSpec) -> Release:
    """Pick the entry for ``zim`` (matching name and flavour) out of a catalog response.

    If several entries match, the one with the latest ``<dc:issued>`` wins. Raises
    ``CatalogError`` if none matches, or if the matching entry has no metalink link or a
    file name not shaped like ``<name>_<flavour>_YYYY-MM.zim``. The error is raised rather
    than ignored, so a change in the catalog's format shows up instead of silently
    stopping updates.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CatalogError(f"catalog response isn't valid XML: {exc}") from exc

    matches = []
    for entry in root.iter(f"{ATOM}entry"):
        if (
            entry.findtext(f"{ATOM}name") == zim.name
            and entry.findtext(f"{ATOM}flavour") == zim.flavour
        ):
            matches.append(entry)
    if not matches:
        raise CatalogError(f"no catalog entry for name={zim.name} flavour={zim.flavour}")

    entry = max(matches, key=lambda e: e.findtext(f"{DC}issued") or "")
    link = next(
        (l for l in entry.iter(f"{ATOM}link") if l.get("rel") == ACQUISITION_REL), None
    )
    if link is None or not link.get("href", "").endswith(".zim.meta4"):
        raise CatalogError(f"catalog entry for {zim.file_prefix}* has no .meta4 link")

    meta4_url = link.get("href")
    file_name = meta4_url.rsplit("/", 1)[-1].removesuffix(".meta4")
    version = version_from_file_name(zim, file_name)
    if version is None:
        raise CatalogError(f"unexpected file name in catalog: {file_name}")

    issued_text = entry.findtext(f"{DC}issued") or ""
    try:
        issued = date.fromisoformat(issued_text[:10])
    except ValueError:
        raise CatalogError(f"bad <dc:issued> in catalog: {issued_text!r}") from None

    length = link.get("length")
    return Release(
        zim=zim,
        version=version,
        issued=issued,
        file_name=file_name,
        meta4_url=meta4_url,
        approx_size=int(length) if length and length.isdigit() else None,
    )


def parse_metalink(xml: bytes) -> Metalink:
    """Read the file name, exact size, SHA-256 and mirror URLs from a ``.meta4``.

    Expects exactly one ``<file>`` element. Raises ``CatalogError`` if the XML is invalid,
    or if the size or SHA-256 is missing or malformed.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CatalogError(f"metalink isn't valid XML: {exc}") from exc

    files = root.findall(f"{METALINK}file")
    if len(files) != 1:
        raise CatalogError(f"expected 1 <file> in metalink, found {len(files)}")
    file = files[0]

    size_text = file.findtext(f"{METALINK}size") or ""
    sha256 = next(
        (h.text or "" for h in file.findall(f"{METALINK}hash") if h.get("type") == "sha-256"),
        "",
    ).strip().lower()
    if not size_text.strip().isdigit():
        raise CatalogError(f"bad or missing <size> in metalink: {size_text!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise CatalogError("missing or malformed sha-256 in metalink")

    urls = file.findall(f"{METALINK}url")
    urls.sort(key=lambda u: int(u.get("priority", "999999")))
    return Metalink(
        file_name=file.get("name", ""),
        size=int(size_text),
        sha256=sha256,
        mirrors=tuple(u.text.strip() for u in urls if u.text),
    )


def latest_release(zim: ZimSpec) -> Release:
    """Fetch the catalog and return the newest release of ``zim``. Network access."""
    return parse_catalog(_fetch(catalog_query_url(zim)), zim)


def fetch_metalink(release: Release) -> Metalink:
    """Fetch and parse ``release``'s metalink. Network access.

    Raises ``CatalogError`` if the metalink names a different file than the catalog did.
    """
    metalink = parse_metalink(_fetch(release.meta4_url))
    if metalink.file_name != release.file_name:
        raise CatalogError(
            f"metalink is for {metalink.file_name}, expected {release.file_name}"
        )
    return metalink
