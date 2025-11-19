# file: gui_settings.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_settings.py
@Time    :   14-04-2025
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   GUI Settings Module for Tidal Media Downloader

This module implements the settings interface of the Tidal Media Downloader application.
It provides a user-friendly UI with collapsible sections for organizing various configuration
options and user preferences, including:

- Tidal account management and API settings
- Spotify integration for playlist importing
- Download paths and file naming templates
- Media download options and behavior settings
- User interface preferences and localization

The settings page uses PyQt6 components with a modern, themed design featuring
expandable/collapsible sections to manage screen space effectively.

NOTE (Refactor):
The SettingsPage class now delegates UI construction and event handling to helper modules:
- gui_settings_setup.py      (UI layout / widget construction)
- gui_settings_handlers.py   (logic, persistence, signaling)
- gui_settings_widgets.py    (custom widgets like CollapsibleSection)
"""

import logging
from PyQt6 import QtWidgets
from PyQt6.QtCore import pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget,
    QScrollArea,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QComboBox,
    QCheckBox,
)
from typing import Optional, TYPE_CHECKING

# Import helper modules for UI setup and event handling
from tidal_dl.gui import gui_settings_setup
from tidal_dl.gui import gui_settings_handlers
from tidal_dl.gui.gui_settings_widgets import CollapsibleSection  # Backward compatibility re-export

if TYPE_CHECKING:
    from tidal_dl.gui.gui_auth_handler import AuthHandler

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (settings operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

# Main Settings Page widget that contains all settings UI elements
class SettingsPage(QtWidgets.QWidget):
    """
    The main settings page widget for the Tidal Media Downloader application.

    This class creates a UI with multiple collapsible sections containing
    various settings organized by category. Settings are loaded from and
    saved to the global SETTINGS object.
    """

    spotifyCredentialsUpdated = pyqtSignal()  # Class-level signal declaration
    settingsSavedAndClosed = (
        pyqtSignal()
    )  # Signal emitted after saving and closing settings
    settingsClosedWithoutSaving = pyqtSignal()
    fontSizeChanged = pyqtSignal(int)  # Signal emitted when font size changes
    playlistDisplaySettingsChanged = pyqtSignal()

    def __init__(
        self,
        auth_handler: Optional["AuthHandler"] = None,
        audio_combo: Optional[QComboBox] = None,  # Add audio_combo
        parent: Optional[QWidget] = None,
    ):  # Add type hint for parent
        """
        Initialize the settings page.

        Args:
            auth_handler (Optional['AuthHandler']): Handler for authentication logic.
            audio_combo (Optional[QComboBox]): Reference to the audio quality combobox.
            parent (QWidget): Parent widget, typically the main application window
        """
        super().__init__(parent)
        self.auth_handler = auth_handler
        self.audio_combo = audio_combo  # Store reference

        # Pre-declare all attributes that init_ui will populate.
        # This preserves the original class surface and type expectations.
        self.pageLayout: Optional[QVBoxLayout] = None
        self.scrollArea: Optional[QScrollArea] = None
        self.scrollWidget: Optional[QWidget] = None
        self.mainLayout: Optional[QVBoxLayout] = None
        self.spotify_section: Optional[CollapsibleSection] = None
        self.paths_section: Optional[CollapsibleSection] = None
        self.btnAccount: Optional[QPushButton] = None
        self.chk_tidal_start_collapsed: Optional[QCheckBox] = None
        self.cmbApiKeyIndex: Optional[QComboBox] = None
        
        # --- NEW: Manual Token Widgets ---
        self.accessTokenInput: Optional[QLineEdit] = None
        self.btnBrowseToken: Optional[QPushButton] = None
        self.btnLoginToken: Optional[QPushButton] = None
        # ---------------------------------

        self.downloadPathInput: Optional[QLineEdit] = None
        self.browseButton: Optional[QPushButton] = None
        self.downloadPathEdit: Optional[QLineEdit] = None
        self.spotifyClientIdInput: Optional[QLineEdit] = None
        self.spotifyClientSecretInput: Optional[QLineEdit] = None
        self.chkAutoSpotifyLogin: Optional[QCheckBox] = None
        self.chkSpotifyUsePlaylistFolders: Optional[QCheckBox] = None
        self.cache_path_label: Optional[QLabel] = None
        self.cache_path_lineEdit: Optional[QLineEdit] = None
        self.browse_cache_button: Optional[QPushButton] = None
        self.cache_ttl_label: Optional[QLabel] = None
        self.cache_ttl_spinBox: Optional[QSpinBox] = None
        self.chkCheckExist: Optional[QCheckBox] = None
        self.chkIncludeEP: Optional[QCheckBox] = None
        self.chkSaveCovers: Optional[QCheckBox] = None
        self.chkMultiThread: Optional[QCheckBox] = None
        self.chkDownloadDelay: Optional[QCheckBox] = None
        self.chkUsePlaylistFolder: Optional[QCheckBox] = None
        self.albumFolderFormatEdit: Optional[QLineEdit] = None
        self.playlistFolderFormatEdit: Optional[QLineEdit] = None
        self.trackFileFormatEdit: Optional[QLineEdit] = None
        self.cmbLanguage: Optional[QComboBox] = None
        self.spinFontSize: Optional[QSpinBox] = None
        self.chkLyricFile: Optional[QCheckBox] = None
        self.chkShowProgress: Optional[QCheckBox] = None
        self.chkShowTrackInfo: Optional[QCheckBox] = None
        self.chkSaveAlbumInfo: Optional[QCheckBox] = None
        self.chkShowPlaylistIcons: Optional[QCheckBox] = None
        self.spinPlaylistIconSize: Optional[QSpinBox] = None
        self.btnBack: Optional[QPushButton] = None
        self.btnSave: Optional[QPushButton] = None
        self.btnSpotifyHelp: Optional[QPushButton] = None
        self.lblSpotifyHelp: Optional[QLabel] = None
        self.btnPathFormatHelp: Optional[QPushButton] = None
        self.lblPathFormatHelp: Optional[QLabel] = None

        self.initUI()  # Set up the user interface

    def initUI(self):
        """
        Build the complete settings user interface with all controls and layouts.
        This method creates the page structure, all settings controls, and connects signals.
        """
        gui_settings_setup.init_ui(self)

    def loadInitialSettings(self):
        """
        Populates all UI controls with current values from the global SETTINGS object.
        """
        gui_settings_handlers.load_initial_settings(self)

    def updateAccountButton(self):
        """
        Updates the account button text and style based on the current login state.
        """
        gui_settings_handlers.update_account_button(self)

    @pyqtSlot()
    def toggleAccount(self):
        """
        Handles the Tidal account login or logout process.
        """
        gui_settings_handlers.toggle_account(self)

    @pyqtSlot()
    def _browse_cache_path(self):
        """Opens a dialog to select the cache directory."""
        gui_settings_handlers._browse_cache_path(self)

    @pyqtSlot()
    def browseDirectory(self):
        """
        Opens a directory selection dialog for choosing the download location.
        """
        gui_settings_handlers.browse_directory(self)

    @pyqtSlot()
    def saveSettings(self):
        """
        Saves all settings from the UI controls to the global SETTINGS object.
        """
        gui_settings_handlers.save_settings(self)

    def expandSpotifySection(self):
        """
        Programmatically expands the Spotify settings section.
        """
        if hasattr(self, "spotify_section") and self.spotify_section:
            # Check if the button is not already checked (expanded)
            if not self.spotify_section.toggle_button.isChecked():
                # Use setChecked(True) which will trigger the toggle_content slot
                self.spotify_section.toggle_button.setChecked(True)
            # Optionally, ensure the settings page is scrolled to show the section

    @pyqtSlot()
    def toggleSpotifyHelp(self):
        """Toggles the visibility of the Spotify help instructions."""
        gui_settings_handlers.toggle_spotify_help(self)

    @pyqtSlot()
    def togglePathFormatHelp(self):
        """Toggles the visibility of the path formatting help instructions."""
        gui_settings_handlers.toggle_path_format_help(self)

    @pyqtSlot(int)
    def _handle_audio_quality_changed(self, index: int):
        """Updates the global audio quality setting."""
        gui_settings_handlers._handle_audio_quality_changed(self, index)

    # --- NEW SLOTS ---
    @pyqtSlot()
    def browseToken(self):
        """Opens file dialog to select a token json file."""
        gui_settings_handlers.browse_token_file(self)

    @pyqtSlot()
    def loginToken(self):
        """Attempts to login using the manually entered token."""
        gui_settings_handlers.login_with_token(self)