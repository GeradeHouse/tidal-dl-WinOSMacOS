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

import logging
import os
import time
from typing import Optional

from PyQt6 import QtWidgets, QtGui
from PyQt6.QtCore import Qt, QSize, QModelIndex
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

from .. import paths

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


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

    def mouseMoveEvent(self, event: Optional[QtGui.QMouseEvent]):
        """Handles mouse movement to update the hover state manually."""
        # current_time = time.time()
        # if current_time - self._last_mousemove_log_time > self._mousemove_log_interval:
        #     logger.debug(f"mouseMoveEvent triggered at {event.position().toPoint() if event else 'N/A'}")
        #     self._last_mousemove_log_time = current_time
        # logger.debug(f"mouseMoveEvent triggered at {event.position().toPoint() if event else 'N/A'}")

        if event is None:
            # logger.debug("mouseMoveEvent: event is None, returning")
            super().mouseMoveEvent(event)
            return

        pos = event.position().toPoint()
        item_under_mouse = self.itemAt(pos)
        item_under_mouse_text = item_under_mouse.text(0) if item_under_mouse else "None"

        currently_hovered_text = (
            self._currently_hovered_item.text(0)
            if self._currently_hovered_item
            else "None"
        )
        # logger.debug(f"mouseMoveEvent: Pos: {pos}, ItemAtMouse: '{item_under_mouse_text}', PrevHovered: '{currently_hovered_text}'")

        if item_under_mouse != self._currently_hovered_item:
            # logger.debug(f"mouseMoveEvent: Hovered item changed. Old: '{currently_hovered_text}', New: '{item_under_mouse_text}'")

            if self._currently_hovered_item is not None:
                # logger.debug(f"mouseMoveEvent: Attempting to UN-HOVER (manual): '{self._currently_hovered_item.text(0)}'")
                self._apply_item_style(self._currently_hovered_item, False)
                # Force an immediate repaint of the old item's area for diagnostics
                # self.viewport().update(self.visualItemRect(self._currently_hovered_item)) # Already there
                # self.viewport().repaint() # Add for more aggressive diagnostic repaint

            if item_under_mouse is not None:
                # logger.debug(f"mouseMoveEvent: Attempting to HOVER (manual): '{item_under_mouse.text(0)}'")
                self._apply_item_style(item_under_mouse, True)
                # Force an immediate repaint of the new item's area for diagnostics
                # self.viewport().update(self.visualItemRect(item_under_mouse)) # Already there
                # self.viewport().repaint() # Add for more aggressive diagnostic repaint

            self._currently_hovered_item = item_under_mouse
            # logger.debug(f"mouseMoveEvent: Updated _currently_hovered_item to '{item_under_mouse_text}'")

        super().mouseMoveEvent(event)

    def leaveEvent(self, a0: Optional[QtCore.QEvent]):
        """Clears hover state when the mouse leaves the widget."""
        # logger.debug(f"leaveEvent triggered. Current hovered: '{self._currently_hovered_item.text(0) if self._currently_hovered_item else 'None'}'")
        if self._currently_hovered_item is not None:
            # logger.debug(f"leaveEvent: Clearing hover state (manual) for item '{self._currently_hovered_item.text(0)}'")
            self._apply_item_style(self._currently_hovered_item, False)
            # self.viewport().repaint() # Add for more aggressive diagnostic repaint
            self._currently_hovered_item = None
        else:
            # logger.debug("leaveEvent: No item was hovered.")
            pass
        super().leaveEvent(a0)

    def _apply_item_style(self, item: QTreeWidgetItem, hover: bool):
        """Applies or removes hover styling directly to the item."""
        item_text = item.text(0) if item else "None"
        action = "APPLYING MANUAL HOVER" if hover else "REMOVING MANUAL HOVER"
        # logger.debug(f"_apply_item_style: {action} for item '{item_text}'")

        # Delegate will now handle visual hover styling (background/foreground).
        # This method can be kept for other hover-related data if needed,
        # or simplified if only visual styling was its purpose.
        # For now, let's prevent it from changing background/foreground.
        # if hover:
        #     # item.setBackground(0, QBrush(self.hover_background_color)) # Disabled
        #     # item.setForeground(0, QBrush(self.hover_text_color)) # Disabled
        #     item.setData(0, self._hover_role, "true")
        # else:
        #     # item.setBackground(0, QBrush(self.default_background_color)) # Disabled
        #     # item.setForeground(0, QBrush(self.default_text_color)) # Disabled
        #     item.setData(0, self._hover_role, "false")

        # The item.setData calls for _hover_role can remain if this role is used elsewhere
        # to track hover state programmatically.

        item_rect = self.visualItemRect(item)
        # logger.debug(f"  Item '{item_text}' visualRect: {item_rect}. Requesting update.")

        viewport = self.viewport()
        if viewport and item_rect.isValid():
            viewport.update(item_rect)
            # For extreme diagnostics, force a full repaint after every style change
            # logger.debug(f"  Calling viewport.repaint() after styling '{item_text}'")
            # viewport.repaint()
        else:
            logger.warning(
                f"  Viewport or item_rect invalid for '{item_text}'. Viewport: {viewport}, Rect valid: {item_rect.isValid()}"
            )


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

        self.spotify_connect_button.setStyleSheet(
            "QPushButton { text-align: left; border: none; background: transparent; padding: 4px; color: white; }"
            "QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }"
        )
        self.spotify_connect_button.setVisible(False)

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
