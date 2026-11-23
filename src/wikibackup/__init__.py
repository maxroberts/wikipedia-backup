"""wikibackup: keep an offline, LAN-served copy of English Wikipedia on this Mac.

The package checks Kiwix's catalog for new English Wikipedia ZIM releases, and downloads
one about every 6 months to an archive on the HDD. It keeps the newest 3 there, plus a
verified hot copy of the newest on the SSD, and runs ``kiwix-serve`` so they can be browsed
from the home LAN. The full design is in ``docs/kiwix_backup_plan.md``.

Modules, in dependency order:

- ``config``: loads policy and tracked books from ``config.toml``, and machine paths
  (``DATA_DIR``, ``HOT_DIR``, ``STATE_DIR``) and the port from ``.env``, with defaults.
- ``catalog``: fetches and parses Kiwix's OPDS catalog (newest release of each book) and
  each release's ``.meta4`` metalink (exact size, SHA-256, mirrors). Network, no disk
  writes.
- ``library``: reads the archive's ``manifest.json``, lists archived versions and the hot
  copy, checks whether a drive is mounted, and applies the update rule
  (``decide_update``). Read-only.
- ``cli``: the ``wikibackup`` command. ``check`` and ``status`` work (Stage 2);
  ``update``, ``prune`` and ``serve`` are stubs that exit with status 2.

Planned (not written yet): ``download`` (resumable download, SHA-256 check, hot copy;
Stage 3) and ``server`` (kiwix-serve command line, ``library.xml``, health check;
Stage 4).
"""
