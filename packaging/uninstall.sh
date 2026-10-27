#!/usr/bin/env bash
#
# Uninstall Pro Space Optimizer for the current user.
#
# Removes the launcher, desktop entry, icon, virtual environment and the
# application data directory.  Configuration under ~/.config is left in place
# unless --purge is passed.
#
set -euo pipefail

APP_SLUG="pro-space-optimizer"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="${HOME}/.local/bin"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

echo "Removing ${APP_SLUG}…"
rm -f "${BIN_HOME}/${APP_SLUG}"
rm -f "${DATA_HOME}/applications/${APP_SLUG}.desktop"
rm -f "${DATA_HOME}/icons/hicolor/scalable/apps/${APP_SLUG}.svg"
rm -rf "${DATA_HOME}/${APP_SLUG}"

if [ "$PURGE" -eq 1 ]; then
    rm -rf "${CONFIG_HOME}/ProSpaceOptimizer"
    rm -rf "${CONFIG_HOME}/${APP_SLUG}"
    echo "Configuration removed."
else
    echo "Configuration kept. Use --purge to remove it as well."
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${DATA_HOME}/applications" >/dev/null 2>&1 || true
fi

echo "Uninstalled."
