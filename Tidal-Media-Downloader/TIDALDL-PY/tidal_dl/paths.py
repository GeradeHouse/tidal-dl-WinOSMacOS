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
from aigpy import systemHelper
from typing import Any

logger = logging.getLogger(__name__)

# Define public API for the module
__all__ = [
    "getLogPath",
    "getTokenPath",
    "getProfilePath",
    "getSettingsFilePath",
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
    return os.path.join(__getBaseDirectory__(), ".tidal-dl.log")


def getTokenPath():
    """Returns the full path for the token file."""
    return os.path.join(__getBaseDirectory__(), ".tidal-dl.token.json")


def getProfilePath():
    """Returns the base directory used for profile files (settings, token, logs)."""
    # This is now consistent with __getBaseDirectory__
    return __getBaseDirectory__()


def getSettingsFilePath():
    """Returns the full path to the settings file."""
    return os.path.join(__getBaseDirectory__(), ".tidal-dl.json")


# --- Resource Path Function (for bundled assets) ---


def resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller"""
    # Normalize the relative path to use the OS-specific separator
    normalized_relative_path = os.path.normpath(relative_path)
    
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path: str = getattr(sys, "_MEIPASS")
        # Based on the .spec file, assets are placed in a 'tidal_dl' subdirectory
        # inside the bundle root.
        final_path = os.path.join(base_path, "tidal_dl", normalized_relative_path)
        return final_path
    except AttributeError:
        # Not running in a PyInstaller bundle (e.g., in development)
        # The assets are located relative to this file's directory.
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__)))
        return os.path.join(base_path, normalized_relative_path)
