#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_settings_handlers.py
@Time    :   14-04-2025
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Event handlers and logic for the settings page.
             Contains functions for loading, saving, and interacting with settings.
"""

import logging
import os
import json
import base64
from typing import TYPE_CHECKING, Optional, Any, cast

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QFileDialog

from .. import apiKey
from ..enums import Type
from ..printf import Printf
from ..settings import SETTINGS, TOKEN
from ..tidal import AudioQuality, TIDAL_API
from ..login import saveToken, loginByConfig
from .. import paths
from .gui_custom_dialog import CustomQMessageBox

if TYPE_CHECKING:
    from tidal_dl.gui.gui_settings import SettingsPage

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (more verbose GUI output for downloads)
# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from tidal_dl.gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

def load_initial_settings(self: "SettingsPage"):
    """
    Populates all UI controls with current values from the global SETTINGS object.
    Uses placeholder text for default format strings.
    """
    # Assertions to assure Pylance that these widgets are not None
    assert self.cmbApiKeyIndex is not None
    assert self.chk_tidal_start_collapsed is not None
    assert self.spotifyClientIdInput is not None
    assert self.spotifyClientSecretInput is not None
    assert self.chkAutoSpotifyLogin is not None
    assert self.chkSpotifyUsePlaylistFolders is not None
    assert self.downloadPathInput is not None
    assert self.downloadPathEdit is not None
    assert self.albumFolderFormatEdit is not None
    assert self.playlistFolderFormatEdit is not None
    assert self.trackFileFormatEdit is not None
    assert self.chkCheckExist is not None
    assert self.chkIncludeEP is not None
    assert self.chkSaveCovers is not None
    assert self.chkMultiThread is not None
    assert self.chkDownloadDelay is not None
    assert self.chkUsePlaylistFolder is not None
    assert self.cmbLanguage is not None
    assert self.spinFontSize is not None
    assert self.chkLyricFile is not None
    assert self.chkShowProgress is not None
    assert self.chkShowTrackInfo is not None
    assert self.chkSaveAlbumInfo is not None
    assert self.chkShowPlaylistIcons is not None
    assert self.spinPlaylistIconSize is not None
    assert self.chkUseCamelotKeyNotation is not None
    assert self.cache_path_lineEdit is not None
    assert self.cache_ttl_spinBox is not None

    try:
        # Account/API - Load API key index with fallback to 0 if not set
        current_api_index = getattr(SETTINGS, "apiKeyIndex", 0)
        combo_index = self.cmbApiKeyIndex.findData(current_api_index)
        if combo_index == -1:
            combo_index = 0
            SETTINGS.apiKeyIndex = 0
        self.cmbApiKeyIndex.setCurrentIndex(combo_index)
        self.chk_tidal_start_collapsed.setChecked(bool(getattr(SETTINGS, "tidalStartCollapsed", False)))

        # Spotify API credentials - Essential for Spotify playlist import functionality
        self.spotifyClientIdInput.setText(getattr(SETTINGS, "spotifyClientId", ""))
        self.spotifyClientSecretInput.setText(
            getattr(SETTINGS, "spotifyClientSecret", "")
        )
        # Auto Spotify Login
        self.chkAutoSpotifyLogin.setChecked(
            bool(getattr(SETTINGS, "autoSpotifyLogin", False))
        )
        self.chkSpotifyUsePlaylistFolders.setChecked(bool(getattr(SETTINGS, "spotifyUsePlaylistFolders", True)))

        # Paths & Formats
        self.downloadPathInput.setText(SETTINGS.downloadPath)
        self.downloadPathEdit.setText(SETTINGS.downloadPath)

        # Album Folder Format
        album_format = SETTINGS.albumFolderFormat
        if not album_format:  # If empty or None (meaning use default)
            self.albumFolderFormatEdit.setText("")  # Ensure text is empty
            self.albumFolderFormatEdit.setPlaceholderText(
                SETTINGS.getDefaultPathFormat(Type.Album)
            )
        else:
            self.albumFolderFormatEdit.setText(album_format)
            self.albumFolderFormatEdit.setPlaceholderText(
                ""
            )  # Clear placeholder if user has text

        # Playlist Folder Format
        playlist_format = SETTINGS.playlistFolderFormat
        if not playlist_format:
            self.playlistFolderFormatEdit.setText("")
            self.playlistFolderFormatEdit.setPlaceholderText(
                SETTINGS.getDefaultPathFormat(Type.Playlist)
            )
        else:
            self.playlistFolderFormatEdit.setText(playlist_format)
            self.playlistFolderFormatEdit.setPlaceholderText("")

        # Track File Format
        track_format = SETTINGS.trackFileFormat
        if not track_format:
            self.trackFileFormatEdit.setText("")
            self.trackFileFormatEdit.setPlaceholderText(
                SETTINGS.getDefaultPathFormat(Type.Track)
            )
        else:
            self.trackFileFormatEdit.setText(track_format)
            self.trackFileFormatEdit.setPlaceholderText("")

        # --- Download Options - Use getattr for robustness ---
        self.chkCheckExist.setChecked(bool(getattr(SETTINGS, "checkExist", False)))
        self.chkIncludeEP.setChecked(bool(getattr(SETTINGS, "includeEP", False)))
        self.chkSaveCovers.setChecked(bool(getattr(SETTINGS, "saveCovers", False)))
        self.chkMultiThread.setChecked(bool(getattr(SETTINGS, "multiThread", False)))
        self.chkDownloadDelay.setChecked(bool(getattr(SETTINGS, "downloadDelay", False)))
        self.chkUsePlaylistFolder.setChecked(
            bool(getattr(SETTINGS, "usePlaylistFolder", False))
        )
        # --- End Download Options ---

        # --- UI & Behavior - Use getattr for robustness ---
        self.cmbLanguage.setCurrentText(getattr(SETTINGS, "language", "English"))
        self.spinFontSize.setValue(getattr(SETTINGS, "fontSize", 11))
        self.chkLyricFile.setChecked(bool(getattr(SETTINGS, "lyricFile", False)))
        self.chkShowProgress.setChecked(bool(getattr(SETTINGS, "showProgress", False)))
        self.chkShowTrackInfo.setChecked(bool(getattr(SETTINGS, "showTrackInfo", False)))
        self.chkSaveAlbumInfo.setChecked(bool(getattr(SETTINGS, "saveAlbumInfo", False)))
        self.chkShowPlaylistIcons.setChecked(bool(getattr(SETTINGS, "showPlaylistIcons", True)))
        self.spinPlaylistIconSize.setValue(getattr(SETTINGS, "playlistIconSize", 35))
        self.spinPlaylistIconSize.setEnabled(self.chkShowPlaylistIcons.isChecked())
        self.chkUseCamelotKeyNotation.setChecked(
            bool(getattr(SETTINGS, "useCamelotKeyNotation", False))
        )
        # --- End UI & Behavior ---

        # Load Cache Settings
        # Load Cache Settings - Handle None/empty path gracefully
        cache_path = getattr(
            SETTINGS, "playlistCoverCachePath", None
        )  # Use getattr for safety
        self.cache_path_lineEdit.setText(cache_path if cache_path else "")
        self.cache_path_lineEdit.setPlaceholderText(
            "Leave empty for default location"
        )  # Add placeholder
        self.cache_ttl_spinBox.setValue(
            getattr(SETTINGS, "playlistCoverCacheTTL", 7)
        )  # Default to 7 if missing

        # --- Debugging: Log loaded boolean values ---
        logger.debug(
            f"Loaded Settings - checkExist: {self.chkCheckExist.isChecked()} (from {getattr(SETTINGS, 'checkExist', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - includeEP: {self.chkIncludeEP.isChecked()} (from {getattr(SETTINGS, 'includeEP', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - saveCovers: {self.chkSaveCovers.isChecked()} (from {getattr(SETTINGS, 'saveCovers', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - multiThread: {self.chkMultiThread.isChecked()} (from {getattr(SETTINGS, 'multiThread', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - downloadDelay: {self.chkDownloadDelay.isChecked()} (from {getattr(SETTINGS, 'downloadDelay', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - usePlaylistFolder: {self.chkUsePlaylistFolder.isChecked()} (from {getattr(SETTINGS, 'usePlaylistFolder', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - lyricFile: {self.chkLyricFile.isChecked()} (from {getattr(SETTINGS, 'lyricFile', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - showProgress: {self.chkShowProgress.isChecked()} (from {getattr(SETTINGS, 'showProgress', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - showTrackInfo: {self.chkShowTrackInfo.isChecked()} (from {getattr(SETTINGS, 'showTrackInfo', 'N/A')})"
        )
        logger.debug(
            f"Loaded Settings - saveAlbumInfo: {self.chkSaveAlbumInfo.isChecked()} (from {getattr(SETTINGS, 'saveAlbumInfo', 'N/A')})"
        )
        # --- End Debugging ---

    except Exception as e:
        # Log error but don't crash - use defaults if settings can't be loaded
        logger.error(f"Error loading initial settings: {e}", exc_info=True)
        CustomQMessageBox.warning(
            self, "Settings Load Error", "Load Error", f"Could not load all settings: {e}"
        )


def update_account_button(self: "SettingsPage"):
    """
    Updates the account button text based on the current login state.

    This method checks if the user is logged into Tidal and updates the account
    button accordingly, showing either "Connect" (when logged out) or
    "Disconnect" (when logged in).
    """
    assert self.btnAccount is not None
    from tidal_dl.events import loginByConfig

    if loginByConfig():
        username = TOKEN.userid or "Account"
        self.btnAccount.setText(f"Disconnect ({username})")
    else:
        # User is not logged in - show connect option
        self.btnAccount.setText("Connect")


def toggle_account(self: "SettingsPage"):
    """
    Handles the Tidal account login or logout process.

    This method is triggered when the user clicks the account button. It will:
    - If not logged in: Initiate the device code login flow using Tidal's OAuth process
    - If logged in: Log the user out and reset the account state

    The login process uses a device code authentication flow where the user is
    presented with a URL to visit and a code to enter on the Tidal website.
    """
    from tidal_dl.events import loginByConfig

    if not loginByConfig():
        # Not logged in: delegate to the AuthHandler to start the web login flow.
        # The handler will manage getting the URL, showing the dialog, and polling.
        if self.auth_handler:
            self.auth_handler.start_tidal_web_login()
        else:
            CustomQMessageBox.critical(
                self, "Login Error", "Authentication handler is not available."
            )
    else:
        # User is already logged in - perform logout
        # Logout from the Tidal API - No explicit logout in API, clear local credentials
        SETTINGS.apiKeyIndex = 0
        # --- Corrected: Modify TOKEN object, not SETTINGS ---
        TOKEN.accessToken = None
        TOKEN.refreshToken = None
        TOKEN.userid = None
        TOKEN.countryCode = None
        TOKEN.expiresAfter = 0
        TOKEN.save()  # Save the cleared credentials
        # --- End Correction ---
        # Show confirmation message to user
        CustomQMessageBox.information(
            self, "Logout", "Logout Complete", "You have been disconnected."
        )

    # Update the button text to reflect the new login state
    self.updateAccountButton()


def _browse_cache_path(self: "SettingsPage"):
    """Opens a dialog to select the cache directory."""
    assert self.cache_path_lineEdit is not None
    directory = QFileDialog.getExistingDirectory(
        self, "Select Cache Directory", os.path.expanduser("~")
    )
    if directory:
        self.cache_path_lineEdit.setText(directory)


def browse_directory(self: "SettingsPage"):
    """
    Opens a directory selection dialog for choosing the download location.

    This method is connected to the "Browse" button next to the download path field.
    It opens the system's native folder selection dialog, starting at the current
    download path if one is set, and updates both the display field and the editable
    path setting when a folder is selected.
    """
    assert self.downloadPathInput is not None
    assert self.downloadPathEdit is not None
    # Open the system's native folder selection dialog
    # Start at the current download directory if one is set
    current_path = self.downloadPathInput.text() or os.path.expanduser("~")
    directory = QtWidgets.QFileDialog.getExistingDirectory(
        self, "Select Download Directory", current_path
    )

    # Only update paths if the user selected a directory (not canceled)
    if directory:
        # Update both the display-only field and the editable path field
        self.downloadPathInput.setText(directory)
        self.downloadPathEdit.setText(directory)


def save_settings(self: "SettingsPage"):
    """
    Saves all settings from the UI controls to the global SETTINGS object.
    """
    # Assertions to assure Pylance that these widgets are not None
    assert self.cmbApiKeyIndex is not None
    assert self.chk_tidal_start_collapsed is not None
    assert self.spotifyClientIdInput is not None
    assert self.spotifyClientSecretInput is not None
    assert self.chkAutoSpotifyLogin is not None
    assert self.chkSpotifyUsePlaylistFolders is not None
    assert self.downloadPathEdit is not None
    assert self.albumFolderFormatEdit is not None
    assert self.playlistFolderFormatEdit is not None
    assert self.trackFileFormatEdit is not None
    assert self.chkCheckExist is not None
    assert self.chkIncludeEP is not None
    assert self.chkSaveCovers is not None
    assert self.chkMultiThread is not None
    assert self.chkDownloadDelay is not None
    assert self.chkUsePlaylistFolder is not None
    assert self.cmbLanguage is not None
    assert self.spinFontSize is not None
    assert self.chkLyricFile is not None
    assert self.chkShowProgress is not None
    assert self.chkShowTrackInfo is not None
    assert self.chkSaveAlbumInfo is not None
    assert self.chkShowPlaylistIcons is not None
    assert self.spinPlaylistIconSize is not None
    assert self.chkUseCamelotKeyNotation is not None
    assert self.cache_path_lineEdit is not None
    assert self.cache_ttl_spinBox is not None

    try:
        # Store current Spotify credentials before updating
        old_spotify_client_id = getattr(SETTINGS, "spotifyClientId", "")
        old_spotify_client_secret = getattr(SETTINGS, "spotifyClientSecret", "")

        old_api_key_index_raw = getattr(SETTINGS, "apiKeyIndex", 0)
        old_api_key_index = old_api_key_index_raw if isinstance(old_api_key_index_raw, int) else 0

        # Read values from widgets and save to SETTINGS object
        # --- Account/API Settings ---
        selected_api_key_index = self.cmbApiKeyIndex.currentData()
        if not isinstance(selected_api_key_index, int):
            selected_api_key_index = 0
        SETTINGS.apiKeyIndex = selected_api_key_index
        TIDAL_API.apiKey = apiKey.getItem(selected_api_key_index)
        SETTINGS.tidalStartCollapsed = self.chk_tidal_start_collapsed.isChecked()

        # --- Spotify API Integration ---
        new_spotify_client_id = self.spotifyClientIdInput.text()
        new_spotify_client_secret = self.spotifyClientSecretInput.text()
        SETTINGS.spotifyClientId = new_spotify_client_id
        SETTINGS.spotifyClientSecret = new_spotify_client_secret
        # Auto Spotify Login
        SETTINGS.autoSpotifyLogin = self.chkAutoSpotifyLogin.isChecked()
        SETTINGS.spotifyUsePlaylistFolders = self.chkSpotifyUsePlaylistFolders.isChecked()

        # --- Download Paths & Format Settings ---
        SETTINGS.downloadPath = self.downloadPathEdit.text()
        SETTINGS.albumFolderFormat = self.albumFolderFormatEdit.text()
        SETTINGS.playlistFolderFormat = self.playlistFolderFormatEdit.text()
        SETTINGS.trackFileFormat = self.trackFileFormatEdit.text()

        # --- Download Behavior Options ---
        SETTINGS.checkExist = self.chkCheckExist.isChecked()
        SETTINGS.includeEP = self.chkIncludeEP.isChecked()
        SETTINGS.saveCovers = self.chkSaveCovers.isChecked()
        SETTINGS.multiThread = self.chkMultiThread.isChecked()
        SETTINGS.downloadDelay = self.chkDownloadDelay.isChecked()
        SETTINGS.usePlaylistFolder = self.chkUsePlaylistFolder.isChecked()

        # --- UI & App Behavior Settings ---
        SETTINGS.language = self.cmbLanguage.currentText()
        SETTINGS.fontSize = self.spinFontSize.value()
        SETTINGS.lyricFile = self.chkLyricFile.isChecked()
        SETTINGS.showProgress = self.chkShowProgress.isChecked()
        SETTINGS.showTrackInfo = self.chkShowTrackInfo.isChecked()
        old_show_playlist_icons = bool(getattr(SETTINGS, "showPlaylistIcons", True))
        old_playlist_icon_size_raw = getattr(SETTINGS, "playlistIconSize", 25)
        old_playlist_icon_size = (
            old_playlist_icon_size_raw
            if isinstance(old_playlist_icon_size_raw, int)
            else 25
        )
        old_use_camelot_key_notation = bool(
            getattr(SETTINGS, "useCamelotKeyNotation", False)
        )

        SETTINGS.saveAlbumInfo = self.chkSaveAlbumInfo.isChecked()
        SETTINGS.showPlaylistIcons = self.chkShowPlaylistIcons.isChecked()
        SETTINGS.playlistIconSize = self.spinPlaylistIconSize.value()
        SETTINGS.useCamelotKeyNotation = self.chkUseCamelotKeyNotation.isChecked()
        key_notation_changed = (
            SETTINGS.useCamelotKeyNotation != old_use_camelot_key_notation
        )
        playlist_display_settings_changed = (
            SETTINGS.showPlaylistIcons != old_show_playlist_icons
            or SETTINGS.playlistIconSize != old_playlist_icon_size
        )

        # --- Cache Settings ---
        # Save Cache Settings - Save None if path is empty
        cache_path_text = self.cache_path_lineEdit.text().strip()
        SETTINGS.playlistCoverCachePath = (
            cache_path_text if cache_path_text else None
        )
        SETTINGS.playlistCoverCacheTTL = self.cache_ttl_spinBox.value()

        # Persist settings to storage
        SETTINGS.save()
        logger.debug("Settings saved to storage")
        logger.debug(f"Saved download path: {SETTINGS.downloadPath}")

        # Emit the fontSizeChanged signal with the new font size
        self.fontSizeChanged.emit(SETTINGS.fontSize)
        if key_notation_changed or playlist_display_settings_changed:
            self.playlistDisplaySettingsChanged.emit()
        # --- Debugging: Log saved boolean values ---
        logger.debug(f"Saved Settings - checkExist: {SETTINGS.checkExist}")
        logger.debug(f"Saved Settings - includeEP: {SETTINGS.includeEP}")
        logger.debug(f"Saved Settings - saveCovers: {SETTINGS.saveCovers}")
        logger.debug(f"Saved Settings - multiThread: {SETTINGS.multiThread}")
        logger.debug(f"Saved Settings - downloadDelay: {SETTINGS.downloadDelay}")
        logger.debug(
            f"Saved Settings - usePlaylistFolder: {SETTINGS.usePlaylistFolder}"
        )
        logger.debug(f"Saved Settings - lyricFile: {SETTINGS.lyricFile}")
        logger.debug(f"Saved Settings - showProgress: {SETTINGS.showProgress}")
        logger.debug(f"Saved Settings - showTrackInfo: {SETTINGS.showTrackInfo}")
        logger.debug(f"Saved Settings - saveAlbumInfo: {SETTINGS.saveAlbumInfo}")
        # --- End Debugging ---

        # Check if Spotify credentials were added or changed
        spotify_creds_changed = False
        if (new_spotify_client_id and new_spotify_client_secret) and (
            new_spotify_client_id != old_spotify_client_id
            or new_spotify_client_secret != old_spotify_client_secret
        ):
            spotify_creds_changed = True
            logger.info("Spotify credentials updated in settings.")

        api_profile_changed = selected_api_key_index != old_api_key_index
        if api_profile_changed:
            TOKEN.accessToken = None
            TOKEN.refreshToken = None
            TOKEN.userid = None
            TOKEN.countryCode = None
            TOKEN.expiresAfter = 0
            TOKEN.apiKeyIndex = selected_api_key_index
            TOKEN.save()
            selected_profile = apiKey.getItem(selected_api_key_index)
            logger.info(
                "TIDAL API key profile changed to %s. Stored TIDAL token was cleared; re-login is required.",
                selected_profile.get("platform", "Unknown"),
            )

        # Show confirmation message to user
        profile_message = ""
        if api_profile_changed:
            profile_message = "\n\nTIDAL API profile changed. Please log in again so the new profile is used."
        CustomQMessageBox.information(
            self, "Settings Saved", f"Settings have been saved and applied.{profile_message}"
        )

        # Emit signal to notify MainView to return to main menu
        self.settingsSavedAndClosed.emit()

        # Emit signal if Spotify credentials were added or changed
        if spotify_creds_changed:
            logger.debug("Emitting spotifyCredentialsUpdated signal.")
            self.spotifyCredentialsUpdated.emit()

    except Exception as e:
        # Show error dialog if settings couldn't be saved
        CustomQMessageBox.critical(self, "Error", f"Error saving settings: {e}")


def toggle_spotify_help(self: "SettingsPage"):
    """Toggles the visibility of the Spotify help instructions."""
    # Assertions to assure Pylance that these widgets are not None
    assert self.scrollArea is not None
    assert self.spotify_section is not None
    assert self.lblSpotifyHelp is not None
    assert self.btnSpotifyHelp is not None

    # Use the stored self.scrollArea instance
    current_scroll_area = self.scrollArea
    # Add checks for spotify_section and its content_layout before accessing sizeHint
    spotify_section_size_hint_str = "N/A"
    if self.spotify_section:
        spotify_section_size_hint_str = str(self.spotify_section.sizeHint())

    if current_scroll_area:
        scroll_widget: Optional[QtWidgets.QWidget] = current_scroll_area.widget()
        scrollbar = current_scroll_area.verticalScrollBar()
        scrollbar_visible = (
            scrollbar.isVisible() if scrollbar else False
        )  # Check visibility safely
        logger.debug(
            f"[toggleSpotifyHelp] Before toggle: lblSpotifyHelp.isVisible={self.lblSpotifyHelp.isVisible()}, spotify_section.sizeHint={spotify_section_size_hint_str}, "
            f'scroll_widget.sizeHint={scroll_widget.sizeHint() if scroll_widget else "N/A"}, scroll_area.verticalScrollBar.isVisible={scrollbar_visible}'
        )
    else:
        logger.warning(
            "[toggleSpotifyHelp] self.scrollArea is None at the start of toggleSpotifyHelp."
        )
        logger.debug(
            f"[toggleSpotifyHelp] Before toggle: lblSpotifyHelp.isVisible={self.lblSpotifyHelp.isVisible()}, spotify_section.sizeHint={spotify_section_size_hint_str}, scroll_widget=N/A, scroll_area=None"
        )

    current_visibility = self.lblSpotifyHelp.isVisible()
    self.lblSpotifyHelp.setVisible(not current_visibility)

    # If the section is currently expanded, tell it to update its content height.
    # The CollapsibleSection's internal logic will handle adjustSize and animation.
    if self.spotify_section and self.spotify_section.is_expanded:
        self.spotify_section.updateContentHeight()
    # If the section is collapsed, the change will be accounted for when it's next expanded.

    # Log post-toggle state
    # logger.debug("[toggleSpotifyHelp] POST-TOGGLE DEBUG:") # Original detailed logging can be restored if needed
    logger.debug(
        f"[toggleSpotifyHelp] lblSpotifyHelp geometry: {self.lblSpotifyHelp.geometry()}"
    )
    spotify_section_geometry_str = (
        str(self.spotify_section.geometry()) if self.spotify_section else "N/A"
    )
    logger.debug(
        f"[toggleSpotifyHelp] spotify_section geometry: {spotify_section_geometry_str}"
    )
    main_layout_geometry_str = (
        str(self.mainLayout.geometry()) if self.mainLayout else "N/A"
    )
    logger.debug(
        f"[toggleSpotifyHelp] mainLayout geometry: {main_layout_geometry_str}"
    )
    logger.debug(
        f"[toggleSpotifyHelp] scrollWidget exists: {hasattr(self, 'scrollWidget') and self.scrollWidget is not None}"
    )

    # Update button text based on new visibility
    if self.lblSpotifyHelp.isVisible():  # Check the new state
        self.btnSpotifyHelp.setText(
            "How to get Spotify Client ID and Secret? (Hide)"
        )
    else:
        self.btnSpotifyHelp.setText(
            "How to get Spotify Client ID and Secret? (Show)"
        )

    # The CollapsibleSection's updateContentHeight and subsequent animation completion
    # should handle necessary geometry updates that propagate to the scroll area.

    # Re-check spotify_section_size_hint_str for the "After toggle" log
    spotify_section_size_hint_str_after = "N/A"
    if self.spotify_section:
        spotify_section_size_hint_str_after = str(self.spotify_section.sizeHint())

    if self.scrollArea:  # Use self.scrollArea for the "after" log
        scroll_widget_after: Optional[QtWidgets.QWidget] = self.scrollArea.widget()
        scrollbar_after = self.scrollArea.verticalScrollBar()
        scrollbar_visible_after = (
            scrollbar_after.isVisible() if scrollbar_after else False
        )  # Check visibility safely
        logger.debug(
            f"[toggleSpotifyHelp] After toggle: lblSpotifyHelp.isVisible={self.lblSpotifyHelp.isVisible()}, spotify_section.sizeHint={spotify_section_size_hint_str_after}, "
            f'scroll_widget.sizeHint={scroll_widget_after.sizeHint() if scroll_widget_after else "N/A"}, scroll_area.verticalScrollBar.isVisible={scrollbar_visible_after}'
        )
    else:
        logger.warning(
            "[toggleSpotifyHelp] self.scrollArea is None after toggle for logging."
        )
        logger.debug(
            f"[toggleSpotifyHelp] After toggle: lblSpotifyHelp.isVisible={self.lblSpotifyHelp.isVisible()}, spotify_section.sizeHint={spotify_section_size_hint_str_after}, scroll_widget=N/A, scroll_area=None"
        )

    # Optional: Change button text based on state


def toggle_path_format_help(self: "SettingsPage"):
    """Toggles the visibility of the path formatting help instructions."""
    # Assertions to assure Pylance that these widgets are not None
    assert self.lblPathFormatHelp is not None
    assert self.btnPathFormatHelp is not None
    assert self.paths_section is not None
    assert self.scrollArea is not None

    current_visibility = self.lblPathFormatHelp.isVisible()
    self.lblPathFormatHelp.setVisible(not current_visibility)

    if self.lblPathFormatHelp.isVisible():
        self.btnPathFormatHelp.setText(
            "Formatting Placeholders (Hide)"
        )
    else:
        self.btnPathFormatHelp.setText(
            "Formatting Placeholders (Show)"
        )

    # If the section is currently expanded, tell it to update its content height.
    if self.paths_section and self.paths_section.is_expanded:
        self.paths_section.updateContentHeight()
    # If the section is collapsed, the change will be accounted for when it's next expanded.

    # Optional: Log geometry changes for debugging if needed
    if self.scrollArea:  # Check if scrollArea exists
        scroll_widget_after: Optional[QtWidgets.QWidget] = self.scrollArea.widget()
        # Safely access sizeHint only if scroll_widget_after is not None
        scroll_widget_size_hint_str = (
            str(scroll_widget_after.sizeHint()) if scroll_widget_after else "N/A"
        )
        paths_section_size_hint_str = (
            str(self.paths_section.sizeHint()) if self.paths_section else "N/A"
        )

        logger.debug(
            f"[togglePathFormatHelp] After toggle: lblPathFormatHelp.isVisible={self.lblPathFormatHelp.isVisible()}, "
            f'paths_section.sizeHint={paths_section_size_hint_str}, '
            f"scroll_widget.sizeHint={scroll_widget_size_hint_str}"
        )
    else:
        logger.debug(
            f"[togglePathFormatHelp] After toggle: lblPathFormatHelp.isVisible={self.lblPathFormatHelp.isVisible()}, "
            f"scroll_widget.sizeHint=N/A (scrollArea is None)"
        )
        logger.debug(
            f"[togglePathFormatHelp] After toggle: lblPathFormatHelp.isVisible=True, "
            f"scroll_widget.sizeHint=N/A (scrollArea is None)"
        )


def _handle_audio_quality_changed(self: "SettingsPage", index: int):
    """Updates the global audio quality setting."""
    if not self.audio_combo:
        return  # Safety check
    selected_quality = self.audio_combo.itemData(index)
    if isinstance(selected_quality, AudioQuality):
        if SETTINGS.audioQuality != selected_quality:
            SETTINGS.audioQuality = selected_quality
            SETTINGS.save()
            logger.info(f"Default audio quality set to {selected_quality.name}")
            logger.info(f"Default audio quality set to {selected_quality.name}")  # type: ignore
    else:
        logger.error(
            f"Invalid data type retrieved from audio quality combobox at index {index}: {type(selected_quality)}"
        )


# --- NEW: Manual Token Handlers ---

def browse_token_file(self: "SettingsPage"):
    """
    Opens a file dialog to select a token JSON file and populates the input field with its content.
    """
    assert self.accessTokenInput is not None
    
    fname, _ = QFileDialog.getOpenFileName(
        self, 
        "Open Token File", 
        "", 
        "JSON Files (*.json);;All Files (*)"
    )
    
    if fname:
        try:
            with open(fname, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # Just set the content directly. login_with_token will handle parsing.
            self.accessTokenInput.setText(content)
            
        except Exception as e:
            logger.error(f"Error reading token file: {e}")
            CustomQMessageBox.critical(self, "Error", f"Failed to read file: {e}")


def login_with_token(self: "SettingsPage"):
    """
    Parses the token input (Raw string, JSON, or Base64-encoded JSON),
    updates the global TOKEN settings, and attempts to log in.
    """
    assert self.accessTokenInput is not None
    
    input_str = self.accessTokenInput.text().strip()
    if not input_str:
        CustomQMessageBox.warning(self, "Input Error", "Please enter a token string or browse for a file.")
        return

    # 1. Try to decode/parse the input into a dictionary
    token_data = {}
    
    # Is it Base64 encoded JSON? (Standard .tidal-dl.token.json format)
    try:
        # Try decoding base64
        decoded_bytes = base64.b64decode(input_str)
        decoded_str = decoded_bytes.decode('utf-8')
        # Try parsing result as JSON
        json_data = json.loads(decoded_str)
        if isinstance(json_data, dict):
            token_data = json_data
    except Exception:
        # Not Base64 encoded JSON.
        pass

    # If not found yet, is it plain JSON?
    if not token_data:
        try:
            json_data = json.loads(input_str)
            if isinstance(json_data, dict):
                token_data = json_data
        except json.JSONDecodeError:
            pass

    # 2. Update global TOKEN object
    # Cast TOKEN to Any to avoid Pylance errors due to None initialization in settings.py
    token_obj = cast(Any, TOKEN)

    if token_data:
        # We found structured data
        logger.info("Parsed structured token data from input.")
        
        # Update fields if they exist in the data
        access_token = token_data.get('accessToken') or token_data.get('access_token')
        refresh_token = token_data.get('refreshToken') or token_data.get('refresh_token')
        user_id = token_data.get('userid') or token_data.get('userId') or token_data.get('user_id')
        country_code = token_data.get('countryCode') or token_data.get('country_code')
        expires_after = token_data.get('expiresAfter')

        if expires_after is None:
            created_at = token_data.get('created_at') or token_data.get('createdAt')
            expires_in = token_data.get('expires_in') or token_data.get('expiresIn')
            if created_at is not None and expires_in is not None:
                try:
                    expires_after = int(created_at) + int(expires_in)
                except (TypeError, ValueError):
                    expires_after = None
            elif expires_in is not None:
                try:
                    import time
                    expires_after = int(time.time()) + int(expires_in)
                except (TypeError, ValueError):
                    expires_after = None

        if access_token:
            token_obj.accessToken = access_token
        if refresh_token:
            token_obj.refreshToken = refresh_token
        if user_id:
            token_obj.userid = user_id
        if country_code:
            token_obj.countryCode = country_code
        if expires_after is not None:
            token_obj.expiresAfter = expires_after
    else:
        # Assume raw access token string
        logger.info("Treating input as raw access token.")
        token_obj.accessToken = input_str
        # If raw token provided, we don't have a refresh token. 
        # We shouldn't necessarily clear the existing one unless we want to force a clean state.
        # But usually manual entry implies "use this specific credential".
        # Let's keep it simple: just set access token.

    selected_api_key_index = self.cmbApiKeyIndex.currentData() if self.cmbApiKeyIndex else None
    if not isinstance(selected_api_key_index, int):
        selected_api_key_index = getattr(SETTINGS, "apiKeyIndex", 0)
    if not isinstance(selected_api_key_index, int):
        selected_api_key_index = 0
    SETTINGS.apiKeyIndex = selected_api_key_index
    TIDAL_API.apiKey = apiKey.getItem(selected_api_key_index)
    token_obj.apiKeyIndex = selected_api_key_index
    SETTINGS.save()

    # 3. Save to disk immediately to persist the manual entry
    TOKEN.save()

    # 4. Attempt login using the standard flow
    # loginByConfig handles verification and refreshing (if refresh token exists)
    try:
        success = loginByConfig()
        
        if success:
            update_account_button(self)
            CustomQMessageBox.information(
                self,
                "Success",
                "Login Successful",
                "You are now connected to Tidal."
            )
            self.accessTokenInput.clear()
        else:
            # If login failed, it might be because the token is expired and no refresh token was provided/valid.
            msg = "Login failed."
            if token_data and not token_data.get('refreshToken'):
                msg += "\nThe provided token file did not contain a refresh token, and the access token appears to be expired."
            elif not token_data:
                msg += "\nThe provided string was treated as an access token but was rejected."
            
            CustomQMessageBox.critical(
                self,
                "Login Failed",
                "Authentication Failed",
                msg
            )
            
    except Exception as e:
        logger.error(f"Error during manual token login: {e}", exc_info=True)
        
        # Use custom dialog for error
        error_icon_path = ""
        try:
            error_icon_path = paths.resource_path("assets/icons/error.png")
            if not os.path.exists(error_icon_path):
                 error_icon_path = paths.resource_path("assets/icons/info_icon.png")
        except Exception:
            error_icon_path = ""
            
        CustomQMessageBox.critical(
            self,
            "Login Failed",
            "Connection Error",
            f"Error: {e}"
        )
