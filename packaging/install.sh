#!/usr/bin/env bash
#
# Install Pro Space Optimizer for the *current user*.
#
# This script:
#   * checks the Python version,
#   * creates a virtual environment under ~/.local/share/pro-space-optimizer,
#   * installs the application and its dependencies into it,
#   * installs the application icon and a desktop entry,
#   * creates a launcher in ~/.local/bin.
#
# It does NOT run as root and does not modify unrelated system settings.
# Re-running it is safe (idempotent).
#
set -euo pipefail

APP_SLUG="pro-space-optimizer"
APP_NAME="Pro Space Optimizer"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="${HOME}/.local/bin"
APP_DATA_DIR="${DATA_HOME}/${APP_SLUG}"
VENV_DIR="${APP_DATA_DIR}/venv"
ICON_DIR="${DATA_HOME}/icons/hicolor/scalable/apps"
DESKTOP_DIR="${DATA_HOME}/applications"

log()  { printf '  %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

printf '%s installer\n' "$APP_NAME"
printf '==============================\n'

# 1. Locate a suitable Python -------------------------------------------------
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    fail "python3 was not found. Install it with: sudo apt install python3 python3-venv"
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_MAJOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[0])')"
PY_MINOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[1])')"

log "Using Python ${PY_VERSION} (${PYTHON_BIN})"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    fail "Python 3.9 or newer is required (found ${PY_VERSION})."
fi

# 2. Check for the venv module -----------------------------------------------
if ! "$PYTHON_BIN" -c 'import venv' >/dev/null 2>&1; then
    fail "The 'venv' module is missing. Install it with: sudo apt install python3-venv"
fi

# 3. Create the virtual environment -------------------------------------------
log "Creating virtual environment at ${VENV_DIR}"
mkdir -p "$APP_DATA_DIR" "$BIN_HOME" "$ICON_DIR" "$DESKTOP_DIR"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR" || fail "Could not create the virtual environment."
fi
VENV_PY="${VENV_DIR}/bin/python"
[ -x "$VENV_PY" ] || fail "Virtual environment interpreter not found."

# 4. Install the application --------------------------------------------------
log "Installing dependencies (this may take a moment)…"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
if ! "$VENV_PY" -m pip install --upgrade "$PROJECT_DIR"; then
    fail "Installing the application failed. See the output above."
fi

# 5. Install the launcher -----------------------------------------------------
log "Creating launcher at ${BIN_HOME}/${APP_SLUG}"
cat > "${BIN_HOME}/${APP_SLUG}" <<EOF
#!/usr/bin/env bash
exec "${VENV_PY}" -m pro_space_optimizer.app "\$@"
EOF
chmod +x "${BIN_HOME}/${APP_SLUG}"

# 6. Install the icon ---------------------------------------------------------
ICON_DEST="${ICON_DIR}/${APP_SLUG}.svg"
if [ -f "${PROJECT_DIR}/resources/icons/app.svg" ]; then
    log "Installing icon to ${ICON_DEST}"
    cp "${PROJECT_DIR}/resources/icons/app.svg" "$ICON_DEST"
else
    log "WARNING: app.svg not found; using a themed fallback icon."
    ICON_DEST="folder"
fi

# 7. Install the desktop entry ------------------------------------------------
DESKTOP_DEST="${DESKTOP_DIR}/${APP_SLUG}.desktop"
log "Installing desktop entry to ${DESKTOP_DEST}"
sed -e "s|__EXEC__|${BIN_HOME}/${APP_SLUG}|g" \
    -e "s|__ICON__|${ICON_DEST}|g" \
    "${PROJECT_DIR}/packaging/${APP_SLUG}.desktop.in" > "$DESKTOP_DEST"
chmod +x "$DESKTOP_DEST"

# 8. Refresh desktop caches where possible -----------------------------------
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -d "${DATA_HOME}/icons/hicolor" ]; then
    gtk-update-icon-cache -f -t "${DATA_HOME}/icons/hicolor" >/dev/null 2>&1 || true
fi

printf '\n%s installed.\n' "$APP_NAME"
printf 'Launch from the application menu, or run: %s/%s\n' "$BIN_HOME" "$APP_SLUG"
case ":${PATH}:" in
    *":${BIN_HOME}:"*) ;;
    *) printf 'NOTE: %s is not on your PATH. Add it to use the short command.\n' "$BIN_HOME" ;;
esac
