# wikibackup

Keep an offline, browsable copy of English Wikipedia on this Mac and serve it to the home
LAN. It uses Kiwix ZIM files and `kiwix-serve`.

> **Status: Stage 1 of 6 (scaffold).** The `wikibackup` command exists, but every
> subcommand is a stub that exits with "not implemented yet". Nothing downloads or serves
> yet. The plan and its stages are in [docs/kiwix_backup_plan.md](docs/kiwix_backup_plan.md).

## What it will do

- About once a month, check Kiwix's catalog for a new English Wikipedia (maxi, with images)
  release, about 119 GiB. Download it only when the newest local copy is at least 6 months
  old.
- Verify each download (size and SHA-256). Keep the newest 3 in an archive on a hard disk,
  and delete older ones.
- Keep a verified **hot copy** of the newest version on an SSD, so it's fast to browse and
  stored on two drives.
- Run `kiwix-serve` under launchd so the kept versions can be read at
  `http://<this-mac>.local:8080` from any device on the LAN. The newest version is served
  from the SSD, or from the hard disk if the SSD isn't attached.

## Prerequisites

- macOS on Apple Silicon (built for one MacBook Air M2).
- [`uv`](https://docs.astral.sh/uv/). uv installs Python 3.12 itself if needed.
- From Stage 4: the official kiwix-tools macOS arm64 build (not on Homebrew).
- An archive drive (an HDD is fine) with space for about 475 GiB (3 versions plus one
  being downloaded), mounted at the path in `DATA_DIR`.
- Optional: an SSD with space for about 238 GiB (the hot copy plus its replacement during an
  update), at the path in `HOT_DIR`, outside any synced folder.

## Setup

```sh
cp .env.example .env    # then edit DATA_DIR / PORT if needed
uv sync --dev
uv run pytest
uv run wikibackup --help
```

`.venv/` is marked with the `com.dropbox.ignored` attribute so Dropbox doesn't sync it. If
you delete and recreate it, mark it again: `xattr -w com.dropbox.ignored 1 .venv`.

## Configuration

- `config.toml` (committed): which ZIMs to track and the policy (`min_age_months`,
  `keep_versions`).
- `.env` (not committed): `DATA_DIR` (archive), `HOT_DIR` (hot copy; empty turns it off),
  `STATE_DIR` (generated `library.xml`), `BIND_ADDRESS`, `PORT`, `LOG_LEVEL`. See
  `.env.example`.

Neither file is read yet; config loading arrives in Stage 2.

## Codebase architecture

Today:

- `src/wikibackup/cli.py`: the `wikibackup` command, with subcommands `check`, `status`,
  `update`, `prune` and `serve`, all stubs.
- `tests/test_smoke.py`: checks that the package imports and each subcommand parses.
- `docs/kiwix_backup_plan.md`: the design, the facts it relies on (checked against Kiwix's
  servers and docs on 2026-11-23), and the staged plan.

Planned (see the plan doc): `config.py`, `catalog.py` (Kiwix OPDS catalog and metalinks),
`download.py` (resumable, SHA-256-verified downloads), `library.py` (manifest, retention,
`library.xml`), `server.py` (kiwix-serve), launchd plists, and `install.sh` and
`uninstall.sh`. Data never lives in this repo:

- The archive (all kept ZIMs, the manifest, logs) is in `DATA_DIR` on the HDD.
- The hot copy of the newest ZIM is in `HOT_DIR` on the SSD.
- `library.xml` is in `STATE_DIR` on the internal disk.
