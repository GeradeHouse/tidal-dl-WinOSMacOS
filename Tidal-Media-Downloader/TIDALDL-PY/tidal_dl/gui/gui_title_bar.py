#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_title_bar.py
@Time    :   2025-04-26
@Author  :   GeradeHouse (Modified by Roo Code)
@Version :   1.0
@Desc    :   Custom Title Bar widget for the application window.
"""
from typing import Optional

import logging
import time  # Add time import
from PyQt6 import QtCore, QtGui  # Add base imports
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QSizePolicy,
    QApplication,
    QMenu,
)

# Add QObject to the import
from PyQt6.QtCore import Qt, QPoint, QSize, pyqtSignal, QEvent, QObject
from PyQt6.QtGui import QMouseEvent, QIcon, QPixmap, QCursor  # Add QCursor
from typing import cast  # Add cast
from .. import paths  # For resolving icon paths
import os
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (title bar operations)
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


# +++ START: Overlay Widget Definition +++
class TitleBarInteractionArea(QWidget):
    """A transparent widget to handle hover and drag events for the title bar's empty space."""

    THROTTLE_INTERVAL = 0.5  # Throttle interval for logging

    def __init__(self, parent: QWidget):  # Parent should be CustomTitleBar
        super().__init__(parent)
        self.setMouseTracking(True)
        # Make it transparent - WA_TranslucentBackground might be needed if styling doesn't work
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)  # Let parent show through
        # self.setStyleSheet("background-color: rgba(255, 0, 0, 80);") # Semi-transparent red for DEBUGGING

        # Drag state
        self._mouse_pressed: bool = False
        self._mouse_press_pos: QPoint = QPoint()
        self._window_pos_before_move: QPoint = QPoint()
        # Throttling timer
        self._last_log_time_move = 0.0

    def get_main_window(self) -> Optional[QWidget]:
        """Helper to get the main application window."""
        # Assumes parent is CustomTitleBar, and its parent is MainView
        # Cast parent to CustomTitleBar to satisfy Pylance about parent_window attribute
        custom_title_bar = cast("CustomTitleBar", self.parentWidget())
        if custom_title_bar and hasattr(custom_title_bar, "parent_window"):
            return custom_title_bar.parent_window
        return None

    # Rename 'event' to 'a0' to match base class signature (Fix Pylance warning)
    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        main_window = self.get_main_window()
        if not a0 or not main_window:
            super().mousePressEvent(a0)
            return

        # --- Check if press is on the top border ---
        BORDER_WIDTH = 5  # Define border width locally
        global_pos = a0.globalPosition().toPoint()
        window_rect = main_window.geometry()
        window_y = window_rect.y()
        relative_y = global_pos.y() - window_y
        is_on_top_border = relative_y >= 0 and relative_y < BORDER_WIDTH
        # +++ Add Detailed Log BEFORE the check +++
        logger.debug(
            "TitleBarInteractionArea.mousePressEvent: Checking Press. GlobalY=%s, WinY=%s, RelativeY=%s, IsOnTop=%s",
            global_pos.y(), window_y, relative_y, is_on_top_border
        )

        if is_on_top_border:
            # If press is on the top border, DO NOTHING here.
            # Let the event propagate to MainView for ResizeHandler.
            logger.debug(
                "TitleBarInteractionArea: Mouse press on top border. IGNORING, propagating event."
            )
            # Do not accept the event: a0.ignore() or just don't call accept()
            # Let super() handle propagation if necessary, but likely MainView will catch it.
            super().mousePressEvent(
                a0
            )  # Call super to ensure basic propagation if needed
        elif a0.button() == Qt.MouseButton.LeftButton:
            # If press is NOT on top border, initiate drag for this area.
            self._mouse_pressed = True
            self._mouse_press_pos = global_pos  # Already have global_pos
            self._window_pos_before_move = main_window.pos()
            logger.debug(
                "TitleBarInteractionArea: Mouse press detected (not top border), drag started."
            )
            a0.accept()  # Accept press event ONLY when starting drag here
        else:
            # Handle other buttons or cases by calling super
            super().mousePressEvent(a0)

    # Rename 'event' to 'a0' to match base class signature (Fix Pylance warning)
    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):
        main_window = self.get_main_window()
        if not a0 or not main_window:
            super().mouseMoveEvent(a0)
            return

        # --- Throttled Logging ---
        current_time = time.time()
        log_this_event = (
            current_time - self._last_log_time_move > self.THROTTLE_INTERVAL
        )

        if log_this_event:
            logger.debug(
                "TitleBarInteractionArea.mouseMoveEvent: GlobalPos=%s. Pressed=%s",
                a0.globalPosition().toPoint(), self._mouse_pressed
            )

        # --- Handle Top Edge Hover Cursor ---
        if not self._mouse_pressed:  # Only check hover if not dragging
            BORDER_WIDTH = 5  # Define border width locally
            global_pos = a0.globalPosition().toPoint()
            window_rect = main_window.geometry()
            window_y = window_rect.y()
            relative_y = global_pos.y() - window_y
            is_on_top_border = relative_y >= 0 and relative_y < BORDER_WIDTH

            if log_this_event:
                logger.debug(
                    "  HoverCheck: GlobalY=%s, WinY=%s, RelativeY=%s, IsOnTop=%s",
                    global_pos.y(), window_y, relative_y, is_on_top_border
                )

            current_cursor = main_window.cursor().shape()

            if is_on_top_border:
                if current_cursor != Qt.CursorShape.SizeVerCursor:
                    if log_this_event:
                        logger.debug("  HoverCheck: Setting SizeVerCursor.")
                    main_window.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                if current_cursor == Qt.CursorShape.SizeVerCursor:
                    if log_this_event:
                        logger.debug("  HoverCheck: Unsetting cursor.")
                    main_window.unsetCursor()

        # --- Handle Dragging ---
        elif (
            self._mouse_pressed
        ):  # Use elif because hover check is only when not pressed
            delta = a0.globalPosition().toPoint() - self._mouse_press_pos
            main_window.move(self._window_pos_before_move + delta)
            if log_this_event:
                logger.debug("  DragLogic: Moving window by %s.", delta)
            a0.accept()  # Accept move event during drag

        # --- Update throttle timer ---
        if log_this_event:
            self._last_log_time_move = current_time

        # Call super only if event wasn't accepted (i.e., not dragging)
        if not a0.isAccepted():
            super().mouseMoveEvent(a0)

    # Rename 'event' to 'a0' to match base class signature (Fix Pylance warning)
    def mouseReleaseEvent(self, a0: Optional[QMouseEvent]):
        if a0 and a0.button() == Qt.MouseButton.LeftButton and self._mouse_pressed:
            self._mouse_pressed = False
            logger.debug(
                "TitleBarInteractionArea: Mouse release detected, drag stopped."
            )
            a0.accept()  # Accept release event
        else:
            super().mouseReleaseEvent(a0)


# +++ END: Overlay Widget Definition +++


class CustomTitleBar(QWidget):
    s_showSettings = pyqtSignal()  # New signal
    # THROTTLE_INTERVAL REMOVED - No longer filtering here

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent  # Store reference to main window

        # +++ START: Added for rounded corners and transparency +++
        # This allows the corners of the title bar to be transparent,
        # assuming the main window is frameless and also has WA_TranslucentBackground.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # When WA_TranslucentBackground is True, setAutoFillBackground should typically be False
        # to allow stylesheets or paintEvent to handle all background painting.
        self.setAutoFillBackground(False)

        # +++ Add Debug Log +++
        logger.debug(
            "CustomTitleBar WA_TranslucentBackground: %s",
            self.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        )
        logger.debug("CustomTitleBar autoFillBackground: %s", self.autoFillBackground())
        # +++ End Debug Log +++
        # +++ END: Added for rounded corners and transparency +++

        self._icon_max = None
        self._icon_restore = None
        self.max_button = None  # Keep for icon toggling
        self.initUI()
        # +++ Add Debug Log for Stylesheet +++
        logger.debug("CustomTitleBar effective stylesheet: %s", self.styleSheet())
        # +++ End Debug Log +++

    # --- Menu Slots ---
    def show_menu(self):
        # Show the menu below the button
        menu_button = self.findChild(QPushButton)  # More robust way to find the button
        if menu_button:
            # Calculate position relative to the menu button
            # Map the button's bottom-left corner to global coordinates
            global_pos = menu_button.mapToGlobal(QPoint(0, menu_button.height()))
            self.menu.popup(global_pos)
        else:
            # Fallback position if button not found
            self.menu.popup(QtGui.QCursor.pos())

    def _on_settings_triggered(self):
        self.s_showSettings.emit()

    # --- Window Control Slots ---
    def minimize_window(self):
        if self.parent_window:
            self.parent_window.showMinimized()

    def maximize_restore_window(self):
        if self.parent_window:
            if self.parent_window.isMaximized():
                self.parent_window.showNormal()
                # switch to max icon
                if self.max_button and self._icon_max:
                    self.max_button.setIcon(self._icon_max)
            else:
                self.parent_window.showMaximized()
                # switch to restore icon
                if self.max_button and self._icon_restore:
                    self.max_button.setIcon(self._icon_restore)

    def close_window(self):
        if self.parent_window:
            self.parent_window.close()

    def initUI(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # Menu Button (Left)
        menu_button = QPushButton()
        menu_icon_path = paths.resource_path("assets/icons/menu.png")
        default_icon_size = QSize(24, 24)

        if os.path.exists(menu_icon_path):
            pixmap = QPixmap(menu_icon_path)
            if not pixmap.isNull():
                icon = QIcon(pixmap)
                menu_button.setIcon(icon)
                menu_button.setIconSize(default_icon_size)
                logger.debug(
                    "Loaded menu icon from: %s",
                    menu_icon_path.replace("\\", "/")
                )
            else:
                logger.error("Failed to load QPixmap from menu icon: %s", menu_icon_path)
                menu_button.setText("☰")
                menu_button.setIconSize(default_icon_size)
        else:
            logger.warning("Menu icon not found at: %s", menu_icon_path)
            menu_button.setText("☰")
            menu_button.setIconSize(default_icon_size)

        # Adjust button size and hover style like other controls
        menu_button.setFixedSize(menu_button.iconSize() + QSize(6, 6))
        menu_button.setStyleSheet(
            """
            QPushButton {
                border: none;
                background: transparent;
            }
            QPushButton:hover {
                background-color: #444444;
            }
        """
        )

        # Setup Menu
        self.menu = QMenu(self)
        self.settings_action = self.menu.addAction("Settings")
        menu_button.clicked.connect(self.show_menu)
        if self.settings_action:
            self.settings_action.triggered.connect(self._on_settings_triggered)

        layout.addWidget(menu_button)

        # Title Label (Center)
        title_label = QLabel("TIDAL-DL")
        title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        title_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        title_label.setContentsMargins(50, 0, 0, 0)  # Left, Top, Right, Bottom
        title_label.setStyleSheet("QLabel { font-weight: bold; color: #ccc; }")
        layout.addWidget(title_label)

        # +++ Add Interaction Area Overlay +++
        self.interaction_area = TitleBarInteractionArea(self)
        # Add with stretch factor to fill space between title and buttons
        layout.addWidget(self.interaction_area, 1)  # Stretch factor = 1

        # Window Control Buttons (Right)

        # Load custom icons
        min_icon_path = paths.resource_path("assets/icons/icon_min.png")
        max_icon_path = paths.resource_path("assets/icons/icon_max.png")
        restore_icon_path = paths.resource_path("assets/icons/icon_restore_down.png")
        close_icon_path = paths.resource_path("assets/icons/icon_close.png")
        icon_size = QSize(24, 24)

        # Minimize
        min_button = QPushButton()
        if os.path.exists(min_icon_path):
            pix = QPixmap(min_icon_path)
            if not pix.isNull():
                ic = QIcon(pix)
            else:
                ic = QIcon(QPixmap())
        else:
            ic = QIcon(QPixmap())
        min_button.setIcon(ic)
        min_button.setIconSize(icon_size)
        min_button.setFixedSize(QSize(30, 30))
        min_button.setStyleSheet(
            """
            QPushButton { border: none; background: transparent; }
            QPushButton:hover { background-color: #444444; }
        """
        )
        min_button.clicked.connect(self.minimize_window)
        layout.addWidget(min_button)

        # Maximize / Restore toggle
        self.max_button = QPushButton()
        # load both icons
        if os.path.exists(max_icon_path):
            pixm = QPixmap(max_icon_path)
            self._icon_max = QIcon(pixm) if not pixm.isNull() else QIcon(QPixmap())
        else:
            self._icon_max = QIcon(QPixmap())
        if os.path.exists(restore_icon_path):
            pixr = QPixmap(restore_icon_path)
            self._icon_restore = QIcon(pixr) if not pixr.isNull() else QIcon(QPixmap())
        else:
            self._icon_restore = QIcon(QPixmap())
        # set initial icon
        if self.parent_window and self.parent_window.isMaximized():
            self.max_button.setIcon(self._icon_restore)
        else:
            self.max_button.setIcon(self._icon_max)
        self.max_button.setIconSize(icon_size)
        self.max_button.setFixedSize(QSize(30, 30))
        self.max_button.setStyleSheet(
            """
            QPushButton { border: none; background: transparent; }
            QPushButton:hover { background-color: #444444; }
        """
        )
        self.max_button.clicked.connect(self.maximize_restore_window)
        layout.addWidget(self.max_button)

        # Close
        close_button = QPushButton()
        if os.path.exists(close_icon_path):
            pixc = QPixmap(close_icon_path)
            icc = QIcon(pixc) if not pixc.isNull() else QIcon(QPixmap())
        else:
            icc = QIcon(QPixmap())
        close_button.setIcon(icc)
        close_button.setIconSize(icon_size)
        close_button.setFixedSize(QSize(30, 30))
        close_button.setStyleSheet(
            """
            QPushButton { border: none; background: transparent; }
            QPushButton:hover { background-color: red; }
        """
        )
        close_button.clicked.connect(self.close_window)
        layout.addWidget(close_button)

        self.setLayout(layout)
        self.setFixedHeight(35)  # Set fixed height for the title bar

        # +++ START: Modified Stylesheet for rounded corners +++
        self.setStyleSheet(
            """
            CustomTitleBar {
                background-color: #2E2E2E; /* Ensure this is uncommented for visibility */
                border-top-left-radius: 10px; /* Adjust radius as needed */
                border-top-right-radius: 10px; /* Adjust radius as needed */
                border: none; /* Prevents default border painting */
            }
        """
        )
        # +++ END: Modified Stylesheet for rounded corners +++

    # Mouse event handlers (Simplified - Drag logic moved to overlay)
    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        # Just call super, drag initiated by overlay now
        super(CustomTitleBar, self).mousePressEvent(a0)

    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):
        # Just call super, drag handled by overlay now
        super(CustomTitleBar, self).mouseMoveEvent(a0)

    def mouseReleaseEvent(self, a0: Optional[QMouseEvent]):
        # Just call super, drag state managed by overlay now
        super().mouseReleaseEvent(a0)
