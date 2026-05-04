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
from typing import List, Dict, Optional, Any
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
    candidatePreviewRequested = QtCore.pyqtSignal(int, object)  # main_row_index, tidal_track
    candidateAutoReviewAccepted = QtCore.pyqtSignal(int)  # main_row_index
    candidateUnlinkRequested = QtCore.pyqtSignal(int)  # main_row_index

    def __init__(
        self,
        main_row_index: int,
        candidates: List[Dict],
        initial_selected_track_id: Optional[str] = None,
        review_mode: str = "manual",
        original_spotify_track: Optional[Dict[str, Any]] = None,
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
        self.review_mode = review_mode or "manual"
        self.original_spotify_track = (
            original_spotify_track if isinstance(original_spotify_track, dict) else {}
        )
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

        if self.review_mode == "auto_single_candidate_review":
            self.comparison_widget = self._build_auto_review_comparison_widget()
            layout.addWidget(self.comparison_widget)

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
        self.candidate_table.itemDoubleClicked.connect(
            self._on_candidate_double_clicked
        )
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

        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)

        if self.review_mode == "auto_single_candidate_review":
            unlink_button = QtWidgets.QPushButton("Unlink this track")
            unlink_button.clicked.connect(self._on_unlink_clicked)
            actions_layout.addWidget(unlink_button)

            accept_button = QtWidgets.QPushButton("Match is good enough")
            accept_button.clicked.connect(self._on_auto_review_accepted_clicked)
            actions_layout.addWidget(accept_button)
        else:
            self.none_match_button = QtWidgets.QPushButton("None of these are a match")
            self.none_match_button.clicked.connect(self._on_none_match_clicked)
            actions_layout.addWidget(self.none_match_button)

        actions_layout.addStretch(1)
        layout.addLayout(actions_layout)

        # 2. Set the layout on the CandidateWidget (self)
        self.setLayout(layout)

        # Apply styling for visual distinction
        self.setStyleSheet(
            f"QWidget {{ background-color: {widget_bg_color}; border: 1px solid #555555; }}"
        )

    def _selected_or_first_candidate_track(self) -> Optional[Track]:
        for candidate_data in self.candidates:
            tidal_track = candidate_data.get("tidal_track") if isinstance(candidate_data, dict) else None
            if (
                isinstance(tidal_track, Track)
                and self.selected_tidal_track_id
                and str(getattr(tidal_track, "id", "")) == str(self.selected_tidal_track_id)
            ):
                return tidal_track

        if self.candidates and isinstance(self.candidates[0], dict):
            first_track = self.candidates[0].get("tidal_track")
            return first_track if isinstance(first_track, Track) else None

        return None

    def _spotify_artists_text(self) -> str:
        artists = self.original_spotify_track.get("artists")
        if isinstance(artists, list):
            return ", ".join(str(artist) for artist in artists if artist) or "N/A"
        if isinstance(artists, str):
            return artists
        return "N/A"

    def _spotify_album_text(self) -> str:
        album = self.original_spotify_track.get("album")
        if isinstance(album, dict):
            return str(album.get("name") or "N/A")
        if isinstance(album, str):
            return album
        return "N/A"

    def _spotify_duration_text(self) -> str:
        duration_ms = self.original_spotify_track.get("duration_ms")
        try:
            duration_seconds = int(duration_ms) / 1000
        except Exception:
            return "N/A"
        if duration_seconds <= 0:
            return "N/A"
        return Printf.formatDuration(int(round(duration_seconds)))

    def _tidal_artists_text(self, tidal_track: Optional[Track]) -> str:
        if not isinstance(tidal_track, Track):
            return "N/A"
        artists = getattr(tidal_track, "artists", None)
        if isinstance(artists, list) and artists:
            return ", ".join(
                str(getattr(artist, "name", "") or "")
                for artist in artists
                if getattr(artist, "name", None)
            ) or "N/A"
        artist = getattr(tidal_track, "artist", None)
        return str(getattr(artist, "name", "") or "N/A")

    def _tidal_album_text(self, tidal_track: Optional[Track]) -> str:
        if not isinstance(tidal_track, Track):
            return "N/A"
        album = getattr(tidal_track, "album", None)
        return str(getattr(album, "title", "") or "N/A")

    def _tidal_duration_text(self, tidal_track: Optional[Track]) -> str:
        if not isinstance(tidal_track, Track):
            return "N/A"
        duration = getattr(tidal_track, "duration", 0) or 0
        return Printf.formatDuration(duration) if duration else "N/A"

    def _comparison_label(self, text: str, bold: bool = False) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(str(text or "N/A"))
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        label.setStyleSheet("color: white; padding: 1px 3px; border: none;")
        font = label.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        font.setBold(bold)
        label.setFont(font)
        return label

    def _build_auto_review_comparison_widget(self) -> QtWidgets.QFrame:
        tidal_track = self._selected_or_first_candidate_track()

        frame = QtWidgets.QFrame()
        frame.setObjectName("autoReviewComparison")
        frame.setStyleSheet(
            """
            QFrame#autoReviewComparison {
                background-color: #333333;
                border: 1px solid #5a5a5a;
            }
            """
        )

        grid = QtWidgets.QGridLayout(frame)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)

        grid.addWidget(self._comparison_label("Field", True), 0, 0)
        grid.addWidget(self._comparison_label("Original Spotify row", True), 0, 1)
        grid.addWidget(self._comparison_label("Auto-selected TIDAL candidate", True), 0, 2)

        comparison_rows = [
            (
                "Title",
                self.original_spotify_track.get("name", "N/A"),
                getattr(tidal_track, "title", "N/A") if tidal_track else "N/A",
            ),
            ("Artists", self._spotify_artists_text(), self._tidal_artists_text(tidal_track)),
            ("Album", self._spotify_album_text(), self._tidal_album_text(tidal_track)),
            ("Length", self._spotify_duration_text(), self._tidal_duration_text(tidal_track)),
        ]

        for row_index, (field, spotify_text, tidal_text) in enumerate(comparison_rows, start=1):
            grid.addWidget(self._comparison_label(field, True), row_index, 0)
            grid.addWidget(self._comparison_label(str(spotify_text or "N/A")), row_index, 1)
            grid.addWidget(self._comparison_label(str(tidal_text or "N/A")), row_index, 2)

        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return frame

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

    def _candidate_track_for_row(self, row_index: int) -> Optional[Track]:
        if row_index < 0 or row_index >= len(self.candidates):
            return None
        candidate = self.candidates[row_index]
        if not isinstance(candidate, dict):
            return None
        tidal_track = candidate.get("tidal_track")
        return tidal_track if isinstance(tidal_track, Track) else None

    def _on_candidate_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        tidal_track = self._candidate_track_for_row(item.row())
        if tidal_track is None:
            return
        logger.info(
            "[CandidateWidget Row %s] Preview requested for TIDAL track ID: %s",
            self.main_row_index,
            getattr(tidal_track, "id", "N/A"),
        )
        self.candidatePreviewRequested.emit(self.main_row_index, tidal_track)

    @pyqtSlot()
    def _on_auto_review_accepted_clicked(self) -> None:
        logger.info(
            "[CandidateWidget Row %s] Auto-selected candidate accepted.",
            self.main_row_index,
        )
        self.candidateAutoReviewAccepted.emit(self.main_row_index)

    @pyqtSlot()
    def _on_unlink_clicked(self) -> None:
        logger.info(
            "[CandidateWidget Row %s] Auto-selected candidate unlinked.",
            self.main_row_index,
        )
        self.candidateUnlinkRequested.emit(self.main_row_index)

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
        visible_buttons = [
            button
            for button in self.findChildren(QtWidgets.QPushButton)
            if button.isVisible()
        ]
        if visible_buttons:
            button_height = max(button.sizeHint().height() for button in visible_buttons) + 10

        # Optional source-vs-candidate comparison panel height
        comparison_height = 0
        comparison_widget = getattr(self, "comparison_widget", None)
        if comparison_widget and comparison_widget.isVisible():
            comparison_height = comparison_widget.sizeHint().height() + spacing

        # Total calculation
        total_height = (
            base_height
            + comparison_height
            + header_height
            + rows_height
            + spacing
            + button_height
        )
        
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
