# wikibackup (project notes)

Offline English Wikipedia via Kiwix ZIM files, downloaded about every 6 months and served
to the home LAN by `kiwix-serve` under launchd. Design and stages:
`docs/kiwix_backup_plan.md`. Work goes one stage at a time.

## Commands

- `uv sync --dev`: install
- `uv run pytest`: tests
- `uv run wikibackup <check|status|update|prune|serve|install-tools>`: the CLI
- Real end-to-end test without the 119 GiB download: a scratch `config.toml` tracking
  `openzim_en_all` / `maxi` (3.4 MB), plus `DATA_DIR` and `HOT_DIR` pointing at scratch
  folders: `uv run wikibackup --config <scratch>/config.toml update`

## Things that are easy to get wrong

- **Never write ZIMs into this repo.** It is inside Dropbox on the T7 drive. Data goes in
  `DATA_DIR` (default `/Volumes/Starfish_Prime/kiwix-data`). Neither drive is guaranteed to
  be mounted, so check before doing anything.
- Three data locations:
  - `DATA_DIR`: the archive on Starfish_Prime (HDD), with all kept versions and the
    manifest, which is the source of truth.
  - `HOT_DIR`: a verified copy of the newest version only, on the T7 (SSD) but outside
    Dropbox. It's served in preference to the archive copy.
  - `STATE_DIR`: `library.xml`, on the internal disk.

  Keep "where a version is served from" separate from "where it's archived" in the code.
  Either drive may be missing.
- `.venv/` carries the `com.dropbox.ignored` xattr. Re-apply it if the venv is recreated.
- **Catalog naming:** the book is `name=wikipedia_en_all` plus `<flavour>maxi</flavour>`,
  not `wikipedia_en_all_maxi`. The catalog ignores a `flavour=` query parameter, so filter
  on it ourselves. The catalog host is `opds.library.kiwix.org` (`library.kiwix.org`
  redirects there).
- Take the exact size and SHA-256 from the `.zim.meta4` metalink. The catalog's `length` is
  approximate.
- Kiwix's redirector may pick a different mirror on each request, so downloads go
  straight to the metalink's mirrors in priority order, and every resume checks
  `Content-Range`.
- kiwix-tools isn't on Homebrew. `wikibackup install-tools` (run by `install.sh`)
  installs the official build into `~/.local/opt/kiwix-tools-<version>/`. Kiwix publishes
  only MD5s for these builds.
- Only the newest version of each book is served, so `-z/--nodatealiases` is safe and is
  used: it gives stable undated URLs.
- kiwix-manage writes ZIM paths relative to `library.xml`'s folder, so don't move
  `library.xml`.
- Tests use fake kiwix-tools (`tests/conftest.py`, autouse). Don't let a test touch the
  real `~/.local` or the network.
- Two launchd agents: `com.local.wikibackup-serve` (log in
  `~/Library/Logs/wikibackup/serve.log`) and `com.local.wikibackup-update` (monthly; logs
  in `DATA_DIR/logs/wikibackup.log`, and launchd's output in
  `~/Library/Logs/wikibackup/update.log`). Re-run `install.sh` after changing a plist template.
- Another project of the user's writes to Starfish_Prime, so don't unmount it.
- The user's global rules apply: the user makes all commits; after each stage, pause for
  review.
