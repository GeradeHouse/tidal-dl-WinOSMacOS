#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :  gui_table.py
@Date    :  2022/03/28
@Author  :  Yaronzz
@Modified by: GeradeHouse
@Version :  1.0
@Contact :  yaronhuang@foxmail.com
@Desc    :  This module implements a custom table widget using PyQt6.
A custom table widget that displays data in columns with interactive selection capabilities.
This table widget extends QTableWidget to provide specialized behavior for displaying and selecting
rows of data, typically representing media tracks for download. The widget supports:
- Row-based selection with standard single-click replacement and distinctive highlighting
- Shift-click for range selection
- Ctrl-click/Cmd-click for toggling individual row selection
- Smart column resizing that maintains specific columns at fixed widths while
    dynamically adjusting others to fill available space
- Clipboard operations (Ctrl+C for copying selected rows)
Key features:
- Columns are resizable by dragging their headers
- Columns 1 (Title) and 2 (Artist) automatically resize to fill available space
- Columns 0 (Index), 3 (Length), and 4 (Quality) maintain fixed widths
- Selected rows are highlighted with a light blue background
Parameters:
        column_names (list): A list of strings representing the header labels for each column
        parent (QWidget, optional): The parent widget. Defaults to None.
Usage example:
        table = SplitterTable(["Index", "Title", "Artist", "Length", "Quality"])
        table.addRow(["1", "Song Title", "Artist Name", "3:45", "FLAC"], track_object)
        # Get indices of selected rows
        selected_indices = table.getSelectedRows()
"""

# Standard library imports
import os
import logging
import time
from typing import List, Dict, Optional, Any, Set, cast

# Third-party imports
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
)
import aigpy

# Local application imports
from tidal_dl.model import Track
from tidal_dl.printf import Printf
from tidal_dl.settings import SETTINGS
from tidal_dl.paths import getSettingsFilePath
from .gui_table_delegate import HighlightPreservingDelegate
from .gui_table_candidate_widget import CandidateWidget

# --- Setup Logging ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set specific level for this module

TABLE_ROW_HEIGHT = 36
TABLE_COVER_ICON_SIZE = 26

_CAMELOT_WHEEL_COLORS: Dict[int, str] = {
    1: "#e53935",
    2: "#fb8c00",
    3: "#fdd835",
    4: "#c0ca33",
    5: "#43a047",
    6: "#00897b",
    7: "#00acc1",
    8: "#1e88e5",
    9: "#3949ab",
    10: "#ab47bc",
    11: "#ec407a",
    12: "#f06292",
}

# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from tidal_dl.gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

# The SelectableLabel and ColumnWidget classes have been replaced by a QTableWidget–based implementation.
# The new SplitterTable class below implements a spreadsheet–like widget that supports row selection
# for download. Clicking on any cell in a row selects that row, while Ctrl/Cmd-click toggles
# individual rows; selected rows are highlighted with a uniform background color.
#
# At initialization:
#   - All columns (0 "Index", 1 "Title", 2 "Artist", 3 "Length", and 4 "Quality") are set to be interactive,
#     so that the user can change their width by dragging the handler.
#   - After initialization, the widget adjusts columns 1 and 2 to fill the available space (using the full viewport width).
#   - When the window is resized, columns 1 and 2 will grow/shrink to use the extra (or reduced) space while columns 0, 3 and 4 retain their widths.
# The user is free to manually resize any column afterward.

# --- Custom Viewport for Background Image (Removed) ---
# The BackgroundViewport class was removed as the background is now handled via stylesheets
# applied in gui_app_setup.py to the QScrollArea's viewport.


class SplitterTable(QtWidgets.QTableWidget):
    # Add a signal to SplitterTable to forward the candidate selection from the sub-widget
    candidateSelectedInSubRow = pyqtSignal(
        int, object
    )  # Args: main_row_index, selected_track
    noMatchSelectedInSubRow = pyqtSignal(int) # Arg: main_row_index
    candidatePreviewRequestedInSubRow = pyqtSignal(int, object)
    candidateAutoReviewAcceptedInSubRow = pyqtSignal(int)
    candidateUnlinkRequestedInSubRow = pyqtSignal(int)

    def __init__(self, column_names, parent=None):
        super().__init__(parent)
        self.setObjectName("splitterTrackTable")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setLineWidth(0)
        self.setMouseTracking(True)
        viewport = cast(Any, self.viewport())
        if viewport is not None:
            viewport.setMouseTracking(True)
            viewport.setAutoFillBackground(False)
            viewport.setStyleSheet("background: transparent; border: none;")
        self.setSortingEnabled(True)  # Enable sorting
        self.linking_gui_handler = None
        self.setColumnCount(len(column_names))
        self.setHorizontalHeaderLabels(column_names)
        # Hide the vertical header to prevent displaying a numbered list in the first and second row.
        v_header = self.verticalHeader()
        if v_header:  # Add check
            v_header.setVisible(False)
        # Allow the user to drag column headers to resize columns.
        # Enable stretchLastSection to make the last column fill available space.
        h_header = self.horizontalHeader()
        if h_header:  # Add check
            h_header.setStretchLastSection(True)  # Make last section stretch
            h_header.setSortIndicatorShown(True)  # Show sort indicator
            h_header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            h_header.customContextMenuRequested.connect(
                self._show_column_visibility_menu
            )
            # Apply stylesheet for smaller header padding without visible cell borders.
            base_header_style = (
                "QHeaderView { background-color: transparent; border: none; }"
                "QHeaderView::section { "
                "background-color: transparent; "
                "padding-top: 1px; padding-bottom: 1px; "
                "padding-left: 4px; padding-right: 4px; "
                "border: none; "
                "}"
                "QTableCornerButton::section { background-color: transparent; border: none; }"
            )
            h_header.setStyleSheet(base_header_style)  # Apply base style first
        # Set compact TIDAL-like row height while preserving larger row artwork.
        v_header = self.verticalHeader()
        if v_header:
            v_header.setDefaultSectionSize(TABLE_ROW_HEIGHT)
            v_header.setMinimumSectionSize(TABLE_ROW_HEIGHT)
        self.setIconSize(QtCore.QSize(TABLE_COVER_ICON_SIZE, TABLE_COVER_ICON_SIZE))
        self.setShowGrid(False)
        self.setGridStyle(Qt.PenStyle.NoPen)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        vertical_scrollbar = cast(Any, self.verticalScrollBar())
        if vertical_scrollbar is not None:
            vertical_scrollbar.setSingleStep(14)
        # Use ExtendedSelection to allow selecting multiple rows.
        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        # Enable row–based selection.
        self.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        # Disable editing since the focus is solely on row selection.
        self.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )  # Disable editing
        self.setItemDelegate(HighlightPreservingDelegate(self))  # Add this line
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setAlternatingRowColors(False)
        self.setStyleSheet(
            """
            QTableWidget#splitterTrackTable {
                background-color: transparent;
                alternate-background-color: transparent;
                gridline-color: transparent;
                border: none;
                outline: 0;
                selection-background-color: rgba(58, 58, 63, 0.86);
                selection-color: #ffffff;
            }
            QTableWidget#splitterTrackTable::item {
                background-color: transparent;
                border: none;
                padding: 0px;
            }
            QTableWidget#splitterTrackTable::item:hover {
                background-color: transparent;
                border: none;
            }
            QTableWidget#splitterTrackTable::item:selected {
                background-color: rgba(58, 58, 63, 0.86);
                border: none;
            }
            QTableWidget#splitterTrackTable::item:focus {
                outline: 0;
                border: none;
            }
            QHeaderView {
                background-color: transparent;
                border: none;
            }
            QHeaderView::section {
                background-color: transparent;
                color: #d8d8d8;
                padding: 1px 4px;
                border: none;
                font-weight: bold;
            }
            QTableCornerButton::section {
                background-color: transparent;
                border: none;
            }
            """
        )

        # Initially, set the resize modes for all columns to Interactive so that the user can change each column's width.
        h_header = self.horizontalHeader()  # Get header again
        if h_header:  # Add check
            for col in range(len(column_names)):
                h_header.setSectionResizeMode(
                    col, QtWidgets.QHeaderView.ResizeMode.Interactive
                )

            # Construct absolute paths for icons relative to this script's location
            script_dir = os.path.dirname(__file__)
            icon_up_path = os.path.abspath(
                os.path.join(script_dir, "..", "assets", "icons", "icon-up-arrow.png")
            ).replace("\\", "/")
            icon_down_path = os.path.abspath(
                os.path.join(script_dir, "..", "assets", "icons", "icon-down-arrow.png")
            ).replace("\\", "/")

            # Get existing borderless style to append to
            base_header_style = h_header.styleSheet()

            # Define styles for sort indicators
            indicator_style = f"""
                QHeaderView::up-arrow {{
                    image: url({icon_up_path});
                    width: 12px; /* Adjust size as needed */
                    height: 12px;
                }}
                QHeaderView::down-arrow {{
                    image: url({icon_down_path});
                    width: 12px; /* Adjust size as needed */
                    height: 12px;
                }}
            """
            # Combine and set the full stylesheet
            h_header.setStyleSheet(base_header_style + indicator_style)

        self.apply_column_visibility_preferences()

        # Data structures for row selection.
        self.selectedRows = set()
        self.lastClickedRow = None
        self._selection_before_mouse_press = set()
        self._hoveredRow = -1
        self._playlist_header_widget = None

        # Variables for mouse–based toggling.
        self._mousePressPos = None
        self._mousePressRow = None
        self._dragging = False
        self._cached_header_signature: tuple[str, ...] = tuple()
        self._cached_length_col_index: int = -1
        self._last_adjust_signature: tuple[str, ...] = tuple()
        self.drag_threshold = 5  # pixels

        # State for tracking column resize drag (using signals now)
        self._initial_widths_on_press = []

        # Debounced logger removed, logic moved to resizeEvent

        # Connect header signals for resize logger
        h_header = self.horizontalHeader()  # Get header again
        if h_header:  # Add check
            h_header.sectionPressed.connect(self._handle_section_pressed)
            h_header.sectionResized.connect(self._handle_section_resized)
            # Connect sort indicator changed to collapse sub-rows
            h_header.sortIndicatorChanged.connect(self.on_sort_indicator_changed)

        self.itemSelectionChanged.connect(self._on_selection_model_changed)

    def set_linking_gui_handler(self, handler):
        self.linking_gui_handler = handler

    def set_playlist_header_widget(self, playlist_header_widget: Any) -> None:
        self._playlist_header_widget = playlist_header_widget

    # Removed _set_initial_column_widths and _restore_interactive_resize_modes methods

    def clearRows(self):
        """
        Removes all rows from the table and resets row selection tracking.
        """
        self.setRowCount(0)
        self.selectedRows.clear()
        self.lastClickedRow = None
        self._selection_before_mouse_press = set()
        self._hoveredRow = -1

    def _is_toggle_selection_modifier(self, modifiers) -> bool:
        """Return True when the platform toggle-selection modifier is pressed."""
        toggle_modifiers = (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        )
        return bool(modifiers & toggle_modifiers)

    def _get_selection_model_rows(self) -> Set[int]:
        """Return the row indices currently selected by Qt's selection model."""
        selection_model = self.selectionModel()
        if not selection_model:
            return set()
        return {
            index.row()
            for index in selection_model.selectedRows()
            if 0 <= index.row() < self.rowCount()
        }

    def _apply_selected_rows_to_selection_model(self) -> None:
        """Synchronize Qt's selection model with the custom selectedRows set."""
        selection_model = self.selectionModel()
        model = self.model()
        if not selection_model or not model:
            return

        valid_rows = {
            row
            for row in self.selectedRows
            if 0 <= row < self.rowCount()
        }
        self.selectedRows = valid_rows

        if not valid_rows or self.columnCount() <= 0:
            selection_model.clearSelection()
            return

        item_selection = QtCore.QItemSelection()
        for row in sorted(valid_rows):
            top_left_index = model.index(row, 0)
            bottom_right_index = model.index(row, self.columnCount() - 1)
            if top_left_index.isValid() and bottom_right_index.isValid():
                item_selection.select(top_left_index, bottom_right_index)

        selection_model.select(
            item_selection,
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QtCore.QItemSelectionModel.SelectionFlag.Rows,
        )

    @QtCore.pyqtSlot()
    def _on_selection_model_changed(self) -> None:
        """Keep custom highlighting state aligned with native Qt selection changes."""
        self.selectedRows = self._get_selection_model_rows()
        self._update_row_selection_visuals()

    def _get_length_column_index(self) -> int:
        """Resolve and cache the current 'Length' column index based on header labels."""
        header_signature_parts: List[str] = []
        for i in range(self.columnCount()):
            header_item = self.horizontalHeaderItem(i)
            header_signature_parts.append(header_item.text() if header_item else "")
        header_signature = tuple(header_signature_parts)
        if header_signature != self._cached_header_signature:
            self._cached_header_signature = header_signature
            self._cached_length_col_index = -1
            for i, header_text in enumerate(header_signature):
                if header_text == "Length":
                    self._cached_length_col_index = i
                    break
        return self._cached_length_col_index

    def _get_column_index_by_header(self, header_name: str) -> int:
        """Resolve a column index by header label."""
        for i in range(self.columnCount()):
            header_item = self.horizontalHeaderItem(i)
            if header_item and header_item.text() == header_name:
                return i
        return -1

    def _extract_camelot_number(self, value: str) -> Optional[int]:
        text = str(value or "").strip().upper()
        if len(text) < 2:
            return None
        if text[-1] not in {"A", "B"}:
            return None
        number_text = text[:-1].strip()
        if not number_text.isdigit():
            return None
        number = int(number_text)
        if 1 <= number <= 12:
            return number
        return None

    def _get_camelot_wheel_color(self, value: str) -> Optional[QtGui.QColor]:
        number = self._extract_camelot_number(value)
        if number is None:
            return None
        color_hex = _CAMELOT_WHEEL_COLORS.get(number)
        if not color_hex:
            return None
        return QtGui.QColor(color_hex)

    def setRowData(
        self,
        row: int,
        row_data,
        track=None,
        apply_row_style: bool = True,
    ) -> None:
        """Populate an existing row index with table data."""
        if row < 0:
            return

        if row >= self.rowCount():
            self.setRowCount(row + 1)

        quality_col_index = self._get_column_index_by_header("Quality")
        total_cols = self.columnCount()
        centered_headers = {"Release Year", "BPM", "Key", "Spotify Key", "TIDAL Key", "Length"}

        for col_index in range(total_cols):
            cell_text = str(row_data[col_index]) if col_index < len(row_data) else ""
            item = QtWidgets.QTableWidgetItem(cell_text)
            item.setToolTip(cell_text)
            header_item = self.horizontalHeaderItem(col_index)
            header_text = header_item.text() if header_item else ""

            # Right-align '#' column (index 0)
            if col_index == 0:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            elif header_text in centered_headers:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )

            # Set tooltip for Quality column
            if col_index == quality_col_index:
                item.setToolTip(cell_text)

            # Store original item data (Track object or Spotify dict) in the title column (column 1)
            if col_index == 1 and track is not None:
                item.setData(QtCore.Qt.ItemDataRole.UserRole, track)
            # Initialize indicator data: no candidates, not expanded in the indicator column (column 0)
            elif col_index == 0:
                item.setData(
                    QtCore.Qt.ItemDataRole.UserRole,
                    {"has_candidates": False, "expanded": False},
                )

            # Make items selectable and enabled. Editing is disabled.
            item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled
            )
            self.setItem(row, col_index, item)

        if apply_row_style:
            self._update_row_appearance_for_row(row)

    def setRowCoverPixmap(self, row: int, pixmap: QtGui.QPixmap) -> None:
        if pixmap.isNull() or row < 0 or row >= self.rowCount():
            return

        title_column = self._get_column_index_by_header("Title")
        if title_column < 0:
            title_column = 1
        item = self.item(row, title_column)
        if item is None:
            return

        icon_pixmap = pixmap.scaled(
            TABLE_COVER_ICON_SIZE,
            TABLE_COVER_ICON_SIZE,
            QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        item.setIcon(QtGui.QIcon(icon_pixmap))
        self.setRowHeight(row, TABLE_ROW_HEIGHT)

    def addRow(self, row_data, track=None):
        """
        Adds a new row to the table and populates each column with a QTableWidgetItem.
        Stores Track object in the title column's user data.
        """
        row = self.rowCount()
        self.setRowData(row, row_data, track=track, apply_row_style=True)
        # Do not adjust columns here to preserve any user resizing on columns 0, 3, and 4.
        # The auto–resize behavior for columns 1 and 2 is handled during widget initialization and resize events.

    def rowCount(self):
        """
        Returns the current number of rows in the table.
        """
        return super().rowCount()

    def updateCell(self, row, col, text):
        """
        Updates the cell at (row, col) with new text, inserting rows if needed.
        """
        if row < self.rowCount() and col < self.columnCount():
            item = self.item(row, col)
            if item is not None:
                item.setText(str(text))
            else:
                item = QtWidgets.QTableWidgetItem(str(text))
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsSelectable
                    | QtCore.Qt.ItemFlag.ItemIsEnabled
                )
                self.setItem(row, col, item)
            self._update_row_appearance_for_row(row)
        else:
            while self.rowCount() <= row:
                self.insertRow(self.rowCount())
            self.updateCell(row, col, text)

    # Fix parameter name mismatch: col -> column
    def item(self, row: int, column: int) -> Optional[QTableWidgetItem]:
        """
        Returns the QTableWidgetItem at (row, column) or None if invalid.
        """
        try:
            return super().item(row, column)
        except Exception:
            return None

    def mousePressEvent(self, e: QtGui.QMouseEvent | None) -> None:
        """
        Records the initial mouse position and the row index at the press location.
        """
        if e:
            self._selection_before_mouse_press = set(self.selectedRows)
            self._mousePressPos = e.pos()
            index = self.indexAt(self._mousePressPos)
            self._mousePressRow = index.row() if index.isValid() else None
            self._dragging = False
        else:
            # Handle the case where e is None, perhaps reset state or log
            self._selection_before_mouse_press = set()
            self._mousePressPos = None
            self._mousePressRow = None
            self._dragging = False
        super().mousePressEvent(e)

    def _set_hovered_row(self, row: int) -> None:
        if row == self._hoveredRow:
            return
        old_row = self._hoveredRow
        self._hoveredRow = row
        for affected_row in (old_row, row):
            if 0 <= affected_row < self.rowCount():
                for col in range(self.columnCount()):
                    item = self.item(affected_row, col)
                    if item is not None:
                        viewport = cast(Any, self.viewport())
                        if viewport is not None:
                            viewport.update(self.visualItemRect(item))

    def wheelEvent(self, a0: QtGui.QWheelEvent | None) -> None:
        e = a0
        if e is not None:
            playlist_header = getattr(self, "_playlist_header_widget", None)
            consume = getattr(playlist_header, "consume_wheel_event_for_header", None)
            if callable(consume) and consume(e):
                e.accept()
                return
        super().wheelEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent | None) -> None:
        """
        Monitors mouse movement to determine if the user is dragging.
        """
        if e:
            index = self.indexAt(e.pos())
            self._set_hovered_row(index.row() if index.isValid() else -1)
            super().mouseMoveEvent(e)
            if self._mousePressPos is not None and not self._dragging:
                if (
                    e.pos() - self._mousePressPos
                ).manhattanLength() > self.drag_threshold:
                    self._dragging = True

    def leaveEvent(self, a0: QtCore.QEvent | None) -> None:
        self._set_hovered_row(-1)
        super().leaveEvent(a0)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent | None) -> None:
        """
        If the mouse is released without dragging, applies standard row selection behavior.
        Supports SHIFT-click for range selection and Ctrl/Cmd-click for toggling rows.
        """
        super().mouseReleaseEvent(e)
        if e is None:
            return  # Event might be None, exit early
        # Proceed only if the event is valid and it's a left button release without dragging
        if (
            e.button() == Qt.MouseButton.LeftButton
            and not self._dragging
            and self._mousePressRow is not None
        ):
            clicked_index = self.indexAt(e.pos())
            if clicked_index.isValid():
                clicked_row = clicked_index.row()
                clicked_col = clicked_index.column()

                # Check if the click was in the indicator column (column 0) and the row has candidates
                if clicked_col == 0:
                    indicator_item = self.item(clicked_row, 0)
                    indicator_data = (
                        indicator_item.data(QtCore.Qt.ItemDataRole.UserRole)
                        if indicator_item
                        else {}
                    )
                    if indicator_data.get("has_candidates", False):
                        logger.debug(
                            f"[SplitterTable] Indicator column (0) clicked on row {clicked_row}. Toggling expand."
                        )
                        self._toggle_expand(clicked_row)
                        e.accept()  # Consume the event if we handled the expand toggle
                        self._mousePressPos = None
                        self._mousePressRow = None
                        self._dragging = False
                        self._selection_before_mouse_press = set(self.selectedRows)
                        return  # Stop further processing for this click

                # If not an indicator click, proceed with normal selection logic
                if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._handle_shift_click(clicked_row)
                elif self._is_toggle_selection_modifier(e.modifiers()):
                    logger.debug(
                        f"[SplitterTable] Toggle-modifier click detected on row {clicked_row}"
                    )
                    self._toggle_row_selection(clicked_row)
                else:
                    logger.debug(
                        f"[SplitterTable] Single Click detected on row {clicked_row}"
                    )
                    self._handle_single_click(clicked_row)

                self._apply_selected_rows_to_selection_model()
                logger.debug(
                    f"[SplitterTable] mouseReleaseEvent finished click handling. SelectedRows: {self.selectedRows}"
                )
                self._update_row_selection_visuals()

        elif e.button() == Qt.MouseButton.LeftButton and self._dragging:
            # Update selection state after drag
            self.selectedRows = self._get_selection_model_rows()

            # Update last clicked row based on release position
            index_at_release = self.indexAt(e.pos())
            if index_at_release.isValid():
                self.lastClickedRow = index_at_release.row()
            logger.debug(
                f"[SplitterTable] mouseReleaseEvent finished drag handling. SelectedRows: {self.selectedRows}, lastClickedRow: {self.lastClickedRow}"
            )
            self._update_row_selection_visuals()

        self._mousePressPos = None
        self._mousePressRow = None
        self._dragging = False
        self._selection_before_mouse_press = set(self.selectedRows)

    def _handle_single_click(self, row):
        """
        Handles a single click: select only the clicked row.
        """
        self.selectedRows = {row}
        self.lastClickedRow = row
        # Log after single click handling
        logger.debug(
            f"[SplitterTable] _handle_single_click finished for row {row}. SelectedRows: {self.selectedRows}"
        )

    def _handle_shift_click(self, row):
        """
        Handles SHIFT–click to select a range of rows.
        """
        # Initialize start/end to handle unbound variable warning
        start = -1
        end = -1
        if self.lastClickedRow is None:
            self.selectedRows.add(row)
        else:
            start = min(self.lastClickedRow, row)
            end = max(self.lastClickedRow, row)
            # For simplicity, SHIFT–click always selects the range.
            for r in range(start, end + 1):
                self.selectedRows.add(r)
        self.lastClickedRow = row
        # Log after shift click handling (remove duplicate log)
        logger.debug(
            f"[SplitterTable] _handle_shift_click finished for row {row}. Range: {start}-{end}. SelectedRows: {self.selectedRows}"
        )

    def _toggle_row_selection(self, row):
        """
        Toggles the row selection for Ctrl/Cmd-click.
        """
        selected_rows = set(self._selection_before_mouse_press)
        if row in selected_rows:
            selected_rows.remove(row)
        else:
            selected_rows.add(row)
        self.selectedRows = selected_rows
        self.lastClickedRow = row
        # Log after toggle handling (remove duplicate log)
        logger.debug(
            f"[SplitterTable] _toggle_row_selection finished for row {row}. SelectedRows: {self.selectedRows}"
        )

    def _update_row_selection_visuals(self):
        """
        Updates the background color for all rows based on the selection state.
        Selected rows get a distinctive background color.
        """
        for row in range(self.rowCount()):
            self._update_row_appearance_for_row(row)

    def _update_row_appearance_for_row(self, row):
        """
        Updates the background and foreground color for a specific row
        based on selection state and linking status.
        """
        if row < 0 or row >= self.rowCount():  # Add boundary check
            logger.warning(f"[SplitterTable UpdateAppearance] Invalid row index: {row}")
            return

        # Determine background color based on selection.
        row_selected = row in self.selectedRows
        selected_bg_color = QtGui.QColor(58, 58, 63, 224)

        # Determine foreground color
        default_fg_color = QtGui.QColor(
            Qt.GlobalColor.white
        )  # From QSS QTableWidget::item
        manual_link_fg_color = QtGui.QColor("#ff7f7f")
        auto_review_fg_color = QtGui.QColor("#ffb347")
        fg_color_to_apply = default_fg_color

        title_item = self.item(row, 1)
        item_data_for_status = (
            title_item.data(QtCore.Qt.ItemDataRole.UserRole) if title_item else None
        )

        link_status_from_data = None
        is_manual_review = False
        is_auto_review = False
        if isinstance(item_data_for_status, dict):
            link_status_from_data = item_data_for_status.get("link_status")
            is_manual_review = link_status_from_data in {
                "manual_review_needed",
                "candidates_only",
            }
            is_auto_review = link_status_from_data == "needs_review_confirm"

        if is_manual_review:
            fg_color_to_apply = manual_link_fg_color
            logger.debug(
                "[SplitterTable Row %s UpdateAppearance] Applying RED foreground. Link status: %s. Selected: %s",
                row,
                link_status_from_data,
                row in self.selectedRows,
            )
        elif is_auto_review:
            fg_color_to_apply = auto_review_fg_color
            logger.debug(
                "[SplitterTable Row %s UpdateAppearance] Applying ORANGE foreground. Link status: %s. Selected: %s",
                row,
                link_status_from_data,
                row in self.selectedRows,
            )

        # Update appearance for all items in the row
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                if row_selected:
                    item.setBackground(selected_bg_color)
                else:
                    item.setData(QtCore.Qt.ItemDataRole.BackgroundRole, None)
                foreground_brush = QtGui.QBrush(fg_color_to_apply)
                item.setForeground(foreground_brush)  # Set foreground
                item.setData(QtCore.Qt.ItemDataRole.ForegroundRole, foreground_brush)

        for key_col in [
            self._get_column_index_by_header("Key"),
            self._get_column_index_by_header("Spotify Key"),
            self._get_column_index_by_header("TIDAL Key"),
        ]:
            if key_col < 0:
                continue
            key_item = self.item(row, key_col)
            if key_item:
                camelot_color = self._get_camelot_wheel_color(key_item.text())
                if camelot_color is not None:
                    camelot_brush = QtGui.QBrush(camelot_color)
                    key_item.setForeground(camelot_brush)
                    key_item.setData(QtCore.Qt.ItemDataRole.ForegroundRole, camelot_brush)
                elif is_manual_review or is_auto_review:
                    review_brush = QtGui.QBrush(
                        manual_link_fg_color if is_manual_review else auto_review_fg_color
                    )
                    key_item.setForeground(review_brush)
                    key_item.setData(QtCore.Qt.ItemDataRole.ForegroundRole, review_brush)
                else:
                    default_brush = QtGui.QBrush(default_fg_color)
                    key_item.setForeground(default_brush)
                    key_item.setData(QtCore.Qt.ItemDataRole.ForegroundRole, default_brush)

        # Update indicator text (existing logic)
        indicator_item = self.item(row, 0)  # INDICATOR_COLUMN_INDEX = 0
        if indicator_item:
            indicator_data_for_indicator = (
                indicator_item.data(QtCore.Qt.ItemDataRole.UserRole) or {}
            )
            if indicator_data_for_indicator.get("has_candidates", False):
                count = indicator_data_for_indicator.get("candidate_count", 0)
                indicator_item.setText(
                    "-"
                    if indicator_data_for_indicator.get("expanded", False)
                    else f"+ ({count})"
                )
            else:
                indicator_item.setText("")

    def copySelection(self):
        """
        Copies the content of all selected rows to the clipboard.
        """
        if not self.selectedRows:
            return
        table_data = []
        for row in sorted(self.selectedRows):
            row_text = []
            for col in range(self.columnCount()):
                item = self.item(row, col)
                row_text.append(item.text() if item else "")
            table_data.append("\t".join(row_text))
        text = "\n".join(table_data)
        # Fix Pylance: Check if clipboard object exists before using setText
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)

    def keyPressEvent(self, e: QtGui.QKeyEvent | None) -> None:
        """
        Captures Ctrl+A to select all rows and Ctrl+C to copy selected rows.
        """
        if e:  # Check if the event object is not None
            if self._is_toggle_selection_modifier(e.modifiers()):
                if e.key() == Qt.Key.Key_A:
                    # Select all rows.
                    self.selectedRows = set(range(self.rowCount()))
                    # Log after Ctrl+A handling
                    logger.debug(
                        f"[SplitterTable] keyPressEvent handled Ctrl+A. Selected all {len(self.selectedRows)} rows."
                    )
                    self._apply_selected_rows_to_selection_model()
                    # Update custom visuals (background color)
                    self._update_row_selection_visuals()
                    e.accept()
                    return
                elif e.key() == Qt.Key.Key_C:
                    self.copySelection()
                    e.accept()
                    return
            # Call the base class implementation if the event wasn't handled or if e was None initially
            # Note: If e is None, this call effectively does nothing related to the event,
            # but maintains the inheritance chain call structure.
            super().keyPressEvent(e)
        else:
            # If e is None, still call the superclass method.
            # It's unlikely Qt would pass None here, but this handles the type hint possibility.
            super().keyPressEvent(e)

    def resizeEvent(self, e: QtGui.QResizeEvent | None) -> None:  # Correct type hint
        """
        Overridden resize event.
        IMPORTANT: We NO LONGER call adjustColumnWidths here automatically,
        as that overrides manual user resizing. The default QTableWidget/QHeaderView
        behavior with Interactive sections should handle resizing better.
        We still log the resize event using the debounced timer.
        """
        super().resizeEvent(e)  # Call base implementation

        # --- Debounced logger Logic (Keep this) ---
        # Calculate resize information for logger purposes only
        viewport = cast(Any, self.viewport())
        if not viewport:
            return  # Add check
        total_width = viewport.width()
        num_cols = self.columnCount()

        fixed_cols_indices = []
        stretch_cols_indices = []
        fixed_width_total = 0

        # --- Identify fixed vs. stretch columns ---
        # Same logic as in adjustColumnWidths
        default_fixed_widths = {0: 50, "Length": 80}

        for i in range(num_cols):
            header_item = self.horizontalHeaderItem(i)
            header_text = header_item.text() if header_item else str(i)
            current_width = self.columnWidth(i)

            if i == 0 or header_text == "Length":  # Fixed columns
                fixed_cols_indices.append(i)
                width_to_use = (
                    current_width
                    if current_width > 0
                    else default_fixed_widths.get(i if i == 0 else header_text, 50)
                )
                fixed_width_total += width_to_use
            else:
                stretch_cols_indices.append(i)

        available_width = total_width - fixed_width_total

        # Store the data for debounced logger
        self._resize_info = {
            "total_width": total_width,
            "fixed_cols_indices": fixed_cols_indices,
            "fixed_width_total": fixed_width_total,
            "stretch_cols_indices": stretch_cols_indices,
            "available_width": available_width,
        }

        # Debounce timer calls removed as the timer itself was removed.

        # DO NOT call self.adjustColumnWidths() here anymore. Let Qt handle resizing.

    def _compact_columns_to_viewport(self, minimum_widths_by_header: Dict[str, int]) -> None:
        """Reduce lower-priority columns until the initial table fits the viewport."""
        viewport = self.viewport()
        if not viewport:
            return

        target_width = max(0, viewport.width() - 2)
        if target_width <= 0:
            return

        total_width = sum(self.columnWidth(i) for i in range(self.columnCount()))
        overflow = total_width - target_width
        if overflow <= 0:
            return

        for header_text, min_width in minimum_widths_by_header.items():
            col_index = self._get_column_index_by_header(header_text)
            if col_index < 0:
                continue

            current_width = self.columnWidth(col_index)
            reducible_width = max(0, current_width - min_width)
            if reducible_width <= 0:
                continue

            reduction = min(reducible_width, overflow)
            self.setColumnWidth(col_index, current_width - reduction)
            overflow -= reduction
            if overflow <= 0:
                return

    def _column_header_text(self, column: int) -> str:
        header_item = self.horizontalHeaderItem(column)
        return header_item.text().strip() if header_item else ""

    def _protected_visible_columns(self) -> set[str]:
        return {"#", "Title"}

    def _get_column_visibility_preferences(self) -> Dict[str, bool]:
        raw_preferences = getattr(SETTINGS, "tableColumnVisibility", {}) or {}
        if not isinstance(raw_preferences, dict):
            return {}
        return {
            str(key).strip(): bool(value)
            for key, value in raw_preferences.items()
            if str(key).strip()
        }

    def _save_column_visibility_preferences(self, preferences: Dict[str, bool]) -> None:
        SETTINGS.tableColumnVisibility = dict(preferences)
        try:
            SETTINGS.save(getSettingsFilePath())
        except Exception:
            logger.warning(
                "Failed to persist table column visibility preferences.",
                exc_info=True,
            )

    def apply_column_visibility_preferences(self) -> None:
        preferences = self._get_column_visibility_preferences()
        protected_columns = self._protected_visible_columns()

        for column in range(self.columnCount()):
            header_text = self._column_header_text(column)
            if not header_text:
                continue
            visible = preferences.get(header_text, True)
            if header_text in protected_columns:
                visible = True
            self.setColumnHidden(column, not visible)

    def _set_column_visible_by_name(self, header_text: str, visible: bool) -> None:
        header_text = str(header_text or "").strip()
        if not header_text:
            return

        if header_text in self._protected_visible_columns():
            visible = True

        preferences = self._get_column_visibility_preferences()
        preferences[header_text] = bool(visible)
        self._save_column_visibility_preferences(preferences)

        for column in range(self.columnCount()):
            if self._column_header_text(column) == header_text:
                self.setColumnHidden(column, not visible)
                break

        self.apply_column_visibility_preferences()
        viewport = cast(Any, self.viewport())
        if viewport is not None:
            viewport.update()

    def _show_column_visibility_menu(self, position: QtCore.QPoint) -> None:
        header = self.horizontalHeader()
        if not header:
            return

        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                background-color: #2d2d31;
                color: #ffffff;
                border: 1px solid #5a5a5f;
                padding: 4px;
            }
            QMenu::item {
                background-color: transparent;
                padding: 4px 28px 4px 24px;
            }
            QMenu::item:selected {
                background-color: #3f3f46;
            }
            QMenu::indicator {
                width: 14px;
                height: 14px;
            }
            QMenu::item:disabled {
                color: #8a8a8f;
            }
            """
        )
        protected_columns = self._protected_visible_columns()
        preferences = self._get_column_visibility_preferences()

        for column in range(self.columnCount()):
            header_text = self._column_header_text(column)
            if not header_text:
                continue

            action = menu.addAction(header_text)
            if not action:
                continue

            action.setCheckable(True)
            is_visible = preferences.get(header_text, not self.isColumnHidden(column))
            if header_text in protected_columns:
                is_visible = True
                action.setEnabled(False)

            action.setChecked(is_visible)
            action.toggled.connect(
                lambda checked, text=header_text: self._set_column_visible_by_name(
                    text,
                    checked,
                )
            )

        if menu.isEmpty():
            return

        menu.exec(header.mapToGlobal(position))

    def adjustColumnWidths(self):
        """
        Sets specific initial widths for some columns using fixed indices,
        ensures all are interactive, and relies on stretchLastSection=True.
        Called when table content changes (e.g., loading tracks).
        """
        # --- ADD LOGGING AT START ---
        logger.debug(
            f"[SplitterTable adjustColumnWidths] STARTING. Current columnCount: {self.columnCount()}"
        )
        # --- END LOGGING ---
        logger.debug("[SplitterTable] Adjusting column widths...")
        adjust_start = time.perf_counter()
        try:
            header = self.horizontalHeader()
            if not header:
                return  # Add check
            num_cols = self.columnCount()
            if num_cols <= 0:
                return

            header_signature_parts: List[str] = []
            for i in range(num_cols):
                header_item = self.horizontalHeaderItem(i)
                header_signature_parts.append(header_item.text() if header_item else "")
            header_signature = tuple(header_signature_parts)
            if header_signature == self._last_adjust_signature:
                self.apply_column_visibility_preferences()
                logger.debug(
                    "[SplitterTable] Skipping adjustColumnWidths; column signature unchanged."
                )
                return

            # 1. Ensure all columns are interactive
            for i in range(num_cols):
                header.setSectionResizeMode(
                    i, QtWidgets.QHeaderView.ResizeMode.Interactive
                )
            logger.debug("[SplitterTable] All columns set to Interactive.")

            # 2. Set specific initial widths using fixed indices
            title_width = 195
            artists_width = 190
            album_width = 155

            # Use fixed indices: 0='#', 1='Title', 2='Artists', 3='Album'
            hash_col = 0
            title_col = 1
            artists_col = 2
            album_col = 3

            # Set widths if columns exist
            if hash_col < num_cols:
                self.setColumnWidth(hash_col, 40)  # Set fixed width for '#' column
                header.setSectionResizeMode(
                    hash_col, QtWidgets.QHeaderView.ResizeMode.Fixed
                )  # Make '#' column fixed size
                logger.debug(
                    f"[SplitterTable] Set fixed width for # (col {hash_col}) to 40"
                )
            if title_col < num_cols:
                self.setColumnWidth(title_col, title_width)
                logger.debug(
                    f"[SplitterTable] Set width for Title (col {title_col}) to {title_width} and mode to Stretch"
                )
            if artists_col < num_cols:
                self.setColumnWidth(artists_col, artists_width)
                logger.debug(
                    f"[SplitterTable] Set width for Artists (col {artists_col}) to {artists_width} and mode to Stretch"
                )
            if album_col < num_cols:
                self.setColumnWidth(album_col, album_width)
                logger.debug(
                    f"[SplitterTable] Set width for Album (col {album_col}) to {album_width} and mode to Stretch"
                )

            preferred_widths = {
                "Release Year": 92,
                "BPM": 70,
                "Key": 64,
                "Spotify Key": 92,
                "TIDAL Key": 82,
                "Genre": 175,
                "Label": 170,
                "Length": 72,
                "Quality": 155,
                "Status": 185,
                "Progress": 120,
            }
            for header_text, width in preferred_widths.items():
                col_index = self._get_column_index_by_header(header_text)
                if col_index >= 0:
                    self.setColumnWidth(col_index, width)
                    logger.debug(
                        f"[SplitterTable] Set width for {header_text} (col {col_index}) to {width}"
                    )

            self._compact_columns_to_viewport(
                {
                    "Album": 115,
                    "Label": 130,
                    "Genre": 130,
                    "Status": 145,
                    "Artists": 165,
                    "Title": 165,
                    "Quality": 130,
                }
            )

            # Other columns ('Length', 'Quality', 'Link Status') will size interactively.
            # The last column will stretch due to stretchLastSection=True.

            self.apply_column_visibility_preferences()
            self._last_adjust_signature = header_signature

            # --- ADD LOGGING AT END ---
            if logger.isEnabledFor(logging.DEBUG):
                final_widths = [self.columnWidth(i) for i in range(self.columnCount())]
                logger.debug(
                    f"[SplitterTable adjustColumnWidths] FINISHED. Final widths: {final_widths}"
                )
            adjust_elapsed_ms = (time.perf_counter() - adjust_start) * 1000.0
            if adjust_elapsed_ms >= 120.0:
                logger.warning(
                    "[DIAGNOSIS] Slow adjustColumnWidths detected | elapsed_ms=%.1f row_count=%s col_count=%s",
                    adjust_elapsed_ms,
                    self.rowCount(),
                    self.columnCount(),
                )
            # --- END LOGGING ---
        except Exception as e:
            logger.error(
                f"[SplitterTable] Error in adjustColumnWidths: {e}", exc_info=True
            )

    def getSelectedRows(self):
        """
        Returns a sorted list of selected row indices.
        """
        selection_model_rows = self._get_selection_model_rows()
        if selection_model_rows != self.selectedRows:
            self.selectedRows = selection_model_rows
            self._update_row_selection_visuals()
        return sorted(list(self.selectedRows))

    @QtCore.pyqtSlot(int)
    def collapseSubRow(self, main_row_index: int):
        logger.debug(
            f"[SplitterTable] Attempting to collapse sub-row for main row: {main_row_index}"
        )
        table_widget = self
        INDICATOR_COLUMN_INDEX = 0

        main_indicator_item = table_widget.item(main_row_index, INDICATOR_COLUMN_INDEX)
        # It's okay if main_indicator_data is initially empty or reflects 'expanded' state.

        sub_row = main_row_index + 1

        successfully_collapsed = False
        if sub_row < table_widget.rowCount():
            cell_w = table_widget.cellWidget(sub_row, 0)
            if cell_w and isinstance(cell_w, CandidateWidget):
                logger.debug(
                    f"Collapsing sub-row {sub_row} (CandidateWidget found) for main row {main_row_index}."
                )
                table_widget.removeCellWidget(sub_row, 0)
                table_widget.setRowHidden(sub_row, True)  # Hide before removing
                table_widget.removeRow(sub_row)
                successfully_collapsed = True
            else:
                logger.debug(
                    f"Sub-row {sub_row} for main row {main_row_index} did not contain a CandidateWidget. CellWidget: {cell_w}"
                )
        else:
            logger.debug(
                f"Sub-row {sub_row} for main row {main_row_index} does not exist."
            )

        # ALWAYS reset the main row's indicator after attempting collapse,
        # especially after a manual link.
        if main_indicator_item:
            original_indicator_data_raw = main_indicator_item.data(
                QtCore.Qt.ItemDataRole.UserRole
            )
            original_indicator_data = (
                original_indicator_data_raw
                if isinstance(original_indicator_data_raw, dict)
                else {}
            )

            candidate_count = original_indicator_data.get("candidate_count", 0)
            linked_id = original_indicator_data.get("linked_tidal_track_id", None)
            candidates_list = original_indicator_data.get(
                "candidates_list", []
            )  # Preserve the list of candidates
            review_mode = original_indicator_data.get(
                "candidate_review_mode",
                "manual",
            )
            original_spotify_track = original_indicator_data.get("original_spotify_track")
            if not isinstance(original_spotify_track, dict):
                original_spotify_track = None

            new_indicator_data = {
                "expanded": False,
                "has_candidates": (
                    True if candidate_count > 0 else False
                ),  # Keep has_candidates true if there are candidates
                "candidate_count": candidate_count,
                "candidates_list": candidates_list,
                "linked_tidal_track_id": linked_id,
                "candidate_review_mode": review_mode,
                "original_spotify_track": original_spotify_track,
            }
            main_indicator_item.setData(
                QtCore.Qt.ItemDataRole.UserRole, new_indicator_data
            )
            if candidate_count > 0:
                main_indicator_item.setText(f"+ ({candidate_count})")
            else:
                main_indicator_item.setText("")
            logger.debug(
                f"[SplitterTable collapseSubRow] Main row {main_row_index} indicator data and text reset for re-expansion. Collapsed: {successfully_collapsed}"
            )
        else:
            logger.warning(
                f"[SplitterTable collapseSubRow] Could not find indicator item for main row {main_row_index} to reset its state."
            )

    def collapse_all_sub_rows(self):
        """
        Finds and collapses all expanded candidate sub-rows in the table.
        This is crucial to call before sorting to prevent UI corruption.
        """
        logger.debug("[SplitterTable] Collapsing all open sub-rows before sort.")
        # Iterate in reverse to avoid index shifting issues when removing rows
        for row in range(self.rowCount() - 1, -1, -1):
            indicator_item = self.item(row, 0)
            if not indicator_item:
                continue

            indicator_data = indicator_item.data(QtCore.Qt.ItemDataRole.UserRole) or {}
            is_expanded = indicator_data.get("expanded", False)

            if is_expanded:
                # If the row is marked as expanded, call the toggle function
                # which will handle the actual collapse logic.
                logger.debug(f"[SplitterTable] Found expanded row at index {row}. Collapsing it.")
                self._toggle_expand(row)

    def _toggle_expand(self, row: int):
        """
        Toggles the expanded state of a row with candidates, showing/hiding a sub-row
        containing the CandidateWidget.
        """
        logger.debug(f"[SplitterTable] Toggling expand for row: {row}")
        indicator_item = self.item(row, 0)
        if not indicator_item:
            logger.warning(
                f"[SplitterTable] Cannot toggle expand for row {row}: Indicator item missing."
            )
            return

        indicator_data = indicator_item.data(QtCore.Qt.ItemDataRole.UserRole) or {}
        if not indicator_data.get("has_candidates", False):
            logger.debug(f"[SplitterTable] Row {row} has no candidates, cannot expand.")
            return  # Cannot expand if no candidates

        is_expanded = indicator_data.get("expanded", False)
        table_widget = self  # Reference to the table itself

        if is_expanded:
            # Collapse the row
            logger.debug(f"[SplitterTable] Collapsing row {row}.")
            # The sub-row is directly below the main row
            sub_row = row + 1
            if sub_row < table_widget.rowCount():
                # Remove the cell widget (CandidateWidget)
                # Check if the cell widget exists before removing
                if table_widget.cellWidget(sub_row, 0):
                    table_widget.removeCellWidget(sub_row, 0)
                # Hide and remove the sub-row
                table_widget.setRowHidden(sub_row, True)
                table_widget.removeRow(sub_row)
            else:
                logger.warning(
                    f"[SplitterTable] Attempted to collapse row {row}, but sub-row {sub_row} does not exist."
                )

            # Update indicator data
            indicator_data["expanded"] = False
            indicator_item.setData(QtCore.Qt.ItemDataRole.UserRole, indicator_data)
            self._update_row_appearance_for_row(row)  # Update indicator text

        else:
            # Expand the row
            logger.debug(f"[SplitterTable] Expanding row {row}.")
            # Get raw candidates from the indicator_data, which should now reliably store them
            candidates_raw = indicator_data.get("candidates_list")
            # Get the currently linked track ID from the main row's indicator data to pass to CandidateWidget
            current_linked_id = indicator_data.get("linked_tidal_track_id", None)
            original_spotify_track = indicator_data.get("original_spotify_track")
            if not isinstance(original_spotify_track, dict):
                original_spotify_track = None

            # --- ADD THIS LOG ---
            logger.debug(
                f"[SplitterTable _toggle_expand] Row {row}, candidates_raw from metadata: {candidates_raw}"
            )
            # --- END LOG ---

            # Deserialize Track objects from dicts if loaded from cache
            candidates = []
            if isinstance(candidates_raw, list):
                for i, cand_dict_raw in enumerate(candidates_raw):  # Add enumerate
                    track_dict = cand_dict_raw.get("tidal_track")
                    # --- ADD THESE LOGS ---
                    logger.debug(
                        f"[SplitterTable _toggle_expand] Candidate {i} raw dict: {cand_dict_raw}"
                    )
                    logger.debug(
                        f"[SplitterTable _toggle_expand] Candidate {i} track_dict: {track_dict}"
                    )
                    # --- END LOGS ---
                    if isinstance(track_dict, dict):
                        # Convert dict back to Track object
                        try:
                            track_obj = aigpy.model.dictToModel(track_dict, Track())
                            # --- ADD THIS LOG ---
                            logger.debug(
                                f"[SplitterTable _toggle_expand] Candidate {i} deserialized track_obj: {track_obj}, Title: {getattr(track_obj, 'title', 'N/A')}"
                            )
                            # --- END LOG ---
                            if (
                                track_obj and getattr(track_obj, "id", None) is not None
                            ):  # Ensure track_obj is valid and has an ID
                                candidates.append(
                                    {
                                        "tidal_track": track_obj,
                                        "score": cand_dict_raw.get("score"),
                                        "mismatch_reasons": cand_dict_raw.get(
                                            "mismatch_reasons"
                                        ),
                                    }
                                )  # Store the Track object
                            else:
                                logger.warning(
                                    f"[SplitterTable _toggle_expand] Failed to deserialize or invalid track_obj for candidate {i}: {track_dict}"
                                )
                        except Exception as e:
                            logger.error(
                                f"[SplitterTable] Error deserializing candidate track dict for row {row}, candidate {i}: {e}",
                                exc_info=True,
                            )
                    elif isinstance(track_dict, Track):
                        # Handle case where it might already be a Track object (e.g., immediately after linking)
                        logger.debug(
                            f"[SplitterTable _toggle_expand] Candidate {i} 'tidal_track' in row {row} is already a Track object."
                        )
                        candidates.append(
                            {
                                "tidal_track": track_dict,
                                "score": cand_dict_raw.get("score"),
                                "mismatch_reasons": cand_dict_raw.get(
                                    "mismatch_reasons"
                                ),
                            }
                        )  # Already a Track object
                    else:
                        logger.warning(
                            f"[SplitterTable _toggle_expand] Unexpected type for cached candidate {i} 'tidal_track' in row {row}: {type(track_dict)}"
                        )

            logger.debug(
                f"[SplitterTable _toggle_expand] Final deserialized candidates list (count: {len(candidates)}): {candidates}"
            )  # Log final list
            if (
                not candidates
            ):  # Check if deserialization failed or list was empty/invalid
                logger.warning(
                    f"[SplitterTable] Cannot expand row {row}: No valid candidate data found or deserialization failed."
                )
                indicator_data["has_candidates"] = False
                indicator_data["expanded"] = False  # Ensure it's marked as not expanded
                indicator_data["candidate_count"] = 0  # Set count to 0
                indicator_data["candidates_list"] = []  # Set empty list
                indicator_item.setData(QtCore.Qt.ItemDataRole.UserRole, indicator_data)
                self._update_row_appearance_for_row(
                    row
                )  # This will clear the indicator text
                return

            # Insert a new row directly below the main row for the CandidateWidget
            sub_row = row + 1
            table_widget.insertRow(sub_row)

            # Create the CandidateWidget and set it as a cell widget spanning all columns
            review_mode = indicator_data.get("candidate_review_mode", "manual")

            candidate_widget = CandidateWidget(
                row,
                candidates,
                initial_selected_track_id=current_linked_id,
                review_mode=review_mode,
                original_spotify_track=original_spotify_track,
                parent=table_widget,
            )
            candidate_widget.candidateSelected.connect(
                self.candidateSelectedInSubRow
            )
            candidate_widget.noMatchSelected.connect(self.noMatchSelectedInSubRow)
            candidate_widget.candidatePreviewRequested.connect(
                self.candidatePreviewRequestedInSubRow
            )
            candidate_widget.candidateAutoReviewAccepted.connect(
                self.candidateAutoReviewAcceptedInSubRow
            )
            candidate_widget.candidateUnlinkRequested.connect(
                self.candidateUnlinkRequestedInSubRow
            )

            self.setCellWidget(sub_row, 0, candidate_widget)
            self.setSpan(sub_row, 0, 1, self.columnCount())  # Span across all columns

            # Ensure the sub-row is visible
            self.setRowHidden(sub_row, False)

            # --- Adjust Row Height ---
            required_height = candidate_widget.calculate_required_height()
            logger.debug(
                f"[SplitterTable Row {row}] Setting sub-row {sub_row} height to {required_height}px. CandidateWidget visible: {candidate_widget.isVisible()}"
            )  # MODIFIED LOG
            self.setRowHeight(sub_row, required_height)
            # --- End Adjust Row Height ---

            # (Connection moved up after widget creation)

            # Update indicator data
            indicator_data["expanded"] = True
            indicator_data["has_candidates"] = True
            indicator_data["candidate_count"] = len(candidates)
            indicator_data["candidates_list"] = (
                candidates_raw  # IMPORTANT: Store the raw list again
            )
            indicator_item.setData(QtCore.Qt.ItemDataRole.UserRole, indicator_data)
            self._update_row_appearance_for_row(row)  # Update indicator text

            # Ensure the main row is selected when expanded (optional, but can improve UX)
            # self.selectRow(row)

    # --- Signal Handlers for Resize logger ---

    def _handle_section_pressed(self, logicalIndex):
        """
        Slot connected to header's sectionPressed signal.
        Logs the start of a potential column resize drag.
        """
        # Store initial widths when a section header is pressed
        self._initial_widths_on_press = [
            self.columnWidth(i) for i in range(self.columnCount())
        ]
        logger.debug(
            f"[SplitterTable - Header Resize] Started. Original widths: {self._initial_widths_on_press}"
        )
        # Stop any pending timer from a previous resize that might not have logged yet
        # self._resize_log_timer.stop() # Removed timer access

    def _handle_section_resized(self, logicalIndex, oldSize, newSize):
        """
        Slot connected to header's sectionResized signal.
        Restarts the debounce timer each time the section is resized during a drag.
        """
        # If initial widths were captured (meaning sectionPressed was called),
        # restart the timer. This effectively debounces the logger.
        if self._initial_widths_on_press:
            # self._resize_log_timer.start() # Restart timer with the preset interval (e.g., 250ms) # Removed timer access
            pass  # Timer logic removed

    def _log_final_resize_widths(self):
        """
        Slot connected to the timeout signal of _resize_log_timer.
        Logs the final widths after resizing has stopped for the timer interval.
        """
        final_widths = [self.columnWidth(i) for i in range(self.columnCount())]
        # Only log if widths actually changed from when the press started
        if (
            self._initial_widths_on_press
            and final_widths != self._initial_widths_on_press
        ):
            logger.debug(
                f"[SplitterTable - Header Resize] Finished. Final widths: {final_widths}"
            )

        # Clear the stored initial widths now that logger is done (or skipped)
        self._initial_widths_on_press = []

    @QtCore.pyqtSlot(int, Qt.SortOrder)
    def on_sort_indicator_changed(self, logicalIndex: int, order: Qt.SortOrder):
        """
        Slot triggered when the user clicks a header to sort the table.
        Collapses all sub-rows BEFORE the sort is visually applied to prevent corruption.
        """
        logger.debug(f"Sort indicator changed for column {logicalIndex}. Collapsing all sub-rows.")
        self.collapse_all_sub_rows()
        # The table will proceed with its internal sorting *after* this slot completes.

    # Removed eventFilter method as it's replaced by signal handling
