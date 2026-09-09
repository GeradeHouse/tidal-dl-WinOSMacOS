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
        self.setObjectName("playlistItemProgressWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        
        # --- Main Vertical Layout ---
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 5, 8, 5)
        self.main_layout.setSpacing(2)
        # The tree owns row geometry; do not force it to match long label widths.
        self.main_layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)

        # --- Top Row Horizontal Layout (for Icon and Name) ---
        top_row_widget = QWidget()
        top_row_layout = QHBoxLayout(top_row_widget)
        top_row_layout.setContentsMargins(5, 0, 0, 0)
        top_row_layout.setSpacing(8)

        # --- Icon Label ---
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(22, 22)
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
        top_row_layout.addStretch()

        self.main_layout.addWidget(top_row_widget)

        # --- Status Container Widget (to be shown/hidden) ---
        self.status_container = QWidget()
        self.status_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum
        )
        self.status_layout = QVBoxLayout(self.status_container)
        self.status_layout.setContentsMargins(35, 3, 6, 0)
        self.status_layout.setSpacing(2)

        # Status Label
        self.status_label = QLabel("Status")
        font = self.status_label.font()
        font.setPointSize(font.pointSize() - 2)
        self.status_label.setFont(font)
        self.status_label.setStyleSheet("color: #bbb;")
        self.status_label.setWordWrap(False)
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

    def _refresh_geometry(self) -> None:
        """Refresh local layout metrics and request parent row relayout."""
        self.main_layout.invalidate()
        self.updateGeometry()
        self.geometryRequest.emit()

    def set_drop_target_active(self, active: bool) -> None:
        """Applies the visual state used while dragged tracks hover over this playlist."""
        active = bool(active)

        if self.property("spotifyDropTarget") == active:
            return

        self.setProperty("spotifyDropTarget", active)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, active)

        if active:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Drop selected tracks to add them to this Spotify playlist.")
            self.setStyleSheet("""
                PlaylistItemProgressWidget {
                    background-color: rgba(29, 185, 84, 42);
                    border: 1px solid rgba(29, 185, 84, 210);
                    border-radius: 8px;
                }
                PlaylistItemProgressWidget QLabel {
                    background: transparent;
                    border: none;
                    color: #ffffff;
                }
                PlaylistItemProgressWidget QProgressBar {
                    background-color: rgba(0, 0, 0, 95);
                    border: 1px solid rgba(255, 255, 255, 45);
                    border-radius: 4px;
                }
            """)
        else:
            self.unsetCursor()
            self.setToolTip("")
            self.setStyleSheet("")

        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_icon(self, icon: QIcon):
        if not icon.isNull():
            pixmap = icon.pixmap(self.icon_label.size())
            self.icon_label.setPixmap(pixmap)
        else:
            self.icon_label.clear()

    def set_progress(self, current: int, total: int, action_text: str):
        """Updates the displayed status and progress."""
        visibility_changed = False
        progress_was_visible = self.progress_bar.isVisible()
        percentage_was_visible = self.percentage_label.isVisible()
        action_text_normalized = str(action_text or "").strip().lower()
        is_missing_check_phase = action_text_normalized in {
            "checking missing tracks",
            "calculating missing tracks",
        }
        
        if not self.status_container.isVisible():
            self.status_container.setVisible(True)
            visibility_changed = True

        safe_total = max(0, int(total))
        safe_current = max(0, int(current))

        if is_missing_check_phase:
            # Show an indeterminate (busy) bar while existence checks are running.
            self.status_label.setText(action_text)
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
            self.percentage_label.setVisible(False)
            if visibility_changed or (not progress_was_visible) or percentage_was_visible:
                self._refresh_geometry()
            return

        if safe_total <= 0:
            # For non-count states (e.g. "All tracks already completed"), show status text only.
            self.status_label.setText(action_text)
            self.progress_bar.setVisible(False)
            self.percentage_label.setVisible(False)
            if visibility_changed or progress_was_visible or percentage_was_visible:
                self._refresh_geometry()
            return

        # Ensure progress bar is visible (might be hidden by set_queued)
        self.progress_bar.setVisible(True)
        self.percentage_label.setVisible(True)
        self.progress_bar.setRange(0, safe_total)

        self.status_label.setText(f"{action_text}: {safe_current}/{safe_total}")
        self.progress_bar.setMaximum(safe_total)
        self.progress_bar.setValue(min(safe_current, safe_total))

        percentage = int((min(safe_current, safe_total) / safe_total) * 100)
        self.percentage_label.setText(f"{percentage}%")

        # Request geometry recalculation when row height might have changed.
        if visibility_changed or (not progress_was_visible) or (not percentage_was_visible):
            self._refresh_geometry()

    def set_queued(self):
        """Sets the widget to a 'Queued' state."""
        visibility_changed = False
        progress_was_visible = self.progress_bar.isVisible()
        percentage_was_visible = self.percentage_label.isVisible()
        
        if not self.status_container.isVisible():
            self.status_container.setVisible(True)
            visibility_changed = True
             
        self.status_label.setText("Queued to be processed")
        # Hide progress bar elements for cleaner look
        self.progress_bar.setVisible(False)
        self.percentage_label.setVisible(False)
        
        if visibility_changed or progress_was_visible or percentage_was_visible:
            self._refresh_geometry()

    def reset_state(self):
        """Hides the progress indicators and restores the default view."""
        visibility_changed = False
        
        if self.status_container.isVisible():
            self.status_container.setVisible(False)
            visibility_changed = True
            
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(100)
        self.percentage_label.setText("0%")
        self.progress_bar.setVisible(True) # Reset visibility
        self.percentage_label.setVisible(True)
        
        if visibility_changed:
            self._refresh_geometry()

    def sizeHint(self) -> QSize:
        """Read layout metrics without changing geometry during tree layout."""
        return self.main_layout.sizeHint()
