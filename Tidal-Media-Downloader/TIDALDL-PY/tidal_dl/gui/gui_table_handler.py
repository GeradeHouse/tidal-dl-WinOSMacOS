# --- START OF FILE gui_table_handler.py ---

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_table_handler.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Manages the results QTableWidget in the GUI.
"""

import logging
from typing import List, Dict, Optional, Any, Union, cast, TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import QTimer, QObject, pyqtSlot, Qt, QPoint, pyqtSignal
from PyQt6.QtWidgets import QTableWidgetItem, QProgressBar, QMenu

from .gui_table import SplitterTable
from ..tidal import Type, Track, Playlist, TIDAL_API
from ..printf import Printf
from .gui_utils import format_duration_ms
from ..persistence import LinkPersistenceManager

# Robust import alias for aigpy dictToModel
try:
    from aigpy import model as aigmodel
except Exception:  # pragma: no cover - environment dependent
    try:
        from aigpy import modelHelper as aigmodel  # type: ignore
    except Exception:
        aigmodel = None  # type: ignore


if TYPE_CHECKING:
    from .gui import MainView
    from .gui_download import DownloadHandler
    from .gui_linking_handler import LinkingGuiHandler


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

MANUAL_LINK_REQUIRED_ROLE = Qt.ItemDataRole.UserRole + 100


class TableHandler(QObject):
    """
    Manages the results table (SplitterTable), including populating it
    with data and handling context menus specific to the table content.
    """

    def __init__(
        self,
        table_widget: Optional[SplitterTable],
        persistence_manager: LinkPersistenceManager,
        linking_handler: Optional["LinkingGuiHandler"],
        parent: "MainView",
    ):
        super().__init__(parent)
        self.main_view = parent
        self.table_widget = table_widget
        self.persistence_manager = persistence_manager
        self.linking_handler = linking_handler
        self.download_handler: Optional["DownloadHandler"] = None

        if self.table_widget:
            self._connect_table_signals()
            if self.linking_handler:
                self.table_widget.set_linking_gui_handler(self.linking_handler)
        else:
            logger.warning(
                "TableHandler initialized with table_widget=None. Signals not connected yet."
            )

    def set_download_handler(self, handler: "DownloadHandler"):
        self.download_handler = handler

    def set_linking_handler(self, handler: "LinkingGuiHandler"):
        self.linking_handler = handler
        if self.table_widget:
            self.table_widget.set_linking_gui_handler(handler)
            # Robustly connect the signal here, where we know both handlers exist
            self.table_widget.noMatchSelectedInSubRow.connect(handler.on_no_match_selected)


    def _connect_table_signals(self):
        if not self.table_widget:
            logger.error(
                "Attempted to connect table signals, but table_widget is None."
            )
            return
        self.table_widget.customContextMenuRequested.connect(
            self.handle_table_context_menu
        )

        # The connection for noMatchSelectedInSubRow is now moved to set_linking_handler
        # for better robustness, ensuring the handler exists when the connection is made.

        header = self.table_widget.horizontalHeader()
        if header:
            header.sortIndicatorChanged.connect(self.on_sort_indicator_changed)

    def clear_table(self):
        if not self.table_widget:
            return
        self.table_widget.clearRows()
        if self.download_handler:
            self.download_handler._update_download_button_text()

    def show_loading_message(self, message: str):
        if not self.table_widget:
            return
        self.clear_table()
        self.table_widget.setColumnCount(1)
        self.table_widget.setHorizontalHeaderLabels([message])
        self.table_widget.addRow([message], None)
        if self.download_handler:
            self.download_handler._update_download_button_text()

    def show_error_message(self, message: str):
        if not self.table_widget:
            return
        self.clear_table()
        self.table_widget.setColumnCount(1)
        self.table_widget.setHorizontalHeaderLabels(["Error"])
        self.table_widget.addRow([message], None)
        if self.download_handler:
            self.download_handler._update_download_button_text()

    def populate_search_results(
        self,
        results_array: List[Any],
        result_type: Type,
        search_context: Optional[object],
    ):
        if not self.table_widget:
            return
        self.main_view.s_array = results_array
        self.main_view.s_type = result_type
        self.main_view.s_playlist = isinstance(search_context, (Playlist, dict))
        self.main_view.s_playlist_obj = cast(
            Optional[Union[Playlist, Dict]], search_context
        )
        self._populate_table_generic(results_array, result_type)
        if self.linking_handler:
            self.linking_handler.update_link_button_state()
        if self.download_handler:
            self.download_handler._update_download_button_text()

    @pyqtSlot(list, str)
    def populate_tidal_tracks(self, tracks: List[Track], error_msg: str):
        if not self.table_widget:
            return
        if error_msg:
            self.show_error_message(error_msg)
        else:
            self.main_view.s_array = tracks
            self.main_view.s_type = Type.Track
            self._populate_table_generic(tracks, Type.Track)
        if self.linking_handler:
            self.linking_handler.update_link_button_state()
        if self.download_handler:
            self.download_handler._update_download_button_text()

    @pyqtSlot(str, list)
    def populate_spotify_tracks(
        self, playlist_id: str, tracks: Optional[List[Dict[str, Any]]]
    ):
        if not self.table_widget:
            return
        if tracks is None:
            self.show_error_message("Error fetching Spotify tracks.")
            tracks = []

        spotify_track_array_for_mainview: List[Dict[str, Any]] = [
            {"type": "spotify_track", "data": t} for t in tracks
        ]
        self.main_view.s_array = spotify_track_array_for_mainview
        self.main_view.s_type = Type.Track
        self._populate_table_generic(
            spotify_track_array_for_mainview, Type.Track, playlist_id=playlist_id
        )
        if self.linking_handler:
            self.linking_handler.update_link_button_state()
        if self.download_handler:
            self.download_handler._update_download_button_text()

    @pyqtSlot(list, str)
    def _populate_table_from_search(self, tracks: List[Track], error_msg: str):
        if error_msg:
            self.show_error_message(error_msg)
            return
        self.main_view.s_playlist = False
        self.main_view.s_playlist_obj = None
        self.main_view.s_array = tracks
        self.main_view.s_type = Type.Track
        self._populate_table_generic(tracks, Type.Track)
        if self.linking_handler:
            self.linking_handler.update_link_button_state()
        if self.download_handler:
            self.download_handler._update_download_button_text()

    def _populate_table_generic(
        self,
        results_array: List[Any],
        result_type: Type,
        playlist_id: Optional[str] = None,
    ):
        if not self.table_widget or not self.download_handler:
            return
        table = self.table_widget
        table.clearRows()

        is_spotify_track_list = (
            result_type == Type.Track
            and results_array
            and isinstance(results_array[0], dict)
            and results_array[0].get("type") == "spotify_track"
        )

        base_headers = ["#", "Title", "Artists", "Album", "Length", "Quality"]
        column_headers = base_headers + ["Status"]
        table.setColumnCount(len(column_headers))
        table.setHorizontalHeaderLabels(column_headers)
        self.column_indices = {header: i for i, header in enumerate(column_headers)}

        if not results_array:
            table.update()
            if self.download_handler:
                self.download_handler._update_download_button_text()
            return

        persisted_links: Dict[str, Any] = {}
        if is_spotify_track_list and playlist_id:
            persisted_links = self.persistence_manager.get_links_for_playlist(
                playlist_id
            )

        Printf.info(
            f"Populating table with {len(results_array)} items of type {result_type.name}..."
        )

        for index, item in enumerate(results_array):
            rowData: Optional[List[str]] = None
            item_metadata: Any = item
            track_id_for_download_check: Optional[str] = None

            try:
                if is_spotify_track_list:
                    original_spotify_track_data = item.get("data", {})
                    spotify_track_id = original_spotify_track_data.get("id")
                    spotify_track_data_to_use = original_spotify_track_data

                    link_status_text = "Not Linked"
                    link_status_data_for_title: Dict[str, Any] = {
                        "type": "spotify_track",
                        "link_status": "not_linked",
                        "data": spotify_track_data_to_use,
                    }

                    persisted_tracks_dict = persisted_links.get("tracks", {})
                    if (
                        spotify_track_id
                        and spotify_track_id in persisted_tracks_dict
                        and aigmodel is not None
                    ):
                        link_info = persisted_tracks_dict.get(spotify_track_id, {})
                        tidal_track_obj = None
                        tdata = link_info.get("tidal_track_details")
                        if tdata:
                            try:
                                tidal_track_obj = aigmodel.dictToModel(tdata, Track())
                            except Exception:
                                tidal_track_obj = None

                        if tidal_track_obj:
                            track_id_for_download_check = str(tidal_track_obj.id)
                            link_status_text = "Linked"
                            link_status_data_for_title.update(
                                {
                                    "link_status": "cached_linked",
                                    "tidal_track_id": track_id_for_download_check,
                                    "tidal_track": tidal_track_obj,
                                }
                            )

                    artists_str = ", ".join(
                        spotify_track_data_to_use.get("artists", [])
                    )
                    album_name = spotify_track_data_to_use.get("album", "N/A")
                    duration_str = format_duration_ms(
                        spotify_track_data_to_use.get("duration_ms")
                    )
                    rowData = [
                        str(index + 1),
                        spotify_track_data_to_use.get("name", "N/A"),
                        artists_str,
                        album_name,
                        duration_str,
                        "-",
                        link_status_text,
                    ]
                    item_metadata = link_status_data_for_title

                elif isinstance(item, Track):
                    track_id_for_download_check = str(item.id)
                    quality_string = Printf.map_quality(item)
                    album_title = item.album.title if item.album else "N/A"
                    artists = (
                        item.artists
                        if isinstance(item.artists, list)
                        else [item.artist]
                    )
                    rowData = [
                        str(index + 1),
                        str(item.title),
                        TIDAL_API.getArtistsName(artists),
                        str(album_title),
                        Printf.formatDuration(item.duration),
                        str(quality_string),
                        "-",
                    ]
                    item_metadata = item

                if rowData:
                    table.addRow(rowData, item_metadata)
                    status_col_idx = self.column_indices.get("Status")
                    if (
                        status_col_idx is not None
                        and track_id_for_download_check in self.download_handler.active_downloads
                    ):
                        download_state = self.download_handler.active_downloads[
                            track_id_for_download_check
                        ]
                        status = download_state.get("status", "unknown")
                        progress = download_state.get("progress", 0)

                        status_item = table.item(index, status_col_idx)
                        if not status_item:
                            status_item = QTableWidgetItem()
                            table.setItem(index, status_col_idx, status_item)

                        if status == "pending":
                            status_item.setText("Pending for download")
                            status_item.setToolTip(download_state.get("tooltip", ""))
                        elif status == "downloading":
                            table.removeCellWidget(index, status_col_idx)
                            bar = QProgressBar()
                            bar.setValue(progress)
                            bar.setRange(0, 100)
                            bar.setFixedHeight(14)
                            bar.setTextVisible(False)
                            table.setCellWidget(index, status_col_idx, bar)
                        elif status == "completed":
                            status_item.setText("Completed")
                        elif status == "failed":
                            status_item.setText("Failed")
                            status_item.setToolTip(download_state.get("error", ""))

            except Exception as e:
                logger.error(
                    f"Error processing item at index {index}: {e}", exc_info=True
                )
                table.addRow([str(index + 1), f"Error: {e}"], None)

        table.adjustColumnWidths()
        table.update()
        Printf.success(f"Table populated with {table.rowCount()} items.")
        if self.download_handler:
            self.download_handler._update_download_button_text()

    def handle_table_context_menu(self, pos: QPoint):
        if not self.table_widget:
            return
        table = self.table_widget
        index = table.indexAt(pos)
        if not index.isValid():
            return

        selected_rows_indices = sorted(
            list(set(idx.row() for idx in table.selectedIndexes()))
        )
        if not selected_rows_indices:
            selected_rows_indices = [index.row()]

        first_row_index = selected_rows_indices[0]
        title_item = table.item(first_row_index, 1)
        item_metadata_raw = (
            title_item.data(Qt.ItemDataRole.UserRole) if title_item else None
        )

        is_spotify_track_from_meta = False
        is_linked = False
        if isinstance(item_metadata_raw, dict):
            is_spotify_track_from_meta = (
                item_metadata_raw.get("type") == "spotify_track"
            )
            valid_linked_statuses = [
                "found",
                "auto_linked",
                "manual_linked",
                "cached_linked",
                "found_uncertain",
                "cached_linked_full",
                "cached_linked_id_fetched",
            ]
            is_linked = item_metadata_raw.get("link_status") in valid_linked_statuses
        elif isinstance(item_metadata_raw, Track):
            is_linked = True

        context_menu = QMenu(table)
        context_menu.setStyleSheet(
            """
            QMenu { background-color: #333333; color: white; border: 1px solid #555555; }
            QMenu::item:selected { background-color: #555555; }
            """
        )

        if is_spotify_track_from_meta:
            if self.linking_handler:
                self.linking_handler.spotifyLinkContextMenu(
                    context_menu, selected_rows_indices
                )
            if is_linked and self.download_handler:
                if not context_menu.isEmpty():
                    context_menu.addSeparator()
                self.download_handler.downloadTableContextMenu(
                    context_menu, selected_rows_indices
                )
        elif self.download_handler:
            self.download_handler.downloadTableContextMenu(
                context_menu, selected_rows_indices
            )

        if not context_menu.isEmpty():
            viewport = table.viewport()
            if viewport:
                context_menu.exec(viewport.mapToGlobal(pos))
            else:
                context_menu.exec(QtGui.QCursor.pos())

    def update_linking_status(
        self,
        row_index: int,
        status: str,
        status_text: Optional[str] = None,
        tidal_track: Optional[Track] = None,
        candidates: Optional[List[Dict[str, Any]]] = None,
        score: Optional[int] = None,
        error_message: Optional[str] = None,
    ):
        if not self.table_widget:
            return
        table = self.table_widget
        if row_index >= table.rowCount():
            return

        final_status_text = status_text or ""
        status_item = table.item(row_index, self.column_indices["Status"])
        if not status_item:
            status_item = QTableWidgetItem()
            table.setItem(row_index, self.column_indices["Status"], status_item)
        status_item.setText(final_status_text)

        title_item = table.item(row_index, 1)
        if title_item:
            item_data = title_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict):
                item_data["link_status"] = status
                item_data["tidal_track"] = tidal_track
                item_data["score"] = score
                if error_message:
                    item_data["error_message"] = error_message
                title_item.setData(Qt.ItemDataRole.UserRole, item_data)

        indicator_item = table.item(row_index, 0)
        if not indicator_item:
            indicator_item = QTableWidgetItem()
            table.setItem(row_index, 0, indicator_item)

        if candidates and len(candidates) > 0:
            indicator_data = {
                "has_candidates": True,
                "expanded": False,
                "candidate_count": len(candidates),
                "candidates_list": candidates,
                "linked_tidal_track_id": tidal_track.id if tidal_track else None,
            }
            indicator_item.setData(QtCore.Qt.ItemDataRole.UserRole, indicator_data)
            logger.debug(f"[TableHandler] Set indicator data for row {row_index} with {len(candidates)} candidates.")
        else:
            indicator_data = {
                "has_candidates": False,
                "expanded": False,
                "candidate_count": 0,
                "candidates_list": [],
                "linked_tidal_track_id": tidal_track.id if tidal_track else None,
            }
            indicator_item.setData(QtCore.Qt.ItemDataRole.UserRole, indicator_data)
            logger.debug(f"[TableHandler] Cleared indicator data for row {row_index}.")

        table._update_row_appearance_for_row(row_index)

    def refresh_view_for_pending_downloads(self, tracks_in_queue: List[Track]):
        if not self.table_widget:
            return
        for i, track in enumerate(tracks_in_queue):
            row = self._find_row_for_track_id(str(track.id))
            if row is not None:
                status_item = self.table_widget.item(row, self.column_indices["Status"])
                if status_item:
                    status_item.setText("Pending for download")
                    tooltip = f"Position {i+1} of {len(tracks_in_queue)} in queue."
                    status_item.setToolTip(tooltip)

    def refresh_table_view(self):
        """Repopulates the table using the current main_view state."""
        if not self.main_view:
            return
        playlist_obj = self.main_view.s_playlist_obj
        playlist_id = None
        if isinstance(playlist_obj, dict):
            playlist_id = playlist_obj.get("data", {}).get("id")
        elif isinstance(playlist_obj, Playlist):
            playlist_id = playlist_obj.uuid

        self._populate_table_generic(
            self.main_view.s_array, self.main_view.s_type or Type.Null, playlist_id
        )

    def _find_row_for_track_id(self, track_id_to_find: str) -> Optional[int]:
        if not self.table_widget:
            return None
        for row in range(self.table_widget.rowCount()):
            title_item = self.table_widget.item(row, 1)
            if not title_item:
                continue
            item_data = title_item.data(Qt.ItemDataRole.UserRole)
            current_track_id = None
            if isinstance(item_data, Track):
                current_track_id = str(item_data.id)
            elif isinstance(item_data, dict):
                tidal_track = item_data.get("tidal_track")
                if isinstance(tidal_track, Track):
                    current_track_id = str(tidal_track.id)
                elif item_data.get("tidal_track_id"):
                    current_track_id = str(item_data.get("tidal_track_id"))
            if current_track_id and current_track_id == track_id_to_find:
                return row
        return None

    @pyqtSlot(str)
    def setup_progress_bar_for_download(self, track_id: str):
        if not self.table_widget or "Status" not in self.column_indices:
            return
        row = self._find_row_for_track_id(track_id)
        if row is not None:
            status_col = self.column_indices["Status"]
            # Clear any previous content in the status cell
            self.table_widget.removeCellWidget(row, status_col)
            item = self.table_widget.item(row, status_col)
            if item:
                item.setText("")
                item.setToolTip("")

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(14)
            self.table_widget.setCellWidget(row, status_col, bar)
            logger.debug(f"Setup progress bar for track {track_id} at row {row}")

    @pyqtSlot(str, int)
    def update_track_progress(self, track_id: str, percentage: int):
        if not self.table_widget or "Status" not in self.column_indices:
            return
        row = self._find_row_for_track_id(track_id)
        if row is None:
            return
        status_col = self.column_indices["Status"]
        widget = self.table_widget.cellWidget(row, status_col)
        if not isinstance(widget, QProgressBar):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(14)
            self.table_widget.setCellWidget(row, status_col, bar)
            widget = bar
        widget.setValue(int(percentage))

    def mark_track_completed(self, track_id: str, ok: bool, error_msg: str = ""):
        if not self.table_widget or "Status" not in self.column_indices:
            return
        row = self._find_row_for_track_id(track_id)
        if row is None:
            return
        status_col = self.column_indices["Status"]
        if self.table_widget.cellWidget(row, status_col):
            self.table_widget.removeCellWidget(row, status_col)

        item = self.table_widget.item(row, status_col)
        if not item:
            item = QTableWidgetItem()
            self.table_widget.setItem(row, status_col, item)

        item.setText("Completed" if ok else "Failed")
        item.setToolTip("" if ok else error_msg)

    def collapse_sub_row(self, main_row_index: int):
        if not self.table_widget:
            return
        self.table_widget.collapseSubRow(main_row_index)

    @pyqtSlot(int, Qt.SortOrder)
    def on_sort_indicator_changed(self, logicalIndex: int, order: Qt.SortOrder):
        """
        Slot triggered when the user clicks a header to sort the table.
        Collapses all sub-rows BEFORE the sort is visually applied to prevent corruption.
        """
        if self.table_widget:
            logger.debug(f"Sort indicator changed for column {logicalIndex}. Collapsing all sub-rows.")
            self.table_widget.collapse_all_sub_rows()
            # The table will proceed with its internal sorting *after* this slot completes.

    @pyqtSlot(int, Qt.SortOrder)
    def _on_sort_indicator_changed(self, column: int, order: Qt.SortOrder):
        # A short delay allows the table's internal sort to finish before we redraw.
        QTimer.singleShot(0, self._rebuild_status_column_from_state)

    def _rebuild_status_column_from_state(self):
        if (
            not self.table_widget
            or "Status" not in self.column_indices
            or not self.download_handler
        ):
            return

        status_col = self.column_indices["Status"]
        # Clear all widgets and text/tooltips first
        for row in range(self.table_widget.rowCount()):
            self.table_widget.removeCellWidget(row, status_col)
            item = self.table_widget.item(row, status_col)
            if item:
                item.setText("")
                item.setToolTip("")

        # Rebuild from model state
        for row in range(self.table_widget.rowCount()):
            track_id = None
            title_item = self.table_widget.item(row, 1)
            if title_item:
                data = title_item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, Track):
                    track_id = str(data.id)
                elif isinstance(data, dict):
                    tidal_track = data.get("tidal_track")
                    if isinstance(tidal_track, Track):
                        track_id = str(tidal_track.id)
                    elif data.get("tidal_track_id"):
                        track_id = str(data.get("tidal_track_id"))

            status_item = self.table_widget.item(row, status_col)
            if not status_item:
                status_item = QTableWidgetItem()
                self.table_widget.setItem(row, status_col, status_item)

            if not track_id:
                # No tidal track associated; restore link text if available
                link_text = ""
                if title_item:
                    data = title_item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict):
                        map_text = {
                            "manual_review_needed": "Manual Review",
                            "found_uncertain": "Linked (Uncertain)",
                            "cached_linked": "Linked",
                            "auto_linked": "Linked",
                            "manual_linked": "Linked",
                            "not_linked": "Not Linked",
                            "not_found": "Not Found",
                            "candidates_only": "Manual Review",
                            "error": "Error",
                        }
                        link_text = map_text.get(data.get("link_status") or "", "")
                status_item.setText(link_text)
                continue

            state = self.download_handler.active_downloads.get(track_id)
            if not state:
                # Restore link-state text for non-active rows
                link_text = ""
                if title_item:
                    data = title_item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict):
                        map_text = {
                            "manual_review_needed": "Manual Review",
                            "found_uncertain": "Linked (Uncertain)",
                            "cached_linked": "Linked",
                            "auto_linked": "Linked",
                            "manual_linked": "Linked",
                            "not_linked": "Not Linked",
                            "not_found": "Not Found",
                            "candidates_only": "Manual Review",
                            "error": "Error",
                        }
                        link_text = map_text.get(data.get("link_status") or "", "")
                status_item.setText(link_text)
                continue

            status = state.get("status")
            progress = state.get("progress", 0)
            tooltip = state.get("tooltip", "")
            error = state.get("error", "")

            if status == "pending":
                status_item.setText("Pending for download")
                status_item.setToolTip(tooltip)
            elif status == "downloading":
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setTextVisible(False)
                bar.setFixedHeight(14)
                bar.setValue(int(progress))
                self.table_widget.setCellWidget(row, status_col, bar)
            elif status == "completed":
                status_item.setText("Completed")
            elif status in ("failed", "cancelled"):
                status_item.setText("Failed" if status == "failed" else "Cancelled")
                if error:
                    status_item.setToolTip(error)

# --- END OF FILE gui_table_handler.py ---