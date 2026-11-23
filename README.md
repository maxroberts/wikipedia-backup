# wikibackup

Keep an offline, browsable copy of English Wikipedia on this Mac and serve it to the home
LAN. It uses Kiwix ZIM files and `kiwix-serve`.

> **Status: Stage 4 of 6.** All commands work, and `install.sh` sets up the launchd agent
> that serves on the LAN. There's no monthly update job yet (Stage 5), and the real
> 119 GiB download hasn't been run, so there's nothing to browse until then. The plan and
> its stages are in [docs/kiwix_backup_plan.md](docs/kiwix_backup_plan.md).

## What it will do

- About once a month, check Kiwix's catalog for a new English Wikipedia (maxi, with images)
  release, about 119 GiB. Download it only when the newest local copy is at least 6 months
  old.
- Verify each download (size and SHA-256). Keep the newest 3 in an archive on a hard disk,
  and delete older ones.
- Keep a verified **hot copy** of the newest version on an SSD, so it's fast to browse and
  stored on two drives.
- Run `kiwix-serve` under launchd so the **newest** version can be read at
  `http://<this-mac>.local:8080` from any device on the LAN. It's served from the SSD, or
  from the hard disk if the hot copy isn't ready. Older versions are backups only.
  - The undated address `http://<this-mac>.local:8080/content/wikipedia_en_all_maxi/`
    keeps working across updates.
  - There's no login: anyone on the LAN can read it, so don't forward the port beyond
    your LAN.
- Back up the kiwix-tools builds (macOS, plus Linux x86_64 and aarch64 for a possible
  move to Ubuntu 24.04) on both drives, so the software to read the ZIMs is kept with
  them.

## Prerequisites

- macOS on Apple Silicon (built for one MacBook Air M2).
- [`uv`](https://docs.astral.sh/uv/). uv installs Python 3.12 itself if needed.
- kiwix-tools: `install.sh` downloads Kiwix's official macOS arm64 build (it isn't on
  Homebrew) and installs it in `~/.local/opt`.
- An archive drive (an HDD is fine) with space for about 475 GiB (3 versions plus one
  being downloaded), mounted at the path in `DATA_DIR`.
- Optional: an SSD with space for about 238 GiB (the hot copy plus its replacement during an
  update), at the path in `HOT_DIR`, outside any synced folder.

## Setup

1. Make sure the hot-copy folder (`HOT_DIR`, default `/Volumes/BackUp_T7/wikipedia-storage`)
   exists and is writable by you. On this Mac the T7's top level belongs to root, so
   wikibackup can't create the folder itself. The user created it by hand on 2026-11-23.
   `uv run wikibackup status` reports it if it's missing or can't be created.

2. Install. This creates `.env` from `.env.example` if it's missing, syncs the Python
   environment, backs up and installs kiwix-tools, and loads the launchd agent:

   ```sh
   ./install.sh
   uv run wikibackup status
   ```

   It's safe to run again after pulling new code. `./uninstall.sh` stops and removes the
   agent, leaving tools and data in place.

3. For development: `uv sync --dev && uv run pytest`.

`.venv/` is marked with the `com.dropbox.ignored` attribute so Dropbox doesn't sync it. If
you delete and recreate it, mark it again: `xattr -w com.dropbox.ignored 1 .venv`.

## Usage

```sh
uv run wikibackup check    # newest release in Kiwix's catalog, and whether update would download it
uv run wikibackup status   # drives, free space, archived versions, the hot copy
uv run wikibackup update   # download a new release if due, verify it, refresh the hot copy, prune
uv run wikibackup prune    # refresh the hot copy and prune, with no network access
uv run wikibackup serve    # run kiwix-serve in the foreground (the launchd agent does this)
uv run wikibackup install-tools   # back up kiwix-tools builds, install the macOS one
```

- The server log is `~/Library/Logs/wikibackup/serve.log`. Restart the server with
  `launchctl kickstart -k gui/$(id -u)/com.local.wikibackup-serve`.
- **Firewall:** the macOS application firewall was off when this was set up. If you turn
  it on and other devices can't connect, allow kiwix-serve with
  `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add ~/.local/opt/kiwix-tools-3.8.2/kiwix-serve`.
- The server is reachable only while this Mac is awake with the T7 attached, because the
  code lives on the T7.

- `check` and `status` are read-only. `check` needs network access.
- `update` and `prune` write only inside `DATA_DIR` and `HOT_DIR`.
  - They log timestamped progress to stderr and to `DATA_DIR/logs/wikibackup.log`.
  - If the archive drive isn't mounted, they say so and exit 0 without doing anything.
  - Only one of them can run at a time (a lock file in `DATA_DIR`).
  - An interrupted download resumes on the next run.
  - A file whose SHA-256 doesn't match is deleted, and no older version is pruned.
- Exit status: 0 on success, 1 on an error (bad config, network or checksum failure, too
  little free space, another run in progress), 2 for a usage error.
- `serve` runs until stopped and exits with kiwix-serve's status. `install-tools` exits
  1 if it can't find or verify a build.
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
- `software.py`: kiwix-tools backups and installation.
  - Downloads each configured build and checks Kiwix's MD5, then records a SHA-256.
  - Keeps the builds in `DATA_DIR/software/` and `HOT_DIR/software/`.
  - Unpacks the macOS build into `~/.local/opt/kiwix-tools-<version>/`.
- `server.py`: serving.
  - Chooses which copy of the newest version to serve: the hot copy, else the HDD copy.
  - Builds `library.xml` with kiwix-manage: a temporary file, then a rename.
  - `serve` runs kiwix-serve and rebuilds the library within 60 s when a drive or a copy
    changes.
  - Collects the server checks for `status`.
- `update.py`: `update` and `prune`. Under a lock, for each book: decide, download into
  `partial/`, verify, move into `zim/`, record in the manifest, refresh the hot copy, and
  prune.
  - Nothing is deleted until its replacement is verified and `library.xml` no longer
    lists it.
  - `update` also refreshes the kiwix-tools backups.
- `cli.py`: the `wikibackup` command and its logging setup.

Flow:

- `check`: `cli` → `config` → `catalog` → `library` → report.
- `status`: `cli` → `config` → `library` → report.
- `update` and `prune`: `cli` → `config` → `update`, which uses `catalog`, `download`,
  `library`, `software` (backups) and `server` (`library.xml`).
- `serve`: launchd → `.venv/bin/wikibackup serve` → `server.run_serve` → kiwix-serve.

Outside the package:

- `install.sh` and `uninstall.sh` set up or remove everything on this Mac.
- `launchd/com.local.wikibackup-serve.plist.template` is the agent definition; `install.sh`
  fills in the paths.

Tests are in `tests/`. They use saved Kiwix responses in `tests/fixtures/`, a local HTTP
server and fake kiwix-tools (`tests/conftest.py`), and temporary folders, so they never
touch the internet, the real drives or `~/.local`.

Planned (see the plan doc): the monthly update job (Stage 5).

Data never lives in this repo:

- The archive (all kept ZIMs, the manifest, logs, partial downloads) is in `DATA_DIR` on
  the HDD.
- The hot copy of the newest ZIM is in `HOT_DIR` on the SSD.
- kiwix-tools backups are in `software/` in both `DATA_DIR` and `HOT_DIR`.
- `library.xml` is in `STATE_DIR` on the internal disk, and the server log is in
  `~/Library/Logs/wikibackup/`.
