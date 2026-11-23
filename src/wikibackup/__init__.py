"""wikibackup: keep an offline, LAN-served copy of English Wikipedia on this Mac.

The package checks Kiwix's catalog for new English Wikipedia ZIM releases, downloads one
about every 6 months to an external data drive, keeps the newest few, and runs
``kiwix-serve`` so they can be browsed from the home LAN. The full design is in
``docs/kiwix_backup_plan.md``.

Modules:

- ``cli``: the ``wikibackup`` command (``check``, ``update``, ``prune``, ``status``,
  ``serve``). In Stage 1 every subcommand is a stub that reports it isn't implemented
  and exits with status 2.

Planned (not written yet): ``config`` (load ``config.toml`` and ``.env``), ``catalog``
(OPDS catalog and metalink parsing), ``download`` (resumable download and SHA-256 check),
``library`` (manifest, retention, ``library.xml``) and ``server`` (kiwix-serve command line
and health check).
"""
