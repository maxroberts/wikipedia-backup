# wikibackup (project notes)

Offline English Wikipedia via Kiwix ZIM files, downloaded about every 6 months and served
to the home LAN by `kiwix-serve` under launchd. Design and stages:
`docs/kiwix_backup_plan.md`. Work goes one stage at a time.

## Commands

- `uv sync --dev`: install
- `uv run pytest`: tests
- `uv run wikibackup <check|status|update|prune|serve>`: the CLI

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
- Downloads redirect to a different mirror on each request. Re-resolve the URL on every
  resume and check `Content-Range`.
- kiwix-tools isn't on Homebrew. Use the official macOS arm64 tarball from
  `download.kiwix.org/release/kiwix-tools/`. Running it needs the user's approval.
- Don't use kiwix-serve's `-z/--nodatealiases`. It creates undated aliases, which conflict
  when 3 versions are kept.
- The user's global rules apply: the user makes all commits; after each stage, pause for
  review.
