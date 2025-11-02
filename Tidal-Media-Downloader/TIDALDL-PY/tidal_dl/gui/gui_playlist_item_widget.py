# tidal_dl/gui/gui_playlist_item_widget.py

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon, QPixmap

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Set up GUI logging with DEBUG level for this module (need visibility for debugging)
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.DEBUG)

class PlaylistItemProgressWidget(QWidget):
    """A custom widget for displaying playlist status including a progress bar."""

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        logger.debug(f"🎯 WIDGET INITIALIZATION: Starting initialization for playlist '{name}'")
        logger.debug(f"🎯 WIDGET INITIALIZATION: Parent widget: {parent}")
        super().__init__(parent)
        
        logger.debug(f"🎯 WIDGET INITIALIZATION: Super().__init__ completed for '{name}'")
        logger.debug(f"🎯 WIDGET INITIALIZATION: Widget object created, setting up layouts for '{name}'")
        
        # --- Main Vertical Layout ---
        logger.debug(f"🎯 WIDGET INITIALIZATION: Creating main layout for '{name}'")
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 3, 5, 3) # Reduced left margin
        self.main_layout.setSpacing(2)
        logger.debug(f"🎯 WIDGET INITIALIZATION: Main layout configured for '{name}'")

        # --- Top Row Horizontal Layout (for Icon and Name) ---
        logger.debug(f"🎯 WIDGET INITIALIZATION: Creating top row layout for '{name}'")
        top_row_widget = QWidget()
        top_row_layout = QHBoxLayout(top_row_widget)
        top_row_layout.setContentsMargins(5, 0, 0, 0)
        top_row_layout.setSpacing(8)

        # --- NEW: Icon Label ---
        logger.debug(f"🎯 WIDGET INITIALIZATION: Creating icon label for '{name}'")
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(22, 22) # Consistent icon size
        self.icon_label.setScaledContents(True)
        top_row_layout.addWidget(self.icon_label)
        logger.debug(f"🎯 WIDGET INITIALIZATION: Icon label created and added for '{name}'")

        # --- Playlist Name Label ---
        logger.debug(f"🎯 WIDGET INITIALIZATION: Creating name label '{name}'")
        self.name_label = QLabel(name)
        font = self.name_label.font()
        font.setWeight(QFont.Weight.Medium)
        self.name_label.setFont(font)
        top_row_layout.addWidget(self.name_label)
        top_row_layout.addStretch() # Pushes content to the left
        logger.debug(f"🎯 WIDGET INITIALIZATION: Name label created and added for '{name}'")

        self.main_layout.addWidget(top_row_widget)
        logger.debug(f"🎯 WIDGET INITIALIZATION: Top row widget added to main layout for '{name}'")

        # --- Status Container Widget (to be shown/hidden) ---
        logger.debug(f"🎯 WIDGET INITIALIZATION: Creating status container for '{name}'")
        self.status_container = QWidget()
        self.status_layout = QVBoxLayout(self.status_container)
        self.status_layout.setContentsMargins(35, 2, 0, 0) # Indent progress to align with text
        self.status_layout.setSpacing(2)

        # Status Label (e.g., "Linking: 5/52")
        logger.debug(f"🎯 WIDGET INITIALIZATION: Creating status label for '{name}'")
        self.status_label = QLabel("Status")
        font = self.status_label.font()
        font.setPointSize(font.pointSize() - 2)
        self.status_label.setFont(font)
        self.status_label.setStyleSheet("color: #bbb;")
        self.status_layout.addWidget(self.status_label)
        logger.debug(f"🎯 WIDGET INITIALIZATION: Status label created for '{name}'")

        # Progress Bar and Percentage Layout
        logger.debug(f"🎯 WIDGET INITIALIZATION: Creating progress bar for '{name}'")
        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(5)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
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
        logger.debug(f"🎯 WIDGET INITIALIZATION: Progress bar style applied for '{name}'")
        progress_layout.addWidget(self.progress_bar)
        logger.debug(f"🎯 WIDGET INITIALIZATION: Progress bar created and added for '{name}'")

        self.percentage_label = QLabel("0%")
        font = self.percentage_label.font()
        font.setPointSize(font.pointSize() - 2)
        self.percentage_label.setFont(font)
        self.percentage_label.setFixedWidth(35)
        self.percentage_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percentage_label.setStyleSheet("color: #bbb;")
        progress_layout.addWidget(self.percentage_label)
        logger.debug(f"🎯 WIDGET INITIALIZATION: Percentage label created for '{name}'")

        self.status_layout.addLayout(progress_layout)
        self.main_layout.addWidget(self.status_container)

        # Initially hide the status part
        logger.debug(f"🎯 WIDGET INITIALIZATION: Hiding status container initially for '{name}'")
        self.status_container.setVisible(False)
        
        logger.debug(f"🎯 WIDGET INITIALIZATION: Widget '{name}' fully initialized")
        logger.debug(f"🎯 WIDGET INITIALIZATION: Final widget geometry - size: {self.size()}, sizeHint: {self.sizeHint()}")
        logger.debug(f"🎯 WIDGET INITIALIZATION: Status container visibility: {self.status_container.isVisible()}")
        logger.debug(f"🎯 WIDGET INITIALIZATION: Progress bar initial value: {self.progress_bar.value()}/{self.progress_bar.maximum()}")

    # --- NEW: Method to set the icon ---
    def set_icon(self, icon: QIcon):
        logger.debug(f"🖼️ ICON SETTING: Setting icon for widget '{self.objectName()}'")
        logger.debug(f"🖼️ ICON SETTING: Icon isNull: {icon.isNull()}")
        
        if not icon.isNull():
            logger.debug(f"🖼️ ICON SETTING: Converting icon to pixmap, size: {self.icon_label.size()}")
            pixmap = icon.pixmap(self.icon_label.size())
            logger.debug(f"🖼️ ICON SETTING: Pixmap created, size: {pixmap.size()}")
            self.icon_label.setPixmap(pixmap)
            logger.debug(f"🖼️ ICON SETTING: Icon pixmap set successfully")
        else:
            logger.debug(f"🖼️ ICON SETTING: Icon is null, clearing icon label")
            self.icon_label.clear()
            logger.debug(f"🖼️ ICON SETTING: Icon label cleared")
            
        logger.debug(f"🖼️ ICON SETTING: Icon setting completed for widget")

    def set_progress(self, current: int, total: int, action_text: str):
        """Updates the displayed status and progress."""
        logger.debug(f"📊 PROGRESS UPDATE: Called with current={current}, total={total}, action='{action_text}'")
        logger.debug(f"📊 PROGRESS UPDATE: Current status container visibility: {self.status_container.isVisible()}")
        
        if not self.status_container.isVisible():
            logger.debug(f"📊 PROGRESS UPDATE: Status container was hidden, showing it now")
            self.status_container.setVisible(True)
            self.updateGeometry()
            logger.debug(f"📊 PROGRESS UPDATE: Status container shown and geometry updated")
        else:
            logger.debug(f"📊 PROGRESS UPDATE: Status container already visible")

        logger.debug(f"📊 PROGRESS UPDATE: Updating status label text")
        self.status_label.setText(f"{action_text}: {current}/{total}")
        logger.debug(f"📊 PROGRESS UPDATE: Status label updated to: {f"{action_text}: {current}/{total}"}")
        
        logger.debug(f"📊 PROGRESS UPDATE: Setting progress bar max from {self.progress_bar.maximum()} to {total}")
        self.progress_bar.setMaximum(total)
        
        logger.debug(f"📊 PROGRESS UPDATE: Setting progress bar value from {self.progress_bar.value()} to {current}")
        self.progress_bar.setValue(current)
        logger.debug(f"📊 PROGRESS UPDATE: Progress bar now at {self.progress_bar.value()}/{self.progress_bar.maximum()}")
        
        percentage = 0
        if total > 0:
            percentage = int((current / total) * 100)
        
        logger.debug(f"📊 PROGRESS UPDATE: Calculated percentage: {percentage}%")
        self.percentage_label.setText(f"{percentage}%")
        logger.debug(f"📊 PROGRESS UPDATE: Percentage label updated to: {percentage}%")
    def _log_widget_state(self, context: str):
        """Log comprehensive state information for debugging."""
        logger.debug(f"📋 WIDGET STATE [{context}]: Widget='{self.objectName()}'")
        logger.debug(f"📋 WIDGET STATE [{context}]: Visible={self.isVisible()}, Hidden={self.isHidden()}")
        logger.debug(f"📋 WIDGET STATE [{context}]: Size={self.size()}, MinimumSize={self.minimumSize()}, MaximumSize={self.maximumSize()}")
        logger.debug(f"📋 WIDGET STATE [{context}]: SizeHint={self.sizeHint()}, SizePolicy={self.sizePolicy()}")
        logger.debug(f"📋 WIDGET STATE [{context}]: StatusContainer Visible={self.status_container.isVisible()}")
        logger.debug(f"📋 WIDGET STATE [{context}]: ProgressBar Value={self.progress_bar.value()}/{self.progress_bar.maximum()}")
        logger.debug(f"📋 WIDGET STATE [{context}]: PercentageLabel='{self.percentage_label.text()}'")
        logger.debug(f"📋 WIDGET STATE [{context}]: StatusLabel='{self.status_label.text()}'")
        logger.debug(f"📋 WIDGET STATE [{context}]: IconLabel hasPixmap={not self.icon_label.pixmap().isNull()}")
        
        # Log layout information
        try:
            main_geo = self.main_layout.geometry()
            logger.debug(f"📋 WIDGET STATE [{context}]: MainLayout geometry={main_geo}")
        except:
            logger.debug(f"📋 WIDGET STATE [{context}]: MainLayout geometry unavailable")
        
        try:
            status_geo = self.status_container.geometry()
            logger.debug(f"📋 WIDGET STATE [{context}]: StatusContainer geometry={status_geo}")
        except:
            logger.debug(f"📋 WIDGET STATE [{context}]: StatusContainer geometry unavailable")

    def showEvent(self, a0):
        """Override show event to add debugging."""
        logger.debug(f"👁️ SHOW EVENT: Widget '{self.objectName()}' showing")
        self._log_widget_state("SHOW_EVENT")
        super().showEvent(a0)
        logger.debug(f"👁️ SHOW_EVENT: Widget '{self.objectName()}' show completed")

    def hideEvent(self, a0):
        """Override hide event to add debugging."""
        logger.debug(f"🙈 HIDE EVENT: Widget '{self.objectName()}' hiding")
        self._log_widget_state("HIDE_EVENT")
        super().hideEvent(a0)
        logger.debug(f"🙈 HIDE_EVENT: Widget '{self.objectName()}' hide completed")

    def resizeEvent(self, a0):
        """Override resize event to add debugging."""
        if a0:
            logger.debug(f"📐 RESIZE EVENT: Widget '{self.objectName()}' resizing from {a0.oldSize()} to {a0.size()}")
        else:
            logger.debug(f"📐 RESIZE EVENT: Widget '{self.objectName()}' resize event with None parameter")
        self._log_widget_state("RESIZE_EVENT")
        super().resizeEvent(a0)
        logger.debug(f"📐 RESIZE_EVENT: Widget '{self.objectName()}' resize completed")

    def paintEvent(self, a0):
        """Override paint event to add debugging."""
        if a0:
            logger.debug(f"🎨 PAINT EVENT: Widget '{self.objectName()}' painting (rect={a0.rect()})")
        else:
            logger.debug(f"🎨 PAINT EVENT: Widget '{self.objectName()}' paint event with None parameter")
        super().paintEvent(a0)
        logger.debug(f"🎨 PAINT_EVENT: Widget '{self.objectName()}' paint completed")

    def changeEvent(self, a0):
        """Override change event to track various state changes."""
        event_type = a0.type() if a0 else None
        if event_type is not None:
            logger.debug(f"🔄 CHANGE EVENT: Widget '{self.objectName()}' change event type={event_type}")
            
            if event_type == 12:  # QEvent.Type.ParentChange
                logger.debug(f"🔄 CHANGE EVENT: Parent changed for '{self.objectName()}'")
            elif event_type == 14:  # QEvent.Type.LocaleChange
                logger.debug(f"🔄 CHANGE EVENT: Locale changed for '{self.objectName()}'")
            elif event_type == 17:  # QEvent.Type.FontChange
                logger.debug(f"🔄 CHANGE EVENT: Font changed for '{self.objectName()}'")
            elif event_type == 18:  # QEvent.Type.EnabledChange
                logger.debug(f"🔄 CHANGE EVENT: Enabled state changed for '{self.objectName()}'")
        else:
            logger.debug(f"🔄 CHANGE EVENT: Widget '{self.objectName()}' change event with None parameter")
        
        super().changeEvent(a0)
        logger.debug(f"🔄 CHANGE_EVENT: Widget '{self.objectName()}' change event completed")

    def _debug_tree_integration(self, action: str, details: str = ""):
        """Debug method to track integration with tree handler."""
        logger.debug(f"🌳 TREE INTEGRATION [{action}]: Widget='{self.objectName()}', Playlist='{self.name_label.text()}'")
        if details:
            logger.debug(f"🌳 TREE INTEGRATION [{action}]: Details: {details}")
        logger.debug(f"🌳 TREE INTEGRATION [{action}]: Widget visible={self.isVisible()}, status container={self.status_container.isVisible()}")
        logger.debug(f"🌳 TREE INTEGRATION [{action}]: Progress={self.progress_bar.value()}/{self.progress_bar.maximum()}")
        
    def __str__(self):
        """String representation for debugging."""
        return f"PlaylistItemProgressWidget(name='{self.name_label.text()}', visible={self.isVisible()}, progress={self.progress_bar.value()}/{self.progress_bar.maximum()})"

    def __repr__(self):
        """Detailed representation for debugging."""
        return (f"PlaylistItemProgressWidget("
                f"name='{self.name_label.text()}', "
                f"visible={self.isVisible()}, "
                f"status_visible={self.status_container.isVisible()}, "
                f"progress={self.progress_bar.value()}/{self.progress_bar.maximum()}, "
                f"percentage='{self.percentage_label.text()}', "
                f"status='{self.status_label.text()}')")
        
        # Force widget update to ensure UI reflects changes immediately
        logger.debug(f"📊 PROGRESS UPDATE: Forcing widget update")
        self.update()
        self.repaint()
        logger.debug(f"📊 PROGRESS UPDATE: Progress update completed")

    def reset_state(self):
        """Hides the progress indicators and restores the default view."""
        logger.debug(f"🔄 RESET STATE: Resetting widget state")
        logger.debug(f"🔄 RESET STATE: Current status container visibility: {self.status_container.isVisible()}")
        
        if self.status_container.isVisible():
            logger.debug(f"🔄 RESET STATE: Status container is visible, hiding it")
            self.status_container.setVisible(False)
            self.updateGeometry()
            logger.debug(f"🔄 RESET STATE: Status container hidden and geometry updated")
        else:
            logger.debug(f"🔄 RESET STATE: Status container already hidden, no action needed")
            
        # Reset progress bar to initial state
        logger.debug(f"🔄 RESET STATE: Resetting progress bar to 0/0")
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(100)  # Reset to default maximum
        self.percentage_label.setText("0%")
        logger.debug(f"🔄 RESET STATE: Progress bar and percentage label reset")
        
        logger.debug(f"🔄 RESET STATE: Widget state reset completed")

    def sizeHint(self) -> QSize:
        """Provide a dynamic size hint based on visibility."""
        logger.debug(f"📏 SIZE HINT: Calculating size hint")
        logger.debug(f"📏 SIZE HINT: Status container visible: {self.status_container.isVisible()}")
        logger.debug(f"📏 SIZE HINT: Main layout size hint: {self.main_layout.sizeHint()}")
        
        height = self.main_layout.sizeHint().height()
        width = super().sizeHint().width()
        result_size = QSize(width, height)
        
        logger.debug(f"📏 SIZE HINT: Calculated size hint: {result_size}")
        return result_size