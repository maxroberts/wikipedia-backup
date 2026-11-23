"""wikibackup: keep an offline, LAN-served copy of English Wikipedia on this Mac.

The package checks Kiwix's catalog for new English Wikipedia ZIM releases, and downloads
one about every 6 months to an archive on the HDD. It keeps the newest 3 there, plus a
verified hot copy of the newest on the SSD. It runs ``kiwix-serve`` so the **newest**
version can be browsed from the home LAN, and backs up the kiwix-tools builds the same
way. The full design is in ``docs/kiwix_backup_plan.md``.

Modules, in dependency order:

- ``config``: loads policy, tracked books and the kiwix-tools version and platforms from
  ``config.toml``, and machine paths
  (``DATA_DIR``, ``HOT_DIR``, ``STATE_DIR``) and the port from ``.env``, with defaults.
- ``catalog``: fetches and parses Kiwix's OPDS catalog (newest release of each book) and
  each release's ``.meta4`` metalink (exact size, SHA-256, mirrors). Network, no disk
  writes.
- ``download``: resumable HTTP download across mirrors, SHA-256 hashing, and the verified
  copy used for the hot copy. Knows nothing about the manifest or the policy.
- ``library``: reads and atomically writes the archive's ``manifest.json``, lists archived
  versions and the hot copy, checks whether a drive is mounted, and applies the update
  rule (``decide_update``).
- ``software``: backs up kiwix-tools builds to the archive and the hot copy (MD5 checked
  against Kiwix, then a SHA-256 recorded), and installs the macOS build in
  ``~/.local/opt`` (``install-tools``).
- ``server``: chooses the copy to serve (hot copy, else HDD), builds ``library.xml`` with
  kiwix-manage, runs kiwix-serve and rebuilds the library when drives or copies change
  (``serve``), and collects health checks for ``status``.
- ``update``: the ``update`` and ``prune`` commands. It decides the order of downloading,
  verifying, installing, hot-copying, pruning and rebuilding the library, under a lock,
  and never deletes a version before its replacement is verified.
- ``cli``: the ``wikibackup`` command: ``check``, ``status``, ``update``, ``prune``,
  ``serve``, ``install-tools``.
"""
