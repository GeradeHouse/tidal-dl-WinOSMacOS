#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :  gui_table_candidate_widget.py
@Date    :  2025/04/15
@Author  :  GeradeHouse
@Version :  1.1
@Desc    :  Widget to display and select candidate tracks for manual linking.
"""

import logging
from typing import List, Dict, Optional
from functools import partial

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import QHBoxLayout

import aigpy
from tidal_dl.model import Track
from tidal_dl.printf import Printf

# --- Setup Logging ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

def _setup_gui_logging():
    try:
        from tidal_dl.gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        pass

_setup_gui_logging()


class CandidateWidget(QtWidgets.QWidget):
    """
    A widget to display a table of potential Tidal track candidates for a Spotify track.
    Includes a 'Select' button for manual linking.
    """

    # Signal emitted when a user manually selects a candidate track
    candidateSelected = QtCore.pyqtSignal(
        int, object
    )  # Args: main_row_index, selected_track (Track object)
    
    noMatchSelected = QtCore.pyqtSignal(int)  # Arg: main_row_index

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
        
        # Set smaller font for header
        h_header = self.candidate_table.horizontalHeader()
        if h_header:  # Check if header exists
            header_font = h_header.font()
            header_font.setPointSize(
                max(6, header_font.pointSize() - 1)
            )  # Decrease font size by 1pt, ensure minimum size (e.g., 6pt)
            h_header.setFont(header_font)

        # Define columns for the candidate table
        candidate_column_headers = [
            "Action",
            "Score",
            "Tidal Title",
            "Artists",
            "Album",
            "Length",
            "ISRC",
            "Mismatch Reasons",
        ]
        self.candidate_table.setColumnCount(len(candidate_column_headers))
        self.candidate_table.setHorizontalHeaderLabels(candidate_column_headers)

        # Connect itemClicked to handle selection visual
        self.candidate_table.itemClicked.connect(self._on_candidate_item_clicked)

        # Apply stylesheet
        widget_bg_color = "#383838"
        table_bg_color = "#404040"
        item_hover_bg_color = "#4E4E4E"
        item_selected_bg_color = "#0078D4"
        item_selected_text_color = Qt.GlobalColor.white

        h_header = self.candidate_table.horizontalHeader()
        if h_header:
            h_header.setStyleSheet(
                "QHeaderView::section { background-color: #454545; border: 1px solid #555555; padding: 4px; }"
            )

        self.candidate_table.setStyleSheet(
            f"""
            QTableWidget {{
                background-color: {table_bg_color};
                border: none;
                gridline-color: #505050;
                outline: 0;
            }}
            QTableWidget::item {{
                background-color: transparent;
                border: none;
                padding: 2px;
            }}
            QTableWidget::item:hover {{
                background-color: {item_hover_bg_color};
                color: white;
            }}
            QTableWidget::item:selected {{
                background-color: {item_selected_bg_color};
                color: {item_selected_text_color};
            }}
            QTableWidget::item:selected:hover {{
                background-color: {item_hover_bg_color};
                color: white;
            }}
            QTableWidget::item:focus {{
                outline: 0;
                border: none;
                background-color: transparent;
            }}
        """
        )

        self._populate_candidate_table()

        layout.addWidget(self.candidate_table)

        # --- Bottom Button Layout ---
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 5, 0, 0) # Add some top margin

        self.none_match_button = QtWidgets.QPushButton("None of these are a match")
        self.none_match_button.setStyleSheet(
            "QPushButton { padding: 4px; background-color: #553333; border: 1px solid #775555; }"
            "QPushButton:hover { background-color: #664444; }"
        )
        self.none_match_button.clicked.connect(self._on_none_match_clicked)
        
        bottom_layout.addWidget(self.none_match_button)
        bottom_layout.addStretch()

        layout.addLayout(bottom_layout)

        # 2. Set the layout on the CandidateWidget (self)
        self.setLayout(layout)

        # Apply styling for visual distinction
        self.setStyleSheet(
            f"QWidget {{ background-color: {widget_bg_color}; border: 1px solid #555555; }}"
        )

    def _populate_candidate_table(self):
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Populating table. Number of candidates: {len(self.candidates)}"
        )
        self.candidate_table.clearSelection()
        self.candidate_table.setRowCount(len(self.candidates))

        # Set a smaller default row height for the candidate table
        v_header = self.candidate_table.verticalHeader()
        if v_header:
            v_header.setDefaultSectionSize(28)

        selected_font_color = QtGui.QColor("#20867a")
        default_font_color = QtGui.QColor(Qt.GlobalColor.white)

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
                )
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

                def create_item(
                    text,
                    alignment=Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignVCenter,
                ):
                    item = QtWidgets.QTableWidgetItem(str(text))
                    item.setTextAlignment(alignment)
                    item_font = item.font()
                    new_point_size = item_font.pointSizeF() - 2.0
                    item_font.setPointSizeF(max(6.0, new_point_size))
                    item.setFont(item_font)
                    if is_this_row_selected:
                        item.setForeground(selected_font_color)
                    else:
                        item.setForeground(default_font_color)
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
                
                select_button.setStyleSheet(
                    "QPushButton { padding: 1px 3px; margin: 0px; }"
                )
                button_font = select_button.font()
                button_font.setPointSize(8)
                select_button.setFont(button_font)
                select_button.setFixedHeight(22)

                select_button.clicked.connect(
                    partial(self._on_select_clicked, self.main_row_index, tidal_track)
                )
                self.candidate_table.setCellWidget(row_index, 0, select_button)

            except Exception as e:
                logger.error(
                    f"[CandidateWidget Row {self.main_row_index}] Error populating candidate sub-row {row_index}: {e}",
                    exc_info=True,
                )
                self.candidate_table.setItem(
                    row_index, 0, QtWidgets.QTableWidgetItem("Error loading candidate")
                )

        self.candidate_table.resizeColumnsToContents()

        # Explicitly set row height for all rows
        fixed_row_height = 28
        for i in range(self.candidate_table.rowCount()):
            self.candidate_table.setRowHeight(i, fixed_row_height)

        viewport = self.candidate_table.viewport()
        if viewport:
            viewport.update()
        self.candidate_table.updateGeometry()
        self.updateGeometry()

    def _on_select_clicked(self, main_row_index: int, selected_track: object):
        """Slot to handle 'Select' button clicks."""
        logger.debug(
            f"[CandidateWidget Row {main_row_index}] Select button clicked for track ID: {getattr(selected_track, 'id', 'N/A')}"
        )
        self.selected_tidal_track_id = getattr(selected_track, "id", None)
        self.candidateSelected.emit(main_row_index, selected_track)
        self._populate_candidate_table()

    @pyqtSlot()
    def _on_none_match_clicked(self):
        """Slot to handle 'None of these are a match' button clicks."""
        logger.debug(f"[CandidateWidget Row {self.main_row_index}] 'None Match' button clicked.")
        self.noMatchSelected.emit(self.main_row_index)

    def calculate_required_height(self, max_rows_no_scroll=6) -> int:
        """Calculates the total height needed for the widget, including table and button."""
        # Get the layout margins
        layout = self.layout()
        if not layout:
            return 50
            
        margins = layout.contentsMargins()
        base_height = margins.top() + margins.bottom()
        spacing = layout.spacing()

        # Header Height
        h_header = self.candidate_table.horizontalHeader()
        header_height = 0
        if h_header and not h_header.isHidden():
            header_height = h_header.sizeHint().height()
            if header_height <= 0:
                header_height = 25

        # Rows Height
        rows_to_calculate = self.candidate_table.rowCount()
        fixed_candidate_row_height = 28
        rows_height = rows_to_calculate * fixed_candidate_row_height

        # Button Section Height
        button_height = 0
        if hasattr(self, 'none_match_button') and self.none_match_button.isVisible():
            # Button height + layout spacing/margins
            # The bottom layout has top margin 5.
            button_height = self.none_match_button.sizeHint().height() + 10

        # Total calculation
        total_height = base_height + header_height + rows_height + spacing + button_height
        
        # Ensure minimum height
        min_height = 50
        
        final_height = max(total_height, min_height)
        
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Calculated height: {final_height} "
            f"(Base: {base_height}, Header: {header_height}, Rows: {rows_height}, Button: {button_height})"
        )
        return final_height

    @pyqtSlot(QtWidgets.QTableWidgetItem)
    def _on_candidate_item_clicked(self, item: QtWidgets.QTableWidgetItem):
        if not item:
            return
        selected_row = item.row()
        logger.debug(
            f"[CandidateWidget Row {self.main_row_index}] Candidate item clicked at sub-row {selected_row}"
        )