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
- ``download``: resumable HTTP download across mirrors, SHA-256 hashing, and the verified
  copy used for the hot copy. Knows nothing about the manifest or the policy.
- ``library``: reads and atomically writes the archive's ``manifest.json``, lists archived
  versions and the hot copy, checks whether a drive is mounted, and applies the update
  rule (``decide_update``).
- ``update``: the ``update`` and ``prune`` commands. It decides the order of downloading,
  verifying, installing, hot-copying and pruning, under a lock, and never deletes a
  version before its replacement is verified.
- ``cli``: the ``wikibackup`` command. ``check``, ``status``, ``update`` and ``prune``
  work; ``serve`` is a stub that exits with status 2 (Stage 4).

Planned (not written yet): ``server`` (kiwix-serve command line, ``library.xml`` via
kiwix-manage, health check; Stage 4). Until then, ``update.rebuild_library`` is a no-op.
"""
