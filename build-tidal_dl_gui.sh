#!/usr/bin/env bash
# Build Tidal DL GUI macOS app with PyInstaller, packaging all deps reliably.
# - Creates/uses a local venv
# - Installs local AIGPY (editable)
# - Installs requirements (filters any AIGPY line)
# - Uses a package-safe launcher (fixes relative-import crash)
# - Collects qt_material data + all PyQt6 submodules
# - Optionally collects ffpyplayer + imageio_ffmpeg (and tries to bundle ffmpeg exe)
# - Adds a runtime hook to fix SSL module issues and bundles correct OpenSSL libs
# - Excludes conflicting libraries from ffpyplayer via a custom hook
# - Produces dist/tidal_dl_gui.app and dist/tidal_dl_gui-macos.zip
#
# UPDATED:
# - Ensures Homebrew's OpenSSL dylibs (libssl.3.dylib / libcrypto.3.dylib) are copied
#   into the .app at Contents/Frameworks.
# - Rewrites binary load paths via install_name_tool so the app always loads those
#   bundled copies using @executable_path/../Frameworks/... instead of /opt/homebrew/...
#   on the build machine. This makes the .app self-contained and runnable on another Mac.

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
${PIP} install "pyinstaller>=6.11,<6.17"

# --- Local AIGPY (editable) ---
if [[ -d "${SCRIPT_DIR}/AIGPY" ]]; then
  echo "Installing local AIGPY (editable)..."
  ${PIP} install -e "${SCRIPT_DIR}/AIGPY"
  "${PY}" - <<'PY'
import sys
try:
    import aigpy  # noqa: F401
except Exception as e:
    print("Error: AIGPY import failed:", e, file=sys.stderr)
    sys.exit(1)
print("AIGPY import OK.")
PY
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

echo "Preflight: compiling GUI python files..."
# Compile the entire GUI package tree to catch syntax errors reliably (no fragile shell globs).
if ! "${PY}" - <<PY
import compileall, os, sys
gui_dir = os.path.abspath(r"${PKG_ROOT}/gui")
print("Preflight: compiling directory:", gui_dir)
ok = compileall.compile_dir(gui_dir, force=True, quiet=1)
sys.exit(0 if ok else 1)
PY
then
  echo "Preflight byte-compilation failed. Aborting build."
  exit 1
fi

# --- Clean outputs BEFORE creating build artifacts ---
rm -rf "${DIST_PATH}" "${BUILD_PATH}" "${HOOKS_PATH}"
mkdir -p "${DIST_PATH}" "${BUILD_PATH}" "${HOOKS_PATH}"

# --- Create Custom Hooks ---
# IMPORTANT: filter ffpyplayer to remove its private OpenSSL copies.
HOOK_FFPYPLAYER="${HOOKS_PATH}/hook-ffpyplayer.py"
echo "Creating custom hook to exclude conflicting libraries: ${HOOK_FFPYPLAYER}"
cat > "${HOOK_FFPYPLAYER}" <<'PY'
# hooks/hook-ffpyplayer.py
from PyInstaller.utils.hooks import collect_all

_EXCLUDE = {"libssl.3.dylib","libcrypto.3.dylib","libssl.dylib","libcrypto.dylib"}

def _drop(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    # ffpyplayer wheels store vendor libs under ffpyplayer/__dot__dylibs/
    return name in _EXCLUDE or "/__dot__dylibs/" in path and any(x in name for x in _EXCLUDE)

def hook(hook_api):
    datas, binaries, hidden = collect_all("ffpyplayer")
    datas = [x for x in datas if not _drop(x[0])]
    binaries = [x for x in binaries if not _drop(x[0])]
    hook_api.add_datas(datas)
    hook_api.add_binaries(binaries)
    hook_api.add_imports(*hidden)
PY

# NEW: Create a hook for the entire 'tidal_dl' package to ensure all submodules are included.
# This will automatically pick up the new 'metadata' sub-package.
HOOK_TIDAL_DL="${HOOKS_PATH}/hook-tidal_dl.py"
echo "Creating package hook to force inclusion of all tidal_dl submodules: ${HOOK_TIDAL_DL}"
cat > "${HOOK_TIDAL_DL}" <<'PY'
# hooks/hook-tidal_dl.py
# Force PyInstaller to see every submodule under the 'tidal_dl' package.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("tidal_dl")
PY

# --- Optional collects and hooks ---
EXTRA_OPTS=( --collect-data qt_material --collect-submodules PyQt6 )

# Create a runtime hook to force SSL module loading (after we ensured correct libs are bundled)
RTHOOK_SSL="${BUILD_PATH}/rt_hook_ssl.py"
echo "Creating SSL runtime hook at ${RTHOOK_SSL}..."
cat > "${RTHOOK_SSL}" <<'PY'
# Ensure 'ssl' is initialized so anything relying on it (e.g., redis/requests) works under PyInstaller.
import ssl  # noqa: F401
PY
EXTRA_OPTS+=( --runtime-hook "${RTHOOK_SSL}" )

# Create a runtime hook to suppress verbose Qt plugin logging.
RTHOOK_QT_LOGGING="${BUILD_PATH}/rt_hook_qt_logging.py"
echo "Creating Qt logging runtime hook at ${RTHOOK_QT_LOGGING}..."
cat > "${RTHOOK_QT_LOGGING}" <<'PY'
import os
# Disable verbose Qt plugin logging unless a specific rule is already set for debugging.
if 'QT_LOGGING_RULES' not in os.environ:
    os.environ['QT_LOGGING_RULES'] = "qt.core.plugin.factoryloader.debug=false"
PY
EXTRA_OPTS+=( --runtime-hook "${RTHOOK_QT_LOGGING}" )

# --- Bundle the correct OpenSSL from Homebrew ---
if command -v brew &>/dev/null; then
    OPENSSL_PREFIX=$(brew --prefix openssl@3 || true)
    if [[ -n "$OPENSSL_PREFIX" && -d "$OPENSSL_PREFIX/lib" ]]; then
        echo "Found Homebrew OpenSSL. Bundling correct libssl and libcrypto."
        EXTRA_OPTS+=( --add-binary "$OPENSSL_PREFIX/lib/libssl.3.dylib:Contents/Frameworks" )
        EXTRA_OPTS+=( --add-binary "$OPENSSL_PREFIX/lib/libcrypto.3.dylib:Contents/Frameworks" )
    else
        echo "Warning: Homebrew openssl@3 not found. SSL issues may persist."
    fi
fi

# ffpyplayer (custom hook handles exclusions)
if "${PY}" -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("ffpyplayer") else 1)'; then
  echo "Optional: bundling ffpyplayer (via custom hook)."
fi

# imageio_ffmpeg (collect package + try to include real ffmpeg binary)
if "${PY}" -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("imageio_ffmpeg") else 1)'; then
  echo "Optional: bundling imageio_ffmpeg (collect-all) and attempting to include ffmpeg executable."
  EXTRA_OPTS+=( --collect-all imageio_ffmpeg )

  FFMPEG_BIN="$("${PY}" -c 'import os, imageio_ffmpeg; p = imageio_ffmpeg.get_ffmpeg_exe(); print(p if p and os.path.exists(p) else "")' || true)"
  if [[ -n "${FFMPEG_BIN:-}" && -f "${FFMPEG_BIN}" ]]; then
    echo "Found ffmpeg at: ${FFMPEG_BIN} (adding as binary)"
    EXTRA_OPTS+=( --add-binary "${FFMPEG_BIN}:." )
    RTHOOK_FFMPEG="${BUILD_PATH}/rt_hook_imageio_ffmpeg.py"
    cat > "${RTHOOK_FFMPEG}" <<'PY'
import os, sys, pathlib
root = pathlib.Path(getattr(sys, "_MEIPASS", "."))  # PyInstaller bundle dir
for name in ("ffmpeg", "ffmpeg.exe"):
    p = root / name
    if p.exists():
        os.environ["IMAGEIO_FFMPEG_EXE"] = str(p)
        break
PY
    EXTRA_OPTS+=( --runtime-hook "${RTHOOK_FFMPEG}" )
  else
    echo "Note: Could not prebundle ffmpeg binary; imageio_ffmpeg may download on first run."
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

# --- Verify bundle + zip ---
APP_BUNDLE="${DIST_PATH}/${APP_NAME}.app"
if [[ -d "${APP_BUNDLE}" ]]; then
  echo "App bundle: ${APP_BUNDLE}"
else
  echo "Error: .app bundle not found where expected: ${APP_BUNDLE}"
  exit 1
fi

# --- Post-build OpenSSL sweep & sanity checks (no bash 4 'mapfile' needed) ---
echo "Auditing bundle for stray OpenSSL libraries..."
# Remove any OpenSSL copies nested under ffpyplayer vendor dirs.
find "${APP_BUNDLE}/Contents/Frameworks" -path "*/ffpyplayer/*" \
  \( -name 'libssl*.dylib' -o -name 'libcrypto*.dylib' \) -print -delete || true

# Fallback copy + re-sign staging: ensure OpenSSL dylibs exist in Frameworks.
if [[ ( ! -f "$APP_BUNDLE/Contents/Frameworks/libssl.3.dylib" || ! -f "$APP_BUNDLE/Contents/Frameworks/libcrypto.3.dylib" ) ]]; then
  if [[ -n "$OPENSSL_PREFIX" && -f "$OPENSSL_PREFIX/lib/libssl.3.dylib" && -f "$OPENSSL_PREFIX/lib/libcrypto.3.dylib" ]]; then
    echo "Info: One or both OpenSSL dylibs missing after sweep; attempting fallback copy."
    mkdir -p "$APP_BUNDLE/Contents/Frameworks"
    cp -f "$OPENSSL_PREFIX/lib/libssl.3.dylib"    "$APP_BUNDLE/Contents/Frameworks/"
    cp -f "$OPENSSL_PREFIX/lib/libcrypto.3.dylib" "$APP_BUNDLE/Contents/Frameworks/"
  else
    echo "ERROR: OpenSSL fallback requested but Homebrew openssl@3 not available."
    exit 1
  fi
fi

# --- Repoint library load paths to bundled dylibs (make app fully self-contained) ---
# We rewrite any references to /opt/homebrew/.../libssl.3.dylib and libcrypto.3.dylib
# so that they instead point at the copies we just ensured are in Contents/Frameworks.
# We apply this to every binary / extension module under Contents/MacOS.
if [[ -n "$OPENSSL_PREFIX" && -d "$OPENSSL_PREFIX/lib" ]]; then
  SSL_SRC_PATH="$OPENSSL_PREFIX/lib/libssl.3.dylib"
  CRYPTO_SRC_PATH="$OPENSSL_PREFIX/lib/libcrypto.3.dylib"
  SSL_DST_PATH='@executable_path/../Frameworks/libssl.3.dylib'
  CRYPTO_DST_PATH='@executable_path/../Frameworks/libcrypto.3.dylib'

  echo "Patching binary load commands to use bundled OpenSSL frameworks..."
  while IFS= read -r BINFILE; do
    # Only attempt patching if it's a regular file.
    if [[ -f "$BINFILE" ]]; then
      # install_name_tool returns nonzero if the old path isn't referenced,
      # so we '|| true' to avoid aborting the build.
      install_name_tool -change "$SSL_SRC_PATH"   "$SSL_DST_PATH"    "$BINFILE" 2>/dev/null || true
      install_name_tool -change "$CRYPTO_SRC_PATH" "$CRYPTO_DST_PATH" "$BINFILE" 2>/dev/null || true
    fi
  done < <(find "${APP_BUNDLE}/Contents/MacOS" -maxdepth 1 -type f)
else
  echo "Warning: OPENSSL_PREFIX not set or not found; skipping install_name_tool path fix."
fi

# --- Ad-hoc codesign AFTER patching binaries ---
if command -v codesign >/dev/null 2>&1; then
  echo "Info: Re-signing app bundle ad-hoc..."
  codesign --force --deep -s - "$APP_BUNDLE"
fi

# Ensure the expected pair exists at top-level Frameworks.
EXPECTED_SSL="${APP_BUNDLE}/Contents/Frameworks/libssl.3.dylib"
EXPECTED_CRYPTO="${APP_BUNDLE}/Contents/Frameworks/libcrypto.3.dylib"
MISSING=0
if [[ ! -f "${EXPECTED_SSL}" ]]; then
  echo "ERROR: Missing expected ${EXPECTED_SSL}"
  MISSING=1
fi
if [[ ! -f "${EXPECTED_CRYPTO}" ]]; then
  echo "ERROR: Missing expected ${EXPECTED_CRYPTO}"
  MISSING=1
fi
if [[ ${MISSING} -ne 0 ]]; then
  echo "Failing build: required OpenSSL dylibs not found at top-level Frameworks."
  exit 1
fi

# Fail if more than one pair remains at top-level Frameworks.
SSL_COUNT="$(find "${APP_BUNDLE}/Contents/Frameworks" -maxdepth 1 -type f \
  \( -name 'libssl*.dylib' -o -name 'libcrypto*.dylib' \) | wc -l | tr -d ' ')"
if [[ "${SSL_COUNT}" -gt 2 ]]; then
  echo "ERROR: Multiple OpenSSL libraries found at top-level Frameworks:"
  find "${APP_BUNDLE}/Contents/Frameworks" -maxdepth 1 -type f \
    \( -name 'libssl*.dylib' -o -name 'libcrypto*.dylib' \)
  exit 1
fi

echo "Remaining OpenSSL libs in app:"
find "${APP_BUNDLE}/Contents/Frameworks" -maxdepth 1 -type f \
  \( -name 'libssl*.dylib' -o -name 'libcrypto*.dylib' \) -print

# --- Zip ---
( cd "${DIST_PATH}" && zip -qry9 "${APP_NAME}-macos.zip" "$(basename "${APP_BUNDLE}")" )
echo "ZIP: ${DIST_PATH}/${APP_NAME}-macos.zip"

echo "Build successful!"
