# tidal_dl/gui/gui_playlist_item_widget.py

import logging
from typing import Optional

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QTreeWidget,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon, QPixmap

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Set up GUI logging with DEBUG level for this module (need visibility for debugging)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.DEBUG)

class PlaylistItemProgressWidget(QWidget):
    """A custom widget for displaying playlist status including a progress bar."""

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        # Set size policies to ensure proper sizing
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum
        )
        
        # --- Main Vertical Layout ---
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 3, 5, 3) # Reduced left margin
        self.main_layout.setSpacing(2)
        self.main_layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)

        # --- Top Row Horizontal Layout (for Icon and Name) ---
        top_row_widget = QWidget()
        top_row_layout = QHBoxLayout(top_row_widget)
        top_row_layout.setContentsMargins(5, 0, 0, 0)
        top_row_layout.setSpacing(8)

        # --- Icon Label ---
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(22, 22) # Consistent icon size
        self.icon_label.setScaledContents(True)
        self.icon_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed
        )
        top_row_layout.addWidget(self.icon_label)

        # --- Playlist Name Label ---
        self.name_label = QLabel(name)
        font = self.name_label.font()
        font.setWeight(QFont.Weight.Medium)
        self.name_label.setFont(font)
        self.name_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed
        )
        top_row_layout.addWidget(self.name_label)
        top_row_layout.addStretch() # Pushes content to the left

        self.main_layout.addWidget(top_row_widget)

        # --- Status Container Widget (to be shown/hidden) ---
        self.status_container = QWidget()
        self.status_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum
        )
        self.status_layout = QVBoxLayout(self.status_container)
        self.status_layout.setContentsMargins(35, 2, 0, 0) # Indent progress to align with text
        self.status_layout.setSpacing(2)

        # Status Label (e.g., "Linking: 5/52")
        self.status_label = QLabel("Status")
        font = self.status_label.font()
        font.setPointSize(font.pointSize() - 2)
        self.status_label.setFont(font)
        self.status_label.setStyleSheet("color: #bbb;")
        self.status_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed
        )
        self.status_layout.addWidget(self.status_label)

        # Progress Bar and Percentage Layout
        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(5)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed
        )
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555;
                border-radius: 4px;
                background-color: #333;
            }
            QProgressBar::chunk {
                background-color: #0078d4;
                border-radius: 3px;
            }
        """)
        progress_layout.addWidget(self.progress_bar)

        self.percentage_label = QLabel("0%")
        font = self.percentage_label.font()
        font.setPointSize(font.pointSize() - 2)
        self.percentage_label.setFont(font)
        self.percentage_label.setFixedWidth(35)
        self.percentage_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percentage_label.setStyleSheet("color: #bbb;")
        self.percentage_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed
        )
        progress_layout.addWidget(self.percentage_label)

        self.status_layout.addLayout(progress_layout)
        self.main_layout.addWidget(self.status_container)

        # Initially hide the status part
        self.status_container.setVisible(False)
        
        # Force initial layout calculation
        self.updateGeometry()
        self.main_layout.invalidate()
        self.main_layout.activate()

    def set_icon(self, icon: QIcon):
        if not icon.isNull():
            pixmap = icon.pixmap(self.icon_label.size())
            self.icon_label.setPixmap(pixmap)
        else:
            self.icon_label.clear()

    def set_progress(self, current: int, total: int, action_text: str):
        """Updates the displayed status and progress."""
        was_visible = self.status_container.isVisible()
        
        # Log only when progress container becomes visible (operation starts)
        if not was_visible:
            logger.debug(f"📊 START: '{self.name_label.text()}' {action_text}: {current}/{total}")
        
        # Log only completion (100%)
        new_percentage = int((current / max(total, 1)) * 100) if total > 0 else 0
        if new_percentage == 100:
            logger.debug(f"📊 COMPLETE: '{self.name_label.text()}' {current}/{total} (100%)")
        
        if not self.status_container.isVisible():
            # CRITICAL FIX: Force comprehensive tree widget layout recalculation
            tree_widget = None
            parent = self.parent()
            while parent and not isinstance(parent, QTreeWidget):
                parent = parent.parent()
            if isinstance(parent, QTreeWidget):
                tree_widget = parent
                # Step 1: Force tree widget to completely recalculate its layout
                tree_widget.updateGeometry()
                # Step 2: Force viewport to update and repaint
                viewport = tree_widget.viewport()
                if viewport:
                    viewport.update()
                    viewport.repaint()
                logger.debug(f"🔴🔴🔴 LAYOUT: Comprehensive tree layout update for '{self.name_label.text()}'")
            
            # Show the progress container
            self.status_container.setVisible(True)
            
            # Force widget layout updates
            self.updateGeometry()
            self.repaint()
            
            layout = self.layout()
            if layout:
                layout.invalidate()
                layout.activate()
            
            # Step 3: Force the tree widget to do a complete layout recalculation
            if tree_widget:
                # This is the key fix: force the tree to recalculate ALL row heights
                # by temporarily expanding and collapsing to trigger a full layout recalculation
                # PyQt6 fix: isExpanded() requires a QModelIndex parameter
                root_index = tree_widget.rootIndex()
                was_expanded = tree_widget.isExpanded(root_index) if root_index.isValid() else True
                if was_expanded:
                    tree_widget.collapseAll()
                tree_widget.expandAll()
                
                # Force a more aggressive layout update
                tree_widget.update()
                viewport = tree_widget.viewport()
                if viewport:
                    viewport.update()
                    viewport.repaint()
                
                # Force the tree's internal model to update layout
                try:
                    # Removed problematic signal emission - layout update handled by tree_widget.update() above
                except (AttributeError, RuntimeError):
                    # Model might not be available in some cases
                    pass
            
            # Update parent layout if exists
            parent_layout = self.parent().layout() if self.parent() else None
            if parent_layout:
                parent_layout.invalidate()
                parent_layout.activate()

        self.status_label.setText(f"{action_text}: {current}/{total}")
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        
        percentage = 0
        if total > 0:
            percentage = int((current / total) * 100)
        
        self.percentage_label.setText(f"{percentage}%")

    def reset_state(self):
        """Hides the progress indicators and restores the default view."""
        if self.status_container.isVisible():
            logger.debug(f"🔄 RESET: '{self.name_label.text()}'")
            self.status_container.setVisible(False)
            
            # Force layout recalculation when hiding
            self.updateGeometry()
            
            # Additional layout recalculation to ensure proper display
            layout = self.layout()
            if layout:
                layout.invalidate()
                layout.activate()
            
            # Update parent layout if exists
            parent_layout = self.parent().layout() if self.parent() else None
            if parent_layout:
                parent_layout.invalidate()
                parent_layout.activate()
            
            self.progress_bar.setValue(0)
            self.progress_bar.setMaximum(100)  # Reset to default maximum
            self.percentage_label.setText("0%")

    def sizeHint(self) -> QSize:
        """Provide a dynamic size hint based on visibility."""
        height = self.main_layout.sizeHint().height()
        width = super().sizeHint().width()
        return QSize(width, height)

    def showEvent(self, a0):
        """Override show event to debug visibility changes."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"🎭 SHOW: '{self.name_label.text()}' showEvent - visible: {self.isVisible()}, size: {self.sizeHint()}")
        super().showEvent(a0)

    def hideEvent(self, a0):
        """Override hide event to debug visibility changes."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"🎭 HIDE: '{self.name_label.text()}' hideEvent - visible: {self.isVisible()}")
        super().hideEvent(a0)

    def resizeEvent(self, a0):
        """Override resize event to debug size changes."""
        # Only log resize events in debug mode to avoid console clutter
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"📏 RESIZE: '{self.name_label.text()}' resized to {self.size()}")
        super().resizeEvent(a0)

    def paintEvent(self, a0):
        """Override paint event to debug rendering."""
        super().paintEvent(a0)

    def changeEvent(self, a0):
        """Override change event to debug state changes."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"🎭 CHANGE: '{self.name_label.text()}' changeEvent - type: {a0.type()}")
        super().changeEvent(a0)