#!/usr/bin/env bash
# Stop and remove the wikibackup launchd agent (com.local.wikibackup-serve).
#
# Leaves in place: kiwix-tools in ~/.local/opt and its ~/.local/bin symlinks, the
# archive and hot copy (ZIMs, software backups), library.xml, and the logs. Delete those
# by hand if you want them gone.
set -euo pipefail

LABEL="com.local.wikibackup-serve"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
    echo "==> Stopping & unloading agent"
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
fi

if [ -f "${PLIST_DEST}" ]; then
    rm -f "${PLIST_DEST}"
    echo "==> Removed ${PLIST_DEST}"
fi

echo "Done. Project files in $(cd "$(dirname "$0")" && pwd) and all data are untouched."
