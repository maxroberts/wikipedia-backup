# wikipedia-backup

Keep an offline, browsable copy of English Wikipedia (with images) on a Mac, and serve it
to every device on the home network. It uses Kiwix ZIM files and `kiwix-serve`. Its
command and Python package have the short name `wikibackup`. It does three things:
- downloads a new release about every 6 months
- verifies it and keeps the last 3
- serves the newest one at `http://<this-mac>.local:9454/`

> **Status:** all six stages of [the plan](docs/kiwix_backup_plan.md) are done. The plan
> holds the design decisions, the measurements and the stage notes. As of 2026-11-23, the
> 2026-08 release (119 GiB) is archived, has a hot copy and is being served.

## How it works

- **Monthly check.** A launchd agent runs `wikibackup update` on the 1st of each month at
  04:00. It looks up the newest English Wikipedia `maxi` release in Kiwix's catalog. It
  downloads that release only if nothing is stored locally, or if the newest local version
  is at least 6 months old.
- **Verified downloads.** Each download resumes after an interruption. It's checked
  against the exact size and SHA-256 that Kiwix publishes, and only then added to the
  **archive** on a hard disk (`DATA_DIR`). The archive keeps the newest 3 versions. An
  older version is deleted only after its replacement is verified.
- **Hot copy.** A verified copy of the newest version also goes on an SSD (`HOT_DIR`).
  Browsing from the SSD is much faster: in the Stage 5 benchmark, a page with images took
  0.04 s from the SSD but 5.8 s from the HDD. The hot copy also puts the newest version on
  a second drive.
- **Serving.** A second launchd agent keeps `kiwix-serve` running. It serves only the
  newest version: from the SSD, or from the HDD copy if the hot copy isn't available. Older
  versions are backups and can't be browsed. Within 60 s of a drive being plugged in or
  removed, it switches to whichever copy is available.
- **Software backups.** The kiwix-tools builds that read ZIM files are backed up on both
  drives: macOS arm64, plus Linux x86_64 and aarch64 for a possible move to Ubuntu 24.04.
- **Failure alert.** A real download happens only about every 6 months, so failures
  shouldn't depend on someone reading the logs. If an update fails, a file called
  `wikipedia-backup UPDATE FAILED.txt` appears on the Desktop. It holds the error, what to
  do next, and the paths to the repo, the data folders and every log.

## Getting started

### Prerequisites

- **A Mac with Apple Silicon.** `install.sh` installs Kiwix's macOS arm64 build.
- **[uv](https://docs.astral.sh/uv/getting-started/installation/).** It installs Python
  3.12 itself if needed. kiwix-tools isn't on Homebrew, so `install.sh` downloads Kiwix's
  official build into `~/.local/opt/`. You don't need to install it yourself.
- **An archive drive** (an HDD is fine) with at least **475 GiB** free. That's 3 versions
  of about 119 GiB each, plus one being downloaded. By default it's
  `/Volumes/Starfish_Prime/kiwix-data`.
- **Optional: an SSD for the hot copy**, with at least **238 GiB** free. That's the
  current copy plus its replacement during an update. By default it's
  `/Volumes/BackUp_T7/wikipedia-storage`. It must be outside any Dropbox or iCloud folder.
- **Internet access for each download.** The first download (119 GiB) took 7 h 19 min at
  about 4.8 MB/s on this Mac's connection. Checking it and making the hot copy took another
  45 min.
- **No accounts, credentials or VPN.** Kiwix's catalog and mirrors are public.

### 1. Prepare the drives (one time)

Mount both drives before installing. `install.sh` backs up kiwix-tools only when the
archive drive is mounted, and copies the backups to the SSD only if it's mounted too. The
hot-copy folder must exist and be writable by you.

`wikibackup` creates `DATA_DIR` and `HOT_DIR` itself if it can. On this Mac, though, the
top level of the T7 belongs to root, so create the hot-copy folder by hand:

```sh
mkdir /Volumes/BackUp_T7/wikipedia-storage
# If that fails with "Permission denied" (the drive's top level belongs to root):
sudo mkdir /Volumes/BackUp_T7/wikipedia-storage && sudo chown "$USER" /Volumes/BackUp_T7/wikipedia-storage
```

To use different paths, a different port, or no hot copy at all, see
[Configuration](#configuration) before installing.

### 2. Install

```sh
cd /path/to/wikipedia-backup
./install.sh
```

`install.sh` does the following, and is safe to run again, e.g. after pulling new code or
editing a plist template:

1. Syncs the Python environment into `.venv/`, and marks it so Dropbox doesn't sync it.
2. Creates `.env` from `.env.example`, if `.env` doesn't exist.
3. Installs kiwix-tools. If the archive drive is mounted, it first downloads the builds
   listed in `config.toml`, checks them against Kiwix's MD5s, and backs them up to
   `software/kiwix-tools/` in `DATA_DIR`, plus `HOT_DIR` if that's mounted. It then
   installs the macOS build in `~/.local/opt/kiwix-tools-<version>/`, with symlinks in
   `~/.local/bin/`. This step needs a verified macOS build in one of the two backups.
4. Installs and loads two launchd agents. Loading doesn't start a download.
   - `com.local.wikibackup-serve` starts kiwix-serve now and keeps it running.
   - `com.local.wikibackup-update` runs the monthly update.
5. Prints a firewall command, but only if the macOS application firewall is on.

To check the setup:

```sh
uv run wikibackup status
```

`status` shows:
- each data folder, whether it exists and its free space
- the archived versions and the state of the hot copy
- what `library.xml` serves, and whether the server answers on `127.0.0.1` and on the LAN
- the Mac's `.local` name and the firewall state

Before the first download, it serves an empty library, which is expected.

### 3. First download and browsing

To see what a download would do without starting one:

```sh
uv run wikibackup check
```

`check` shows the newest release in the catalog (version, size, mirrors, SHA-256), the
newest local version, and whether `update` would download it. For example:

```
wikipedia_en_all (maxi)
  catalog:  2026-08 (issued 2026-08-25), wikipedia_en_all_maxi_2026-08.zim
            118.7 GiB (127,418,087,648 bytes), 7 mirrors
            sha256 34162d18…
  local:    none
  decision: DOWNLOAD: …
```

To start the first download now rather than on the 1st, run it under launchd. It then
survives closing the terminal, and `caffeinate -i` stops the Mac from idle-sleeping while
it runs:

```sh
launchctl kickstart gui/$(id -u)/com.local.wikibackup-update
tail -f /Volumes/Starfish_Prime/kiwix-data/logs/wikibackup.log   # DATA_DIR/logs/wikibackup.log
```

The log adds a line for each GiB downloaded. A run ends with `wikibackup update: done`.
`status` should then show the version as `verified` and the hot copy as `current`.

To browse, open any of these from any device on the LAN:

- `http://<this-mac>.local:9454/`: the library page. `status` prints this Mac's name.
- `http://<this-mac>.local:9454/content/wikipedia_en_all_maxi/`: Wikipedia itself. This
  undated address keeps working after updates, so it's a good one to bookmark.
- `http://<LAN IP>:9454/`, for devices that can't resolve `.local` names. `status` prints
  the IP.

## Using it day to day

Normally there's nothing to do. The update agent runs monthly and the server keeps
running.

**If `wikipedia-backup UPDATE FAILED.txt` appears on your Desktop**, the last update
failed. Open it, follow its steps, and run the update again. The next successful update
deletes the file, so if the file is still there afterwards, the update failed again.
- **What writes an alert:** any `update` that exits with an error (including a bug's
  traceback), and an `update` that couldn't run because the archive drive wasn't
  mounted.
- **What doesn't:** warnings, such as a skipped hot copy, and `prune`.
- **One case gets no alert:** if the T7 isn't attached, launchd can't start the job at
  all, because the code lives there.

All commands are run from the repo folder:

```sh
uv run wikibackup status          # drives, free space, archived versions, hot copy, server
uv run wikibackup check           # newest release in the catalog; would update download it?
uv run wikibackup update          # download if due, verify, refresh the hot copy, prune
uv run wikibackup prune           # refresh the hot copy and prune only (no network)
uv run wikibackup serve           # run kiwix-serve in the foreground (the serve agent does this)
uv run wikibackup install-tools   # back up the kiwix-tools builds and install the macOS one
```

- **Read-only:** `check` and `status`. `check` needs internet access.
- **What `update` and `prune` write:** only inside `DATA_DIR` and `HOT_DIR`.
  - If the archive drive isn't mounted, they say so and exit 0 without doing anything.
  - Only one can run at a time.
  - An interrupted download resumes on the next run.
  - A download whose SHA-256 doesn't match is deleted, nothing is pruned, and the command
    exits 1.
- **Exit status:** 0 on success or nothing to do; 1 on an error (bad config, a network or
  checksum failure, too little free space, another run in progress); 2 for a usage error.
  `serve` exits with kiwix-serve's status.
- **Logs:**

  | What | Where |
  |---|---|
  | Updates (and `prune`) | `DATA_DIR/logs/wikibackup.log` |
  | launchd's output for the update agent, e.g. "archive drive not mounted" | `~/Library/Logs/wikibackup/update.log` |
  | The server | `~/Library/Logs/wikibackup/serve.log` |

- **Restart the server**, e.g. after editing `.env`:
  `launchctl kickstart -k gui/$(id -u)/com.local.wikibackup-serve`.

### When the server is reachable

- **This Mac must be awake.** This project changes no power settings.
- **The T7 must be attached.** The code and `.venv` live on the T7, so the server can't
  start without it, even though the HDD has a copy. With the T7 attached and the HDD
  unplugged, the hot copy is still served.
- **Anyone on the LAN can read it; there's no login.** Don't forward port 9454 on the
  router or otherwise expose it beyond the LAN.

## Configuration

Settings come from two files in the repo root. Both are read once, when a command or the
server starts, so restart the server after changing them.

**`.env`** (not committed): this machine's settings. It's optional. A key it leaves out
takes the default from `.env.example`, and a variable set in the shell overrides `.env`.

| Key | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `/Volumes/Starfish_Prime/kiwix-data` | The archive: all kept versions, `manifest.json`, logs, partial downloads, software backups. Never inside Dropbox. |
| `HOT_DIR` | `/Volumes/BackUp_T7/wikipedia-storage` | The hot copy of the newest version, plus software backups. Empty turns the hot copy off. Never inside Dropbox. |
| `STATE_DIR` | `~/.local/state/wikibackup` | The generated `library.xml`. On the internal disk, so it's there whichever drive is missing. Don't move `library.xml` by hand: it stores ZIM paths relative to its own folder. |
| `BIND_ADDRESS` | `all` | kiwix-serve `--address`. `all` lets LAN devices connect; `127.0.0.1` limits it to this Mac. |
| `PORT` | `9454` | kiwix-serve `--port`. 9454 spells "WIKI" on a phone keypad, and is less likely to clash than 8080. |
| `LOG_LEVEL` | `INFO` | A Python logging level: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |

**`config.toml`** (committed): what to keep.

- `[policy]`:
  - `min_age_months = 6`: don't replace a local version younger than this.
  - `keep_versions = 3`: how many versions of each book the archive keeps.
- `[[zim]]`: one table per tracked book, given by the catalog's `name` and `flavour`. For
  English Wikipedia with images, that's `name = "wikipedia_en_all"` with
  `flavour = "maxi"`, not `wikipedia_en_all_maxi`.
- `[kiwix_tools]`: the kiwix-tools `version` and which platform builds to back up. To move
  to a new release, change `version` and re-run `./install.sh`.

## LAN access and the firewall

- **The firewall.** The macOS application firewall was off when this was set up, so no
  rule was needed. If you turn it on and other devices can't connect, allow kiwix-serve
  (`install.sh` prints the same command, with the path filled in):

  ```sh
  sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add ~/.local/opt/kiwix-tools-3.8.2/kiwix-serve
  ```

  A kiwix-tools upgrade installs to a new folder, so add that folder's `kiwix-serve` too.
- **Checking from another device.** `status` checks the LAN address from this Mac. That
  confirms kiwix-serve is listening on the LAN interface, but not that the firewall or the
  network lets other devices in. To be sure, open the page from another device.

## Troubleshooting

Errors from `update` and `prune` are logged as `ERROR wikibackup update: <message>` (or
`prune`), both on screen and in `DATA_DIR/logs/wikibackup.log`. Other commands print
`wikibackup <command>: error: <message>`. The table quotes the `<message>` part.

| Symptom | Cause and fix |
|---|---|
| `status`: hot copy `CAN'T be created: … isn't writable` | The parent folder belongs to root. Create the folder yourself (see [step 1](#1-prepare-the-drives-one-time)). Meanwhile, the HDD copy is served. |
| `wikibackup update: archive drive not mounted (…); nothing to do` (and a Desktop alert) | Mount the drive and run the update again (`launchctl kickstart …` or `uv run wikibackup update`). A missed monthly run isn't retried until the next month. |
| `another update or prune is running (…/.wikibackup.lock)` | Only one runs at a time. Check with `pgrep -fl "wikibackup update"`, and wait for it to finish. The lock is released automatically if the process dies. |
| `archive needs N GiB free for …, has M GiB` | `update` needs the download's remaining size plus 5 GiB free. Free up space, or lower `keep_versions`. |
| Log warning `hot copy skipped: …` | The SSD is short of space, not mounted, or not writable. The HDD copy is served meanwhile, and the next `update` or `prune` tries the hot copy again. |
| `…: SHA-256 … doesn't match the metalink's …; deleted the download` (exit 1) | The bad file has been deleted and nothing was pruned. The next run downloads it again. |
| The download seems stuck | Check the log's per-GiB lines and any `attempt N failed` warnings. A failed or stalled attempt is retried on the next mirror, after a pause of 2–60 s, up to 30 attempts per run. After that the run exits 1, and the next run resumes the partial file. |
| Other devices can't connect | The Mac is asleep, the T7 isn't attached, or the firewall is on (see [above](#lan-access-and-the-firewall)). Try the IP address instead of the `.local` name. Check `status` and `~/Library/Logs/wikibackup/serve.log`. |
| A page answers with HTTP 500 | The served file disappeared, e.g. a drive was unplugged. Within 60 s the server switches to the other copy. |
| `./install.sh`: `Bootstrap failed: 5: Input/output error` | launchd was still unloading the old agent. `install.sh` waits up to 10 s for that; if it still happens, run `./install.sh` again. |

## Uninstalling

```sh
./uninstall.sh
```

This stops and removes both launchd agents. A running update is stopped, and its partial
download is kept for next time. It doesn't delete kiwix-tools (`~/.local/opt/`,
`~/.local/bin/`), the archive, the hot copy, `library.xml` or the logs; remove those by
hand if you want them gone.

## Reading the backups without this project

The ZIM files are standard Kiwix files, so any Kiwix reader can open them. Each drive also
keeps the kiwix-tools builds in `software/kiwix-tools/`, with a `.sha256` file next to
each. On Linux, for example:

```sh
tar xzf kiwix-tools_linux-x86_64-3.8.2.tar.gz
./kiwix-tools_linux-x86_64-3.8.2/kiwix-serve --port 9454 /path/to/wikipedia_en_all_maxi_2026-08.zim
```

## Developer guide

### Codebase architecture

The package is `src/wikibackup/`. Its modules, in the order they depend on each other:

- `config.py`: loads `config.toml` and `.env` into a `Config` (policy, tracked books,
  kiwix-tools builds, paths, port). It finds the repo root from its own location, so it
  assumes it runs from the source checkout, as `uv run` and `.venv/bin/wikibackup` do.
- `catalog.py`: fetches Kiwix's OPDS catalog (`opds.library.kiwix.org`) and picks the
  newest release of each tracked book, filtering on `flavour` itself because the catalog
  ignores that query parameter. It then fetches the release's `.meta4` metalink for the
  exact size, the SHA-256 and the mirror list. It uses the network only and never writes
  to disk.
- `download.py`: the parts that move bytes.
  - A resumable download that goes straight to the metalink's mirrors in priority order.
    It accepts a reply only if its `Content-Range` continues exactly where the partial
    file ends.
  - SHA-256 hashing.
  - The verified copy used for the hot copy: copy, hash, rename, then write a `.sha256`
    file.
- `library.py`: reads and writes the archive's `manifest.json`, the record of verified
  versions and the source of truth. Writes are atomic.
  - It lists the archived versions and the state of the hot copy.
  - It checks whether a drive under `/Volumes` is mounted.
  - It applies the update rule: download if there's nothing local, or if a newer release
    exists and the newest local version is at least `min_age_months` old.
- `software.py`: kiwix-tools.
  - Downloads each configured build and checks Kiwix's MD5 (the only checksum Kiwix
    publishes for these), then records a SHA-256.
  - Keeps the builds in `software/kiwix-tools/` in `DATA_DIR` and `HOT_DIR`.
  - Unpacks the macOS build into `~/.local/opt/kiwix-tools-<version>/`.
- `server.py`: serving.
  - Chooses which copy of the newest version to serve:
    1. the hot copy, if it's current
    2. otherwise the HDD copy
    3. otherwise, with the HDD unmounted, the newest verified hot-copy file
  - Builds `library.xml` with kiwix-manage: into a temporary file, then a rename.
  - `run_serve` runs kiwix-serve with `--monitorLibrary --nodatealiases`. Every 60 s it
    rebuilds the library if the choice of copy has changed, and it passes SIGTERM on to
    kiwix-serve.
  - Collects the server checks for `status`.
- `update.py`: `update` and `prune`. Under a lock (`DATA_DIR/.wikibackup.lock`), for each
  book it decides, downloads into `partial/`, verifies, moves into `zim/`, records the
  version in the manifest, refreshes the hot copy, and prunes.
  - Nothing is deleted until its replacement is verified and `library.xml` no longer
    lists it.
  - `update` also refreshes the kiwix-tools backups.
- `alert.py`: the Desktop alert. `cli` calls `write_alert` when `update` fails (or
  can't run because the archive drive isn't mounted), and `clear_alert` when it succeeds.
- `cli.py`: the `wikibackup` command, its exit statuses, its logging setup and the
  Desktop alert calls.

Flow:

- `check`: `cli` → `config` → `catalog` → `library` → report.
- `status`: `cli` → `config` → `library` and `server` → report.
- `update` and `prune`: `cli` → `config` → `update`, which uses `catalog`, `download`,
  `library`, `software` (backups) and `server` (`library.xml`).
- Serving: launchd → `.venv/bin/wikibackup serve` → `server.run_serve` → kiwix-serve.
- Monthly: launchd → `caffeinate -i .venv/bin/wikibackup update`.

Outside the package:

- `install.sh` and `uninstall.sh` set up or remove everything on this Mac.
- `launchd/*.plist.template`: the two agents. `install.sh` fills in the paths. Edit the
  templates, not the installed copies in `~/Library/LaunchAgents/`.
- `scripts/bench_browse.py`: the HDD vs SSD browsing benchmark. Its method and results are
  in the plan.
- `docs/kiwix_backup_plan.md`: the design, the verified facts it relies on, the stages,
  the risks and the benchmark results.

Data never lives in this repo, which sits inside Dropbox:

```
DATA_DIR/                      archive (HDD)
  manifest.json                verified versions: file, size, SHA-256
  zim/                         kept versions, newest 3
  partial/                     a download in progress (*.zim.part)
  logs/wikibackup.log
  software/kiwix-tools/        kiwix-tools builds + .sha256
  .wikibackup.lock
HOT_DIR/                       hot copy (SSD)
  <newest>.zim + .sha256
  software/kiwix-tools/
STATE_DIR/library.xml          what kiwix-serve serves (internal disk)
```

### Development

```sh
uv sync --dev
uv run pytest
```

The tests use:
- saved Kiwix responses in `tests/fixtures/`
- a local HTTP server
- fake kiwix-tools (`tests/conftest.py`, applied to every test automatically)
- temporary folders

So they never touch the internet, the real drives or `~/.local`.

To test end to end without the 119 GiB download, make a scratch `config.toml` that
tracks a tiny book, e.g. `name = "openzim_en_all"`, `flavour = "maxi"` (3.4 MB). Then
point the data folders at scratch folders:

```sh
DATA_DIR=/tmp/wb/archive HOT_DIR=/tmp/wb/hot uv run wikibackup --config /tmp/wb/config.toml update
```

`--config` goes before the subcommand. This run really does download from Kiwix, and it
also refreshes the kiwix-tools backups in the scratch `DATA_DIR`.

`.venv/` carries the `com.dropbox.ignored` attribute. `install.sh` sets it; if you
recreate the venv another way, run `xattr -w com.dropbox.ignored 1 .venv`.
