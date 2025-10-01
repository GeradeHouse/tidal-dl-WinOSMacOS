# --- START OF FILE gui_settings.py ---

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
import os

The settings page uses PyQt6 components with a modern, themed design featuring
expandable/collapsible sections to manage screen space effectively.
"""

from PyQt6 import QtWidgets, QtCore  # Import QtCore

# Import QStyle for standard icons
from PyQt6.QtCore import (
    pyqtSignal,
    pyqtSlot,
    Qt,
    QPropertyAnimation,
    QEasingCurve,
    QSize,
    QObject,
)  # Import QObject
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QFileDialog,
    QMessageBox,
    QFrame,
    QScrollArea,
    QSpacerItem,
    QSizePolicy,
    QStyle,
    QLabel,
    QSpinBox,
    QComboBox,  # Import QComboBox
    QLayout,
    QCheckBox,
)  # Import QLayout for type hint, Import QCheckBox
from PyQt6.QtGui import QIcon, QFont  # Import QFont

# from PyQt6.QtCore import pyqtProperty # Removed unused import
import os
import logging

# import json # Removed unused import
from typing import Optional, TYPE_CHECKING, Union, cast  # Add Union, cast
import logging

# Import application-specific modules
from ..settings import (
    SETTINGS,
    TOKEN,
)  # Global settings object that stores user preferences, Import TOKEN
from ..apiKey import getItems  # Function to retrieve available API keys
from ..printf import Printf  # Utility for printing messages
from ..tidal import TIDAL_API  # Interface to the Tidal API
from ..tidal import AudioQuality  # Removed VideoQuality import
from ..enums import Type  # Import Type enum for default path format keys

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set specific level for this module to debugging

# from ..printf import Printf # Duplicate import removed
if TYPE_CHECKING:
    from .gui_auth_handler import AuthHandler


# --- CollapsibleSection Class Definition ---
# This custom widget creates expandable/collapsible sections for grouping settings
# Each section has a header button with an icon and title that can be clicked to show/hide content
class CollapsibleSection(QWidget):
    # Modified to accept either icon_enum (QStyle.StandardPixmap) or icon_path (str)
    # Add type hints for parameters
    def __init__(
        self,
        title: str = "",
        icon_enum: Optional[QStyle.StandardPixmap] = None,
        icon_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        """
        Initialize a collapsible section widget with a header and expandable content area.

        Args:
            title (str): The section title displayed in the header
            icon_enum (QStyle.StandardPixmap): Optional standard icon enum to use
            icon_path (str): Optional path to a custom icon (takes precedence over icon_enum)
            parent (QWidget): Parent widget
        """
        super().__init__(parent)
        self.is_expanded = False  # Track expansion state

        # Create the header button that toggles the section
        self.toggle_button = QPushButton()

        # --- Icon handling logic ---
        # Try to load the icon in order of preference: 1) Custom path, 2) Standard enum
        loaded_icon: Optional[QIcon] = None
        if icon_path:
            loaded_icon = QIcon(icon_path)  # Load from path if provided
            if loaded_icon.isNull():  # Check if icon loaded successfully
                print(f"Warning: Could not load icon from path: {icon_path}")
                loaded_icon = None  # Fallback if path is invalid
        # If no path icon, try enum
        if not loaded_icon and icon_enum is not None:
            # Ensure the widget has a style before accessing standardIcon
            style = self.style() or QtWidgets.QApplication.style()
            if style:  # Check if style is available
                loaded_icon = style.standardIcon(
                    icon_enum
                )  # Apply icon if one was successfully loaded
            else:
                logging.warning("Could not obtain style to load standard icon.")
        if loaded_icon:
            self.toggle_button.setIcon(loaded_icon)
            self.toggle_button.setIconSize(QSize(16, 16))  # Keep icon size reasonable

        # Configure header button appearance and behavior
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)  # Make button toggle on/off
        self.toggle_button.setChecked(False)  # Start in collapsed state

        # Apply custom styling to the header button
        # Creates a modern, flat appearance with hover effects
        self.toggle_button.setStyleSheet(
            """
            QPushButton {
                text-align: left;
                padding: 8px;
                border: none;
                background-color: #333; /* Darker background for header */
                color: white;
                font-weight: bold;
                border-bottom: 1px solid #555; /* Separator line */
            }
            QPushButton:checked {
                background-color: #444; /* Slightly lighter when expanded */
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
        """
        )
        self._update_button_text()  # Set initial indicator text (▶ or ▼)

        # Create the collapsible content area container
        self.content_area = QFrame()
        self.content_area.setMaximumHeight(0)  # Start in collapsed state (height=0)
        self.content_area.setMinimumHeight(0)
        self.content_area.setFrameShape(QFrame.Shape.NoFrame)  # No border
        # Add padding for better visual hierarchy and readability
        # Left margin creates indentation to align with header text after the icon
        self.content_area.setContentsMargins(25, 5, 15, 10)
        # Create layout for the expandable content area - this is where section widgets will be added
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(
            0, 0, 0, 0
        )  # No additional margins inside content
        self.content_layout.setSpacing(
            8
        )  # Spacing between child widgets in the section

        # Set up the main layout for the entire collapsible section (header + content)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)  # No margins, content touches edges
        main_layout.setSpacing(
            0
        )  # No space between header and content area for seamless look
        main_layout.addWidget(self.toggle_button)  # Add header button at top
        main_layout.addWidget(self.content_area)  # Add content area below header

        # Set up smooth animation for expanding/collapsing
        # Using Qt Property Animation to animate the maximumHeight property
        self.animation = QPropertyAnimation(self.content_area, b"maximumHeight")
        self.animation.setDuration(
            200
        )  # Animation duration in ms (faster = less smooth)
        self.animation.setEasingCurve(
            QEasingCurve.Type.InOutQuad
        )  # Acceleration curve for natural feel
        # Connect the toggle button's checked state to our expand/collapse handler
        self.toggle_button.toggled.connect(self.toggle_content)
        # Connect animation finished signal to update geometry AFTER animation
        self.animation.finished.connect(self._animation_finished_update)

    def _calculate_target_expanded_height(self) -> int:
        """Calculates the ideal height when fully expanded, ensuring child widgets are sized."""
        # Iterate through items in content_layout to ensure their individual sizeHints are correct.
        # This is especially important for QLabels with wordWrap.
        for i in range(self.content_layout.count()):
            item = self.content_layout.itemAt(i)
            if item:
                widget = item.widget()
                # Add a check to ensure widget is not None before accessing isVisible or adjustSize
                if widget is not None:
                    if widget.isVisible():  # Only consider visible widgets
                        widget.adjustSize()  # Crucial for word-wrapped QLabels or complex widgets

                layout_item = (
                    item.layout()
                )  # Check if the item is a sub-layout (e.g., spotify_layout)
                if layout_item:
                    # **** CRITICAL MODIFICATION ****
                    # 1. Activate the sub-layout first. This allows it to propose/set initial
                    #    widths for its child widgets. This is vital for word-wrapped QLabels,
                    #    as their heightHint depends on their width.
                    layout_item.activate()

                    # 2. Now, iterate through the sub-layout's widgets.
                    for j in range(layout_item.count()):
                        sub_widget_item = layout_item.itemAt(j)
                        actual_sub_widget = (
                            sub_widget_item.widget() if sub_widget_item else None
                        )
                        if actual_sub_widget is not None:
                            if actual_sub_widget.isVisible():
                                # adjustSize() will now use the width set by the activated layout
                                # to correctly calculate the height for word-wrapped content.
                                actual_sub_widget.adjustSize()

                    # 3. Activate the sub-layout again. Its own sizeHint might have changed
                    #    now that its children's sizes are finalized.
                    layout_item.activate()
                    # **** END OF CRITICAL MODIFICATION ****

        self.content_layout.activate()  # Ensure main content_layout is up-to-date
        margins = self.content_area.contentsMargins()
        buffer = 10  # Increased buffer slightly for safety with complex layouts
        calculated_height = (
            self.content_layout.sizeHint().height()
            + margins.top()
            + margins.bottom()
            + buffer
        )
        logging.debug(
            f"[{self.toggle_button.text()}] _calculate_target_expanded_height: content_layout.sizeHint={self.content_layout.sizeHint().height()}, margins={margins.top()}+{margins.bottom()}, buffer={buffer}, total={calculated_height}"
        )
        return max(0, calculated_height)  # Ensure non-negative

    def _update_button_text(self):
        """
        Updates the button text to include an expand/collapse indicator (▼ or ▶).
        This provides a visual cue to the user about the section's expanded/collapsed state.
        """
        # Choose triangle indicator based on expanded state
        prefix = "▼ " if self.is_expanded else "▶ "
        current_text = self.toggle_button.text()

        # Remove old prefix if present before adding new one to avoid duplicating indicators
        if current_text.startswith("▼ ") or current_text.startswith("▶ "):
            original_title = current_text[
                2:
            ]  # Remove first two chars (the old indicator)
        else:
            original_title = current_text  # No indicator present yet

        # Set new text with appropriate indicator
        self.toggle_button.setText(prefix + original_title)

    # Add type hint for checked
    def toggle_content(self, checked: bool):
        """
        Handles the expansion/collapse animation when section header is clicked.

        Args:
            checked (bool): Whether the toggle button is checked (True = expanded)
        """
        # Update internal state and visual indicator
        self.is_expanded = checked
        self._update_button_text()

        # Get current height as animation starting point
        start_height = self.content_area.height()

        if checked:
            end_height = self._calculate_target_expanded_height()
            logging.debug(
                f"[CollapsibleSection {self.toggle_button.text()}] Expanding. Calculated end_height: {end_height}"
            )
        else:
            end_height = 0

        # Performance optimization: Skip animation if heights are the same
        # or if end_height is problematic
        if start_height == end_height or end_height < 0:
            self.content_area.setMaximumHeight(end_height)  # Directly set final state
            return

        # Configure and start the height animation
        self.animation.setStartValue(start_height)
        self.animation.setEndValue(end_height)
        self.animation.start()

    # Add type hint for widget
    def addWidget(self, widget: QWidget):
        """
        Adds a widget to the content area's layout.
        This is a convenience method to add controls to this section.

        Args:
            widget (QWidget): The widget to add to this section
        """
        self.content_layout.addWidget(widget)

    # Add type hint for layout
    def addLayout(self, layout: QLayout):
        """
        Adds a layout to the content area's layout.
        This is a convenience method to add complex layouts to this section.

        Args:
            layout (QLayout): The layout to add to this section
        """
        self.content_layout.addLayout(layout)

    def _animation_finished_update(self):
        """Slot called after expand/collapse animation finishes."""
        # Update geometry hints after animation to ensure parent layouts resize correctly
        self.content_area.updateGeometry()
        logging.debug(
            f"[CollapsibleSection {self.toggle_button.text()}] Animation finished. content_area.sizeHint={self.content_area.sizeHint()}, self.sizeHint={self.sizeHint()}"
        )
        self.updateGeometry()

    def updateContentHeight(self):
        if not self.is_expanded:
            logging.debug(
                f"[{self.toggle_button.text()}] updateContentHeight called but section not expanded."
            )
            return

        logging.debug(
            f"[{self.toggle_button.text()}] updateContentHeight called while expanded."
        )

        # Recalculate the target height. This internally calls adjustSize on children.
        new_target_height = self._calculate_target_expanded_height()
        current_content_height = (
            self.content_area.height()
        )  # Current actual rendered height

        # Animate if the new target height is valid AND
        # (it's different from the current maximumHeight constraint OR it's different from the current actual height)
        # This covers cases where it needs to grow, shrink, or correct itself if it was clipped.
        if new_target_height >= 0 and (
            new_target_height != self.content_area.maximumHeight()
            or new_target_height != current_content_height
        ):
            logging.info(
                f"[{self.toggle_button.text()}] Content height changing. Animating from {current_content_height} to {new_target_height}. (Old max: {self.content_area.maximumHeight()})"
            )
            self.animation.stop()  # Stop any current animation
            self.animation.setStartValue(current_content_height)
            self.animation.setEndValue(new_target_height)
            self.animation.start()
        else:
            logging.debug(
                f"[{self.toggle_button.text()}] Content height effectively unchanged or invalid. New target: {new_target_height}, Current height: {current_content_height}, Current max: {self.content_area.maximumHeight()}"
            )


# --- End of CollapsibleSection Class ---


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

    def __init__(
        self,
        auth_handler: Optional["AuthHandler"] = None,
        audio_combo: Optional[QtWidgets.QComboBox] = None,  # Add audio_combo
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
        self.scrollArea: Optional[QScrollArea] = None  # Declare scrollArea
        self.scrollWidget: Optional[QWidget] = None  # Declare scrollWidget
        self.mainLayout: Optional[QVBoxLayout] = (
            None  # Declare mainLayout for scrollWidget
        )
        self.spotify_section: Optional[CollapsibleSection] = (
            None  # Declare spotify_section
        )
        self.paths_section: Optional[CollapsibleSection] = None  # Declare paths_section
        self.initUI()  # Set up the user interface

    def initUI(self):
        """
        Build the complete settings user interface with all controls and layouts.
        This method creates the page structure, all settings controls, and connects signals.
        """
        # Set window title for when displayed as a separate window
        self.setWindowTitle("Settings")

        # --- Main page layout setup ---
        # Use a QVBoxLayout for the main page structure (vertical arrangement)
        self.pageLayout = QVBoxLayout(self)
        # Remove margins from main page layout (margins will be on the inner layout)
        # This ensures the content extends to the edges of the window
        self.pageLayout.setContentsMargins(0, 0, 0, 0)
        self.pageLayout.setSpacing(
            0
        )  # No spacing between title/scroll/buttons for clean look

        # --- Page Title ---
        # Add title label at the top of the settings page
        titleLabel = QtWidgets.QLabel("Settings")
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Style the title with large bold text and padding
        titleLabel.setStyleSheet(
            "font-size: 24px; font-weight: bold; padding: 15px; color: white;"
        )
        self.pageLayout.addWidget(titleLabel)

        # --- Scrollable Content Area ---
        # Create a scroll area to contain the collapsible sections
        # This allows the page to be usable even when there are many settings
        self.scrollArea = QScrollArea()  # MODIFIED: Assign to self.scrollArea
        self.scrollArea.setWidgetResizable(
            True
        )  # Allow the content to resize with the window
        # Make scroll area transparent so the container color shows through
        self.scrollArea.setStyleSheet(
            "QScrollArea { border: none; background-color: transparent; }"
        )

        # Create a container widget to hold all settings sections inside the scroll area
        self.scrollWidget = QWidget()  # MODIFIED: Assign to self.scrollWidget
        # Set dark background for the settings container
        # Set dark background for the settings container and style checkboxes
        self.scrollWidget.setStyleSheet(
            """
            QWidget {
                background-color: #222;
                color: white;
            }

            /* QLineEdit Styling (including hover and focus) */
            QLineEdit {
                background-color: #2c2c2c; /* Slightly lighter than main bg */
                border: 1px solid #555;    /* Default border */
                padding: 4px;
                border-radius: 3px;
                color: white;
            }
            QLineEdit:hover {
                border: 1px solid #777;    /* Lighter border on hover */
            }
            QLineEdit:focus {
                border: 1px solid #0078d4; /* Standard focus blue */
            }
            QLineEdit[readOnly="true"] { /* Style for read-only QLineEdit */
                background-color: #3a3a3a; /* Darker for read-only */
                color: #aaa;
            }
            QLineEdit[readOnly="true"]:hover { /* Prevent hover effect for read-only */
                border: 1px solid #555;
            }

            /* QCheckBox Styling (including hover for indicator) */
            QCheckBox {
                color: white;
                spacing: 5px;
            }
            QCheckBox::indicator {
                background-color: #444;
                border: 1px solid #666;
                border-radius: 3px;
                width: 13px;
                height: 13px;
            }
            QCheckBox::indicator:hover {
                background-color: #555;
                border: 1px solid #888;
            }
            QCheckBox::indicator:checked {
                background-color: #66ccff;
                border: 1px solid #66ccff;
            }
            QCheckBox::indicator:checked:hover {
                background-color: #77ddff;
                border: 1px solid #77ddff;
            }
        """
        )
        # This layout will hold all the collapsible sections
        self.mainLayout = QVBoxLayout(
            self.scrollWidget
        )  # MODIFIED: Use self.scrollWidget
        # Add horizontal margins but not vertical ones (sections handle their own vertical spacing)
        self.mainLayout.setContentsMargins(15, 0, 15, 0)  # Left, Top, Right, Bottom
        self.mainLayout.setSpacing(
            0
        )  # No space between section headers for a seamless look
        # Make sections start at the top of the container
        self.mainLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # --- Initialize All Setting Controls ---
        # Create all UI controls before adding them to specific sections
        # This improves code organization and makes section creation cleaner

        # Account Controls
        self.btnAccount = (
            QPushButton()
        )  # Connect/Disconnect button (text set in updateAccountButton)

        # API Controls (some might be unused but kept for backward compatibility)
        # self.apiKeyInput = QLineEdit()  # Direct API key input (may be unused with API profiles)
        # self.apiSecretInput = QLineEdit()  # Direct API secret input (may be unused with API profiles)
        self.cmbApiKeyIndex = (
            QtWidgets.QComboBox()
        )  # Dropdown for selecting API key profile

        # Download Location Controls
        self.downloadPathInput = QLineEdit()  # Display field showing selected directory
        self.downloadPathInput.setPlaceholderText("Select download directory")
        self.downloadPathInput.setReadOnly(
            True
        )  # User can't directly edit, must use Browse
        self.browseButton = QPushButton(
            "Browse"
        )  # Button to open directory selection dialog
        self.downloadPathEdit = (
            QLineEdit()
        )  # Variable format path field (can include placeholders)

        # Spotify Integration Controls
        self.spotifyClientIdInput = QLineEdit()  # Spotify API client ID
        self.spotifyClientSecretInput = QLineEdit()  # Spotify API secret
        self.spotifyClientSecretInput.setEchoMode(
            QLineEdit.EchoMode.Password
        )  # Mask password input
        self.chkAutoSpotifyLogin = QtWidgets.QCheckBox()  # Auto Spotify Login checkbox

        # Cache Settings Controls
        self.cache_path_label = QLabel("Playlist Cover Cache Path:")
        self.cache_path_lineEdit = QLineEdit()
        self.cache_path_lineEdit.setPlaceholderText(
            "Leave empty for default location"
        )  # Updated placeholder
        self.browse_cache_button = QPushButton("Browse...")
        self.cache_ttl_label = QLabel("Cache TTL (days):")
        self.cache_ttl_spinBox = QSpinBox()
        self.cache_ttl_spinBox.setMinimum(1)
        self.cache_ttl_spinBox.setMaximum(365)
        # self.cache_ttl_spinBox.setValue(SETTINGS.playlistCoverCacheTTL) # Default value set in loadInitialSettings

        # Download Option Checkboxes
        self.chkCheckExist = QCheckBox()  # Use QCheckBox directly
        self.chkIncludeEP = QCheckBox()
        self.chkSaveCovers = QCheckBox()
        self.chkMultiThread = QCheckBox()
        self.chkDownloadDelay = QCheckBox()
        self.chkUsePlaylistFolder = QCheckBox()

        # Path Format Controls
        self.albumFolderFormatEdit = (
            QLineEdit()
        )  # Format pattern for album folder names
        self.playlistFolderFormatEdit = (
            QLineEdit()
        )  # Format pattern for playlist folder names
        self.trackFileFormatEdit = (
            QLineEdit()
        )  # Format pattern for audio track filenames

        # UI Options
        self.cmbLanguage = QtWidgets.QComboBox()  # Language selection dropdown
        self.chkLyricFile = QCheckBox()  # Use QCheckBox directly
        self.chkShowProgress = QCheckBox()
        self.chkShowTrackInfo = QCheckBox()
        self.chkSaveAlbumInfo = QCheckBox()
        # Navigation Buttons
        self.btnBack = QPushButton("Back")  # Return to main menu
        self.btnSave = QPushButton("Save")  # Save settings and return to main menu

        # ===========================================
        # === SECTION 1: TIDAL ACCOUNT SETTINGS ===
        # ===========================================
        # Create collapsible section for Tidal account settings with custom icon
        # --- CORRECTED PATH ---
        tidal_icon_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "assets", "icons", "icon-white-rgb.png"
            )
        )
        # --- END CORRECTION ---
        account_section = CollapsibleSection(
            "Tidal Account Settings", icon_path=tidal_icon_path
        )

        # Use form layout for aligned label-control pairs
        account_layout = QFormLayout()
        account_layout.setContentsMargins(
            0, 5, 0, 5
        )  # Add vertical padding within section
        account_layout.setSpacing(10)  # Space between form rows
        account_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight
        )  # Right-align labels

        # Connect button signal to toggle login/logout function
        self.btnAccount.clicked.connect(self.toggleAccount)
        self.updateAccountButton()  # Set initial button text based on login state
        account_layout.addRow("Account:", self.btnAccount)

        # API Key Profile Selection
        # This dropdown allows selecting from predefined API key profiles
        # instead of manually entering API keys
        apiKeyItems = getItems()  # Get list of API keys from configuration
        for idx, entry in enumerate(apiKeyItems):
            self.cmbApiKeyIndex.addItem(f"{entry['platform']}: {entry['formats']}", idx)
        account_layout.addRow("API Key Profile:", self.cmbApiKeyIndex)

        # Add the form layout to the section
        account_section.addLayout(account_layout)
        # Add the completed section to the main layout
        self.mainLayout.addWidget(account_section)

        # ===========================================
        # === SECTION 2: SPOTIFY ACCOUNT SETTINGS ===
        # ===========================================
        # Create collapsible section for Spotify integration with custom icon
        # --- CORRECTED PATH ---
        spotify_icon_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "assets",
                "icons",
                "Spotify_Primary_Logo_RGB_White.png",
            )
        )
        # --- END CORRECTION ---
        # Store spotify_section as an instance variable
        self.spotify_section = CollapsibleSection(
            "Spotify Account Settings", icon_path=spotify_icon_path
        )

        # Use form layout for aligned label-control pairs
        spotify_layout = QFormLayout()
        spotify_layout.setContentsMargins(
            0, 5, 0, 5
        )  # Add vertical padding within section
        spotify_layout.setSpacing(10)  # Space between form rows
        spotify_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight
        )  # Right-align labels

        # Add Spotify API credential fields
        # These are required to access the Spotify API for playlist import functionality
        spotify_layout.addRow("Spotify Client ID:", self.spotifyClientIdInput)
        spotify_layout.addRow("Spotify Client Secret:", self.spotifyClientSecretInput)
        # Auto Spotify Login option
        spotify_layout.addRow(
            "Automatically login on startup:", self.chkAutoSpotifyLogin
        )

        # --- Spotify Help Section ---
        self.btnSpotifyHelp = QPushButton(
            "How to get Spotify Client ID and Secret? (Show)"
        )  # MODIFIED TEXT
        # --- NEW: Style for btnSpotifyHelp (Transparent with hover) ---
        self.btnSpotifyHelp.setStyleSheet(
            """
            QPushButton {
                text-align: left;
                padding: 5px 0px; /* Adjust padding, 0px left/right if only text area hover */
                border: none;
                background-color: transparent;
                color: white; /* Keep text white or remove to inherit */
                font-style: italic;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.05); /* Subtle white tint on hover */
                /* color: #77ddff; */ /* Removed color change on hover */
            }
        """
        )
        # --- END NEW Style ---
        self.lblSpotifyHelp = QtWidgets.QLabel()
        self.lblSpotifyHelp.setWordWrap(True)
        self.lblSpotifyHelp.setTextFormat(Qt.TextFormat.RichText)
        self.lblSpotifyHelp.setOpenExternalLinks(True)  # Allow opening links
        self.lblSpotifyHelp.setStyleSheet(
            "QLabel { background-color: #282828; padding: 10px; border-radius: 4px; color: #ccc; }"
        )

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
        self.lblSpotifyHelp.setVisible(False)  # Start hidden

        # Add button and label to layout
        # --- MODIFICATION START for btnSpotifyHelp ---
        # Create a QHBoxLayout to hold the button and a spacer
        spotify_help_button_container_layout = QHBoxLayout()
        spotify_help_button_container_layout.setContentsMargins(
            0, 0, 0, 0
        )  # No margins for this inner layout
        spotify_help_button_container_layout.addStretch(1)  # Add stretch before
        spotify_help_button_container_layout.addWidget(self.btnSpotifyHelp)
        spotify_help_button_container_layout.addStretch(1)  # Add stretch after

        # Add the QHBoxLayout (containing the button) to the QFormLayout
        # The QFormLayout will stretch the QHBoxLayout, but within the QHBoxLayout,
        # the button will remain its preferred size due to the stretch.
        spotify_layout.addRow(spotify_help_button_container_layout)
        # --- MODIFICATION END for btnSpotifyHelp ---

        spotify_layout.addRow(self.lblSpotifyHelp)  # The label is added in its own row

        # Connect button to toggle function
        self.btnSpotifyHelp.clicked.connect(self.toggleSpotifyHelp)

        # Add the form layout to the section
        self.spotify_section.addLayout(spotify_layout)
        # Add the completed section to the main layout
        self.mainLayout.addWidget(self.spotify_section)

        # ==========================================
        # === SECTION 3: PATHS & FILE FORMATS ===
        # ==========================================
        # Create collapsible section for paths and file naming with folder icon
        # --- CORRECTED PATH ---
        paths_icon_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "assets", "icons", "folder-white.png"
            )
        )
        # --- END CORRECTION ---
        # Store paths_section as an instance variable
        self.paths_section = CollapsibleSection(
            "Paths & Formats", icon_path=paths_icon_path
        )  # MODIFIED

        # --- Path Format Help Section (New) ---
        self.btnPathFormatHelp = QPushButton(
            "Formatting Placeholders (Show)"
        )  # MODIFIED TEXT
        # --- NEW: Style for btnPathFormatHelp (Transparent with hover) ---
        self.btnPathFormatHelp.setStyleSheet(
            """
            QPushButton {
                text-align: left;
                padding: 5px 0px; /* Adjust padding */
                border: none;
                background-color: transparent;
                color: white; /* Keep text white or remove to inherit */
                font-style: italic;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.05); /* Subtle white tint on hover */
                /* color: #77ddff; */ /* Removed color change on hover */
            }
        """
        )
        # --- END NEW Style ---
        self.lblPathFormatHelp = QLabel()
        self.lblPathFormatHelp.setWordWrap(True)
        self.lblPathFormatHelp.setTextFormat(
            Qt.TextFormat.RichText
        )  # Allow rich text if needed later
        self.lblPathFormatHelp.setStyleSheet(
            "QLabel { background-color: #282828; padding: 10px; border-radius: 4px; color: #ccc; }"
        )

        # Use the path_tooltip_base content for the label
        # path_tooltip_base is defined further down, ensure it's correct when used
        path_tooltip_base_text_content = (
            "<b>Available Placeholders:</b><br>"
            "{ArtistName}, {AlbumArtistName}, {AlbumTitle}, {AlbumID}, {AlbumYear}, {Flag}, "
            "{TrackNumber}, {TrackTitle}, {ExplicitFlag}, {AudioQuality}, {DurationSeconds}, {Duration}, {TrackID}, "
            "{PlaylistName}, {PlaylistUUID}"
        )
        self.lblPathFormatHelp.setText(path_tooltip_base_text_content)
        self.lblPathFormatHelp.setVisible(False)  # Start hidden

        # Add button and label to the paths_section's content layout
        # --- MODIFICATION START for btnPathFormatHelp ---
        # Create a container widget for the button and its horizontal layout
        path_format_help_button_container = QWidget()
        path_format_help_button_h_layout = QHBoxLayout(
            path_format_help_button_container
        )  # Set layout on container
        path_format_help_button_h_layout.setContentsMargins(
            0, 0, 0, 0
        )  # No margins for this inner layout
        path_format_help_button_h_layout.addStretch(1)  # Add stretch before
        path_format_help_button_h_layout.addWidget(self.btnPathFormatHelp)
        path_format_help_button_h_layout.addStretch(1)  # Add stretch after

        # Add the container (which holds the button and stretch) to the paths_section
        self.paths_section.addWidget(path_format_help_button_container)
        # --- MODIFICATION END for btnPathFormatHelp ---

        self.paths_section.addWidget(
            self.lblPathFormatHelp
        )  # Add the label after the button container

        # Connect button to toggle function
        self.btnPathFormatHelp.clicked.connect(self.togglePathFormatHelp)
        # --- End Path Format Help Section ---

        # Use form layout for aligned label-control pairs (below the help button/label)
        paths_layout = QFormLayout()
        paths_layout.setContentsMargins(
            0, 10, 0, 5
        )  # Add some top margin to separate from help
        paths_layout.setSpacing(10)  # Space between form rows
        paths_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight
        )  # Right-align labels

        # Create a special row with both a display field and browse button
        # This lets users visually select a download directory rather than typing the path
        download_dir_layout = QHBoxLayout()
        download_dir_layout.addWidget(self.downloadPathInput)  # Shows the selected path
        download_dir_layout.addWidget(self.browseButton)  # Opens file browser dialog
        self.browseButton.clicked.connect(
            self.browseDirectory
        )  # Connect browse button to dialog function
        paths_layout.addRow("Download Directory:", download_dir_layout)

        # Download Path Variable field - allows for placeholder variables in path
        # This is a separate field from the direct download directory above,
        # as it can contain template variables that get expanded at runtime
        paths_layout.addRow("Download Path Var:", self.downloadPathEdit)

        # --- Add Tooltips for Path Formats ---
        path_tooltip_base = (
            "Available Placeholders:\n"
            "{ArtistName}, {AlbumArtistName}, {AlbumTitle}, {AlbumID}, {AlbumYear}, {Flag}, "
            "{TrackNumber}, {TrackTitle}, {ExplicitFlag}, {AudioQuality}, {DurationSeconds}, {Duration}, {TrackID}, "
            "{PlaylistName}, {PlaylistUUID}"
        )  # Removed Video placeholders

        # --- Set Tooltips for Path Formats (after QLineEdit creation) ---
        self.albumFolderFormatEdit.setToolTip(
            path_tooltip_base_text_content
        )  # Use the same text
        self.playlistFolderFormatEdit.setToolTip(path_tooltip_base_text_content)
        self.trackFileFormatEdit.setToolTip(path_tooltip_base_text_content)
        # --- End Tooltips ---

        # Format String Fields - These control the naming structure for files and folders
        # Each field accepts template variables (e.g., {artist}, {album}, {title})
        paths_layout.addRow("Album Folder Format:", self.albumFolderFormatEdit)
        paths_layout.addRow("Playlist Folder Format:", self.playlistFolderFormatEdit)
        paths_layout.addRow("Track File Format:", self.trackFileFormatEdit)

        # Add the form layout to the section (below the info label)
        self.paths_section.addLayout(paths_layout)  # MODIFIED
        # Add the completed section to the main layout
        self.mainLayout.addWidget(self.paths_section)  # MODIFIED

        # --- REMOVED: Make "Paths & Formats" section expanded by default ---
        # if self.paths_section: # Check if it was created
        #     self.paths_section.toggle_button.setChecked(True) # This will trigger its toggle_content slot
        # --- END REMOVAL ---

        # --- MODIFICATION: Make "Formatting Placeholders" label visible by default ---
        # This assumes you want the *label inside* the "Paths & Formats" section to also be visible
        self.togglePathFormatHelp()  # Call this once to set initial state to visible
        # Or, more directly if you know you want it visible:
        # self.lblPathFormatHelp.setVisible(True)
        # self.btnPathFormatHelp.setText("Formatting Placeholders (Hide)")
        # --- END MODIFICATION ---

        # ========================================
        # === SECTION 4: DOWNLOAD OPTIONS ===
        # ========================================
        # Create collapsible section for download behavior options with download icon
        # --- CORRECTED PATH ---
        dl_options_icon_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "assets", "icons", "download-white.png"
            )
        )
        # --- END CORRECTION ---
        dl_options_section = CollapsibleSection(
            "Download Options", icon_path=dl_options_icon_path
        )

        # Use form layout for aligned label-control pairs
        dl_options_layout = QFormLayout()
        dl_options_layout.setContentsMargins(
            0, 5, 0, 5
        )  # Add vertical padding within section
        dl_options_layout.setSpacing(10)  # Space between form rows
        dl_options_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight
        )  # Right-align labels

        # Add download option checkboxes with descriptive labels
        # These control various aspects of the download behavior
        dl_options_layout.addRow(
            "Verify File Existence:", self.chkCheckExist
        )  # Skip if file exists
        dl_options_layout.addRow(
            "Include Singles & EPs:", self.chkIncludeEP
        )  # Download singles/EPs in artist mode
        dl_options_layout.addRow(
            "Save Covers:", self.chkSaveCovers
        )  # Download album artwork
        dl_options_layout.addRow(
            "Multi-Thread Download:", self.chkMultiThread
        )  # Parallel downloads
        dl_options_layout.addRow(
            "Use Download Delay:", self.chkDownloadDelay
        )  # Add delay between downloads
        dl_options_layout.addRow(
            "Use Playlist Folder:", self.chkUsePlaylistFolder
        )  # Create playlist folders

        # Add the form layout to the section
        dl_options_section.addLayout(dl_options_layout)
        # Add the completed section to the main layout
        self.mainLayout.addWidget(dl_options_section)

        # ======================================
        # === SECTION 5: UI & BEHAVIOR ===
        # ======================================
        # Create collapsible section for user interface and app behavior options
        # --- CORRECTED PATH ---
        ui_icon_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "assets",
                "icons",
                "ui-behavior-white.png",
            )
        )
        # --- END CORRECTION ---
        ui_section = CollapsibleSection("UI & Behavior", icon_path=ui_icon_path)

        # Use form layout for aligned label-control pairs
        ui_layout = QFormLayout()
        ui_layout.setContentsMargins(0, 5, 0, 5)  # Add vertical padding within section
        ui_layout.setSpacing(10)  # Space between form rows
        ui_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)  # Right-align labels

        # Add language selection dropdown
        # Populate the dropdown with available language options
        for lang in ["English", "Dutch"]:  # Supported languages in the application
            self.cmbLanguage.addItem(lang)
        ui_layout.addRow("Language:", self.cmbLanguage)

        # Add UI behavior options as checkboxes
        ui_layout.addRow(
            "Save .lrc Lyric File:", self.chkLyricFile
        )  # Save time-synced lyrics
        ui_layout.addRow(
            "Show Progress Bar:", self.chkShowProgress
        )  # Display download progress
        ui_layout.addRow(
            "Show Track Info:", self.chkShowTrackInfo
        )  # Show details during download
        ui_layout.addRow(
            "Save AlbumInfo.txt:", self.chkSaveAlbumInfo
        )  # Save album metadata

        # Add the form layout to the section
        ui_section.addLayout(ui_layout)
        # Add the completed section to the main layout
        self.mainLayout.addWidget(ui_section)

        # ======================================
        # === SECTION 6: CACHE SETTINGS ===
        # ======================================
        cacheSection = CollapsibleSection(
            "Cache Settings", icon_enum=QStyle.StandardPixmap.SP_DriveHDIcon
        )
        cacheLayout = QFormLayout()
        cacheLayout.setSpacing(10)
        cacheLayout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Cache Path Row
        cachePathLayout = QHBoxLayout()
        cachePathLayout.addWidget(self.cache_path_lineEdit)
        cachePathLayout.addWidget(self.browse_cache_button)
        cacheLayout.addRow(self.cache_path_label, cachePathLayout)

        # Cache TTL Row
        cacheLayout.addRow(self.cache_ttl_label, self.cache_ttl_spinBox)

        cacheSection.addLayout(cacheLayout)  # Use addLayout for QFormLayout
        self.mainLayout.addWidget(cacheSection)

        # Connect browse button signal
        self.browse_cache_button.clicked.connect(self._browse_cache_path)

        # Add a spacer at the end to push sections up when the window is taller than needed
        # This prevents the bottom of the last section from being stretched awkwardly
        spacer = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.mainLayout.addItem(spacer)

        # Set the main layout onto the container widget and put it in the scroll area
        # self.scrollWidget = scrollWidget # Already self.scrollWidget
        self.scrollArea.setWidget(
            self.scrollWidget
        )  # MODIFIED: Use self.scrollArea and self.scrollWidget
        self.pageLayout.addWidget(
            self.scrollArea
        )  # MODIFIED: Add self.scrollArea below the title

        # === BOTTOM NAVIGATION BUTTONS ===
        # Create a horizontal layout for the Back and Save buttons at the bottom of the page
        buttonLayout = QHBoxLayout()
        # Add margins (top, right, bottom) and spacing between buttons
        buttonLayout.setContentsMargins(0, 10, 15, 10)  # Add 15px right margin
        buttonLayout.setSpacing(10)  # Add 10px space between buttons

        # Apply consistent styling to both buttons to match the theme
        button_style = """
            QPushButton {
                background-color: #555;
                color: white;
                padding: 8px 15px;
                border: 1px solid #666;
                border-radius: 4px;
                min-width: 60px; /* Ensure buttons have some width */
            }
            QPushButton:hover {
                background-color: #666;
            }
            QPushButton:pressed {
                background-color: #444;
            }
        """
        self.btnBack.setStyleSheet(button_style)
        self.btnSave.setStyleSheet(button_style)

        # Add a stretch to push buttons to the right side of the window
        buttonLayout.addStretch(1)
        # Add the buttons in order (Back, then Save)
        buttonLayout.addWidget(self.btnBack)
        buttonLayout.addWidget(self.btnSave)
        # Add button layout to the bottom of the page layout
        self.pageLayout.addLayout(buttonLayout)

        # Connect button signals to their respective handler methods
        self.btnBack.clicked.connect(
            self.settingsClosedWithoutSaving.emit
        )  # Return without saving
        self.btnSave.clicked.connect(self.saveSettings)  # Save and return

        # Initialize all input controls with current settings values
        self.loadInitialSettings()

        # Connect quality combobox changes to update SETTINGS
        if self.audio_combo:
            self.audio_combo.currentIndexChanged.connect(
                self._handle_audio_quality_changed
            )

    def loadInitialSettings(self):
        """
        Populates all UI controls with current values from the global SETTINGS object.
        Uses placeholder text for default format strings.
        """
        try:
            # Account/API - Load API key index with fallback to 0 if not set
            current_api_index = getattr(SETTINGS, "apiKeyIndex", 0)
            self.cmbApiKeyIndex.setCurrentIndex(current_api_index)

            # Spotify API credentials - Essential for Spotify playlist import functionality
            self.spotifyClientIdInput.setText(getattr(SETTINGS, "spotifyClientId", ""))
            self.spotifyClientSecretInput.setText(
                getattr(SETTINGS, "spotifyClientSecret", "")
            )
            # Auto Spotify Login
            self.chkAutoSpotifyLogin.setChecked(
                getattr(SETTINGS, "autoSpotifyLogin", False)
            )

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
            self.chkCheckExist.setChecked(getattr(SETTINGS, "checkExist", False))
            self.chkIncludeEP.setChecked(getattr(SETTINGS, "includeEP", False))
            self.chkSaveCovers.setChecked(getattr(SETTINGS, "saveCovers", False))
            self.chkMultiThread.setChecked(getattr(SETTINGS, "multiThread", False))
            self.chkDownloadDelay.setChecked(getattr(SETTINGS, "downloadDelay", False))
            self.chkUsePlaylistFolder.setChecked(
                getattr(SETTINGS, "usePlaylistFolder", False)
            )
            # --- End Download Options ---

            # --- UI & Behavior - Use getattr for robustness ---
            self.cmbLanguage.setCurrentText(getattr(SETTINGS, "language", "English"))
            self.chkLyricFile.setChecked(getattr(SETTINGS, "lyricFile", False))
            self.chkShowProgress.setChecked(getattr(SETTINGS, "showProgress", False))
            self.chkShowTrackInfo.setChecked(getattr(SETTINGS, "showTrackInfo", False))
            self.chkSaveAlbumInfo.setChecked(getattr(SETTINGS, "saveAlbumInfo", False))
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
            logging.debug(
                f"Loaded Settings - checkExist: {self.chkCheckExist.isChecked()} (from {getattr(SETTINGS, 'checkExist', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - includeEP: {self.chkIncludeEP.isChecked()} (from {getattr(SETTINGS, 'includeEP', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - saveCovers: {self.chkSaveCovers.isChecked()} (from {getattr(SETTINGS, 'saveCovers', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - multiThread: {self.chkMultiThread.isChecked()} (from {getattr(SETTINGS, 'multiThread', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - downloadDelay: {self.chkDownloadDelay.isChecked()} (from {getattr(SETTINGS, 'downloadDelay', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - usePlaylistFolder: {self.chkUsePlaylistFolder.isChecked()} (from {getattr(SETTINGS, 'usePlaylistFolder', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - lyricFile: {self.chkLyricFile.isChecked()} (from {getattr(SETTINGS, 'lyricFile', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - showProgress: {self.chkShowProgress.isChecked()} (from {getattr(SETTINGS, 'showProgress', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - showTrackInfo: {self.chkShowTrackInfo.isChecked()} (from {getattr(SETTINGS, 'showTrackInfo', 'N/A')})"
            )
            logging.debug(
                f"Loaded Settings - saveAlbumInfo: {self.chkSaveAlbumInfo.isChecked()} (from {getattr(SETTINGS, 'saveAlbumInfo', 'N/A')})"
            )
            # --- End Debugging ---

        except Exception as e:
            # Log error but don't crash - use defaults if settings can't be loaded
            logging.error(f"Error loading initial settings: {e}", exc_info=True)
            QMessageBox.warning(
                self, "Settings Load Error", f"Could not load all settings: {e}"
            )

    def updateAccountButton(self):
        """
        Updates the account button text and style based on the current login state.

        This method checks if the user is logged into Tidal and updates the account
        button accordingly, showing either "Connect" (when logged out) or
        "Disconnect" with the username (when logged in). The button appearance
        is also adjusted to provide a visual cue about the login state.
        """
        from ..events import loginByConfig

        if loginByConfig():
            # User is logged in - show disconnect option
            self.btnAccount.setText("Disconnect")
            # Optional: Could set a different style when logged in
            # self.btnAccount.setStyleSheet("color: #66ccff;")
        else:
            # User is not logged in - show connect option
            self.btnAccount.setText("Connect")
            # Reset button styling to default
            # self.btnAccount.setStyleSheet("")

    @pyqtSlot()
    def toggleAccount(self):
        """
        Handles the Tidal account login or logout process.

        This method is triggered when the user clicks the account button. It will:
        - If not logged in: Initiate the device code login flow using Tidal's OAuth process
        - If logged in: Log the user out and reset the account state

        The login process uses a device code authentication flow where the user is
        presented with a URL to visit and a code to enter on the Tidal website.
        """
        from ..events import loginByConfig

        if not loginByConfig():
            # Not logged in: delegate to the AuthHandler to start the web login flow.
            # The handler will manage getting the URL, showing the dialog, and polling.
            if self.auth_handler:
                self.auth_handler.start_tidal_web_login()
            else:
                QMessageBox.critical(
                    self, "Login Error", "Authentication handler is not available."
                )
        else:
            # User is already logged in - perform logout
            # Logout from the Tidal API - No explicit logout in API, clear local credentials
            # TIDAL_API.logout() # Removed non-existent call
            SETTINGS.apiKeyIndex = 0  # Reset API key index? Or just clear token?
            # --- Corrected: Modify TOKEN object, not SETTINGS ---
            TOKEN.accessToken = None
            TOKEN.refreshToken = None
            TOKEN.userid = None
            TOKEN.countryCode = None
            TOKEN.expiresAfter = 0
            TOKEN.save()  # Save the cleared credentials
            # --- End Correction ---
            # Show confirmation message to user
            QtWidgets.QMessageBox.information(
                self, "Logout", "You have been disconnected."
            )

        # Update the button text to reflect the new login state
        self.updateAccountButton()

    @pyqtSlot()
    def _browse_cache_path(self):
        """Opens a dialog to select the cache directory."""
        directory = QFileDialog.getExistingDirectory(
            self, "Select Cache Directory", os.path.expanduser("~")
        )
        if directory:
            self.cache_path_lineEdit.setText(directory)

    @pyqtSlot()
    def browseDirectory(self):
        """
        Opens a directory selection dialog for choosing the download location.

        This method is connected to the "Browse" button next to the download path field.
        It opens the system's native folder selection dialog, starting at the current
        download path if one is set, and updates both the display field and the editable
        path setting when a folder is selected.
        """
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

    @pyqtSlot()
    def saveSettings(self):
        """
        Saves all settings from the UI controls to the global SETTINGS object.
        """
        try:
            # Store current Spotify credentials before updating
            old_spotify_client_id = getattr(SETTINGS, "spotifyClientId", "")
            old_spotify_client_secret = getattr(SETTINGS, "spotifyClientSecret", "")

            # Read values from widgets and save to SETTINGS object
            # --- Account/API Settings ---
            SETTINGS.apiKeyIndex = (
                self.cmbApiKeyIndex.currentData()
            )  # Get selected API profile index

            # --- Spotify API Integration ---
            new_spotify_client_id = self.spotifyClientIdInput.text()
            new_spotify_client_secret = self.spotifyClientSecretInput.text()
            SETTINGS.spotifyClientId = new_spotify_client_id
            SETTINGS.spotifyClientSecret = new_spotify_client_secret
            # Auto Spotify Login
            SETTINGS.autoSpotifyLogin = self.chkAutoSpotifyLogin.isChecked()

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
            SETTINGS.lyricFile = self.chkLyricFile.isChecked()
            SETTINGS.showProgress = self.chkShowProgress.isChecked()
            SETTINGS.showTrackInfo = self.chkShowTrackInfo.isChecked()
            SETTINGS.saveAlbumInfo = self.chkSaveAlbumInfo.isChecked()

            # --- Cache Settings ---
            # Save Cache Settings - Save None if path is empty
            cache_path_text = self.cache_path_lineEdit.text().strip()
            SETTINGS.playlistCoverCachePath = (
                cache_path_text if cache_path_text else None
            )
            SETTINGS.playlistCoverCacheTTL = self.cache_ttl_spinBox.value()

            # Persist settings to storage
            SETTINGS.save()
            logging.debug("Settings saved to storage")
            logging.debug(f"Saved download path: {SETTINGS.downloadPath}")
            # --- Debugging: Log saved boolean values ---
            logging.debug(f"Saved Settings - checkExist: {SETTINGS.checkExist}")
            logging.debug(f"Saved Settings - includeEP: {SETTINGS.includeEP}")
            logging.debug(f"Saved Settings - saveCovers: {SETTINGS.saveCovers}")
            logging.debug(f"Saved Settings - multiThread: {SETTINGS.multiThread}")
            logging.debug(f"Saved Settings - downloadDelay: {SETTINGS.downloadDelay}")
            logging.debug(
                f"Saved Settings - usePlaylistFolder: {SETTINGS.usePlaylistFolder}"
            )
            logging.debug(f"Saved Settings - lyricFile: {SETTINGS.lyricFile}")
            logging.debug(f"Saved Settings - showProgress: {SETTINGS.showProgress}")
            logging.debug(f"Saved Settings - showTrackInfo: {SETTINGS.showTrackInfo}")
            logging.debug(f"Saved Settings - saveAlbumInfo: {SETTINGS.saveAlbumInfo}")
            # --- End Debugging ---

            # Check if Spotify credentials were added or changed
            spotify_creds_changed = False
            if (new_spotify_client_id and new_spotify_client_secret) and (
                new_spotify_client_id != old_spotify_client_id
                or new_spotify_client_secret != old_spotify_client_secret
            ):
                spotify_creds_changed = True
                logging.info("Spotify credentials updated in settings.")

            # Show confirmation message to user
            QMessageBox.information(
                self, "Settings Saved", "Settings have been saved and applied."
            )

            # Emit signal to notify MainView to return to main menu
            self.settingsSavedAndClosed.emit()

            # Emit signal if Spotify credentials were added or changed
            if spotify_creds_changed:
                logging.debug("Emitting spotifyCredentialsUpdated signal.")
                self.spotifyCredentialsUpdated.emit()

        except Exception as e:
            # Show error dialog if settings couldn't be saved
            QMessageBox.critical(self, "Error", f"Error saving settings: {e}")

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
        # Use the stored self.scrollArea instance
        current_scroll_area = self.scrollArea
        # Add checks for spotify_section and its content_layout before accessing sizeHint
        spotify_section_size_hint_str = "N/A"
        if self.spotify_section:
            spotify_section_size_hint_str = str(self.spotify_section.sizeHint())

        if current_scroll_area:
            scroll_widget: Optional[QWidget] = current_scroll_area.widget()
            scrollbar = current_scroll_area.verticalScrollBar()
            scrollbar_visible = (
                scrollbar.isVisible() if scrollbar else False
            )  # Check visibility safely
            logging.debug(
                f"[toggleSpotifyHelp] Before toggle: lblSpotifyHelp.isVisible={self.lblSpotifyHelp.isVisible()}, spotify_section.sizeHint={spotify_section_size_hint_str}, "
                f'scroll_widget.sizeHint={scroll_widget.sizeHint() if scroll_widget else "N/A"}, scroll_area.verticalScrollBar.isVisible={scrollbar_visible}'
            )
        else:
            logging.warning(
                "[toggleSpotifyHelp] self.scrollArea is None at the start of toggleSpotifyHelp."
            )
            logging.debug(
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
            scroll_widget_after: Optional[QWidget] = self.scrollArea.widget()
            scrollbar_after = self.scrollArea.verticalScrollBar()
            scrollbar_visible_after = (
                scrollbar_after.isVisible() if scrollbar_after else False
            )  # Check visibility safely
            logging.debug(
                f"[toggleSpotifyHelp] After toggle: lblSpotifyHelp.isVisible={self.lblSpotifyHelp.isVisible()}, spotify_section.sizeHint={spotify_section_size_hint_str_after}, "
                f'scroll_widget.sizeHint={scroll_widget_after.sizeHint() if scroll_widget_after else "N/A"}, scroll_area.verticalScrollBar.isVisible={scrollbar_visible_after}'
            )
        else:
            logging.warning(
                "[toggleSpotifyHelp] self.scrollArea is None after toggle for logging."
            )
            logging.debug(
                f"[toggleSpotifyHelp] After toggle: lblSpotifyHelp.isVisible={self.lblSpotifyHelp.isVisible()}, spotify_section.sizeHint={spotify_section_size_hint_str_after}, scroll_widget=N/A, scroll_area=None"
            )

        # Optional: Change button text based on state

    @pyqtSlot()
    def togglePathFormatHelp(self):
        """Toggles the visibility of the path formatting help instructions."""
        current_visibility = self.lblPathFormatHelp.isVisible()
        self.lblPathFormatHelp.setVisible(not current_visibility)

        if self.lblPathFormatHelp.isVisible():
            self.btnPathFormatHelp.setText("Formatting Placeholders (Hide)")  # MODIFIED
        else:
            self.btnPathFormatHelp.setText("Formatting Placeholders (Show)")  # MODIFIED

        # If the section is currently expanded, tell it to update its content height.
        if self.paths_section and self.paths_section.is_expanded:
            self.paths_section.updateContentHeight()
        # If the section is collapsed, the change will be accounted for when it's next expanded.

        # Optional: Log geometry changes for debugging if needed
        if self.scrollArea:  # Check if scrollArea exists
            scroll_widget_after: Optional[QWidget] = self.scrollArea.widget()
            # Safely access sizeHint only if scroll_widget_after is not None
            scroll_widget_size_hint_str = (
                str(scroll_widget_after.sizeHint()) if scroll_widget_after else "N/A"
            )
            paths_section_size_hint_str = (
                str(self.paths_section.sizeHint()) if self.paths_section else "N/A"
            )

            logging.debug(
                f"[togglePathFormatHelp] After toggle: lblPathFormatHelp.isVisible={self.lblPathFormatHelp.isVisible()}, "
                f"paths_section.sizeHint={paths_section_size_hint_str}, "
                f"scroll_widget.sizeHint={scroll_widget_size_hint_str}"
            )
        else:
            logging.debug(
                f"[togglePathFormatHelp] After toggle: lblPathFormatHelp.isVisible={self.lblPathFormatHelp.isVisible()}, "
                f'paths_section.sizeHint={self.paths_section.sizeHint() if self.paths_section else "N/A"}, '
                f"scroll_widget.sizeHint=N/A (scrollArea is None)"
            )

    @pyqtSlot(int)
    def _handle_audio_quality_changed(self, index: int):
        """Updates the global audio quality setting."""
        if not self.audio_combo:
            return  # Safety check
        selected_quality = self.audio_combo.itemData(index)
        if isinstance(selected_quality, AudioQuality):
            if SETTINGS.audioQuality != selected_quality:
                SETTINGS.audioQuality = selected_quality
                SETTINGS.save()
                logging.info(f"Default audio quality set to {selected_quality.name}")
                Printf.info(f"Default audio quality set to {selected_quality.name}")  # type: ignore
        else:
            logging.error(
                f"Invalid data type retrieved from audio quality combobox at index {index}: {type(selected_quality)}"
            )


# --- END OF FILE gui_settings.py ---
