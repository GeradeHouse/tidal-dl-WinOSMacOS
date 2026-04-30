# file: gui_settings_setup.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_settings_setup.py
@Time    :   14-04-2025
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Helper module for building the settings page UI.
             Contains functions to create and layout all widgets and sections.
"""

import os
from typing import TYPE_CHECKING, Any
import logging

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QScrollArea,
    QSpacerItem,
    QSizePolicy,
    QStyle,
    QLabel,
    QSpinBox,
    QCheckBox,
)

from tidal_dl.gui.gui_settings_widgets import CollapsibleSection
from tidal_dl.apiKey import getItems


ATMOS_TV_PLATFORM_NAME = "Atmos TV (Tidal-Web-Downloader)"


def _api_key_profile_label(entry: dict[str, Any]) -> str:
    platform = str(entry.get("platform", "Unknown"))
    formats = str(entry.get("formats", "")).strip()
    label = f"{platform}: {formats}" if formats else platform
    if platform == ATMOS_TV_PLATFORM_NAME:
        label = f"{label}  [Recommended for FLAC/CD]"
    return label

if TYPE_CHECKING:
    from tidal_dl.gui.gui_settings import SettingsPage

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (settings operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

def init_ui(self: "SettingsPage"):
    """
    Main function to build the complete settings UI.
    """
    self.setWindowTitle("Settings")

    setup_ui_structure(self)
    initialize_controls(self)

    # Assertions to satisfy Pylance now that controls are initialized
    assert self.pageLayout is not None
    assert self.scrollArea is not None
    assert self.mainLayout is not None
    assert self.scrollWidget is not None
    assert self.btnBack is not None
    assert self.btnSave is not None

    create_tidal_section(self)
    create_spotify_section(self)
    create_paths_section(self)
    create_download_options_section(self)
    create_ui_behavior_section(self)
    create_cache_section(self)

    # Add a spacer at the end to push sections up
    spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
    self.mainLayout.addItem(spacer)

    self.scrollArea.setWidget(self.scrollWidget)
    self.pageLayout.addWidget(self.scrollArea)

    create_navigation_buttons(self)

    # Connect signals to slots
    self.btnBack.clicked.connect(self.settingsClosedWithoutSaving.emit)
    self.btnSave.clicked.connect(self.saveSettings)
    if self.audio_combo:
        self.audio_combo.currentIndexChanged.connect(self._handle_audio_quality_changed)

    self.loadInitialSettings()


def setup_ui_structure(self: "SettingsPage"):
    """
    Creates the main page layout, title, and scroll area.
    """
    self.pageLayout = QVBoxLayout(self)
    self.pageLayout.setContentsMargins(0, 0, 0, 0)
    self.pageLayout.setSpacing(0)

    titleLabel = QtWidgets.QLabel("Settings")
    titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
    titleLabel.setStyleSheet("font-size: 24px; font-weight: bold; padding: 15px; color: white;")
    self.pageLayout.addWidget(titleLabel)

    self.scrollArea = QScrollArea()
    self.scrollArea.setWidgetResizable(True)
    self.scrollArea.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

    self.scrollWidget = QtWidgets.QWidget()
    self.scrollWidget.setStyleSheet("""
        QWidget { background-color: #222; color: white; }
        QLineEdit { background-color: #2c2c2c; border: 1px solid #555; padding: 4px; border-radius: 3px; color: white; }
        QLineEdit:hover { border: 1px solid #777; }
        QLineEdit:focus { border: 1px solid #0078d4; }
        QLineEdit[readOnly="true"] { background-color: #3a3a3a; color: #aaa; }
        QLineEdit[readOnly="true"]:hover { border: 1px solid #555; }
        QCheckBox { color: white; spacing: 5px; }
        QCheckBox::indicator { background-color: #444; border: 1px solid #666; border-radius: 3px; width: 13px; height: 13px; }
        QCheckBox::indicator:hover { background-color: #555; border: 1px solid #888; }
        QCheckBox::indicator:checked { background-color: #66ccff; border: 1px solid #66ccff; }
        QCheckBox::indicator:checked:hover { background-color: #77ddff; border: 1px solid #77ddff; }
    """)
    self.mainLayout = QVBoxLayout(self.scrollWidget)
    self.mainLayout.setContentsMargins(15, 0, 15, 0)
    self.mainLayout.setSpacing(0)
    self.mainLayout.setAlignment(Qt.AlignmentFlag.AlignTop)


def initialize_controls(self: "SettingsPage"):
    """
    Creates instances of all UI control widgets.
    """
    # Account Controls
    self.btnAccount = QPushButton()
    self.chk_tidal_start_collapsed = QtWidgets.QCheckBox()

    # API Controls
    self.cmbApiKeyIndex = QtWidgets.QComboBox()
    self.cmbApiKeyIndex.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
    self.cmbApiKeyIndex.setMinimumContentsLength(48)

    # --- NEW: Manual Token Controls ---
    self.accessTokenInput = QLineEdit()
    self.accessTokenInput.setPlaceholderText("Paste Access Token or Browse .json file")
    self.btnBrowseToken = QPushButton("Browse")
    self.btnLoginToken = QPushButton("Login with Token")
    # ----------------------------------

    # Download Location Controls
    self.downloadPathInput = QLineEdit()
    self.downloadPathInput.setPlaceholderText("Select download directory")
    self.downloadPathInput.setReadOnly(True)
    self.browseButton = QPushButton("Browse")
    self.downloadPathEdit = QLineEdit()

    # Spotify Integration Controls
    self.spotifyClientIdInput = QLineEdit()
    self.spotifyClientSecretInput = QLineEdit()
    self.spotifyClientSecretInput.setEchoMode(QLineEdit.EchoMode.Password)
    self.chkAutoSpotifyLogin = QtWidgets.QCheckBox()
    self.chkSpotifyUsePlaylistFolders = QtWidgets.QCheckBox()

    # Cache Settings Controls
    self.cache_path_label = QLabel("Playlist Cover Cache Path:")
    self.cache_path_lineEdit = QLineEdit()
    self.cache_path_lineEdit.setPlaceholderText("Leave empty for default location")
    self.browse_cache_button = QPushButton("Browse...")
    self.cache_ttl_label = QLabel("Cache TTL (days):")
    self.cache_ttl_spinBox = QSpinBox()
    self.cache_ttl_spinBox.setMinimum(1)
    self.cache_ttl_spinBox.setMaximum(365)

    # Download Option Checkboxes
    self.chkCheckExist = QCheckBox()
    self.chkIncludeEP = QCheckBox()
    self.chkSaveCovers = QCheckBox()
    self.chkMultiThread = QCheckBox()
    self.chkDownloadDelay = QCheckBox()
    self.chkUsePlaylistFolder = QCheckBox()

    # Path Format Controls
    self.albumFolderFormatEdit = QLineEdit()
    self.playlistFolderFormatEdit = QLineEdit()
    self.trackFileFormatEdit = QLineEdit()
    self.btnRestructureDownloads = QPushButton("Restructure existing downloads...")
    self.chkDebugOpenApiProviderLabel = QCheckBox("Log OpenAPI provider/label diagnostics")

    # UI Options
    self.cmbLanguage = QtWidgets.QComboBox()
    self.spinFontSize = QSpinBox()
    self.chkLyricFile = QCheckBox()
    self.chkShowProgress = QCheckBox()
    self.chkShowTrackInfo = QCheckBox()
    self.chkSaveAlbumInfo = QCheckBox()
    self.chkShowPlaylistIcons = QtWidgets.QCheckBox()
    self.spinPlaylistIconSize = QSpinBox()
    self.chkUseCamelotKeyNotation = QCheckBox()

    # Navigation Buttons
    self.btnBack = QPushButton("Back")
    self.btnSave = QPushButton("Save")


def create_tidal_section(self: "SettingsPage"):
    """
    Creates the 'Tidal Account Settings' section.
    """
    assert self.btnAccount is not None
    assert self.cmbApiKeyIndex is not None
    assert self.chk_tidal_start_collapsed is not None
    assert self.accessTokenInput is not None
    assert self.btnBrowseToken is not None
    assert self.btnLoginToken is not None
    assert self.mainLayout is not None

    tidal_icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "icons", "icon-white-rgb.png"))
    account_section = CollapsibleSection("Tidal Account Settings", icon_path=tidal_icon_path)
    account_layout = QFormLayout()
    account_layout.setContentsMargins(0, 5, 0, 5)
    account_layout.setSpacing(10)
    account_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

    self.btnAccount.clicked.connect(self.toggleAccount)
    self.updateAccountButton()
    account_layout.addRow("Account:", self.btnAccount)

    apiKeyItems = getItems()
    for idx, entry in enumerate(apiKeyItems):
        label = _api_key_profile_label(entry)
        self.cmbApiKeyIndex.addItem(label, idx)
        self.cmbApiKeyIndex.setItemData(idx, label, Qt.ItemDataRole.ToolTipRole)
    account_layout.addRow("API Key Profile:", self.cmbApiKeyIndex)
    account_layout.addRow("Start with Tidal playlists collapsed:", self.chk_tidal_start_collapsed)

    # --- NEW: Manual Token Entry Layout ---
    token_layout = QHBoxLayout()
    token_layout.addWidget(self.accessTokenInput)
    token_layout.addWidget(self.btnBrowseToken)
    
    # Connect signals
    self.btnBrowseToken.clicked.connect(self.browseToken)
    self.btnLoginToken.clicked.connect(self.loginToken)

    account_layout.addRow("Manual Token:", token_layout)
    
    # Add Login button on a separate row, aligned right or stretched
    login_btn_layout = QHBoxLayout()
    login_btn_layout.addStretch()
    login_btn_layout.addWidget(self.btnLoginToken)
    account_layout.addRow("", login_btn_layout)
    # --------------------------------------

    account_section.addLayout(account_layout)
    self.mainLayout.addWidget(account_section)


def create_spotify_section(self: "SettingsPage"):
    """
    Creates the 'Spotify Account Settings' section.
    """
    assert self.spotifyClientIdInput is not None
    assert self.spotifyClientSecretInput is not None
    assert self.chkAutoSpotifyLogin is not None
    assert self.chkSpotifyUsePlaylistFolders is not None
    assert self.mainLayout is not None

    spotify_icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "icons", "Spotify_Primary_Logo_RGB_White.png"))
    self.spotify_section = CollapsibleSection("Spotify Account Settings", icon_path=spotify_icon_path)
    spotify_layout = QFormLayout()
    spotify_layout.setContentsMargins(0, 5, 0, 5)
    spotify_layout.setSpacing(10)
    spotify_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

    spotify_layout.addRow("Spotify Client ID:", self.spotifyClientIdInput)
    spotify_layout.addRow("Spotify Client Secret:", self.spotifyClientSecretInput)
    spotify_layout.addRow("Automatically login on startup:", self.chkAutoSpotifyLogin)
    spotify_layout.addRow("Use Folders for Playlist Names ('Folder - Name'):", self.chkSpotifyUsePlaylistFolders)

    self.btnSpotifyHelp = QPushButton("How to get Spotify Client ID and Secret? (Show)")
    self.btnSpotifyHelp.setStyleSheet("""
        QPushButton { text-align: left; padding: 5px 0px; border: none; background-color: transparent; color: white; font-style: italic; }
        QPushButton:hover { background-color: rgba(255, 255, 255, 0.05); }
    """)
    self.lblSpotifyHelp = QtWidgets.QLabel()
    self.lblSpotifyHelp.setWordWrap(True)
    self.lblSpotifyHelp.setTextFormat(Qt.TextFormat.RichText)
    self.lblSpotifyHelp.setOpenExternalLinks(True)
    self.lblSpotifyHelp.setStyleSheet("QLabel { background-color: #282828; padding: 10px; border-radius: 4px; color: #ccc; }")
    help_text = """
    <b>Why provide Spotify Credentials?</b><br>
    This allows the application to access your Spotify playlists (read-only) for viewing within the app and for linking Spotify tracks to their Tidal equivalents.<br><br>
    <b>How to get your Client ID and Client Secret:</b><br>
    <ol>
        <li>Go to the <a href='https://developer.spotify.com/dashboard/create' style='color: #66ccff;'>Spotify Developer Dashboard</a> and log in with your Spotify account.</li>
        <li>Click on <b>Create app</b>.</li>
        <li>Fill in the <b>App name</b> (e.g., "Tidal-DL Linker") and <b>App description</b>.</li>
        <li>For <b>Redirect URIs</b>, enter exactly: <code style='background-color: #444; padding: 2px 4px; border-radius: 3px;'>http://127.0.0.1:8888/callback</code> (Note: Use the IP address, not 'localhost')</li>
        <li>Under <b>Which API/SDKs are you planning to use?</b>, select <b>Web API</b>.</li>
        <li>Agree to the terms and click <b>Create</b>.</li>
        <li>On the next page, you will see your <b>Client ID</b>. Click <b>Show client secret</b> to reveal the secret.</li>
        <li>Copy the Client ID and Client Secret and paste them into the fields above.</li>
    </ol>
    """
    self.lblSpotifyHelp.setText(help_text)
    self.lblSpotifyHelp.setVisible(False)

    spotify_help_button_container_layout = QHBoxLayout()
    spotify_help_button_container_layout.setContentsMargins(0, 0, 0, 0)
    spotify_help_button_container_layout.addStretch(1)
    spotify_help_button_container_layout.addWidget(self.btnSpotifyHelp)
    spotify_help_button_container_layout.addStretch(1)
    spotify_layout.addRow(spotify_help_button_container_layout)
    spotify_layout.addRow(self.lblSpotifyHelp)
    self.btnSpotifyHelp.clicked.connect(self.toggleSpotifyHelp)

    self.spotify_section.addLayout(spotify_layout)
    self.mainLayout.addWidget(self.spotify_section)


def create_paths_section(self: "SettingsPage"):
    """
    Creates the 'Paths & Formats' section.
    """
    assert self.downloadPathInput is not None
    assert self.browseButton is not None
    assert self.downloadPathEdit is not None
    assert self.albumFolderFormatEdit is not None
    assert self.playlistFolderFormatEdit is not None
    assert self.trackFileFormatEdit is not None
    assert self.btnRestructureDownloads is not None
    assert self.chkDebugOpenApiProviderLabel is not None
    assert self.mainLayout is not None

    paths_icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "icons", "folder-white.png"))
    self.paths_section = CollapsibleSection("Paths & Formats", icon_path=paths_icon_path)

    self.btnPathFormatHelp = QPushButton("Formatting Placeholders (Show)")
    self.btnPathFormatHelp.setStyleSheet("""
        QPushButton { text-align: left; padding: 5px 0px; border: none; background-color: transparent; color: white; font-style: italic; }
        QPushButton:hover { background-color: rgba(255, 255, 255, 0.05); }
    """)
    self.lblPathFormatHelp = QLabel()
    self.lblPathFormatHelp.setWordWrap(True)
    self.lblPathFormatHelp.setTextFormat(Qt.TextFormat.RichText)
    self.lblPathFormatHelp.setStyleSheet("QLabel { background-color: #282828; padding: 10px; border-radius: 4px; color: #ccc; }")
    path_tooltip_base_text_content = (
        "<b>Available Placeholders:</b><br>"
        "{ArtistName}, {AlbumArtistName}, {AlbumTitle}, {AlbumID}, {AlbumYear}, {Flag}, "
        "{TrackNumber}, {TrackTitle}, {ExplicitFlag}, {AudioQuality}, {DurationSeconds}, {Duration}, {TrackID}, "
        "{PlaylistName}, {PlaylistUUID}"
    )
    self.lblPathFormatHelp.setText(path_tooltip_base_text_content)
    self.lblPathFormatHelp.setVisible(False)

    path_format_help_button_container = QtWidgets.QWidget()
    path_format_help_button_h_layout = QHBoxLayout(path_format_help_button_container)
    path_format_help_button_h_layout.setContentsMargins(0, 0, 0, 0)
    path_format_help_button_h_layout.addStretch(1)
    path_format_help_button_h_layout.addWidget(self.btnPathFormatHelp)
    path_format_help_button_h_layout.addStretch(1)
    self.paths_section.addWidget(path_format_help_button_container)
    self.paths_section.addWidget(self.lblPathFormatHelp)
    self.btnPathFormatHelp.clicked.connect(self.togglePathFormatHelp)

    paths_layout = QFormLayout()
    paths_layout.setContentsMargins(0, 10, 0, 5)
    paths_layout.setSpacing(10)
    paths_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

    download_dir_layout = QHBoxLayout()
    download_dir_layout.addWidget(self.downloadPathInput)
    download_dir_layout.addWidget(self.browseButton)
    self.browseButton.clicked.connect(self.browseDirectory)
    paths_layout.addRow("Download Directory:", download_dir_layout)
    paths_layout.addRow("Download Path Var:", self.downloadPathEdit)

    self.albumFolderFormatEdit.setToolTip(path_tooltip_base_text_content)
    self.playlistFolderFormatEdit.setToolTip(path_tooltip_base_text_content)
    self.trackFileFormatEdit.setToolTip(path_tooltip_base_text_content)

    paths_layout.addRow("Album Folder Format:", self.albumFolderFormatEdit)
    paths_layout.addRow("Playlist Folder Format:", self.playlistFolderFormatEdit)
    paths_layout.addRow("Track File Format:", self.trackFileFormatEdit)
    self.btnRestructureDownloads.setToolTip(
        "Move legacy downloads into the current audio-type folder structure."
    )
    paths_layout.addRow("Download Folder Structure:", self.btnRestructureDownloads)

    self.chkDebugOpenApiProviderLabel.setToolTip(
        "Diagnostic option for investigating whether OpenAPI provider metadata can improve Label values."
    )
    paths_layout.addRow("Label Diagnostics:", self.chkDebugOpenApiProviderLabel)

    self.paths_section.addLayout(paths_layout)
    self.mainLayout.addWidget(self.paths_section)

    # This call is intentionally preserved from the original script to make this section
    # expanded by default on startup.
    self.togglePathFormatHelp()


def create_download_options_section(self: "SettingsPage"):
    """
    Creates the 'Download Options' section.
    """
    assert self.chkCheckExist is not None
    assert self.chkIncludeEP is not None
    assert self.chkSaveCovers is not None
    assert self.chkMultiThread is not None
    assert self.chkDownloadDelay is not None
    assert self.chkUsePlaylistFolder is not None
    assert self.mainLayout is not None

    dl_options_icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "icons", "download-white.png"))
    dl_options_section = CollapsibleSection("Download Options", icon_path=dl_options_icon_path)
    dl_options_layout = QFormLayout()
    dl_options_layout.setContentsMargins(0, 5, 0, 5)
    dl_options_layout.setSpacing(10)
    dl_options_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

    dl_options_layout.addRow("Verify File Existence:", self.chkCheckExist)
    dl_options_layout.addRow("Include Singles & EPs:", self.chkIncludeEP)
    dl_options_layout.addRow("Save Covers:", self.chkSaveCovers)
    dl_options_layout.addRow("Multi-Thread Download:", self.chkMultiThread)
    dl_options_layout.addRow("Use Download Delay:", self.chkDownloadDelay)
    dl_options_layout.addRow("Use Playlist Folder:", self.chkUsePlaylistFolder)

    dl_options_section.addLayout(dl_options_layout)
    self.mainLayout.addWidget(dl_options_section)


def create_ui_behavior_section(self: "SettingsPage"):
    """
    Creates the 'UI & Behavior' section.
    """
    assert self.cmbLanguage is not None
    assert self.spinFontSize is not None
    assert self.chkLyricFile is not None
    assert self.chkShowProgress is not None
    assert self.chkShowTrackInfo is not None
    assert self.chkSaveAlbumInfo is not None
    assert self.chkShowPlaylistIcons is not None
    assert self.spinPlaylistIconSize is not None
    assert self.chkUseCamelotKeyNotation is not None
    assert self.mainLayout is not None

    ui_icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "icons", "ui-behavior-white.png"))
    ui_section = CollapsibleSection("UI & Behavior", icon_path=ui_icon_path)
    ui_layout = QFormLayout()
    ui_layout.setContentsMargins(0, 5, 0, 5)
    ui_layout.setSpacing(10)
    ui_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

    for lang in ["English", "Dutch"]:
        self.cmbLanguage.addItem(lang)
    ui_layout.addRow("Language:", self.cmbLanguage)

    self.spinFontSize.setRange(8, 16)
    self.spinFontSize.setSuffix(" pt")
    ui_layout.addRow("Font Size:", self.spinFontSize)

    ui_layout.addRow("Save .lrc Lyric File:", self.chkLyricFile)
    ui_layout.addRow("Show Progress Bar:", self.chkShowProgress)
    ui_layout.addRow("Show Track Info:", self.chkShowTrackInfo)
    ui_layout.addRow("Save AlbumInfo.txt:", self.chkSaveAlbumInfo)
    
    ui_layout.addRow("Show Playlist Icons:", self.chkShowPlaylistIcons)
    self.spinPlaylistIconSize.setRange(20, 60)
    self.spinPlaylistIconSize.setSuffix(" px")
    ui_layout.addRow("Playlist Icon Size:", self.spinPlaylistIconSize)
    self.chkShowPlaylistIcons.stateChanged.connect(self.spinPlaylistIconSize.setEnabled)
    ui_layout.addRow("Use Camelot Key Notation:", self.chkUseCamelotKeyNotation)

    ui_section.addLayout(ui_layout)
    self.mainLayout.addWidget(ui_section)


def create_cache_section(self: "SettingsPage"):
    """
    Creates the 'Cache Settings' section.
    """
    assert self.cache_path_lineEdit is not None
    assert self.browse_cache_button is not None
    assert self.cache_path_label is not None
    assert self.cache_ttl_label is not None
    assert self.cache_ttl_spinBox is not None
    assert self.mainLayout is not None

    cacheSection = CollapsibleSection("Cache Settings", icon_enum=QStyle.StandardPixmap.SP_DriveHDIcon)
    cacheLayout = QFormLayout()
    cacheLayout.setSpacing(10)
    cacheLayout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

    cachePathLayout = QHBoxLayout()
    cachePathLayout.addWidget(self.cache_path_lineEdit)
    cachePathLayout.addWidget(self.browse_cache_button)
    cacheLayout.addRow(self.cache_path_label, cachePathLayout)

    cacheLayout.addRow(self.cache_ttl_label, self.cache_ttl_spinBox)

    cacheSection.addLayout(cacheLayout)
    self.mainLayout.addWidget(cacheSection)

    self.browse_cache_button.clicked.connect(self._browse_cache_path)


def create_navigation_buttons(self: "SettingsPage"):
    """
    Creates the 'Back' and 'Save' buttons at the bottom of the page.
    """
    assert self.btnBack is not None
    assert self.btnSave is not None
    assert self.pageLayout is not None

    buttonLayout = QHBoxLayout()
    buttonLayout.setContentsMargins(0, 10, 15, 10)
    buttonLayout.setSpacing(10)

    button_style = """
        QPushButton { background-color: #555; color: white; padding: 8px 15px; border: 1px solid #666; border-radius: 4px; min-width: 60px; }
        QPushButton:hover { background-color: #666; }
        QPushButton:pressed { background-color: #444; }
    """
    self.btnBack.setStyleSheet(button_style)
    self.btnSave.setStyleSheet(button_style)

    buttonLayout.addStretch(1)
    buttonLayout.addWidget(self.btnBack)
    buttonLayout.addWidget(self.btnSave)
    self.pageLayout.addLayout(buttonLayout)
