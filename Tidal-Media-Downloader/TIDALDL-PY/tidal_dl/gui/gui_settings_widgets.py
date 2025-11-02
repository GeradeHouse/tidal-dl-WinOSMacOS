# file: gui_settings_widgets.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_settings_widgets.py
@Time    :   14-04-2025
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Contains custom widgets for the settings GUI.
"""

import logging
from typing import Optional

from PyQt6 import QtWidgets
from PyQt6.QtCore import (
    QPropertyAnimation,
    QEasingCurve,
    QSize,
)
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QWidget,
    QPushButton,
    QFrame,
    QVBoxLayout,
    QLayout,
    QStyle,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (settings widget operations need visibility)
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

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
                logger.warning("Could not obtain style to load standard icon.")
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
        logger.debug(
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
            logger.debug(
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
        logger.debug(
            f"[CollapsibleSection {self.toggle_button.text()}] Animation finished. content_area.sizeHint={self.content_area.sizeHint()}, self.sizeHint={self.sizeHint()}"
        )
        self.updateGeometry()

    def updateContentHeight(self):
        if not self.is_expanded:
            logger.debug(
                f"[{self.toggle_button.text()}] updateContentHeight called but section not expanded."
            )
            return

        logger.debug(
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
            logger.info(
                f"[{self.toggle_button.text()}] Content height changing. Animating from {current_content_height} to {new_target_height}. (Old max: {self.content_area.maximumHeight()})"
            )
            self.animation.stop()  # Stop any current animation
            self.animation.setStartValue(current_content_height)
            self.animation.setEndValue(new_target_height)
            self.animation.start()
        else:
            logger.debug(
                f"[{self.toggle_button.text()}] Content height effectively unchanged or invalid. New target: {new_target_height}, Current height: {current_content_height}, Current max: {self.content_area.maximumHeight()}"
            )