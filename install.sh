#!/usr/bin/env bash
# Install / re-install wikibackup on this Mac: Python env, kiwix-tools, and the launchd
# agent that serves Wikipedia on the LAN (com.local.wikibackup-serve).
#
# Idempotent: safe to re-run after pulling new code or editing the plist template.
# What it changes outside this repo:
#   ~/.local/opt/kiwix-tools-<version>/, ~/.local/bin/kiwix-{serve,manage}  (install-tools)
#   DATA_DIR/software/ and HOT_DIR/software/  (kiwix-tools backups, if the drives are mounted)
#   ~/Library/Logs/wikibackup/, ~/Library/LaunchAgents/com.local.wikibackup-serve.plist
# Undo with ./uninstall.sh (it leaves the tools, backups and data in place).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.local.wikibackup-serve"
PLIST_SRC="${PROJECT_DIR}/launchd/${LABEL}.plist.template"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/wikibackup"

echo "==> Project dir: ${PROJECT_DIR}"

# 1. Python environment (uv). Keep .venv out of Dropbox's sync.
if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found; install it first (https://docs.astral.sh/uv/)" >&2
    exit 1
fi
mkdir -p "${PROJECT_DIR}/.venv"
xattr -w com.dropbox.ignored 1 "${PROJECT_DIR}/.venv"
echo "==> Syncing Python environment"
(cd "${PROJECT_DIR}" && uv sync --quiet)

# 2. .env (optional; defaults match .env.example)
if [ ! -f "${PROJECT_DIR}/.env" ]; then
    echo "==> Creating .env from .env.example"
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
fi

# 3. kiwix-tools: back up the configured builds, install the macOS one
echo "==> Installing kiwix-tools"
"${PROJECT_DIR}/.venv/bin/wikibackup" install-tools

# 4. LaunchAgent
mkdir -p "${LOG_DIR}" "${HOME}/Library/LaunchAgents"
sed -e "s|__REPO__|${PROJECT_DIR}|g" -e "s|__HOME__|${HOME}|g" "${PLIST_SRC}" > "${PLIST_DEST}"
plutil -lint "${PLIST_DEST}" >/dev/null
echo "==> Installed plist at ${PLIST_DEST}"

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
    echo "==> Unloading existing agent"
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
fi
# bootstrap starts the agent at once (RunAtLoad), so no kickstart is needed. enable clears
# any "disabled" override left by an earlier `launchctl disable`.
echo "==> Loading agent"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DEST}"
launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true

# 5. Firewall note
if /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate | grep -q enabled; then
    cat <<EOF

The macOS application firewall is ON. If other devices can't connect, allow kiwix-serve:
    sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "$("${PROJECT_DIR}/.venv/bin/python" -c 'from wikibackup import config, software; print(software.tool_path(config.load_config(), "kiwix-serve"))')"
EOF
fi

echo
echo "==> Done. Check with:  ${PROJECT_DIR}/.venv/bin/wikibackup status"
echo "Server log:            tail -f '${LOG_DIR}/serve.log'"
