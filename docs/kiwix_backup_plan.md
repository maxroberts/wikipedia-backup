# Plan: offline English Wikipedia on this Mac with Kiwix

**Status:** adopted, Stage 4 done (awaiting review) · **Date:** 2026-11-23 · **Scope:** downloading,
keeping and serving English Wikipedia (maxi) ZIM files on this MacBook Air, for the home LAN

## TL;DR

Download the Kiwix ZIM of English Wikipedia with images (`wikipedia_en_all`, flavour `maxi`)
about every 6 months to the archive on the HDD (`/Volumes/Starfish_Prime/kiwix-data/`), and
keep the newest 3 there. Also keep a verified **hot copy** of the newest version on the T7
SSD, outside Dropbox. `kiwix-serve`, under launchd, serves **only the newest version**:
from the SSD, or from the HDD copy if the hot copy isn't ready. The older versions are
backups and aren't browsable. That makes browsing fast and puts the newest version on two
drives. The kiwix-tools builds that serve it (macOS, plus Linux for a possible move to
Ubuntu) are backed up the same way. Checked on 2026-11-23:

- The current maxi release (2026-08) is **127,418,087,648 bytes (118.7 GiB)** [3].
  - **HDD:** three versions take about **356 GiB**, briefly about **475 GiB** while a fourth
    is verified and before the oldest is pruned. Starfish_Prime has 2.5 TiB free.
  - **SSD:** the hot copy takes about **119 GiB**, briefly about **238 GiB** while it's
    being replaced. The T7 has 886 GiB free.
- Kiwix publishes a **SHA-256** for each ZIM [3][4], and the download mirrors support
  **HTTP range requests** (`206 Partial Content`), so interrupted downloads can resume.
- The two maxi releases on the download server are dated **2026-02-26 and 2026-09-08** [2],
  about 6.4 months apart, which fits a monthly check plus a 6-month minimum age.

## The question

How should this Mac keep an offline, browsable copy of English Wikipedia, update it about
twice a year, and make it readable from other devices on the home LAN?

Constraints (from the user, 2026-11-23):

- **Host:** this MacBook Air M2 only. macOS-specific tools (launchd) are fine.
- **Content:** English Wikipedia maxi only. No other projects (Wiktionary, Wikivoyage, etc.).
- **Data:** `/Volumes/Starfish_Prime/kiwix-data/`. Starfish_Prime is a **hard disk (HDD)**
  (the user confirmed this on 2026-11-23): 4 TB over USB, HFS+, 2.5 TiB free. The code lives
  on the T7, a USB **SSD** (macOS reports "Solid State: Yes"; HFS+; 886 GiB free on
  2026-11-23), inside Dropbox, so ZIM files must never be written to the Dropbox folder.
  Neither drive can be assumed to be mounted.
- **Retention:** keep 3 versions on the HDD.
- **Hot copy** (decided 2026-11-23): the newest version also lives on the T7 SSD, outside
  the Dropbox folder. It makes browsing faster and gives the newest version a second copy.
- **Only the newest version is served** (decided 2026-11-23). Older versions are kept as
  backups only.
- **kiwix-tools backups** (decided 2026-11-23): the kiwix-tools builds are kept in the
  archive and the hot copy, like ZIMs, including Linux builds for a possible later move to
  Ubuntu 24.04.
- **Network:** reachable from the home LAN, not just `localhost`.

Why Kiwix ZIM and not the raw XML dumps or a Commons mirror: a ZIM is a single file that
`kiwix-serve` can serve as browsable HTML, images included, with no import step.

## What a maxi ZIM contains

| Flavour | Latest release | Size (bytes) | Size (GiB) | Articles | Media files | Source |
|---|---|---:|---:|---:|---:|---|
| maxi | 2026-08 | 127,418,087,648 | 118.7 | 21,673,012 | 8,469,385 | [1][3] |
| nopic | 2026-06 | 52,690,707,456 | 49.1 | 19,191,219 | 515,775 | [1] |
| mini | 2026-09 | 14,386,799,616 | 13.4 | 18,095,848 | 33,992 | [1] |

The maxi size comes from its metalink [3]. The nopic and mini sizes are the catalog's
`length` field [1], which for maxi is 800 bytes more than the metalink's figure, so treat
them as approximate. The article count includes redirects and other entries, not just
encyclopedia articles (the catalog doesn't say how they're counted).

The maxi tags are `_pictures:yes;_videos:no;_details:yes;_ftindex:yes` [1]: it includes
images, has no video, includes full article bodies ("details"), and has a full-text search
index. The design session's list of things a ZIM leaves out (interactive maps and graphs, talk
pages, edit history, most audio and video, full-resolution images) came from memory and
wasn't checked against a source in this session *(unverified)*.

## How the parts work (verified facts the design relies on)

### Finding releases: the OPDS catalog

- **URL:** `https://library.kiwix.org/catalog/v2/entries` redirects (301) to
  `https://opds.library.kiwix.org/catalog/v2/entries` [1]. The client must follow redirects.
- **Book name:** the catalog names the book `wikipedia_en_all` and gives the flavour
  (`maxi`, `nopic`, `mini`) in a separate `<flavour>` element. Querying
  `name=wikipedia_en_all_maxi` returns **0 results**. Querying `name=wikipedia_en_all`
  returns 3 entries, one per flavour [1].
- **No flavour filter:** adding `flavour=maxi` to the query is ignored (still 3 results), so
  the code filters on `<flavour>` itself.
- **Only the latest:** the catalog lists one entry per flavour, the latest. Older releases
  are found on the download server [2], not in the catalog.
- **Per-entry fields used:** `<dc:issued>` (release date, e.g. `2026-08-25T00:00:00Z`), the
  acquisition link (`rel="http://opds-spec.org/acquisition/open-access"`) whose `href` is a
  `.zim.meta4` metalink URL, and that link's `length` (approximate size).

### Downloading: metalink, checksum and resume

- The `.meta4` metalink [3] gives the exact `<size>` and `md5`, `sha-1` and `sha-256`
  hashes, plus SHA-1 hashes of every 4 MiB piece. A plain `<file>.zim.sha256` sidecar
  holds the same SHA-256 [4].
- The metalink also lists mirror URLs with priorities. The copy fetched on 2026-11-23 said
  "Found 12 mirrors" and listed 7, starting with `dumps.wikimedia.org` (priority 1). Stage 3
  can download from one of these directly, staying on a single mirror for the whole
  download instead of going through the redirector each time. `catalog.parse_metalink`
  already reads them.
- The ZIM's URL is the `.meta4` URL without `.meta4`. `download.kiwix.org` redirects (301)
  to `lb.download.kiwix.org`, which redirects (302) to a mirror. In the test it was
  `dumps.wikimedia.org`.
- A request for `Range: bytes=1000-1999`, after following the redirects, returned
  `206 Partial Content` with `Content-Range: bytes 1000-1999/127418087648`, so resuming
  works on that mirror. The redirector may pick a different mirror for each request, so the
  downloader re-resolves the URL on every attempt and checks that `Content-Range` agrees with
  the size and offset it expects.

### Serving: kiwix-serve

All from the kiwix-serve documentation [5]:

- `--library LIBRARY_FILE_PATH` serves the ZIMs listed in a library XML file.
- `-M, --monitorLibrary`: "Monitor the library file and reload it automatically when it
  changes." Separately, reloading "can be forced anytime by sending a SIGHUP signal".
- `-i ADDR, --address=ADDR`: by default the server "listens on all available IP
  addresses". `-p PORT`: "default: 80". This project passes `--address all` (or the
  configured address) and `--port 8080` explicitly. Nothing on this Mac was listening on
  8080 on 2026-11-23.
- **No authentication:** the docs list no login, password or access-control option. The
  nearest are `-L/--ipConnectionLimit` and `-b/--blockexternal`, which don't restrict who
  can connect.
- **Date aliases:** despite its name, `-z, --nodatealiases` *creates* undated aliases. For
  example, `wikipedia_en_all_2020-08.zim` becomes reachable as both
  `wikipedia_en_all_2020-08` and `wikipedia_en_all`. Name conflicts "are reported on STDERR
  but … only one of the entries can be accessed via the conflicting name", and the docs
  don't say which one. Since only one version of each book is served, there's no
  conflict, so this project **uses `-z`**. `/content/wikipedia_en_all_maxi/` then stays the
  same across updates (tested with a small ZIM in Stage 4).

### Editing library.xml: kiwix-manage

From kiwix-manage's usage text [6]:

```
kiwix-manage LIBRARYPATH add [--zimPathToSave=<custom_zim_path>] [--url=<http_zim_url>] ZIMPATH ...
kiwix-manage LIBRARYPATH (delete|remove) ZIMID ...
```

`remove` takes the book's UUID, not its path. Rather than tracking UUIDs, the project
**rebuilds `library.xml` from scratch** whenever the set of served ZIMs changes: it writes a
new file with `kiwix-manage <tmp> add <zim> ...`, then renames it over `library.xml` in one
step.

Tested with kiwix-tools 3.8.2 on 2026-11-23, using two small real ZIMs:

- kiwix-manage stores the ZIM path **relative to the library file's folder** (e.g.
  `path="../zims/…"`), even with `--zimPathToSave=<absolute path>`. kiwix-serve resolves it
  correctly, so `library.xml` just mustn't be moved.
- kiwix-manage exits 1 ("Cannot add ZIM …") if a ZIM doesn't exist.
- `--monitorLibrary` noticed a library **replaced by rename** and served the new book about
  2 s later. No SIGHUP is needed.
- When a served ZIM disappears (moved away, as when its drive is unplugged), kiwix-serve
  keeps running but answers **HTTP 500** for that book. It doesn't exit, so launchd
  wouldn't restart it. That's why `serve` checks for changes itself (Design).
- An **empty library** (no books) is fine: kiwix-serve starts and its home page answers 200.

### Installing kiwix-tools

- **Homebrew has no `kiwix-tools` formula**: `brew info kiwix-tools` returned "No available
  formula" on 2026-11-23. `brew search kiwix` lists only `libkiwix` and the `kiwix` desktop
  app.
- Kiwix publishes official macOS builds [7]. The latest is
  `kiwix-tools_macos-arm64-3.8.2.tar.gz` (10.3 MB). It contains `kiwix-serve`,
  `kiwix-manage` and `kiwix-search`, and its MD5 matched the published `.md5` file
  (`60d934c0cc75e5d2db83d2ed2de000ce`).
- Kiwix publishes only an **MD5** for kiwix-tools builds (no SHA-256 or metalink; checked
  2026-11-23). The project checks it, then records its own SHA-256 for later copies.
- Run on 2026-11-23 (with the user's approval): `--version` prints "kiwix-tools 3.8.2,
  libkiwix 14.2.0, libzim 9.5.0". The binaries are ad-hoc signed and, fetched with `curl`,
  carry no quarantine attribute, so Gatekeeper doesn't block them.
- The Linux glibc builds `linux-x86_64` and `linux-aarch64` 3.8.2 are **statically linked**
  (`file` output; not run). They need kernel 3.2+ or 5.4+, which Ubuntu 24.04's 6.8 meets.
- Installed at `~/.local/opt/kiwix-tools-3.8.2/`, with symlinks in `~/.local/bin/` (the
  user's choice, 2026-11-23).

### Older releases

The download server keeps only recent releases: 2 maxi, 2 nopic and 2 mini files for
English [2]. The archive server [8] holds old one-off ZIMs (e.g. 2013 "for schools"), not
past `wikipedia_en_all` releases. So an old version can't be re-downloaded later; the local
copies are the only history.

## Design

### Where things live

| What | Where | Drive |
|---|---|---|
| Archive: all kept versions (`zim/`), `partial/` downloads, `manifest.json`, logs | `DATA_DIR` = `/Volumes/Starfish_Prime/kiwix-data/` | HDD |
| Hot copy: the newest version only, plus a `.sha256` file written after it's verified | `HOT_DIR` = `/Volumes/BackUp_T7/wikipedia-storage/` (outside Dropbox) | SSD |
| kiwix-tools backups (`software/kiwix-tools/*.tar.gz` + `.sha256`) | in both `DATA_DIR` and `HOT_DIR` | HDD + SSD |
| `library.xml` (generated) | `STATE_DIR` = `~/.local/state/wikibackup/` | internal disk |
| kiwix-tools, installed | `~/.local/opt/kiwix-tools-<version>/`, symlinks in `~/.local/bin/` | internal disk |
| serve log | `~/Library/Logs/wikibackup/serve.log` | internal disk |
| Code, config, docs, `.venv` | this repo, inside Dropbox on the T7 | SSD |

The archive is the source of truth: `manifest.json` records every kept version's release
date, size and SHA-256. The hot copy is a cache of the newest version that has been
checked against the manifest. `library.xml` lives on the internal disk so it's always
writable.

**Correction (Stage 4):** an earlier draft said this lets serving work when *either*
external drive is missing. That holds for the HDD. It doesn't for the T7, because the code
and its `.venv` live on the T7, so after a restart without the T7, `serve` can't start.
Whether an already-running `serve` survives the T7 being unplugged hasn't been tested.

**HOT_DIR needs creating by hand:** the user's account can't create folders at the top
of the T7. `/Volumes/BackUp_T7` is owned by `root:wheel`, mode `rwxrwxr-x`, and the user
isn't in `wheel` (found on 2026-11-23). The user created
`/Volumes/BackUp_T7/wikipedia-storage` (owned by them) on 2026-11-23, and it's the
`HOT_DIR` default. If the folder is missing, hot copies are skipped with a warning, and
`status` says why.

### Commands

- **Fetch** (`wikibackup update`, run monthly by launchd):
  1. Read the catalog and choose the `wikipedia_en_all` entry whose flavour is `maxi`.
     Compare its release date with the newest version in the archive.
  2. Download only if a newer release exists **and** the newest archived version is at
     least `min_age_months` (6) old.
  3. Download into `partial/` on the HDD, resuming if interrupted.
  4. Check the size and SHA-256 against the metalink, then rename the file into `zim/`. Both
     folders are on the same volume, so the rename is atomic.
  5. **Ensure the hot copy:** whether or not anything was downloaded, if `HOT_DIR` is
     configured and mounted and doesn't hold the newest version, copy it there under a
     temporary name. Check its SHA-256 against the manifest, then rename it into place. Each
     monthly run therefore also repairs a missing hot copy, e.g. after the T7 was unplugged
     during an update. Before copying, check that free space is at least the file size plus
     a margin; if not, log it and skip.
  6. Run Retain, then rebuild `library.xml`.
- **Retain** (`wikibackup prune`, also run at the end of each update):
  - **HDD:** keep the newest `keep_versions` (3).
  - **SSD:** keep only the newest version.
  - Never delete anything until its replacement is verified and `library.xml` has been
    rebuilt without the file to be deleted.
- **Build the library** (`server.build_library`, used by `update`, `prune` and `serve`):
  write `library.xml` listing **only the newest version** of each book, using
  `kiwix-manage add` into a temporary file and then renaming it over the old one. Which
  copy is used:
  1. the hot copy, if it's current
  2. otherwise the HDD copy
  3. if the HDD isn't mounted, the newest hot-copy file that has a `.sha256`
  4. otherwise nothing, which gives an empty library

  This step doesn't re-hash 119 GiB: the SHA-256 is checked once, when the copy is made.
- **Serve:** a launchd agent (`com.local.wikibackup-serve`, `KeepAlive`, at most one
  restart a minute) runs `.venv/bin/wikibackup serve`. It builds the library and starts
  `kiwix-serve --library --monitorLibrary --nodatealiases --address <BIND_ADDRESS> --port <PORT> <STATE_DIR>/library.xml`
  as a child process.
  - Every 60 s it works out the served files again, and rebuilds the library if they've
    changed: a drive plugged in or unplugged, a new hot copy. This covers the HTTP 500
    case above with no extra launchd jobs.
  - With nothing to serve, kiwix-serve runs with an empty library until a version
    appears.
  - SIGTERM is passed on to kiwix-serve. If kiwix-serve exits, `serve` exits with the same
    status, and launchd restarts it.
- **Install** (`install.sh`, which runs `wikibackup install-tools`): `uv sync`; back up
  the kiwix-tools builds; unpack the macOS one into `~/.local/opt`; fill in the plist
  template and load the agent. `uninstall.sh` unloads the agent and removes the plist,
  leaving tools and data in place.
- **Status** (`wikibackup status`) shows:
  - the versions in the archive, with age and size
  - whether the hot copy is present and current
  - which drives are mounted, with disk use and free space on each
  - what `library.xml` serves and whether those files exist; HTTP answers on `127.0.0.1`
    and the LAN address; the `.local` name; the firewall state; whether kiwix-tools is
    installed
- **Config:** `config.toml` (committed) holds policy and which books to track. `.env` (not
  committed) holds machine paths (`DATA_DIR`, `HOT_DIR`, `STATE_DIR`) and the port. If
  `HOT_DIR` is empty, the hot copy is turned off and everything is served from the HDD.

### Redundancy this gives (and doesn't)

- The newest version is on two drives (HDD and SSD), each checked against the published
  SHA-256 when written. Losing either drive loses no data for that version.
- Older versions exist only on the HDD. Kiwix's server doesn't keep old releases (see
  "Older releases"), so if the HDD fails, the older versions are lost for good.
- kiwix-tools builds are on both drives (every version ever configured is kept), so the
  software to read the ZIMs survives the loss of either drive, and of Kiwix's site.
- Both drives are attached to the same Mac. A single event (theft, a power surge, a spilled
  drink) could take out both. This is not an off-site backup.
- Nothing re-checks the copies after they're written; there's no periodic scrub. A later
  stage could add a `verify` command that re-hashes them. It isn't planned yet.

```toml
[policy]
min_age_months = 6
keep_versions = 3

[[zim]]
name = "wikipedia_en_all"   # catalog <name>
flavour = "maxi"            # catalog <flavour>; the ZIM file is <name>_<flavour>_<YYYY-MM>.zim
```

This changes the design session's draft config, which used `name = "wikipedia_en_all_maxi"`. That
name doesn't exist in the catalog.

## Stages

Each stage ends with a summary and a pause for the user's review. The user makes all commits.

### 1. Plan doc + scaffold (this stage)
- **Goal:** check the assumptions; write this plan; scaffold the `uv` project.
- **Steps:** this document; `git init`; `uv` project with `src/wikibackup`, a smoke test and
  `.claude/CLAUDE.md`.
- **Verify:** `uv run pytest` passes; `uv run wikibackup --help` runs.
- **Rollback:** delete the scaffolded files (nothing outside the repo is touched).

### 2. Catalog + manifest (read-only)
- **Goal:** `wikibackup check` (what's the newest release; would we download?) and
  `wikibackup status`, covering the archive and the hot copy.
- **Steps:** `catalog.py` (OPDS parse, flavour filter, metalink parse), `library.py`
  (local manifest and listing of both locations), `config.py` (including `HOT_DIR` and
  `STATE_DIR`).
- **Verify:** tests against saved catalog and metalink responses (captured on 2026-11-23);
  one live `check` run.
- **Rollback:** read-only; nothing to undo.
- **Done (2026-11-23):** 62 tests pass. On a live run, `check` found
  `wikipedia_en_all_maxi_2026-08.zim` (118.7 GiB, 7 mirrors) and decided "DOWNLOAD: no
  verified local copy". `status` reported both drives mounted and none of the three folders
  created yet, and the run created none of them.
  - **Manifest format:** set in `library.py`'s module docstring. It's
    `{"format": 1, "zims": [...]}`, one entry per verified version, with `file`, `name`,
    `flavour`, `version`, `size`, `sha256` and `added`. Stage 3 writes it.
  - **Version age:** counted in calendar months from the `YYYY-MM` in the file name, not
    the catalog's `issued` date. So 2026-08 counts as 6 months old from 2027-02-01.

### 3. Download + verify + retention
- **Goal:** `wikibackup update` and `wikibackup prune`.
- **Steps:**
  - resumable download, with a range check and a size and SHA-256 check
  - atomic move into `zim/`
  - the "ensure hot copy" step: free-space check, copy, SHA-256 check, rename
  - retention on both drives, never deleting before the replacement is verified
- **Verify:** unit tests with a local HTTP server, covering interrupted and resumed
  downloads, a bad checksum, a corrupted hot copy, a missing `HOT_DIR`, and a hot copy
  repaired on a later run. Then an end-to-end run with a small real ZIM (a few MB) into
  scratch `DATA_DIR` and `HOT_DIR` folders.
- **Rollback:** delete the scratch folders.
- **Done (2026-11-23):** 85 tests pass. End-to-end runs with the real
  `openzim_en_all_maxi_2026-11.zim` (3,386,170 bytes) into scratch folders on the internal
  disk:
  - The first `update` downloaded it from `wi.mirror.driftle.ss` (the metalink's
    first-priority mirror for this book), verified it, installed it and made the hot copy.
  - A second `update` logged "up to date" and downloaded nothing.
  - macOS's `shasum -a 256` of both copies matched Kiwix's separate `.sha256` file.
  - A run starting from a 1 MiB `.part` resumed ("1048576 already present") and produced a
    file identical to the full download.
  - Not yet exercised on the real drives: the `/Volumes` mount checks with scratch folders
    on Starfish_Prime and the T7. Stage 5's first real run will be the first.
  - Choices made in this stage (all documented in `update.py`):
    - Downloads go to the metalink's mirrors in priority order, then the redirector.
    - A `flock` lock in `DATA_DIR` keeps two runs from overlapping.
    - A free-space margin of 5 GiB.
    - An unmounted archive is "nothing to do" (exit 0), not an error.
    - A file in `zim/` that the manifest doesn't list is adopted if its hash matches
      (crash recovery), and otherwise left alone, except when it has the exact name of the
      release being downloaded and the wrong hash.
    - `rebuild_library` is a no-op until Stage 4. (It's implemented in Stage 4.)

### 4. Serving
- **Goal:** the newest version is browsable from another LAN device.
- **Steps:**
  - install the official kiwix-tools arm64 build (the user approves running it)
  - check `--version` and `--help`
  - build `library.xml` with kiwix-manage, choosing the hot or HDD copy as described in
    Design
  - test `--monitorLibrary` against a file replaced by rename
  - add the `serve` launchd agent
  - decide whether to handle drives being plugged in or unplugged while serving (Design,
    "Known gap")
  - check the macOS application firewall
  - write `install.sh` and `uninstall.sh` in the style of `Maxs_MBP`
- **Verify:**
  - with test ZIMs: the newest is served from `HOT_DIR`; it falls back to the HDD when
    `HOT_DIR` is unavailable; and serving still works with only `HOT_DIR` available
  - `status` shows the server is up on the LAN address
  - the user opens it from another device
- **Rollback:** `uninstall.sh` (unloads the agent and removes the plist).
- **Done (2026-11-23), awaiting the user's checks:** 110 tests pass.
  - The real kiwix-serve and kiwix-manage experiments are recorded under "How the parts
    work".
  - `wikibackup install-tools` ran for real. It archived all 3 builds on the HDD (MD5s
    match) and installed the macOS build in `~/.local/opt/kiwix-tools-3.8.2/`. Hot copies of
    the builds were skipped at first, because the first `HOT_DIR`
    (`/Volumes/BackUp_T7/kiwix-hot`) couldn't be created. After the user created
    `wikipedia-storage` and it became `HOT_DIR`, they were made there.
  - The real `wikibackup serve` ran with the 3.4 MB test ZIM, on port 8080, on all
    interfaces. `status` reported HTTP 200 on `127.0.0.1` and on `192.168.0.50`, and
    `/content/openzim_en_all_maxi/` served the book.
  - The macOS application firewall is **off** on this Mac.
  - The user opened the test server from another machine on the LAN, and it worked
    (2026-11-23). On SIGTERM, `serve` stopped kiwix-serve cleanly (status 0).
  - **Not done yet:**
    - running `install.sh`, which loads the launchd agent; with the archive empty, it will
      serve an empty library until Stage 5's download
  - Changes from the earlier plan (user, 2026-11-23): only the newest version is served;
    `-z` is used; kiwix-tools builds are backed up.

### 5. Scheduling + first full download
- **Goal:** the monthly job is installed, and the first maxi version (118.7 GiB) is in the
  archive and in the hot copy.
- **Steps:** add the `update` launchd agent (`StartCalendarInterval`); run the first update
  by hand and follow its log; run the browsing benchmark below.
- **Verify:** SHA-256 matches on both copies; `status` shows the archive version and the
  hot copy; kiwix-serve serves the newest version from `HOT_DIR`; the benchmark results are
  recorded in this doc.
- **Rollback:** unload the agent; delete the partial file and any temporary hot-copy file.

#### Browsing benchmark (HDD vs SSD)

Measures how much the hot copy helps, and how slow the HDD fallback is. It doesn't decide whether the hot copy exists; that was decided on
2026-11-23. It runs on the real maxi version, because ZIM size affects how much of the
index the page cache can hold. Stage 5 is the first time the same large ZIM is on both
drives.

- **Setup:** query kiwix-serve on `localhost` with `curl -w '%{time_total}'`, so Wi-Fi
  latency isn't counted. Serve the same version once from `HOT_DIR` and once from the HDD.
  A small script in `scripts/` records the results.
- **Requests:**
  1. A text-heavy article (HTML only).
  2. An image-heavy article, plus every image it references.
  3. A title suggestion (the search box's autocomplete).
  4. A full-text search.
- **Conditions:**
  - **Cold, spun down (HDD only):** the drive has been idle long enough to spin down. The
    time includes spin-up.
  - **Cold:** the page cache is cleared by unmounting and remounting the drive (no
    `sudo purge` needed).
  - **Warm:** each request is repeated straight after a cold one.
- **Repeats:** 5 different articles per request type and condition; report the median and
  the maximum.
- **Use of the results:** if the HDD numbers are painful, possible follow-ups are noted in
  the doc (e.g. keeping a drive awake, or giving the second-newest version a hot copy too).
  None is planned.

### 6. README
- **Goal:** a README a new user can set up from, with the Codebase architecture section and
  the LAN and firewall notes (readme-setup skill).

## Risks

- **Download time.** 118.7 GiB at an assumed 100 Mbit/s is about 2.8 hours; at 20 Mbit/s,
  about 14 hours. These are projections, not measured, and the Mac may sleep in between.
  Resume makes interruptions cheap, but a sleeping Mac pauses the download.
- **Different mirror on each request.** One mirror might serve a different or stale file.
  The final SHA-256 check catches this; the cost is re-downloading. Since Stage 3, each
  attempt goes straight to a mirror from the metalink, so a download only changes mirror
  after a failure. Mixing bytes from two mirrors is still possible, and still caught by the
  hash.
- **Catalog format changes.** The catalog already moved hosts and doesn't use the name the
  design session expected. Tests use saved responses, and `check` fails loudly if it finds no
  `maxi` entry rather than doing nothing silently.
- **Firewall and binary trust.** The macOS application firewall may block incoming
  connections to an unsigned `kiwix-serve`. Stage 4 checks this.
- **HDD browsing speed** (only when the HDD copy is served as a fallback). These are
  estimates, not yet measured. An HDD random read takes about 10 ms, against about 0.1 ms on
  an SSD. A cold article needs a handful of reads (tens of ms). An image-heavy page needs
  one more lookup and read per image (possibly over 1 s cold), and full-text search does
  many scattered reads. The biggest delay is likely spin-up: several seconds on the first
  request after the drive has gone idle. macOS's page cache hides much of this once browsing
  warms up. The SSD hot copy avoids this for the newest version; only the HDD fallback still
  pays it. The Stage 5 benchmark measures it.
- **The hot copy shares the T7 with Dropbox and other backups.** The copy step checks free
  space first and skips with a log message rather than filling the drive.
- **Serving depends on the T7.** The code lives on the T7, so with the T7 unplugged the
  server can't (re)start, even though the HDD has a copy (see the correction under "Where
  things live"). Moving the code or a copy of it to the internal disk would remove this
  dependency. That isn't planned.
- **Drive attached elsewhere.** Starfish_Prime may sometimes be attached to the router (it
  has a `.TPDLNA` folder). Every command checks which of `DATA_DIR` and `HOT_DIR` are
  mounted before using them.

## Notes carried over for later stages

From the 2026-11-23 design session, not re-checked in Stage 1:

- **launchd and install style:** copy the sibling project
  `/Volumes/BackUp_T7/Dropbox/Computers/Maxs_MBP`: `com.local.*` labels, idempotent
  `install.sh` and `uninstall.sh`, `.env` plus `.env.example`, and logs in `logs/`. It uses
  pip and a venv; this project uses `uv`.
- **Per-binary network trouble:** Maxs_MBP's README records that on that Mac, Homebrew's
  Python couldn't make outbound connections while `/usr/bin/python3` could. It was a
  different Mac, but if downloads fail only under launchd, suspect this first.
- **Reachable only while awake:** LAN devices can reach the server only while the Mac is
  awake with at least one of the data drives attached (the T7 for the newest version
  alone). The project doesn't change power settings.
- **LAN only:** with no authentication, don't expose kiwix-serve beyond the LAN (e.g. by
  forwarding a port on the router).

## Open questions

None at the moment. Questions decided so far:

- **SSD hot copy:** adopted 2026-11-23.
- **A stable "latest" link:** resolved 2026-11-23 by serving only the newest version and
  using `-z`.
- **Where to install kiwix-tools:** `~/.local/opt` plus symlinks, decided 2026-11-23.

## Reproducing

All checks ran on 2026-11-23 from this Mac with `curl`:

```sh
# Catalog (follows the 301 to opds.library.kiwix.org); returns nopic, maxi and mini entries
curl -sSL "https://library.kiwix.org/catalog/v2/entries?name=wikipedia_en_all&lang=eng&count=50"
# The old name returns totalResults 0
curl -sSL "https://library.kiwix.org/catalog/v2/entries?name=wikipedia_en_all_maxi"
# Metalink: exact size and hashes
curl -sSL "https://lb.download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim.meta4"
# SHA-256 sidecar
curl -sSL "https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim.sha256"
# Range support: expect 206 and Content-Range .../127418087648 after the redirects
curl -sS -o /dev/null -D - -r 1000-1999 -L "https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim"
# Release history on the download server
curl -sSL https://download.kiwix.org/zim/wikipedia/ | grep -E 'wikipedia_en_all_(maxi|nopic|mini)_'
# kiwix-tools builds
curl -sSL https://download.kiwix.org/release/kiwix-tools/ | grep -o 'kiwix-tools_macos-arm64[^"<]*'
brew info kiwix-tools
# Port 8080 free (no output)
lsof -nP -iTCP:8080 -sTCP:LISTEN
```

## References

1. Kiwix OPDS catalog, entries for `wikipedia_en_all`:
   https://opds.library.kiwix.org/catalog/v2/entries?name=wikipedia_en_all&lang=eng&count=50
2. Kiwix download server, Wikipedia directory: https://download.kiwix.org/zim/wikipedia/
3. Metalink for the 2026-08 maxi ZIM:
   https://lb.download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim.meta4
4. SHA-256 sidecar for the 2026-08 maxi ZIM:
   https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim.sha256
5. kiwix-serve documentation:
   https://kiwix-tools.readthedocs.io/en/latest/kiwix-serve.html (source:
   https://github.com/kiwix/kiwix-tools/blob/main/docs/kiwix-serve.rst)
6. kiwix-manage source, usage text at lines 63–87:
   https://github.com/kiwix/kiwix-tools/blob/main/src/manager/kiwix-manage.cpp
7. kiwix-tools release builds: https://download.kiwix.org/release/kiwix-tools/
8. Kiwix archive server, Wikipedia directory: https://archive.download.kiwix.org/zim/wikipedia/

Catalog, metalink and directory content changes with each release. The figures above are as
fetched on 2026-11-23.
