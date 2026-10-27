#!/usr/bin/env bash
#
# Build a self-contained pro-space-optimizer .deb.
#
# The resulting package bundles a private Python interpreter and PySide6/Qt
# under /opt/pro-space-optimizer, so the target machine needs no Python,
# no pip and no virtual environment set up.
#
# Requirements on the BUILD machine only:
#   * python3 (the interpreter to bundle)
#   * python3 -m pip, python3 -m venv
#   * dpkg-deb (from dpkg)
#
# Usage:
#   packaging/deb/build-deb.sh
# Environment overrides:
#   PYTHON=/usr/bin/python3   interpreter to bundle (default: /usr/bin/python3)
#   SKIP_TESTS=1              skip the pytest gate
#
set -euo pipefail

APP_SLUG="pro-space-optimizer"
APP_NAME="Pro Space Optimizer"
VERSION="1.0.0"
REVISION="1"
ARCH="amd64"
PYTHON_BIN="${PYTHON:-/usr/bin/python3}"
# Pinned for reproducible builds.  Override with PYSIDE_VERSION=... if needed.
PYSIDE_VERSION="${PYSIDE_VERSION:-6.11.0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${PROJECT_DIR}/build/deb"
STAGE="${BUILD_DIR}/stage"
DEB_OUT="${PROJECT_DIR}/${APP_SLUG}_${VERSION}-${REVISION}_${ARCH}.deb"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] && fail "Do not build as root."
command -v dpkg-deb >/dev/null || fail "dpkg-deb not found (apt install dpkg)."
[ -x "$PYTHON_BIN" ] || fail "Interpreter not found: $PYTHON_BIN"

PY_VER="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_FULL="$("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
log "Bundling Python ${PY_FULL} from ${PYTHON_BIN}"

# 1. Test gate ---------------------------------------------------------------
if [ "${SKIP_TESTS:-0}" != "1" ]; then
    log "Running test suite (set SKIP_TESTS=1 to skip)"
    # Prefer a 'python3' that has pytest on the developer PATH; fall back to
    # the bundling interpreter.
    TEST_PY="$(command -v python3 || true)"
    if ! "$TEST_PY" -c 'import pytest' >/dev/null 2>&1; then
        if "$PYTHON_BIN" -c 'import pytest' >/dev/null 2>&1; then
            TEST_PY="$PYTHON_BIN"
        else
            fail "pytest not found for the test gate. Install it or set SKIP_TESTS=1."
        fi
    fi
    if ! ( cd "$PROJECT_DIR" && QT_QPA_PLATFORM=offscreen "$TEST_PY" -m pytest -q ); then
        fail "Tests failed; refusing to build the package."
    fi
fi

# 2. Fresh staging tree ------------------------------------------------------
log "Preparing staging tree"
rm -rf "$BUILD_DIR"
RUNTIME="${STAGE}/opt/${APP_SLUG}/runtime"
APP_ROOT="${STAGE}/opt/${APP_SLUG}/app"
APP_DIR="${APP_ROOT}/pro_space_optimizer"
mkdir -p "$RUNTIME" "$APP_DIR" "${STAGE}/usr/bin" \
         "${STAGE}/usr/share/applications" \
         "${STAGE}/usr/share/icons/hicolor/scalable/apps" \
         "${STAGE}/usr/share/polkit-1/actions" \
         "${STAGE}/usr/share/doc/${APP_SLUG}" \
         "${STAGE}/DEBIAN"

# 3. Build the private runtime ----------------------------------------------
log "Building private Python runtime"
# 3a. interpreter + stdlib + shared lib, copied into the prefix.
mkdir -p "${RUNTIME}/bin" "${RUNTIME}/lib"
PY_REAL="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
cp "$PY_REAL" "${RUNTIME}/bin/python3"
STDLIB="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_paths()["stdlib"])')"
STDPLAT="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_paths()["platstdlib"])')"
cp -r "$STDLIB" "${RUNTIME}/lib/python${PY_VER}"
if [ "$STDLIB" != "$STDPLAT" ]; then
    cp -r "$STDPLAT"/lib-dynload "${RUNTIME}/lib/python${PY_VER}/" 2>/dev/null || true
fi
# Shared library (may be static; copy when present).
LIBPY="$(ldd "$PY_REAL" 2>/dev/null | awk '/libpython/{print $3}' | head -1 || true)"
[ -n "$LIBPY" ] && cp "$LIBPY" "${RUNTIME}/lib/" || true
# Best-effort copy of any libpython the interpreter may dlopen.
for cand in /usr/lib/*/libpython${PY_VER}*.so* /usr/lib/libpython${PY_VER}*.so*; do
    [ -e "$cand" ] && cp -P "$cand" "${RUNTIME}/lib/" 2>/dev/null || true
done

# 3b. PySide6 essentials + shiboken6 (skips the huge addons wheel).
log "Installing PySide6 (essentials) into the runtime"
SP="${RUNTIME}/lib/python${PY_VER}/site-packages"
mkdir -p "$SP"
WHEELS="${BUILD_DIR}/wheels"
mkdir -p "$WHEELS"
"$PYTHON_BIN" -m pip download --quiet --only-binary=:all: --no-deps \
    -d "$WHEELS" "PySide6-Essentials==${PYSIDE_VERSION}" "shiboken6==${PYSIDE_VERSION}" \
    || fail "Could not download PySide6 wheels (build machine needs network)."
for whl in "$WHEELS"/pyside6_essentials-*.whl "$WHEELS"/shiboken6-*.whl; do
    [ -e "$whl" ] || continue
    "$PYTHON_BIN" - "$whl" "$SP" <<'PY'
import sys, zipfile
whl, dest = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(whl) as z:
    z.extractall(dest)
PY
done
# Also ensure a PySide6 package alias exists (essentials provides PySide6/).
[ -d "$SP/PySide6" ] || fail "PySide6 package missing from wheels."

# 4. Copy the application ----------------------------------------------------
log "Copying application sources"
cp -r "${PROJECT_DIR}/src/pro_space_optimizer/." "$APP_DIR/"
# Drop caches / test artefacts that may have slipped in.
find "$APP_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# 5. Launcher, desktop entry, icon, policy -----------------------------------
log "Installing launcher, desktop entry, icon and policy"
install -m 0755 "${SCRIPT_DIR}/pro-space-optimizer.launcher" \
    "${STAGE}/usr/bin/${APP_SLUG}"
install -m 0644 "${SCRIPT_DIR}/${APP_SLUG}.desktop" \
    "${STAGE}/usr/share/applications/${APP_SLUG}.desktop"
if [ -f "${PROJECT_DIR}/resources/icons/app.svg" ]; then
    install -m 0644 "${PROJECT_DIR}/resources/icons/app.svg" \
        "${STAGE}/usr/share/icons/hicolor/scalable/apps/${APP_SLUG}.svg"
fi
install -m 0644 "${PROJECT_DIR}/packaging/${APP_SLUG}.policy.in" \
    "${STAGE}/usr/share/polkit-1/actions/local.${APP_SLUG}.delete.policy"
install -m 0644 "${SCRIPT_DIR}/copyright" \
    "${STAGE}/usr/share/doc/${APP_SLUG}/copyright"
[ -f "${PROJECT_DIR}/README.md" ] && \
    gzip -9 -c "${PROJECT_DIR}/README.md" > \
        "${STAGE}/usr/share/doc/${APP_SLUG}/README.md.gz"

# 6. DEBIAN control files ----------------------------------------------------
log "Writing control metadata"
INSTALLED_SIZE="$(du -sk "$STAGE" | cut -f1)"

# Detect the version-specific runtime libraries actually present on the build
# machine, so the dependency list always matches this Ubuntu release.  These
# are the packages that own the sonames the bundled Qt links against.
detect_pkg() {
    local soname="$1" fallback="$2" p
    p="$(dpkg -S "$soname" 2>/dev/null | cut -d: -f1 | head -1 || true)"
    printf '%s' "${p:-$fallback}"
}
PKG_ICU="$(detect_pkg 'libicudata.so.*' 'libicu78')"
# dpkg -S needs a path glob; query the ldconfig cache instead for robustness.
PKG_ICU="$(ldconfig -p 2>/dev/null | awk '/libicudata\.so/{print $NF; exit}' | xargs -r dpkg -S 2>/dev/null | cut -d: -f1 | head -1)"
PKG_ICU="${PKG_ICU:-libicu78}"
PKG_DBL="$(ldconfig -p 2>/dev/null | awk '/libdouble-conversion\.so/{print $NF; exit}' | xargs -r dpkg -S 2>/dev/null | cut -d: -f1 | head -1)"
PKG_DBL="${PKG_DBL:-libdouble-conversion3}"
PKG_GLIB="$(ldconfig -p 2>/dev/null | awk '/libglib-2\.0\.so/{print $NF; exit}' | xargs -r dpkg -S 2>/dev/null | cut -d: -f1 | head -1)"
PKG_GLIB="${PKG_GLIB:-libglib2.0-0t64}"
PKG_PNG="$(ldconfig -p 2>/dev/null | awk '/libpng16\.so/{print $NF; exit}' | xargs -r dpkg -S 2>/dev/null | cut -d: -f1 | head -1)"
PKG_PNG="${PKG_PNG:-libpng16-16t64}"
PKG_MTDEV="$(ldconfig -p 2>/dev/null | awk '/libmtdev\.so/{print $NF; exit}' | xargs -r dpkg -S 2>/dev/null | cut -d: -f1 | head -1)"
PKG_MTDEV="${PKG_MTDEV:-libmtdev1t64}"
log "Detected: icu=${PKG_ICU} dbl=${PKG_DBL} glib=${PKG_GLIB} png=${PKG_PNG} mtdev=${PKG_MTDEV}"

sed -e "s/@VERSION@/${VERSION}/g" \
    -e "s/@REVISION@/${REVISION}/g" \
    -e "s/@ARCH@/${ARCH}/g" \
    -e "s/@INSTALLED_SIZE@/${INSTALLED_SIZE}/g" \
    -e "s/@PKG_ICU@/${PKG_ICU}/g" \
    -e "s/@PKG_DBL@/${PKG_DBL}/g" \
    -e "s/@PKG_GLIB@/${PKG_GLIB}/g" \
    -e "s/@PKG_PNG@/${PKG_PNG}/g" \
    -e "s/@PKG_MTDEV@/${PKG_MTDEV}/g" \
    "${SCRIPT_DIR}/control.template" > "${STAGE}/DEBIAN/control"
install -m 0755 "${SCRIPT_DIR}/postinst" "${STAGE}/DEBIAN/postinst"
install -m 0755 "${SCRIPT_DIR}/prerm"    "${STAGE}/DEBIAN/prerm"

# 7. Build the .deb ----------------------------------------------------------
log "Building ${DEB_OUT}"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB_OUT" >/dev/null
SIZE="$(du -h "$DEB_OUT" | cut -f1)"
log "Built ${DEB_OUT} (${SIZE})"
printf '\nInstall with:\n  sudo apt install ./%s\n' "$(basename "$DEB_OUT")"
