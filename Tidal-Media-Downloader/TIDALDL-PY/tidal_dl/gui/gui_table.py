# --- START OF FILE gui_table.py ---

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
- Row-based selection with distinctive highlighting
- Shift-click for range selection
- Ctrl-click for toggling individual row selection
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
from typing import List, Dict, Optional, Any
from functools import partial

# Third-party imports
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
    QItemSelection,
    QItemSelectionModel,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QMouseEvent,
    QKeyEvent,
    QColor,
    QPalette,
    QFont,
)
from PyQt6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QHeaderView,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
)
import aigpy

# Local application imports
from ..model import Track
from ..printf import Printf

# from ..tidal import TIDAL_API # TIDAL_API is not used directly in this file

# --- Setup Logging ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set specific level for this module

# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from .gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

# The SelectableLabel and ColumnWidget classes have been replaced by a QTableWidget–based implementation.
# The new SplitterTable class below implements a spreadsheet–like widget that supports row selection
# for download. Clicking on any cell in a row toggles that row’s selection, and the entire row is highlighted
# with a uniform background color.
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

    def __init__(self, column_names, parent=None):
        super().__init__(parent)
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
            # Apply stylesheet for smaller header padding (ensure correct syntax)
            base_header_style = "QHeaderView::section { background-color: transparent; padding-top: 2px; padding-bottom: 2px; padding-left: 4px; padding-right: 4px; }"
            h_header.setStyleSheet(base_header_style)  # Apply base style first
        # Set default row height for better readability and clickability
        v_header = self.verticalHeader()
        if v_header:
            v_header.setDefaultSectionSize(60)
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

            # Get existing style to append to
            base_header_style = h_header.styleSheet()  # Get the style set earlier

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

        # Data structures for row selection.
        self.selectedRows = set()
        self.lastClickedRow = None

        # Variables for mouse–based toggling.
        self._mousePressPos = None
        self._mousePressRow = None
        self._dragging = False
        self.drag_threshold = 5  # pixels

        # State for tracking column resize drag (using signals now)
        self._initial_widths_on_press = []

        # Debounced logger removed, logic moved to resizeEvent

        # Connect header signals for resize logger
        h_header = self.horizontalHeader()  # Get header again
        if h_header:  # Add check
            h_header.sectionPressed.connect(self._handle_section_pressed)
            h_header.sectionResized.connect(self._handle_section_resized)

    def set_linking_gui_handler(self, handler):
        self.linking_gui_handler = handler

    # Removed _set_initial_column_widths and _restore_interactive_resize_modes methods

    def clearRows(self):
        """
        Removes all rows from the table and resets row selection tracking.
        """
        self.setRowCount(0)
        self.selectedRows.clear()
        self.lastClickedRow = None

    def addRow(self, row_data, track=None):
        """
        Adds a new row to the table and populates each column with a QTableWidgetItem.
        Stores Track object in the title column's user data.
        """
        row = self.rowCount()
        self.insertRow(row)
        # Determine column index for 'Length' for alignment
        length_col_index = -1
        try:
            # Find 'Length' column index by iterating through headers
            for i in range(self.columnCount()):
                header_item = self.horizontalHeaderItem(i)
                if header_item and header_item.text() == "Length":
                    length_col_index = i
                    break
        except Exception as e:
            logger.warning(
                f"[SplitterTable.addRow] Error finding 'Length' column index: {e}"
            )

        for col_index, data in enumerate(row_data):
            item = QtWidgets.QTableWidgetItem(str(data))

            # Right-align '#' column (index 0)
            if col_index == 0:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )

            # Set tooltip for Quality column
            if col_index == 5:  # Quality column index
                item.setToolTip(str(data))

            # Center align 'Length' column
            if col_index == length_col_index:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

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
        # Update the visual appearance of the new row.
        self._update_row_appearance_for_row(row)
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
        else:
            while self.rowCount() <= row:
                self.insertRow(self.rowCount())
            self.updateCell(row, col, text)

    # Fix parameter name mismatch: col -> column
    def item(self, row, column):
        """
        Returns the QTableWidgetItem at (row, column) or None if invalid.
        """
        try:
            return super().item(row, column)
        except Exception:
            return None

    # Fix parameter name mismatch: event -> e
    def mousePressEvent(self, e: QtGui.QMouseEvent | None):
        """
        Records the initial mouse position and the row index at the press location.
        """
        if e:
            self._mousePressPos = e.pos()
            index = self.indexAt(self._mousePressPos)
            self._mousePressRow = index.row() if index.isValid() else None
            self._dragging = False
        else:
            # Handle the case where e is None, perhaps reset state or log
            self._mousePressPos = None
            self._mousePressRow = None
            self._dragging = False
        super().mousePressEvent(e)

    # Fix parameter name mismatch: event -> e
    def mouseMoveEvent(self, e: QtGui.QMouseEvent | None):
        """
        Monitors mouse movement to determine if the user is dragging.
        """
        if e:
            super().mouseMoveEvent(e)
            if self._mousePressPos is not None and not self._dragging:
                if (
                    e.pos() - self._mousePressPos
                ).manhattanLength() > self.drag_threshold:
                    self._dragging = True

    # Fix parameter name mismatch: event -> e
    def mouseReleaseEvent(self, e: QtGui.QMouseEvent | None):
        """
        If the mouse is released without dragging, interprets the click as a row selection toggle.
        Supports SHIFT-click for range selection.
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
                        return  # Stop further processing for this click

                # If not an indicator click, proceed with normal selection logic
                if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._handle_shift_click(clicked_row)
                elif e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    logger.debug(
                        f"[SplitterTable] Ctrl-Click detected on row {clicked_row}"
                    )
                    self._toggle_row_selection(clicked_row)
                else:
                    logger.debug(
                        f"[SplitterTable] Single Click detected on row {clicked_row}"
                    )
                    self._handle_single_click(clicked_row)

                logger.debug(
                    f"[SplitterTable] mouseReleaseEvent finished click handling. SelectedRows: {self.selectedRows}"
                )
                self._update_row_selection_visuals()

        elif e.button() == Qt.MouseButton.LeftButton and self._dragging:
            # Update selection state after drag
            selection_model = self.selectionModel()
            if selection_model:  # Add check
                selected_indexes = selection_model.selectedRows()
                self.selectedRows = {index.row() for index in selected_indexes}
            else:
                logger.warning("Could not get selection model after drag.")
                self.selectedRows = set()  # Reset selection if model is None

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

    def _handle_single_click(self, row):
        """
        Handles a single click: toggles the row's selection state.
        """
        if row in self.selectedRows:
            self.selectedRows.remove(row)
        else:
            self.selectedRows.add(row)
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
        Toggles the row selection for CTRL–click.
        """
        if row in self.selectedRows:
            self.selectedRows.remove(row)
        else:
            self.selectedRows.add(row)
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

        # Determine background color based on selection
        # Using the QSS defined color for selection background: rgba(56, 56, 56, 0.8)
        # Convert to QColor: QColor(56, 56, 56, int(0.8 * 255))
        selected_bg_color = QtGui.QColor(56, 56, 56, 204)
        transparent_bg_color = (
            Qt.GlobalColor.transparent
        )  # Or your default item background from QSS

        bg_color_to_apply = (
            selected_bg_color if row in self.selectedRows else transparent_bg_color
        )

        # Determine foreground color
        default_fg_color = QtGui.QColor(
            Qt.GlobalColor.white
        )  # From QSS QTableWidget::item
        manual_link_fg_color = QtGui.QColor("#ff7f7f")
        fg_color_to_apply = default_fg_color  # Default

        # --- START OF THE FIX for Red Text ---
        # Check the link_status from the data stored in the TITLE item (column 1)
        title_item = self.item(row, 1)
        item_data_for_status = title_item.data(QtCore.Qt.ItemDataRole.UserRole) if title_item else None
        # --- END OF THE FIX for Red Text ---

        is_manual_review = False
        link_status_from_data = None  # Initialize link_status_from_data
        if isinstance(item_data_for_status, dict):
            link_status_from_data = item_data_for_status.get("link_status")
            # Add 'candidates_only' to the list of statuses that require manual review
            if link_status_from_data in ["manual_review_needed", "candidates_only"]:
                is_manual_review = True

        if is_manual_review:
            fg_color_to_apply = manual_link_fg_color
            logger.debug(
                f"[SplitterTable Row {row} UpdateAppearance] Applying RED foreground. Link status: {link_status_from_data}. Selected: {row in self.selectedRows}"
            )
        else:
            # If selected and not manual review, QSS for item:selected (which has no color) applies.
            # The item's default color (white from QTableWidget::item) should be used.
            # If not selected, it's also default white.
            # logger.debug(f"[SplitterTable Row {row} UpdateAppearance] Applying DEFAULT foreground. Link status: {link_status_from_data}. Selected: {row in self.selectedRows}")
            pass

        # Update appearance for all items in the row
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                item.setBackground(bg_color_to_apply)
                item.setForeground(fg_color_to_apply)  # Set foreground

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

    # Fix parameter name mismatch: event -> e
    def keyPressEvent(self, e: QtGui.QKeyEvent | None):
        """
        Captures Ctrl+A to select all rows and Ctrl+C to copy selected rows.
        """
        if e:  # Check if the event object is not None
            if e.modifiers() == Qt.KeyboardModifier.ControlModifier:
                if e.key() == Qt.Key.Key_A:
                    # Select all rows.
                    self.selectedRows = set(range(self.rowCount()))
                    # Log after Ctrl+A handling
                    logger.debug(
                        f"[SplitterTable] keyPressEvent handled Ctrl+A. Selected all {len(self.selectedRows)} rows."
                    )
                    # Explicitly update the QTableWidget's selection model
                    selection_model = self.selectionModel()
                    model = self.model()  # Get model
                    if selection_model and model:  # Add checks
                        top_left_index = model.index(0, 0)
                        bottom_right_index = model.index(
                            self.rowCount() - 1, self.columnCount() - 1
                        )
                        item_selection = QtCore.QItemSelection(
                            top_left_index, bottom_right_index
                        )
                        selection_model.select(
                            item_selection,
                            QtCore.QItemSelectionModel.SelectionFlag.Select
                            | QtCore.QItemSelectionModel.SelectionFlag.Rows,
                        )
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

    # Fix parameter name mismatch: event -> e
    def resizeEvent(self, e: QtGui.QResizeEvent | None):  # Correct type hint
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
        viewport = self.viewport()
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
        try:
            header = self.horizontalHeader()
            if not header:
                return  # Add check
            num_cols = self.columnCount()
            if num_cols <= 0:
                return

            # 1. Ensure all columns are interactive
            for i in range(num_cols):
                header.setSectionResizeMode(
                    i, QtWidgets.QHeaderView.ResizeMode.Interactive
                )
            logger.debug("[SplitterTable] All columns set to Interactive.")

            # 2. Set specific initial widths using fixed indices
            initial_wide_width = 200
            initial_narrow_width = 50

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
                self.setColumnWidth(title_col, initial_wide_width)
                logger.debug(
                    f"[SplitterTable] Set width for Title (col {title_col}) to {initial_wide_width} and mode to Stretch"
                )
            if artists_col < num_cols:
                self.setColumnWidth(artists_col, initial_wide_width)
                logger.debug(
                    f"[SplitterTable] Set width for Artists (col {artists_col}) to {initial_wide_width} and mode to Stretch"
                )
            if album_col < num_cols:
                self.setColumnWidth(album_col, initial_wide_width)
                logger.debug(
                    f"[SplitterTable] Set width for Album (col {album_col}) to {initial_wide_width} and mode to Stretch"
                )

            # Set initial width for Quality column (index 5)
            quality_col = 5
            if quality_col < num_cols:
                self.setColumnWidth(quality_col, 180)  # Set initial width for Quality
                logger.debug(
                    f"[SplitterTable] Set width for Quality (col {quality_col}) to 180"
                )

            # Set initial width for Progress column (index 7 when Spotify)
            progress_col = 7
            if progress_col < num_cols:
                # Check header text to be sure it's the Progress column
                progress_header_item = self.horizontalHeaderItem(progress_col)
                if progress_header_item and progress_header_item.text() == "Progress":
                    self.setColumnWidth(
                        progress_col, 120
                    )  # Set initial width for Progress
                    logger.debug(
                        f"[SplitterTable] Set width for Progress (col {progress_col}) to 120"
                    )

            # Other columns ('Length', 'Quality', 'Link Status') will size interactively.
            # The last column will stretch due to stretchLastSection=True.

            # --- ADD LOGGING AT END ---
            final_widths = [self.columnWidth(i) for i in range(self.columnCount())]
            logger.debug(
                f"[SplitterTable adjustColumnWidths] FINISHED. Final widths: {final_widths}"
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

            new_indicator_data = {
                "expanded": False,
                "has_candidates": (
                    True if candidate_count > 0 else False
                ),  # Keep has_candidates true if there are candidates
                "candidate_count": candidate_count,
                "candidates_list": candidates_list,
                "linked_tidal_track_id": linked_id,
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
            # Get the full metadata stored in the main row's status item (column 6)
            status_item = self.item(row, 6)
            # status_data = status_item.data(QtCore.Qt.ItemDataRole.UserRole) if status_item else {} # Old way
            # item_metadata = status_item.data(QtCore.Qt.ItemDataRole.UserRole) if status_item else {} # No longer get candidates from here
            # Get raw candidates from the indicator_data, which should now reliably store them
            candidates_raw = indicator_data.get("candidates_list")
            # Get the currently linked track ID from the main row's indicator data to pass to CandidateWidget
            current_linked_id = indicator_data.get("linked_tidal_track_id", None)

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
            candidate_widget = CandidateWidget(
                row, candidates, initial_selected_track_id=current_linked_id
            )
            candidate_widget.candidateSelected.connect(
                self.candidateSelectedInSubRow
            )
            # --- START: CONNECT "NONE MATCH" SIGNAL ---
            # This is the robust way: the table catches the signal from its child widget
            # and re-emits its own signal, which the handler will connect to.
            candidate_widget.noMatchSelected.connect(self.noMatchSelectedInSubRow)
            # --- END: CONNECT "NONE MATCH" SIGNAL ---

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

    # Removed eventFilter method as it's replaced by signal handling


class HighlightPreservingDelegate(QtWidgets.QStyledItemDelegate):
    """Keep an item’s Qt.ForegroundRole colour even when it is selected."""

    def paint(
        self,
        painter: Optional[QtGui.QPainter],
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        # If painter is ever None (per PyQt6 signature), fall back to default behavior:
        if painter is None:
            super().paint(painter, option, index)
            return

        # Copy the incoming option so we don't mutate it in place
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        # If selected, pull out any custom brush and push it into the HighlightedText role
        if opt.state & QtWidgets.QStyle.StateFlag.State_Selected:
            brush = index.data(QtCore.Qt.ItemDataRole.ForegroundRole)
            if isinstance(brush, QtGui.QBrush):
                opt.palette.setBrush(QtGui.QPalette.ColorRole.HighlightedText, brush)

        # --- START FIX ---
        # Get the style object, which might be None.
        actual_style: Optional[QtWidgets.QStyle]
        if opt.widget is not None:
            actual_style = opt.widget.style()
        else:
            actual_style = QtWidgets.QApplication.style()

        if actual_style is None:
            # This is a fallback if no style could be obtained.
            # It's highly unlikely for QApplication.style() to be None in a running app.
            logger.error(
                "HighlightPreservingDelegate: Critical error - QStyle object is None. Cannot paint item."
            )
            # Fall back to default painting behavior. Painter is guaranteed not None here.
            super().paint(painter, option, index)
            return

        # Now, actual_style is confirmed to be a QtWidgets.QStyle.
        style: QtWidgets.QStyle = actual_style
        # --- END FIX ---

        # Finally paint with our modified palette
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            opt,
            painter,  # painter is guaranteed not None here
            opt.widget,
        )


# --- Candidate Widget Class ---
class CandidateWidget(QtWidgets.QWidget):
    """
    A widget to display a table of potential Tidal track candidates for a Spotify track.
    Includes a 'Select' button for manual linking.
    """

    # Signal emitted when a user manually selects a candidate track
    candidateSelected = QtCore.pyqtSignal(
        int, object
    )  # Args: main_row_index, selected_track (Track object)
    # --- START FIX: Add new signal ---
    noMatchSelected = QtCore.pyqtSignal(int)  # Arg: main_row_index
    # --- END FIX ---

    def __init__(
        self,
        main_row_index: int,
        candidates: List[Dict],
        initial_selected_track_id: Optional[str] = None,
        parent=None,
    ):
        """
        Initializes the CandidateWidget.

        Args:
            main_row_index (int): The row index in the main table this widget belongs to.
            candidates (List[Dict]): A list of candidate dictionaries.
            initial_selected_track_id (Optional[str]): The ID of the track that should be initially marked as selected.
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.main_row_index = main_row_index
        self.selected_tidal_track_id = initial_selected_track_id
        logger.debug(
            f"[CandidateWidget] Initializing for main row {main_row_index} with {len(candidates)} candidates. Initial selected ID: {initial_selected_track_id}"
        )
        self.candidates = candidates
        self._setup_ui()

    def _setup_ui(self):
        """Sets up the layout and widgets for the CandidateWidget."""

        # Check if a layout is ALREADY set. This is for debugging.
        if self.layout() is not None:
            logger.warning(
                f"[CandidateWidget Row {self.main_row_index}] _setup_ui: Widget already has a layout ({self.layout()}) before attempting to set a new one. This is unexpected."
            )

        # 1. Create the layout object WITHOUT a parent widget initially.
        layout = QtWidgets.QVBoxLayout()
        # No 'if not layout:' check needed here, as this will always create a layout object.

        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.candidate_table = QtWidgets.QTableWidget()
        self.candidate_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.candidate_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )  # Allow single row selection
        self.candidate_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )  # Select whole rows
        v_header = self.candidate_table.verticalHeader()
        if v_header:  # Add check
            v_header.setVisible(False)  # Hide row numbers
        # self.candidate_table.horizontalHeader().setStretchLastSection(True) # Don't stretch last section initially
        # Set smaller font for header
        h_header = self.candidate_table.horizontalHeader()
        if h_header:  # Check if header exists
            header_font = h_header.font()
            header_font.setPointSize(
                max(6, header_font.pointSize() - 1)
            )  # Decrease font size by 1pt, ensure minimum size (e.g., 6pt)
            h_header.setFont(header_font)

        # Define columns for the candidate table (Action moved to front, Score after Action)
        # Rename Duration -> Length
        candidate_column_headers = [
            "Action",
            "Score",
            "Tidal Title",
            "Artists",
            "Album",
            "Length",
            "ISRC",
            "Mismatch Reasons",
        ]  # Length is index 5
        self.candidate_table.setColumnCount(len(candidate_column_headers))
        self.candidate_table.setHorizontalHeaderLabels(candidate_column_headers)

        # Connect itemClicked or currentItemChanged to handle selection visual ---
        self.candidate_table.itemClicked.connect(self._on_candidate_item_clicked)

        # Apply stylesheet for smaller header padding (ensure it doesn't conflict with main style)
        # and selection styling
        widget_bg_color = "#383838"
        table_bg_color = "#404040"
        item_hover_bg_color = "#4E4E4E"  # Lighter grey for hover
        # Define the selection background color explicitly to avoid conflicts
        item_selected_bg_color = "#0078D4"  # Your current selection blue
        item_selected_text_color = Qt.GlobalColor.white  # Text color for selected items

        h_header = self.candidate_table.horizontalHeader()
        if h_header:  # Add check
            h_header.setStyleSheet(
                "QHeaderView::section { background-color: #454545; border: 1px solid #555555; padding: 4px; }"
            )

        self.candidate_table.setStyleSheet(
            f"""
            QTableWidget {{
                background-color: {table_bg_color};
                border: none;
                gridline-color: #505050;
                outline: 0; /* Remove focus outline from the table itself */
            }}
            QTableWidget::item {{
                background-color: transparent; /* Default item background */
                border: none; /* Remove default item borders */
                padding: 2px; /* Add some padding to items */
            }}
            QTableWidget::item:hover {{
                background-color: {item_hover_bg_color};
                color: white; /* Ensure text is white on hover */
            }}
            QTableWidget::item:selected {{
                background-color: {item_selected_bg_color};
                color: {item_selected_text_color}; /* Ensure text is white when selected */
            }}
            /* Ensure hover on a selected item still uses the hover background, if desired */
            /* Or, if you want selected items to keep their selection color on hover, remove/comment this out */
            QTableWidget::item:selected:hover {{
                background-color: {item_hover_bg_color}; /* Override selection color on hover */
                color: white;
            }}
            QTableWidget::item:focus {{ /* Remove focus rectangle around items */
                outline: 0;
                border: none; /* Or set to a specific color if you want a focus border */
                background-color: transparent; /* Ensure focus doesn't change background unless also hovered/selected */
            }}
        """
        )

        self._populate_candidate_table()  # Populate before adding to layout might be fine

        layout.addWidget(self.candidate_table)

        # --- START FIX: Use a horizontal layout for the button ---
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 5, 0, 0) # Add some top margin

        self.none_match_button = QtWidgets.QPushButton("None of these are a match")
        self.none_match_button.setStyleSheet(
            "QPushButton { padding: 4px; background-color: #553333; border: 1px solid #775555; }"
            "QPushButton:hover { background-color: #664444; }"
        )
        self.none_match_button.clicked.connect(self._on_none_match_clicked)
        
        bottom_layout.addWidget(self.none_match_button)
        bottom_layout.addStretch() # This pushes the button to the left

        layout.addLayout(bottom_layout)
        # --- END FIX ---

        # 2. Set the layout on the CandidateWidget (self)
        self.setLayout(
            layout
        )  # This is the correct way to assign the top-level layout.

        # Apply styling for visual distinction
        self.setStyleSheet(
            f"QWidget {{ background-color: {widget_bg_color}; border: 1px solid #555555; }}"
        )
        # Set inner table background and ensure grid lines are subtle if shown
        # Moved stylesheet setting for candidate_table up to include selection
        # Ensure header style matches (already set above)

    def _populate_candidate_table(self):
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Populating table. Number of candidates: {len(self.candidates)}"
        )  # Existing log
        self.candidate_table.clearSelection()  # Clear any existing selection
        self.candidate_table.setRowCount(len(self.candidates))
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Set candidate_table row count to: {len(self.candidates)}"
        )

        # Set a smaller default row height for the candidate table ---
        v_header = self.candidate_table.verticalHeader()
        if v_header:  # Check if header exists
            v_header.setDefaultSectionSize(28)
        else:
            logger.warning(
                f"[CandidateWidget Row {self.main_row_index}] Could not get vertical header to set default section size."
            )

        selected_font_color = QtGui.QColor("#20867a")
        # Explicitly set the default font color to white for unselected items
        default_font_color = QtGui.QColor(Qt.GlobalColor.white)  # MODIFIED HERE

        for row_index, candidate_data in enumerate(self.candidates):
            try:
                tidal_track = candidate_data.get("tidal_track")
                is_this_row_selected = (
                    tidal_track
                    and getattr(tidal_track, "id", None) == self.selected_tidal_track_id
                )
                score = candidate_data.get("score", "N/A")
                mismatch_reasons_list = candidate_data.get(
                    "mismatch_reasons", []
                )  # Ensure it's a list
                mismatch_text = (
                    ", ".join(mismatch_reasons_list)
                    if mismatch_reasons_list
                    else "None"
                )
                artists_str = (
                    ", ".join(
                        [
                            getattr(a, "name", "N/A")
                            for a in getattr(tidal_track, "artists", [])
                        ]
                    )
                    if getattr(tidal_track, "artists", [])
                    else "N/A"
                )
                album_title = getattr(
                    getattr(tidal_track, "album", None), "title", "N/A"
                )
                duration_s = getattr(tidal_track, "duration", 0)
                duration_str = Printf.formatDuration(duration_s)
                isrc = getattr(tidal_track, "isrc", "N/A")
                title_text = getattr(tidal_track, "title", "N/A")

                logger.debug(
                    f"[CandidateWidget Row {self.main_row_index} SubRow {row_index}] Data: Score={score}, Title='{title_text}', Artists='{artists_str}', Album='{album_title}', Len='{duration_str}', ISRC='{isrc}', Mismatch='{mismatch_text}'"
                )  # ADD THIS

                def create_item(
                    text,
                    alignment=Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignVCenter,
                ):
                    item = QtWidgets.QTableWidgetItem(str(text))
                    item.setTextAlignment(alignment)
                    item_font = (
                        item.font()
                    )  # Gets the widget's default font (e.g. 11pt)
                    # MODIFIED: Set to 9.5pt. Application default is 11pt. 11 - 1.5 = 9.5pt.
                    new_point_size = item_font.pointSizeF() - 2.0
                    item_font.setPointSizeF(
                        max(6.0, new_point_size)
                    )  # Use setPointSizeF for fractional sizes
                    item.setFont(item_font)
                    if is_this_row_selected:
                        item.setForeground(selected_font_color)
                    else:
                        item.setForeground(default_font_color)  # Reset for other rows
                    return item

                self.candidate_table.setItem(
                    row_index, 1, create_item(score, Qt.AlignmentFlag.AlignCenter)
                )
                self.candidate_table.setItem(row_index, 2, create_item(title_text))
                self.candidate_table.setItem(row_index, 3, create_item(artists_str))
                self.candidate_table.setItem(row_index, 4, create_item(album_title))
                self.candidate_table.setItem(
                    row_index,
                    5,
                    create_item(duration_str, Qt.AlignmentFlag.AlignCenter),
                )
                self.candidate_table.setItem(
                    row_index, 6, create_item(isrc, Qt.AlignmentFlag.AlignCenter)
                )
                self.candidate_table.setItem(row_index, 7, create_item(mismatch_text))

                select_button = QtWidgets.QPushButton("Select")
                if is_this_row_selected:
                    select_button.setText("Selected")
                    # select_button.setEnabled(False) # Optionally disable
                # Make button smaller ---
                select_button.setStyleSheet(
                    "QPushButton { padding: 1px 3px; margin: 0px; }"
                )  # Minimal padding
                button_font = select_button.font()
                button_font.setPointSize(8)  # Smaller font
                select_button.setFont(button_font)
                select_button.setFixedHeight(22)  # Fixed height

                select_button.clicked.connect(
                    partial(self._on_select_clicked, self.main_row_index, tidal_track)
                )
                self.candidate_table.setCellWidget(row_index, 0, select_button)
                logger.debug(
                    f"[CandidateWidget Row {self.main_row_index} SubRow {row_index}] Set items and button."
                )

            except Exception as e:
                logger.error(
                    f"[CandidateWidget Row {self.main_row_index}] Error populating candidate sub-row {row_index}: {e}",
                    exc_info=True,
                )
                self.candidate_table.setItem(
                    row_index, 0, QtWidgets.QTableWidgetItem("Error loading candidate")
                )
                for col in range(1, self.candidate_table.columnCount()):
                    self.candidate_table.setItem(
                        row_index, col, QtWidgets.QTableWidgetItem("-")
                    )

        self.candidate_table.resizeColumnsToContents()
        # REMOVE or COMMENT OUT: self.candidate_table.resizeRowsToContents()
        # logger.debug(f"[CandidateWidget Row {self.main_row_index}] Called resizeRowsToContents().") # Comment out if line above is removed

        # Explicitly set row height for all rows ---
        fixed_row_height = 28  # Or your desired height
        for i in range(self.candidate_table.rowCount()):
            self.candidate_table.setRowHeight(i, fixed_row_height)
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Set all candidate rows to fixed height: {fixed_row_height}px"
        )

        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Finished populating. Candidate table visible: {self.candidate_table.isVisible()}, Width: {self.candidate_table.width()}, Height: {self.candidate_table.height()}"
        )
        viewport = self.candidate_table.viewport()
        if viewport:
            viewport.update()  # Force viewport update
        self.candidate_table.updateGeometry()
        self.updateGeometry()

    def _on_select_clicked(self, main_row_index: int, selected_track: object):
        """Slot to handle 'Select' button clicks."""
        logger.debug(
            f"[CandidateWidget Row {main_row_index}] Select button clicked for track ID: {getattr(selected_track, 'id', 'N/A')}"
        )
        self.selected_tidal_track_id = getattr(selected_track, "id", None)
        self.candidateSelected.emit(main_row_index, selected_track)

        # Refresh the candidate table to apply new styling for selection
        # This re-runs _populate_candidate_table which will apply the new font color.
        # A more targeted update would be to iterate rows and update styles, but this is simpler.
        self._populate_candidate_table()  # Re-populate to update styles based on new self.selected_tidal_track_id
        # self.candidate_table.viewport().update() # Alternative, if styling is purely dynamic (less reliable here)

    # --- START FIX: Add the slot for the new button ---
    @pyqtSlot()
    def _on_none_match_clicked(self):
        """Slot to handle 'None of these are a match' button clicks."""
        logger.debug(f"[CandidateWidget Row {self.main_row_index}] 'None Match' button clicked.")
        self.noMatchSelected.emit(self.main_row_index)
    # --- END FIX ---

    def calculate_required_height(self, max_rows_no_scroll=6) -> int:
        # Get the layout AFTER _setup_ui has run and self.setLayout() has been called.
        current_layout = self.layout()
        if not isinstance(
            current_layout, QtWidgets.QVBoxLayout
        ):  # Check if it's the expected layout type
            logger.warning(
                f"[CandidateWidget Row {self.main_row_index}] calculate_required_height: Layout is None or not QVBoxLayout! Type: {type(current_layout)}"
            )
            return 50

        # Ensure contentsMargins are from the layout itself
        base_height = (
            current_layout.contentsMargins().top()
            + current_layout.contentsMargins().bottom()
        )

        h_header = self.candidate_table.horizontalHeader()
        header_height = 0
        if h_header and not h_header.isHidden():
            header_height = h_header.sizeHint().height()
            if header_height <= 0:
                header_height = 25
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Header height: {header_height}"
        )

        rows_to_calculate = self.candidate_table.rowCount()
        rows_height = 0
        # default_row_height = 28 # Keep this as a fallback
        fixed_candidate_row_height = 28  # The height you set above

        if rows_to_calculate > 0:
            # If you set a fixed height for all rows, the calculation is simpler:
            rows_height = rows_to_calculate * fixed_candidate_row_height
            # The loop below can be removed if using a fixed height for all rows
            # for i in range(rows_to_calculate):
            #     row_h = self.candidate_table.rowHeight(i)
            #     if row_h <= 5:
            #         logger.warning(f"[CandidateWidget Row {self.main_row_index}] SubRow {i} height is {row_h}. Using default_row_height.")
            #         row_h = default_row_height
            #     rows_height += row_h
            #     logger.debug(f"[CandidateWidget Row {self.main_row_index}] SubRow {i} height used for calc: {row_h}")

        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Total rows_height: {rows_height}"
        )

        padding = 5  # Keep some padding

        total_height = base_height + header_height + rows_height + padding
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Calculated required height: {total_height} (Base: {base_height}, Header: {header_height}, Rows ({rows_to_calculate}): {rows_height}, Padding: {padding})"
        )

        min_height_per_row_content = 25
        min_total_height = (
            base_height
            + header_height
            + (min_height_per_row_content if rows_to_calculate > 0 else 0)
            + padding
        )

        final_height = max(total_height, min_total_height, 50)
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Final height to be returned: {final_height}"
        )
        return final_height

    @pyqtSlot(QtWidgets.QTableWidgetItem)  # Import QTableWidgetItem if not already
    def _on_candidate_item_clicked(self, item: QtWidgets.QTableWidgetItem):
        if not item:
            return
        # When an item is clicked, the table's selection model handles the visual highlighting
        # due to the stylesheet and selection mode.
        # We don't need to do much here unless we want to store the selected candidate.
        selected_row = item.row()
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Candidate item clicked at sub-row {selected_row}"
        )
        # If you need to know which candidate was "selected" (but not yet chosen via "Select" button):
        # self.currently_highlighted_candidate_index = selected_row


# --- END OF FILE gui_table.py ---