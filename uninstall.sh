#!/usr/bin/env bash
# Stop and remove wikipedia-backup's launchd agents (com.local.wikibackup-serve and
# com.local.wikibackup-update). A running update is stopped; its partial download stays
# and resumes on the next update.
#
# Leaves in place: kiwix-tools in ~/.local/opt and its ~/.local/bin symlinks, the
# archive and hot copy (ZIMs, software backups), library.xml, and the logs. Delete those
# by hand if you want them gone.
set -euo pipefail

for LABEL in com.local.wikibackup-serve com.local.wikibackup-update; do
    PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
    if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
        echo "==> Stopping & unloading ${LABEL}"
        launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    fi
    if [ -f "${PLIST_DEST}" ]; then
        rm -f "${PLIST_DEST}"
        echo "==> Removed ${PLIST_DEST}"
    fi
done

echo "Done. Project files in $(cd "$(dirname "$0")" && pwd) and all data are untouched."
