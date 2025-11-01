#!/usr/bin/env bash
# Build Tidal DL GUI macOS app with PyInstaller, packaging all deps reliably.
# - Creates/uses a local venv
# - Installs local AIGPY (editable)
# - Installs requirements (filters any AIGPY line)
# - Uses a package-safe launcher (fixes relative-import crash)
# - Collects qt_material data + all PyQt6 submodules
# - Excludes conflicting libraries from ffpyplayer via a robust custom hook
# - Produces dist/tidal_dl_gui.app and dist/tidal_dl_gui-macos.zip
#
# FINALIZED STRATEGY:
# - Bundles a single, correct version of OpenSSL from Homebrew.
# - Actively prevents and removes conflicting OpenSSL libraries vendored by ffpyplayer.
# - Intelligently and robustly ensures the bundled OpenSSL dylibs are in Contents/Frameworks.
# - Rewrites ALL binary load paths via install_name_tool to use the single
#   bundled copy via @rpath, making the .app fully self-contained and portable.

set -Eeuo pipefail

# --- Robust Homebrew Environment Setup ---
if [[ -x "/opt/homebrew/bin/brew" ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="tidal_dl_gui"
OPENSSL_PREFIX=""

PKG_ROOT="${SCRIPT_DIR}/Tidal-Media-Downloader/TIDALDL-PY/tidal_dl"
GUI_MAIN="${PKG_ROOT}/gui/gui.py"
ASSETS_DIR="${PKG_ROOT}/assets"

ICON_PATH="${ASSETS_DIR}/icons/icon-tidal-dl-gui.icns"
SPLASH_IMAGE="${ASSETS_DIR}/images/splash.png"

DIST_PATH="${SCRIPT_DIR}/dist"
BUILD_PATH="${SCRIPT_DIR}/build"
HOOKS_PATH="${SCRIPT_DIR}/hooks"

VENV_DIR="${SCRIPT_DIR}/.venv"
PY_SYS="$(command -v python3.11 || true)"
PY_SYS="${PY_SYS:-$(command -v python3 || true)}"
PY_SYS="${PY_SYS:-$(command -v python || true)}"
[[ -n "${PY_SYS}" ]] || { echo "Error: python3 not found in PATH."; exit 1; }

# --- Sanity checks ---
[[ -f "${GUI_MAIN}" ]]   || { echo "Error: GUI entry not found: ${GUI_MAIN}"; exit 1; }
[[ -d "${ASSETS_DIR}" ]] || { echo "Error: assets dir not found: ${ASSETS_DIR}"; exit 1; }

ICON_OPT=()
[[ -f "${ICON_PATH}" ]] && ICON_OPT=(--icon "${ICON_PATH}") || echo "Warning: icon not found (${ICON_PATH}); using default."

# --- Platform-aware Splash Screen ---
SPLASH_OPT=()
if [[ "$(uname)" != "Darwin" ]]; then
  if [[ -f "${SPLASH_IMAGE}" ]]; then
    SPLASH_OPT=(--splash "${SPLASH_IMAGE}")
  else
    echo "Warning: splash not found at ${SPLASH_IMAGE}; continuing without splash."
  fi
else
  echo "Info: Splash screen is not supported on macOS and will be skipped."
fi

# --- venv ---
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Creating venv in ${VENV_DIR} ..."
  "${PY_SYS}" -m venv "${VENV_DIR}"
fi
PY="${VENV_DIR}/bin/python"
PIP="${PY} -m pip"

echo "Upgrading pip/wheel/setuptools and installing PyInstaller..."
${PIP} install --upgrade pip wheel setuptools
# Use a recent, stable version of PyInstaller.
${PIP} install "pyinstaller>=6.16,<6.17"

# --- Local AIGPY (editable) ---
if [[ -d "${SCRIPT_DIR}/AIGPY" ]]; then
  echo "Installing local AIGPY (editable)..."
  ${PIP} install -e "${SCRIPT_DIR}/AIGPY"
else
  echo "Warning: ${SCRIPT_DIR}/AIGPY not found. Skipping local AIGPY install."
fi

# --- Requirements (filter out AIGPY just in case) ---
REQ_IN="${SCRIPT_DIR}/Tidal-Media-Downloader/TIDALDL-PY/requirements.txt"
if [[ -f "${REQ_IN}" ]]; then
  echo "Installing Python requirements..."
  REQ_TMP="$(mktemp)"
  if command -v ggrep >/dev/null 2>&1; then GREP=ggrep; else GREP=grep; fi
  ${GREP} -viE '^[[:space:]]*aigpy([[:space:]/]|==|>=|<=|~=|!=|<|>|$).*' "${REQ_IN}" \
    | ${GREP} -vE '^[[:space:]]*(#|$)' > "${REQ_TMP}"
  ${PIP} install -r "${REQ_TMP}"
  rm -f "${REQ_TMP}"
else
  echo "Error: requirements file not found: ${REQ_IN}"
  exit 1
fi

# --- Package-safe entry wrapper (preserves relative imports) ---
LAUNCHER="${PKG_ROOT}/gui/__entry_launcher__.py"
cat > "${LAUNCHER}" <<'PY'
# -*- coding: utf-8 -*-
import sys
from tidal_dl.gui.gui import main
if __name__ == "__main__":
    sys.exit(main())
PY

# --- Clean outputs BEFORE creating build artifacts ---
rm -rf "${DIST_PATH}" "${BUILD_PATH}" "${HOOKS_PATH}"
mkdir -p "${DIST_PATH}" "${BUILD_PATH}" "${HOOKS_PATH}"

# --- Create Custom Hooks ---
# Robust hook for ffpyplayer that filters its vendored OpenSSL from both datas and binaries.
HOOK_FFPYPLAYER="${HOOKS_PATH}/hook-ffpyplayer.py"
echo "Creating robust hook to exclude ffpyplayer's vendored OpenSSL: ${HOOK_FFPYPLAYER}"
cat > "${HOOK_FFPYPLAYER}" <<'PY'
# hooks/hook-ffpyplayer.py
from PyInstaller.utils.hooks import collect_all
import os

datas, binaries, hiddenimports = collect_all("ffpyplayer")

def keep(path: str) -> bool:
    base = os.path.basename(path)
    return not (base.startswith("libssl") or base.startswith("libcrypto"))

binaries = [(src, dst) for (src, dst) in binaries if keep(src)]
datas    = [(src, dst) for (src, dst) in datas    if keep(src)]
PY

# Hook for the entire 'tidal_dl' package to ensure all submodules are included.
HOOK_TIDAL_DL="${HOOKS_PATH}/hook-tidal_dl.py"
echo "Creating package hook to force inclusion of all tidal_dl submodules: ${HOOK_TIDAL_DL}"
cat > "${HOOK_TIDAL_DL}" <<'PY'
# hooks/hook-tidal_dl.py
from PyInstaller.utils.hooks import collect_submodules
hiddenimports = collect_submodules("tidal_dl")
PY

# --- Optional collects and hooks ---
EXTRA_OPTS=( --collect-data qt_material --collect-submodules PyQt6 )

# Runtime hook to suppress verbose Qt plugin logging.
RTHOOK_QT_LOGGING="${BUILD_PATH}/rt_hook_qt_logging.py"
echo "Creating Qt logging runtime hook at ${RTHOOK_QT_LOGGING}..."
cat > "${RTHOOK_QT_LOGGING}" <<'PY'
import os
if 'QT_LOGGING_RULES' not in os.environ:
    os.environ['QT_LOGGING_RULES'] = "qt.core.plugin.factoryloader.debug=false"
PY
EXTRA_OPTS+=( --runtime-hook "${RTHOOK_QT_LOGGING}" )

# --- Bundle the correct OpenSSL from Homebrew ---
if command -v brew &>/dev/null; then
    OPENSSL_PREFIX=$(brew --prefix openssl@3 || true)
    if [[ -n "$OPENSSL_PREFIX" && -d "$OPENSSL_PREFIX/lib" ]]; then
        echo "Found Homebrew OpenSSL. Bundling correct libssl and libcrypto."
        # Use destination '.' and let PyInstaller place dylibs where it wants.
        # We will robustly move them to Frameworks post-build.
        EXTRA_OPTS+=( --add-binary "$OPENSSL_PREFIX/lib/libssl.3.dylib:." )
        EXTRA_OPTS+=( --add-binary "$OPENSSL_PREFIX/lib/libcrypto.3.dylib:." )
    else
        echo "Error: Homebrew openssl@3 not found. This is required for the build."
        exit 1
    fi
fi

# --- Build ---
echo "Starting PyInstaller build..."
"${VENV_DIR}/bin/pyinstaller" --noconfirm \
  --name "${APP_NAME}" \
  --onedir \
  --windowed \
  ${ICON_OPT[@]+"${ICON_OPT[@]}"} \
  ${SPLASH_OPT[@]+"${SPLASH_OPT[@]}"} \
  --distpath "${DIST_PATH}" \
  --workpath "${BUILD_PATH}" \
  --clean \
  --paths "${SCRIPT_DIR}/AIGPY" \
  --paths "${SCRIPT_DIR}/Tidal-Media-Downloader/TIDALDL-PY" \
  --add-data "${ASSETS_DIR}:tidal_dl/assets" \
  --additional-hooks-dir "${HOOKS_PATH}" \
  --exclude-module tkinter \
  ${EXTRA_OPTS[@]+"${EXTRA_OPTS[@]}"} \
  "${LAUNCHER}"

echo "PyInstaller finished."

# --- Verify bundle ---
APP_BUNDLE="${DIST_PATH}/${APP_NAME}.app"
if [[ ! -d "${APP_BUNDLE}" ]]; then
  echo "Error: .app bundle not found where expected: ${APP_BUNDLE}"
  exit 1
fi
echo "App bundle created at: ${APP_BUNDLE}"

# --- Post-build Step 1: Nuke all bundled OpenSSL libs to ensure a clean slate ---
echo "--- [Post-build Step 1: Clean Slate for OpenSSL] ---"
echo "[DEBUG] Searching for and deleting any existing OpenSSL dylibs in bundle..."
find "$APP_BUNDLE/Contents" \( -name 'libssl*.dylib' -o -name 'libcrypto*.dylib' \) -print -delete
echo "[DEBUG] Deletion complete."
echo "--- [Post-build Step 1: Complete] ---"

# --- Post-build Step 2: Copy the correct Homebrew OpenSSL libs into Frameworks ---
echo "--- [Post-build Step 2: Install Correct OpenSSL] ---"
if [[ -n "$OPENSSL_PREFIX" && -d "$OPENSSL_PREFIX/lib" ]]; then
    FRAMEWORKS_DIR="$APP_BUNDLE/Contents/Frameworks"
    SSL_SRC="$OPENSSL_PREFIX/lib/libssl.3.dylib"
    CRYPTO_SRC="$OPENSSL_PREFIX/lib/libcrypto.3.dylib"
    
    echo "[DEBUG] Source Homebrew SSL: $SSL_SRC"
    echo "[DEBUG] Source Homebrew Crypto: $CRYPTO_SRC"
    echo "[DEBUG] Target directory: $FRAMEWORKS_DIR"

    if [[ ! -f "$SSL_SRC" || ! -f "$CRYPTO_SRC" ]]; then
        echo "[ERROR] CRITICAL: Homebrew OpenSSL dylibs not found at source location. Build failed."
        exit 1
    fi

    echo "[ACTION] Copying Homebrew dylibs to Frameworks..."
    cp "$SSL_SRC" "$FRAMEWORKS_DIR/"
    cp "$CRYPTO_SRC" "$FRAMEWORKS_DIR/"

    # Verification
    if [[ -f "$FRAMEWORKS_DIR/libssl.3.dylib" && -f "$FRAMEWORKS_DIR/libcrypto.3.dylib" ]]; then
        echo "[SUCCESS] Correct OpenSSL dylibs installed in Frameworks."
        ls -l "$FRAMEWORKS_DIR/libssl.3.dylib" "$FRAMEWORKS_DIR/libcrypto.3.dylib"
    else
        echo "[ERROR] CRITICAL: Failed to copy Homebrew OpenSSL dylibs. Build failed."
        exit 1
    fi
else
    echo "[ERROR] CRITICAL: OPENSSL_PREFIX not set. Cannot install correct libraries."
    exit 1
fi
echo "--- [Post-build Step 2: Complete] ---"

# --- Post-build Step 3: Repoint library load paths ---
echo "--- [Post-build Step 3: Patching Binary Load Paths] ---"
if [[ -n "$OPENSSL_PREFIX" && -d "$OPENSSL_PREFIX/lib" ]]; then
  SSL_BASENAME="libssl.3.dylib"
  CRYPTO_BASENAME="libcrypto.3.dylib"
  FRAMEWORKS_DIR="$APP_BUNDLE/Contents/Frameworks"
  
  # Generic, non-versioned paths for widespread patching
  SSL_GENERIC_PATH="$OPENSSL_PREFIX/lib/$SSL_BASENAME"
  CRYPTO_GENERIC_PATH="$OPENSSL_PREFIX/lib/$CRYPTO_BASENAME"
  
  echo "[DEBUG] Using generic Homebrew paths for patching:"
  echo "[DEBUG]   SSL Source: $SSL_GENERIC_PATH"
  echo "[DEBUG]   Crypto Source: $CRYPTO_GENERIC_PATH"
  echo "[DEBUG] Target path for all references will be '@rpath/...'"

  # Add an @rpath entry to the main executable.
  echo "[ACTION] Adding @rpath to main executable..."
  install_name_tool -add_rpath "@executable_path/../Frameworks" "$APP_BUNDLE/Contents/MacOS/$APP_NAME"

  # Fix the 'id' of the bundled dylibs themselves.
  echo "[ACTION] Setting install name for bundled $SSL_BASENAME..."
  install_name_tool -id "@rpath/$SSL_BASENAME" "$FRAMEWORKS_DIR/$SSL_BASENAME"
  echo "[ACTION] Setting install name for bundled $CRYPTO_BASENAME..."
  install_name_tool -id "@rpath/$CRYPTO_BASENAME" "$FRAMEWORKS_DIR/$CRYPTO_BASENAME"

  # --- CRITICAL FIX ---
  # Discover the exact, hardcoded, versioned path to libcrypto inside the just-copied libssl.
  echo "[DEBUG] Discovering hardcoded libcrypto path within bundled libssl..."
  HARCODED_CRYPTO_PATH=$(otool -L "$FRAMEWORKS_DIR/$SSL_BASENAME" | grep 'libcrypto' | awk '{print $1}')
  
  if [[ -z "$HARCODED_CRYPTO_PATH" ]]; then
      echo "[ERROR] Could not discover the hardcoded path to libcrypto within libssl. Patching cannot proceed."
      exit 1
  fi
  echo "[DEBUG]   Discovered path: $HARCODED_CRYPTO_PATH"

  # Use that exact discovered path in the -change command to fix the inter-library dependency.
  echo "[ACTION] Patching $SSL_BASENAME to depend on @rpath/$CRYPTO_BASENAME..."
  install_name_tool -change "$HARCODED_CRYPTO_PATH" "@rpath/$CRYPTO_BASENAME" "$FRAMEWORKS_DIR/$SSL_BASENAME"
  # --- END CRITICAL FIX ---

  # Find ALL binaries and repoint ALL known bad paths to the single @rpath source.
  echo "[ACTION] Starting recursive patch of all binaries in the app bundle..."
  find "$APP_BUNDLE" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -111 \) -print0 | while IFS= read -r -d '' BINFILE; do
    # Use a conditional to avoid printing errors for files that don't have the dependency
    if otool -L "$BINFILE" 2>/dev/null | grep -q -E 'openssl|libcrypto|libssl'; then
        echo "[DEBUG]   Patching file: $BINFILE"
        # Rewrite generic Homebrew paths
        install_name_tool -change "$SSL_GENERIC_PATH" "@rpath/$SSL_BASENAME" "$BINFILE" 2>/dev/null || true
        install_name_tool -change "$CRYPTO_GENERIC_PATH" "@rpath/$CRYPTO_BASENAME" "$BINFILE" 2>/dev/null || true
        
        # Also rewrite the specific, hardcoded path in case any other library uses it
        install_name_tool -change "$HARCODED_CRYPTO_PATH" "@rpath/$CRYPTO_BASENAME" "$BINFILE" 2>/dev/null || true
        
        # Rewrite ffpyplayer's vendored paths (in case any binary still references them)
        install_name_tool -change "@loader_path/../__dot__dylibs/libssl.3.dylib"   "@rpath/$SSL_BASENAME"    "$BINFILE" 2>/dev/null || true
        install_name_tool -change "@loader_path/../__dot__dylibs/libcrypto.3.dylib" "@rpath/$CRYPTO_BASENAME" "$BINFILE" 2>/dev/null || true
    fi
  done
  echo "[SUCCESS] Recursive patching complete."
else
  echo "[WARNING] OPENSSL_PREFIX not set or not found; skipping install_name_tool path fix."
fi
echo "--- [Post-build Step 3: Complete] ---"

# --- Post-build Step 4: Ad-hoc codesign AFTER all modifications ---
echo "--- [Post-build Step 4: Code Signing] ---"
if command -v codesign >/dev/null 2>&1; then
  echo "[ACTION] Re-signing app bundle ad-hoc after all modifications..."
  codesign --force --deep -s - "$APP_BUNDLE"
  echo "[SUCCESS] Code signing complete."
else
  echo "[WARNING] 'codesign' command not found. Skipping."
fi
echo "--- [Post-build Step 4: Complete] ---"

# --- Final Sanity Checks ---
echo "--- [Final Sanity Check: Verifying Linkage] ---"
echo "--- 1. All OpenSSL libraries found in bundle (should only be two in Frameworks):"
find "$APP_BUNDLE/Contents" -name 'libssl*.dylib' -o -name 'libcrypto*.dylib'
echo "--- 2. Python _ssl.so Linkage (should point to @rpath):"
otool -L "$APP_BUNDLE/Contents/Frameworks/python3.11/lib-dynload/_ssl.cpython-311-darwin.so" | grep -iE 'ssl|crypto'
echo "--- 3. Bundled libssl.3.dylib Linkage (should point to @rpath):"
otool -L "$FRAMEWORKS_DIR/libssl.3.dylib" | grep -i 'crypto'
echo "--- [Final Sanity Check: Complete] ---"

# --- Zip ---
# Use -y to preserve symlinks, which is important for PyInstaller 6+
( cd "${DIST_PATH}" && zip -qry9 -y "${APP_NAME}-macos.zip" "$(basename "${APP_BUNDLE}")" )
echo "ZIP: ${DIST_PATH}/${APP_NAME}-macos.zip"

echo "Build successful!"