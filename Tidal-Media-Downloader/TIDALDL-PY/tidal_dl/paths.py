#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :  paths.py
@Date    :  2022/06/10
@Author  :  Yaronzz
@Modified by: GeradeHouse
@Version :  1.0
@Contact :  yaronhuang@foxmail.com
@Desc    :  Manages base application paths for config, tokens, and logs.
@Modified by: Roo
"""
import os
import sys
import logging
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Define public API for the module
__all__ = [
    "getLogPath",
    "getTokenPath",
    "getProfilePath",
    "getSettingsFilePath",
    "get_user_download_path",
    "resource_path",
]

# --- Helper Functions ---


def _create_directory_if_not_exists(path: str) -> bool:
    """
    Creates a directory if it does not already exist.

    Args:
        path (str): The path to the directory.

    Returns:
        bool: True if the directory exists or was created successfully, False otherwise.
    """
    if not os.path.exists(path):
        logger.debug(f"Directory '{path}' does not exist. Attempting to create it.")
        try:
            os.makedirs(path, exist_ok=True)
            logger.info(f"Successfully created directory: '{path}'")
            return True
        except PermissionError:
            logger.error(
                f"Permission denied: Could not create directory '{path}'. "
                "Please check folder permissions.",
                exc_info=True,
            )
            return False
        except OSError as e:
            logger.error(
                f"An OS error occurred while creating directory '{path}': {e}",
                exc_info=True,
            )
            return False
    else:
        logger.debug(f"Directory '{path}' already exists.")
    return True


# --- Base Directory and File Path Functions ---


def __getBaseDirectory__():
    """
    Determines the base directory for config, token, and log files.
    This now follows platform-specific standards for user data locations.
    """
    # Highest priority: Portable mode via environment variable.
    if "PORTABLE_CONFIG_DIR" in os.environ:
        portable_dir = os.environ["PORTABLE_CONFIG_DIR"]
        logger.info(
            f"Using portable config directory from environment variable: {portable_dir}"
        )
        _create_directory_if_not_exists(portable_dir)
        return portable_dir

    # Standard locations for bundled applications (PyInstaller)
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":  # macOS
            # macOS: ~/Library/Application Support/<AppName>
            home_dir = os.path.expanduser("~")
            app_support_dir = os.path.join(
                home_dir, "Library", "Application Support", "Tidal-Media-Downloader"
            )
            logger.debug(
                f"Running on macOS. Using Application Support directory: {app_support_dir}"
            )
            _create_directory_if_not_exists(app_support_dir)
            return app_support_dir
        elif sys.platform.startswith("linux"):  # Linux
            # Linux: Use XDG Base Directory Specification
            home_dir = os.path.expanduser("~")
            # Use XDG_CONFIG_HOME if set, otherwise default to ~/.config
            config_dir = os.environ.get(
                "XDG_CONFIG_HOME", os.path.join(home_dir, ".config")
            )
            xdg_config_path = os.path.join(config_dir, "Tidal-Media-Downloader")
            logger.debug(
                f"Running on Linux. Using XDG config directory: {xdg_config_path}"
            )
            _create_directory_if_not_exists(xdg_config_path)
            return xdg_config_path
        else:  # Windows and other OS
            # Windows bundled: Portable mode (config in executable's directory)
            exe_dir = os.path.dirname(sys.executable)
            logger.debug(
                f"Running bundled app on Windows/other. Using portable directory: {exe_dir}"
            )
            return exe_dir

    # Fallback for development (unfrozen) mode: use the current project directory.
    dev_dir = os.path.abspath(".")
    logger.debug(f"Running in development mode. Using current directory: {dev_dir}")
    return dev_dir


def getLogPath():
    """Returns the full path for the log file."""
    path = os.path.join(__getBaseDirectory__(), ".tidal-dl.log")
    logger.debug(f"getLogPath() -> '{path}'")
    return path


def getTokenPath():
    """Returns the full path for the token file."""
    path = os.path.join(__getBaseDirectory__(), ".tidal-dl.token.json")
    logger.debug(f"getTokenPath() -> '{path}'")
    return path


def getProfilePath():
    """Returns the base directory used for profile files (settings, token, logs)."""
    # This is now consistent with __getBaseDirectory__
    path = __getBaseDirectory__()
    logger.debug(f"getProfilePath() -> '{path}'")
    return path


def getSettingsFilePath():
    """Returns the full path to the settings file."""
    path = os.path.join(__getBaseDirectory__(), ".tidal-dl.json")
    logger.debug(f"getSettingsFilePath() -> '{path}'")
    return path


def get_user_download_path(path_from_settings: str) -> str:
    """
    Resolves a user-provided path, anchoring relative paths to the home directory.

    This is critical for macOS .app bundles, where the current working directory
    is '/' and relative paths like './Downloads' would fail.

    Args:
        path_from_settings (str): The path string from the application settings.

    Returns:
        str: An absolute path that is safe to use for downloads.
    """
    if not path_from_settings or not isinstance(path_from_settings, str):
        path_from_settings = "Downloads"  # Fallback to a sensible default

    # If the path is already absolute, use it as is.
    if os.path.isabs(path_from_settings):
        logger.debug(f"Download path '{path_from_settings}' is absolute. Using directly.")
        return path_from_settings

    # If the path is relative, resolve it against the user's home directory.
    home_dir = os.path.expanduser("~")
    resolved_path = os.path.join(home_dir, path_from_settings)
    logger.debug(
        f"Download path '{path_from_settings}' is relative. Resolved against home "
        f"directory to '{resolved_path}'."
    )
    return resolved_path


# --- Resource Path Function (for bundled assets) ---


def resource_path(relative_path: str) -> str:
    """
    Resolve a bundled asset path for dev and PyInstaller (macOS/Win/Linux).

    On macOS, this now prefers the actual .app bundle contents first, with priority on
    the Resources dir where PyInstaller places the Python packages and our `tidal_dl`
    package at runtime in the final, zipped/distributed .app:

        <App>.app/Contents/Resources/tidal_dl/<relative_path>
        <App>.app/Contents/Resources/<relative_path>

    We then consider other bundle dirs (MacOS, Frameworks, Contents) which PyInstaller
    may use depending on mode:

        <App>.app/Contents/MacOS/tidal_dl/<relative_path>
        <App>.app/Contents/Frameworks/tidal_dl/<relative_path>
        <App>.app/Contents/_internal/tidal_dl/<relative_path>        [legacy]
        <App>.app/Contents/<relative_path>

    Finally (for backwards compatibility with your dev/test "portable" onedir layout),
    we *optionally* look for a sibling folder that used to sit next to the .app during
    development:

        dist/<app_stem>/_internal/tidal_dl/<relative_path>

    That portable root is only added if it actually exists at runtime. If the user
    drags the .app somewhere else (e.g. ~/Documents), that sibling folder will not be
    present, and we will not depend on it.

    Also supports historical call sites that pass 'assets/fonts' or 'assets/images'
    by aliasing them to 'fonts' and 'images' respectively.
    """
    normalized = os.path.normpath(relative_path).lstrip("/\\")
    logger.debug(
        f"resource_path: Received relative_path: '{relative_path}' (normalized: '{normalized}')"
    )

    # Environment snapshot for diagnostics
    frozen = getattr(sys, "frozen", False)
    meipass = getattr(sys, "_MEIPASS", None)
    cwd = os.getcwd()
    exe = getattr(sys, "executable", None)
    logger.debug(
        "resource_path: Environment => "
        f"platform='{sys.platform}', frozen={frozen}, "
        f"_MEIPASS='{meipass}', executable='{exe}', cwd='{cwd}', __file__='{__file__}'"
    )

    # NOTE: Use abspath-based uniq ONLY for *roots*; not for relative variants.
    def _uniq_abspath(seq):
        seen = set()
        out = []
        for x in seq:
            ax = os.path.abspath(x)
            if ax not in seen:
                seen.add(ax)
                out.append(ax)
        return out

    def _uniq_rel(seq):
        # Preserve relative strings as-is (no abspath!), keep order
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def _alias_variants(path_rel: str):
        # Back-compat: map 'assets/fonts' -> 'fonts', 'assets/images' -> 'images'
        variants = [path_rel]
        assets_fonts = os.path.join("assets", "fonts")
        assets_images = os.path.join("assets", "images")
        if path_rel.startswith(assets_fonts + os.sep) or path_rel == assets_fonts:
            mapped = path_rel.replace(assets_fonts, "fonts", 1)
            logger.debug(
                f"resource_path: Alias applied for fonts: '{path_rel}' -> '{mapped}'"
            )
            variants.append(mapped)
        if path_rel.startswith(assets_images + os.sep) or path_rel == assets_images:
            mapped = path_rel.replace(assets_images, "images", 1)
            logger.debug(
                f"resource_path: Alias applied for images: '{path_rel}' -> '{mapped}'"
            )
            variants.append(mapped)
        # Critically: DO NOT abspath here — keep these relative so os.path.join(root, rel) works.
        return _uniq_rel(variants)

    candidates = []
    expected_primary_path = None
    expected_relative_from_dist = None

    # --- Platform-specific root discovery ---
    if sys.platform == "darwin" and frozen:
        # Figure out bundle anchors:
        # meipass can end up pointing at .../Contents/Frameworks OR .../Contents/MacOS
        # depending on how PyInstaller decided to stage the runtime.
        # We reconstruct the .app structure around it so we can reliably find:
        #   <App>.app/Contents/Resources/tidal_dl/...
        # even if _MEIPASS changes across builds.

        contents_dir = None
        app_dir = None
        dist_dir = None
        app_stem = None

        if meipass:
            # If _MEIPASS is defined, it will be below <app>.app/Contents/<Something>
            # e.g. .../Contents/Frameworks  (observed in logs)
            # or   .../Contents/MacOS       (classic PyInstaller)
            contents_dir = os.path.abspath(os.path.join(meipass, ".."))          # .../Contents
            app_dir = os.path.dirname(contents_dir)                              # .../tidal_dl_gui.app
            dist_dir = os.path.dirname(app_dir)                                  # parent folder of .app
        else:
            # Fallback: derive from sys.executable
            # sys.executable should be .../<App>.app/Contents/MacOS/<binary>
            if exe:
                # macos_dir_guess = .../Contents/MacOS
                macos_dir_guess = os.path.abspath(os.path.dirname(exe))
                # contents_dir    = .../Contents
                contents_dir = os.path.abspath(os.path.join(macos_dir_guess, ".."))
                # app_dir         = .../<App>.app
                app_dir = os.path.dirname(contents_dir)
                # dist_dir        = parent folder that currently contains the .app
                dist_dir = os.path.dirname(app_dir)

            # And also behave *as if* meipass == <bundle>/Contents/MacOS
            meipass = os.path.join(contents_dir, "MacOS") if contents_dir else None

        if app_dir:
            # Remove .app suffix to get stem. e.g. tidal_dl_gui.app -> tidal_dl_gui
            app_stem = os.path.splitext(os.path.basename(app_dir))[0]

        logger.debug(
            "resource_path[macOS]: anchors => "
            f"contents_dir='{contents_dir}', app_dir='{app_dir}', dist_dir='{dist_dir}', "
            f"app_stem='{app_stem}', effective_meipass='{meipass}'"
        )

        # Build roots in correct priority order:

        roots = []

        # 0) Contents/Resources — this is where PyInstaller actually drops the
        #    'tidal_dl' package (including assets/icons/*.png etc.) in the final
        #    distributable .app you ship/zip. This MUST come first so that
        #    dragging the .app anywhere (~/Documents, ~/Desktop, /Applications)
        #    still finds icons/backgrounds at:
        #        <App>.app/Contents/Resources/tidal_dl/assets/...png
        if contents_dir:
            resources_dir = os.path.join(contents_dir, "Resources")
            if os.path.isdir(resources_dir):
                roots.append(resources_dir)

        # 1) Contents/MacOS — classic PyInstaller layout for many apps.
        #    Some builds keep the Python package tree here.
        if contents_dir:
            macos_dir_candidate = os.path.join(contents_dir, "MacOS")
            if os.path.isdir(macos_dir_candidate):
                roots.append(macos_dir_candidate)

        # 2) Contents/Frameworks — newer PyInstaller for windowed apps on macOS
        #    has been observed to park the runtime and Python libs here and even
        #    sets _MEIPASS to this path.
        if contents_dir:
            frameworks_dir_candidate = os.path.join(contents_dir, "Frameworks")
            if os.path.isdir(frameworks_dir_candidate):
                roots.append(frameworks_dir_candidate)

        # 3) Contents itself (rare fallback).
        if contents_dir and os.path.isdir(contents_dir):
            roots.append(contents_dir)

        # 4) Legacy "portable" sibling folder that used to sit next to the .app
        #    during dev/test, e.g.:
        #        dist/tidal_dl_gui/_internal/tidal_dl/assets/...
        #    We only add this if it *actually exists* at runtime, so production
        #    builds (copied .app with no sibling dir) won't fall back here.
        if dist_dir and app_stem:
            portable_root = os.path.join(dist_dir, app_stem)
            if os.path.isdir(portable_root):
                roots.append(portable_root)

                # For debug logging: show what we *expect* for the portable layout
                expected_primary_path = os.path.join(
                    portable_root, "_internal", "tidal_dl", normalized
                )
                expected_relative_from_dist = os.path.join(
                    app_stem, "_internal", "tidal_dl", normalized
                )

    else:
        # --- non-macOS or not frozen ---
        roots = []
        if frozen:
            # Frozen on Windows/Linux: prefer _MEIPASS first.
            if meipass:
                roots.append(meipass)
                expected_primary_path = os.path.join(meipass, "tidal_dl", normalized)
            else:
                # Fallback to the executable directory
                exe_dir = os.path.abspath(os.path.dirname(exe)) if exe else None
                if exe_dir:
                    roots.append(exe_dir)
                expected_primary_path = os.path.join(
                    exe_dir or "", "tidal_dl", normalized
                )
        else:
            # Dev mode: project dir relative to this file.
            dev_root = os.path.abspath(os.path.dirname(__file__))
            roots.append(dev_root)
            expected_primary_path = os.path.join(dev_root, normalized)

    # De-duplicate root list (order-preserving for first occurrence of each abs path)
    roots = _uniq_abspath([r for r in roots if r])
    logger.debug(f"resource_path: Candidate roots (ordered, unique): {roots}")

    if expected_primary_path:
        logger.debug(
            f"resource_path: EXPECTED PRIMARY ASSET PATH (portable) => '{expected_primary_path}'"
        )
        if sys.platform == "darwin" and expected_relative_from_dist:
            logger.debug(
                f"resource_path: EXPECTED RELATIVE (from dist folder) => '{expected_relative_from_dist}'"
            )

    # Build candidate paths under each root
    for root in roots:
        for rel in _alias_variants(normalized):
            if sys.platform == "darwin" and frozen:
                # For macOS frozen builds we try three forms under each root:
                #
                #   1) <root>/tidal_dl/<rel>
                #      This covers the real final .app layout where assets live at:
                #          <App>.app/Contents/Resources/tidal_dl/assets/icons/menu.png
                #      while callers ask for "assets/icons/menu.png".
                #      Joining root + "tidal_dl" + rel produces that exact path.
                #
                #   2) <root>/<rel>
                #      This covers direct lookups when rel already starts with
                #      'tidal_dl/...', or if PyInstaller happens to flatten the
                #      package so assets land directly under <root>/assets/... .
                #
                #   3) <root>/_internal/tidal_dl/<rel>
                #      This is the legacy portable onedir layout:
                #          dist/tidal_dl_gui/_internal/tidal_dl/assets/...
                #      We keep it last for backwards compatibility during
                #      development runs from an unpacked dist/.
                candidates.append(os.path.join(root, "tidal_dl", rel))
                candidates.append(os.path.join(root, rel))
                candidates.append(os.path.join(root, "_internal", "tidal_dl", rel))
            elif frozen:
                # Windows/Linux frozen onedir/onefile
                # NOTE: Per user request, do NOT change this ordering/logic
                # from the working behavior. We keep both candidates so that:
                #   root/tidal_dl/<rel>   (normal PyInstaller --add-data layout)
                #   root/<rel>            (in case rel already starts with tidal_dl/)
                candidates.append(os.path.join(root, "tidal_dl", rel))
                candidates.append(os.path.join(root, rel))
            else:
                # Dev mode
                candidates.append(os.path.join(root, rel))

    # To avoid excessive spam, preview the first 10 candidates and total count
    if candidates:
        preview_count = min(10, len(candidates))
        logger.debug(
            f"resource_path: Prepared {len(candidates)} candidate paths. "
            f"Preview (first {preview_count}): " + "; ".join(candidates[:preview_count])
        )
    else:
        logger.debug("resource_path: No candidates were prepared (unexpected).")

    # Return the first existing candidate
    for path in candidates:
        if os.path.exists(path):
            logger.debug(
                f"resource_path: Resolved '{relative_path}' -> '{path}' (exists=True)"
            )
            return path

    # Nothing found — log the first tried path for easier triage and return it
    first_tried = (
        candidates[0]
        if candidates
        else os.path.join("_internal", "tidal_dl", normalized)
    )
    logger.warning(
        "resource_path: None of the candidate paths exist for "
        f"'{relative_path}'. First tried: '{first_tried}'. "
        "This usually indicates the packaging layout does not match the expected portable structure."
    )
    logger.debug(
        f"resource_path: FINAL (non-existent) return for '{relative_path}' -> '{first_tried}'"
    )
    return first_tried