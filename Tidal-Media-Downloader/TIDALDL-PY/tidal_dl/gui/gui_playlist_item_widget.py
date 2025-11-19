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
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QPixmap

logger = logging.getLogger(__name__)
# Changed level to INFO to stop flood, though we removed the flood calls anyway
logger.setLevel(logging.INFO) 

# Set up GUI logging
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

class PlaylistItemProgressWidget(QWidget):
    """A custom widget for displaying playlist status including a progress bar."""
    
    # Signal emitted when the widget changes size (e.g., showing/hiding progress bar)
    geometryRequest = pyqtSignal()

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        # --- Main Vertical Layout ---
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 3, 5, 3)
        self.main_layout.setSpacing(2)

        # --- Top Row Horizontal Layout (for Icon and Name) ---
        top_row_widget = QWidget()
        top_row_layout = QHBoxLayout(top_row_widget)
        top_row_layout.setContentsMargins(5, 0, 0, 0)
        top_row_layout.setSpacing(8)

        # --- Icon Label ---
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(22, 22)
        self.icon_label.setScaledContents(True)
        top_row_layout.addWidget(self.icon_label)

        # --- Playlist Name Label ---
        self.name_label = QLabel(name)
        font = self.name_label.font()
        font.setWeight(QFont.Weight.Medium)
        self.name_label.setFont(font)
        top_row_layout.addWidget(self.name_label)
        top_row_layout.addStretch()

        self.main_layout.addWidget(top_row_widget)

        # --- Status Container Widget (to be shown/hidden) ---
        self.status_container = QWidget()
        self.status_layout = QVBoxLayout(self.status_container)
        self.status_layout.setContentsMargins(35, 2, 0, 0)
        self.status_layout.setSpacing(2)

        # Status Label
        self.status_label = QLabel("Status")
        font = self.status_label.font()
        font.setPointSize(font.pointSize() - 2)
        self.status_label.setFont(font)
        self.status_label.setStyleSheet("color: #bbb;")
        self.status_layout.addWidget(self.status_label)

        # Progress Bar and Percentage Layout
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
        progress_layout.addWidget(self.progress_bar)

        self.percentage_label = QLabel("0%")
        font = self.percentage_label.font()
        font.setPointSize(font.pointSize() - 2)
        self.percentage_label.setFont(font)
        self.percentage_label.setFixedWidth(35)
        self.percentage_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percentage_label.setStyleSheet("color: #bbb;")
        progress_layout.addWidget(self.percentage_label)

        self.status_layout.addLayout(progress_layout)
        self.main_layout.addWidget(self.status_container)

        # Initially hide the status part
        self.status_container.setVisible(False)

    def set_icon(self, icon: QIcon):
        if not icon.isNull():
            pixmap = icon.pixmap(self.icon_label.size())
            self.icon_label.setPixmap(pixmap)
        else:
            self.icon_label.clear()

    def set_progress(self, current: int, total: int, action_text: str):
        """Updates the displayed status and progress."""
        visibility_changed = False
        
        if not self.status_container.isVisible():
            self.status_container.setVisible(True)
            visibility_changed = True

        self.status_label.setText(f"{action_text}: {current}/{total}")
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        
        percentage = 0
        if total > 0:
            percentage = int((current / total) * 100)
        self.percentage_label.setText(f"{percentage}%")
        
        # If visibility changed, we need to notify the parent tree item to resize
        if visibility_changed:
            self.geometryRequest.emit()

    def reset_state(self):
        """Hides the progress indicators and restores the default view."""
        visibility_changed = False
        
        if self.status_container.isVisible():
            self.status_container.setVisible(False)
            visibility_changed = True
            
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(100)
        self.percentage_label.setText("0%")
        
        if visibility_changed:
            self.geometryRequest.emit()

    def sizeHint(self) -> QSize:
        """Provide a dynamic size hint based on visibility."""
        # Ensure layout is up to date before calculating
        self.main_layout.activate() 
        height = self.main_layout.sizeHint().height()
        width = super().sizeHint().width()
        return QSize(width, height)