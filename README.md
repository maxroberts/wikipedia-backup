# wikibackup

Keep an offline, browsable copy of English Wikipedia on this Mac and serve it to the home
LAN. It uses Kiwix ZIM files and `kiwix-serve`.

> **Status: Stage 3 of 6.** `check`, `status`, `update` and `prune` work. `serve` is a
> stub (Stage 4), there's no monthly launchd job yet (Stage 5), and the real 119 GiB
> download hasn't been run. The plan and its stages are in
> [docs/kiwix_backup_plan.md](docs/kiwix_backup_plan.md).

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

## Usage

```sh
uv run wikibackup check    # newest release in Kiwix's catalog, and whether update would download it
uv run wikibackup status   # drives, free space, archived versions, the hot copy
uv run wikibackup update   # download a new release if due, verify it, refresh the hot copy, prune
uv run wikibackup prune    # refresh the hot copy and prune, with no network access
```

- `check` and `status` are read-only. `check` needs network access.
- `update` and `prune` write only inside `DATA_DIR` and `HOT_DIR`.
  - They log timestamped progress to stderr and to `DATA_DIR/logs/wikibackup.log`.
  - If the archive drive isn't mounted, they say so and exit 0 without doing anything.
  - Only one of them can run at a time (a lock file in `DATA_DIR`).
  - An interrupted download resumes on the next run.
  - A file whose SHA-256 doesn't match is deleted, and no older version is pruned.
- Exit status: 0 on success, 1 on an error (bad config, network or checksum failure, too
  little free space, another run in progress), 2 for a usage error.
- `--config PATH`, given before the subcommand, uses a different `config.toml`. Combined
  with `DATA_DIR` and `HOT_DIR` set in the shell, it's how to test with a small book. For
  example, with a `config.toml` tracking `openzim_en_all` / `maxi` (3.4 MB):

  ```sh
  DATA_DIR=/tmp/wb/archive HOT_DIR=/tmp/wb/hot uv run wikibackup --config /tmp/wb/config.toml update
  ```

## Configuration

- `config.toml` (committed): which ZIMs to track and the policy (`min_age_months`,
  `keep_versions`).
- `.env` (not committed): `DATA_DIR` (archive), `HOT_DIR` (hot copy; empty turns it off),
  `STATE_DIR` (generated `library.xml`), `BIND_ADDRESS`, `PORT`, `LOG_LEVEL`. See
  `.env.example`. `.env` is optional: a missing key takes the default shown in
  `.env.example`, and a variable set in the shell overrides `.env`.

## Codebase architecture

The package is `src/wikibackup/`. Its modules, in the order they depend on each other:

- `config.py`: loads `config.toml` and `.env` into a `Config` (policy, tracked books,
  paths, port).
- `catalog.py`: fetches Kiwix's OPDS catalog and picks the newest release of each tracked
  book (name `wikipedia_en_all`, flavour `maxi`). It then fetches that release's `.meta4`
  metalink for the exact size, the SHA-256 and the mirror list. Network only; it never
  writes to disk.
- `download.py`: the byte-moving parts.
  - A resumable download that tries mirrors in priority order and accepts a reply only if
    it continues exactly where the partial file ends.
  - SHA-256 hashing.
  - The verified copy used for the hot copy: copy, hash, rename, then write a `.sha256`
    file.
- `library.py`: reads and atomically writes the archive's `manifest.json`, which is the
  record of verified versions. It lists the versions in the archive and the state of the
  hot copy, checks whether a drive under `/Volumes` is mounted, and applies the update
  rule (download if there's nothing local, or if a newer release exists and the newest
  local version is at least `min_age_months` old).
- `update.py`: `update` and `prune`. Under a lock, for each book: decide, download into
  `partial/`, verify, move into `zim/`, record in the manifest, refresh the hot copy, and
  prune.
  - Nothing is deleted until its replacement is verified.
  - `rebuild_library` (keeping `library.xml` in step before deletions) is a no-op until
    Stage 4.
- `cli.py`: the `wikibackup` command and its logging setup.

Flow:

- `check`: `cli` → `config` → `catalog` → `library` → report.
- `status`: `cli` → `config` → `library` → report.
- `update` and `prune`: `cli` → `config` → `update`, which uses `catalog`, `download` and
  `library`.

Tests are in `tests/`. They use saved Kiwix responses in `tests/fixtures/`, a local HTTP
server (`tests/conftest.py`) for downloads, and temporary folders, so they never touch the
internet or the real drives.

Planned (see the plan doc): `library.xml`, kiwix-serve and the launchd serve agent, plus
`install.sh` and `uninstall.sh` (Stage 4); the monthly update job (Stage 5).

Data never lives in this repo:

- The archive (all kept ZIMs, the manifest, logs, partial downloads) is in `DATA_DIR` on
  the HDD.
- The hot copy of the newest ZIM is in `HOT_DIR` on the SSD.
- `library.xml` is in `STATE_DIR` on the internal disk.
