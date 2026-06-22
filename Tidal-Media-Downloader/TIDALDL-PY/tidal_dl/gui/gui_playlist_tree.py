# --- START OF FILE gui_playlist_tree.py ---

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_playlist_tree.py
@Time    :   2025/04/27
@Author  :   GeradeHouse (Refactored by Roo)
@Version :   1.0
@Desc    :   Defines the UI widget for the playlist tree panel.
"""

import contextlib
import logging
import os
import time
from typing import Optional

from PyQt6 import QtWidgets, QtGui
from PyQt6.QtCore import Qt, QSize, QModelIndex, QTimer
from PyQt6 import QtCore
from PyQt6.QtWidgets import (
    QTreeWidgetItem,
)  # Removed QStyledItemDelegate, QStyleOptionViewItem
from PyQt6.QtGui import QPainter, QColor, QPaintEvent, QIcon, QBrush  # Added QBrush
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTreeWidget,
    QPushButton,
    QLineEdit,  # <-- Add QLineEdit
)

from tidal_dl import paths

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Set up GUI logging with INFO level for this module (playlist operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


# --- Custom Widget for Left Panel Background (Moved from gui.py) ---
class LeftPanelWidget(QWidget):
    """A QWidget subclass that paints its own semi-transparent background."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAutoFillBackground(True)

    def paintEvent(self, a0: Optional[QPaintEvent]):
        """Fills the widget background with the desired semi-transparent color."""
        painter = QPainter(self)
        color = QColor("#09090AF5")
        painter.fillRect(self.rect(), color)


# --- Playlist Tree Widget ---
# --- Custom Tree Widget for Hover Handling ---
class HoverAwareTreeWidget(QTreeWidget):
    """A QTreeWidget subclass that manually manages hover state for reliability."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._currently_hovered_item: Optional[QTreeWidgetItem] = None
        self._hover_role = (
            Qt.ItemDataRole.UserRole + 1
        )  # Keep for state tracking if needed elsewhere
        self._last_mousemove_log_time = 0  # Timestamp of the last log
        self._mousemove_log_interval = 0.3  # Log at most every 300ms

        # Define colors for hover effect (can be customized)
        self.hover_background_color = QtGui.QColor(
            Qt.GlobalColor.blue
        )  # MANUAL HOVER IS BLUE
        self.hover_text_color = QtGui.QColor(Qt.GlobalColor.white)  # For debugging
        self.default_background_color = QtGui.QColor(
            Qt.GlobalColor.transparent
        )  # Or your default item background
        self.default_text_color = QtGui.QColor(
            Qt.GlobalColor.white
        )  # Or your default item text color

    def _clear_current_hover_item(self) -> None:
        current_item = self._currently_hovered_item
        self._currently_hovered_item = None

        if current_item is None:
            return

        with contextlib.suppress(RuntimeError):
            self._apply_item_style(current_item, False)

    def mouseMoveEvent(self, event: Optional[QtGui.QMouseEvent]):
        """Handles mouse movement to update hover state without keeping deleted items alive."""
        if event is None:
            with contextlib.suppress(RuntimeError):
                super().mouseMoveEvent(event)
            return

        try:
            pos = event.position().toPoint()
            item_under_mouse = self.itemAt(pos)
        except RuntimeError:
            self._currently_hovered_item = None
            return

        if item_under_mouse is not self._currently_hovered_item:
            self._clear_current_hover_item()

            if item_under_mouse is not None:
                with contextlib.suppress(RuntimeError):
                    self._apply_item_style(item_under_mouse, True)
                self._currently_hovered_item = item_under_mouse

        with contextlib.suppress(RuntimeError):
            super().mouseMoveEvent(event)

    def leaveEvent(self, a0: Optional[QtCore.QEvent]):
        """Clears hover state when the pointer leaves the widget."""
        self._clear_current_hover_item()
        with contextlib.suppress(RuntimeError):
            super().leaveEvent(a0)

    def _apply_item_style(self, item: QTreeWidgetItem, hover: bool):
        """Requests repaint for hover styling while tolerating deleted tree items."""
        if item is None:
            return

        try:
            item_rect = self.visualItemRect(item)
        except RuntimeError:
            return

        try:
            viewport = self.viewport()
        except RuntimeError:
            return

        if viewport and item_rect.isValid():
            viewport.update(item_rect)


# HoverItemDelegate class removed as per simplification
class PlaylistTreeWidget(LeftPanelWidget):
    """
    Widget containing the playlist tree view and associated controls (e.g., Spotify connect button).
    Inherits from LeftPanelWidget to get the custom background painting.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_widgets()
        self._init_layout()
        self.setMinimumWidth(200)

    def _init_widgets(self):
        """Initializes the widgets within this panel."""
        self.tree_widget = HoverAwareTreeWidget()
        self.tree_widget.setObjectName("playlistTreeWidget")
        self.tree_widget.setStyleSheet("""
            QTreeWidget {
                background: transparent;
                border: none;
                outline: 0;
            }

            QTreeWidget::item {
                border: 1px solid transparent;
                padding: 2px 4px;
                margin: 1px 2px;
            }

            QTreeWidget::item:selected {
                border: none;
                outline: 0;
                background: rgba(255, 255, 255, 0.10);
            }

            QTreeWidget::item:focus {
                outline: 0;
                border: none;
            }
        """)

        # --- Filter Input ---
        self.filter_input_widget = QLineEdit()
        self.filter_input_widget.setPlaceholderText("Filter playlists...")
        self.filter_input_widget.setClearButtonEnabled(True)  # Add a clear button
        # Style the filter input field
        self.filter_input_widget.setStyleSheet(
            """
            QLineEdit {
                background-color: #242429; /* From global QLineEdit style */
                color: #ffffff;            /* Assuming white text */
                border: 1px solid #444444; /* From global QLineEdit style */
                border-radius: 8px;        /* User request */
                padding: 3px;              /* Existing padding */
                margin-left:5px;         /* User request */
                margin-right: 5px;        /* User request */
                margin-top: 0px;           /* User request */
                margin-bottom: 0px;        /* User request */
            }
            QLineEdit:hover { /* ADDED HOVER STATE */
                border: 1px solid #777; /* Lighter border on hover, consistent with settings */
            }
            QLineEdit:focus { /* ADDED FOCUS STATE for consistency */
                border: 1px solid #0078d4; /* Standard focus blue, consistent with settings */
            }
        """
        )

        self.spotify_connect_button = QPushButton(" Connect to Spotify")
        self.spotify_connect_button.setObjectName("spotifyConnectButton")
        self.spotify_connect_button.setProperty("attention", False)
        spotify_icon_path = paths.resource_path(
            "assets/icons/Spotify_Primary_Logo_RGB_White.png"
        )
        if os.path.exists(spotify_icon_path):
            connect_icon = QIcon(spotify_icon_path)
            if not connect_icon.isNull():
                self.spotify_connect_button.setIcon(connect_icon)
                self.spotify_connect_button.setIconSize(QSize(24, 24))
        else:
            logger.warning(f"Spotify connect icon not found at: {spotify_icon_path}")

        self._spotify_connect_base_style = """
            QPushButton#spotifyConnectButton {
                text-align: left;
                border: 1px solid transparent;
                border-radius: 8px;
                background: transparent;
                padding: 6px 8px;
                color: white;
                font-weight: 600;
            }
            QPushButton#spotifyConnectButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.18);
            }
        """
        self._spotify_connect_attention_style = """
            QPushButton#spotifyConnectButton {
                text-align: left;
                border: 1px solid rgba(255, 190, 80, 0.75);
                border-radius: 8px;
                background: rgba(255, 190, 80, 0.18);
                padding: 6px 8px;
                color: #fff5df;
                font-weight: 700;
            }
            QPushButton#spotifyConnectButton:hover {
                background-color: rgba(255, 190, 80, 0.28);
                border: 1px solid rgba(255, 210, 120, 0.95);
            }
        """
        self._spotify_connect_attention_timer = QTimer(self)
        self._spotify_connect_attention_timer.setInterval(700)
        self._spotify_connect_attention_timer.timeout.connect(
            self._pulse_spotify_connect_attention
        )
        self.spotify_connect_button.setStyleSheet(self._spotify_connect_base_style)
        self.spotify_connect_button.setVisible(False)

    def set_spotify_connect_attention(self, enabled: bool, reason: str = "") -> None:
        """Highlights the Spotify connect button when the user must repair Spotify credentials."""
        self.spotify_connect_button.setProperty("attention", bool(enabled))
        self.spotify_connect_button.setToolTip(reason if enabled else "")
        self.spotify_connect_button.setStyleSheet(
            self._spotify_connect_attention_style if enabled else self._spotify_connect_base_style
        )
        if enabled:
            if not self._spotify_connect_attention_timer.isActive():
                self._spotify_connect_attention_timer.start()
        else:
            self._spotify_connect_attention_timer.stop()
            self.spotify_connect_button.setGraphicsEffect(None)

    def _pulse_spotify_connect_attention(self) -> None:
        if not self.spotify_connect_button.property("attention"):
            self._spotify_connect_attention_timer.stop()
            self.spotify_connect_button.setGraphicsEffect(None)
            return

        if self.spotify_connect_button.styleSheet() == self._spotify_connect_attention_style:
            self.spotify_connect_button.setStyleSheet(self._spotify_connect_base_style)
        else:
            self.spotify_connect_button.setStyleSheet(self._spotify_connect_attention_style)

    def _init_layout(self):
        """Sets up the layout for this panel."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 2, 6)
        layout.setSpacing(10)
        layout.addWidget(self.filter_input_widget)  # Add filter input at the top
        layout.addWidget(self.spotify_connect_button)
        layout.addWidget(self.tree_widget)

    def get_tree_widget(self):
        return self.tree_widget

    def get_spotify_connect_button(self):
        return self.spotify_connect_button

    def get_filter_input_widget(self):
        return self.filter_input_widget


# --- END OF FILE gui_playlist_tree.py ---
