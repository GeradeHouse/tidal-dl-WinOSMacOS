# tidal_dl/gui/gui_table_handler.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_table_handler.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.2
@Desc    :   Manages the results QTableWidget in the GUI.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional, Any, Union, cast, TYPE_CHECKING, Set

from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import QTimer, QObject, pyqtSlot, Qt, QPoint, pyqtSignal
from PyQt6.QtWidgets import QTableWidgetItem, QProgressBar, QMenu

from tidal_dl.gui.gui_table import SplitterTable
from tidal_dl.tidal import Type, Track, Playlist, Album, AudioQuality, TIDAL_API
from tidal_dl.printf import Printf
from tidal_dl.gui.gui_utils import format_duration_ms
from tidal_dl.persistence import LinkPersistenceManager
from tidal_dl.format import getTrackPath
from tidal_dl.model import StreamUrl

# Robust import alias for aigpy dictToModel
try:
    from aigpy import model as aigmodel
except Exception:  # pragma: no cover - environment dependent
    try:
        from aigpy import modelHelper as aigmodel  # type: ignore
    except Exception:
        aigmodel = None  # type: ignore


if TYPE_CHECKING:
    from tidal_dl.gui.gui_main import MainView
    from tidal_dl.gui.gui_download import DownloadHandler
    from tidal_dl.gui.gui_linking_handler import LinkingGuiHandler


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# Set up GUI logging with INFO level for this module (table operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

MANUAL_LINK_REQUIRED_ROLE = Qt.ItemDataRole.UserRole + 100
QUALITY_PLACEHOLDER_TEXT = "Loading..."


class TableHandler(QObject):
    """
    Manages the results table (SplitterTable), including populating it
    with data and handling context menus specific to the table content.
    """

    trackQualityResolved = pyqtSignal(str, str)

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
        
        # Initialize column_indices to prevent AttributeError if accessed before population
        self.column_indices: Dict[str, int] = {}

        # Lazy quality resolution state
        self._quality_executor = ThreadPoolExecutor(max_workers=2)
        self._quality_state_lock = threading.Lock()
        self._quality_cache_access_lock = threading.Lock()
        self._quality_inflight_track_ids: Set[str] = set()
        self._quality_attempted_track_ids: Set[str] = set()
        self._visible_quality_refresh_timer = QTimer(self)
        self._visible_quality_refresh_timer.setSingleShot(True)
        self._visible_quality_refresh_timer.setInterval(120)
        self._visible_quality_refresh_timer.timeout.connect(
            self._resolve_visible_rows_quality
        )
        self.trackQualityResolved.connect(self._on_track_quality_resolved)

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

        vertical_scrollbar = self.table_widget.verticalScrollBar()
        if vertical_scrollbar:
            vertical_scrollbar.valueChanged.connect(
                self._schedule_visible_quality_resolution
            )

    def clear_table(self):
        if not self.table_widget:
            return
        self._visible_quality_refresh_timer.stop()
        with self._quality_state_lock:
            self._quality_inflight_track_ids.clear()
            self._quality_attempted_track_ids.clear()
        self.table_widget.clearRows()
        if self.download_handler:
            self.download_handler._update_download_button_text()

    def _extract_tidal_track_id_from_row(self, row: int) -> Optional[str]:
        if not self.table_widget:
            return None

        title_item = self.table_widget.item(row, 1)
        if not title_item:
            return None

        item_data = title_item.data(Qt.ItemDataRole.UserRole)
        if isinstance(item_data, Track):
            return str(item_data.id)

        if isinstance(item_data, dict):
            tidal_track = item_data.get("tidal_track")
            if isinstance(tidal_track, Track):
                return str(tidal_track.id)

            tidal_track_id = item_data.get("tidal_track_id")
            if tidal_track_id:
                return str(tidal_track_id)

        return None

    def _set_row_quality_text(self, row: int, quality_text: str) -> None:
        if not self.table_widget:
            return

        quality_col = self.column_indices.get("Quality")
        if quality_col is None:
            return

        quality_item = self.table_widget.item(row, quality_col)
        if not quality_item:
            quality_item = QTableWidgetItem()
            self.table_widget.setItem(row, quality_col, quality_item)

        quality_item.setText(quality_text)
        quality_item.setToolTip(quality_text)

    def _schedule_visible_quality_resolution(self, *_args: Any) -> None:
        if not self.table_widget or "Quality" not in self.column_indices:
            return
        self._visible_quality_refresh_timer.start()

    def _get_cached_quality_threadsafe(self, track_id: str) -> Optional[str]:
        with self._quality_cache_access_lock:
            return self.persistence_manager.get_cached_track_quality(track_id)

    def _set_cached_quality_threadsafe(self, track_id: str, quality_text: str) -> None:
        with self._quality_cache_access_lock:
            self.persistence_manager.set_cached_track_quality(track_id, quality_text)

    def _enqueue_track_quality_resolution(self, track_id: str) -> None:
        track_id = str(track_id or "").strip()
        if not track_id:
            return

        with self._quality_state_lock:
            if track_id in self._quality_attempted_track_ids:
                return
            if track_id in self._quality_inflight_track_ids:
                return
            self._quality_attempted_track_ids.add(track_id)
            self._quality_inflight_track_ids.add(track_id)

        self._quality_executor.submit(self._resolve_track_quality_in_background, track_id)

    def _resolve_track_quality_in_background(self, track_id: str) -> None:
        quality_text: Optional[str] = None
        try:
            track_obj = TIDAL_API.getTrack(str(track_id))
            if isinstance(track_obj, Track):
                quality_text = Printf.map_track_quality(track_obj)

            quality_text = (quality_text or "-").strip() or "-"

            self.trackQualityResolved.emit(track_id, quality_text)

        except Exception as ex:
            logger.debug(
                f"Failed to resolve quality for track {track_id}: {ex}",
                exc_info=True,
            )
            self.trackQualityResolved.emit(track_id, "-")
        finally:
            with self._quality_state_lock:
                self._quality_inflight_track_ids.discard(track_id)

    def _resolve_visible_rows_quality(self) -> None:
        if not self.table_widget or "Quality" not in self.column_indices:
            return

        table = self.table_widget
        if table.rowCount() <= 0:
            return

        first_visible_row = table.rowAt(0)
        if first_visible_row < 0:
            first_visible_row = 0

        last_visible_row = table.rowAt(table.viewport().height() - 1)
        if last_visible_row < 0:
            last_visible_row = table.rowCount() - 1

        for row in range(first_visible_row, min(last_visible_row, table.rowCount() - 1) + 1):
            track_id = self._extract_tidal_track_id_from_row(row)
            if not track_id:
                continue

            quality_col = self.column_indices.get("Quality")
            if quality_col is None:
                continue

            quality_item = table.item(row, quality_col)
            current_text = quality_item.text().strip() if quality_item else ""

            if current_text and current_text not in ("-", QUALITY_PLACEHOLDER_TEXT):
                continue

            cached_quality = self._get_cached_quality_threadsafe(track_id)
            if cached_quality:
                self._set_row_quality_text(row, cached_quality)
                continue

            self._set_row_quality_text(row, QUALITY_PLACEHOLDER_TEXT)
            self._enqueue_track_quality_resolution(track_id)

    @pyqtSlot(str, str)
    def _on_track_quality_resolved(self, track_id: str, quality_text: str) -> None:
        if not self.table_widget or "Quality" not in self.column_indices:
            return

        if quality_text not in ("-", "Unknown", "Unknown Quality"):
            self._set_cached_quality_threadsafe(track_id, quality_text)

        for row in range(self.table_widget.rowCount()):
            row_track_id = self._extract_tidal_track_id_from_row(row)
            if row_track_id == track_id:
                self._set_row_quality_text(row, quality_text)

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

    def _create_progress_bar(self) -> QProgressBar:
        """Creates a styled progress bar for the table."""
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(True)
        bar.setFixedHeight(18)
        bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555;
                border-radius: 4px;
                background-color: #333;
                text-align: center;
                color: white;
                font-weight: bold;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background-color: #0078d4;
                border-radius: 3px;
            }
        """)
        return bar

    def _create_centered_progress_container(self, progress: int = 0) -> QtWidgets.QWidget:
        """Creates a cell widget that vertically centers the progress bar."""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(0)
        layout.addStretch(1)

        bar = self._create_progress_bar()
        bar.setValue(int(progress))
        bar.setFormat(f"{int(progress)}%")
        bar.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        layout.addWidget(bar)
        layout.addStretch(1)
        return container

    def _set_progress_bar_widget(self, row: int, status_col: int, progress: int = 0):
        """Sets a centered progress-bar container in the status cell."""
        if not self.table_widget:
            return
        self.table_widget.removeCellWidget(row, status_col)
        self.table_widget.setCellWidget(
            row,
            status_col,
            self._create_centered_progress_container(progress),
        )

    def _get_progress_bar_widget(self, row: int, status_col: int) -> Optional[QProgressBar]:
        """Gets the progress bar from the status cell, whether wrapped or direct."""
        if not self.table_widget:
            return None

        cell_widget = self.table_widget.cellWidget(row, status_col)
        if isinstance(cell_widget, QProgressBar):
            return cell_widget
        if isinstance(cell_widget, QtWidgets.QWidget):
            return cell_widget.findChild(QProgressBar)
        return None

    def _format_completed_status_text(self, quality_text: Optional[str]) -> str:
        cleaned_quality = (quality_text or "").strip()
        if cleaned_quality:
            return f"Completed:\n{cleaned_quality}"
        return "Completed"

    def _normalize_completed_quality_text(self, quality_text: Optional[str]) -> str:
        cleaned_quality = (quality_text or "").strip()
        if cleaned_quality.lower().startswith("highest available"):
            return ""
        return cleaned_quality

    def _extract_tidal_track_from_item_data(self, item_data: Any) -> Optional[Track]:
        if isinstance(item_data, Track):
            return item_data
        if isinstance(item_data, dict):
            tidal_track = item_data.get("tidal_track")
            if isinstance(tidal_track, Track):
                return tidal_track
        return None

    def _is_hires_track(self, track: Track) -> bool:
        media_metadata = getattr(track, "mediaMetadata", {})
        if not isinstance(media_metadata, dict):
            return False
        tags = media_metadata.get("tags", [])
        if not isinstance(tags, list):
            return False
        return any(isinstance(tag, str) and tag.upper() == "HIRES_LOSSLESS" for tag in tags)

    def _build_candidate_track_paths(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> List[str]:
        artists_list = (
            track.artists if isinstance(getattr(track, "artists", None), list) else [getattr(track, "artist", None)]
        )
        artists = TIDAL_API.getArtistsName(cast(List[Any], [a for a in artists_list if a is not None]))
        artist = getattr(getattr(track, "artist", None), "name", "") or artists

        dummy_stream = StreamUrl()
        dummy_stream.url = "https://local.invalid/placeholder.m4a"
        dummy_stream.codec = "aac"

        base_path = getTrackPath(
            track,
            dummy_stream,
            artist,
            artists,
            album=None,
            playlist_context=playlist_context,
        )
        base_stem, _ = os.path.splitext(os.path.normpath(base_path))
        extensions_in_priority_order = [".flac", ".mp3", ".m4a", ".mp4"]
        return [f"{base_stem}{ext}" for ext in extensions_in_priority_order]

    def _find_existing_track_file_path(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        for candidate_path in self._build_candidate_track_paths(track, playlist_context):
            if os.path.exists(candidate_path):
                return candidate_path
        return None

    def _quality_text_from_existing_file(self, track: Track, existing_file_path: str) -> str:
        extension = os.path.splitext(existing_file_path)[1].lower()

        if extension == ".mp3":
            return Printf.map_quality_enum(AudioQuality.MP3)

        if extension == ".flac":
            flac_quality = AudioQuality.HI_RES_LOSSLESS if self._is_hires_track(track) else AudioQuality.LOSSLESS
            return Printf.map_quality_enum(flac_quality)

        if extension in (".m4a", ".mp4"):
            audio_quality = getattr(track, "audioQuality", None)
            quality_name = ""
            if isinstance(audio_quality, AudioQuality):
                quality_name = audio_quality.name
            elif isinstance(audio_quality, str):
                quality_name = audio_quality.strip().upper()

            if quality_name == AudioQuality.LOW.name:
                return Printf.map_quality_enum(AudioQuality.LOW)
            return Printf.map_quality_enum(AudioQuality.HIGH)

        return Printf.map_track_quality(track)

    def get_completed_quality_for_track(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        existing_path = self._find_existing_track_file_path(track, playlist_context)
        if not existing_path:
            return None
        return self._quality_text_from_existing_file(track, existing_path)

    def is_track_completed(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> bool:
        return self.get_completed_quality_for_track(track, playlist_context) is not None

    def filter_non_completed_tracks(
        self,
        tracks: List[Track],
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> List[Track]:
        return [
            track
            for track in tracks
            if isinstance(track, Track)
            and not self.is_track_completed(track, playlist_context)
        ]

    def _resolve_completed_quality_for_row(
        self,
        row: int,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        if not self.table_widget:
            return None
        title_item = self.table_widget.item(row, 1)
        if not title_item:
            return None
        item_data = title_item.data(Qt.ItemDataRole.UserRole)
        track = self._extract_tidal_track_from_item_data(item_data)
        if not track:
            return None
        return self.get_completed_quality_for_track(track, playlist_context)

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
        with self._quality_state_lock:
            self._quality_inflight_track_ids.clear()
            self._quality_attempted_track_ids.clear()

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

        logger.info(
            f"Populating table with {len(results_array)} items of type {result_type.name}..."
        )

        playlist_context_for_display = getattr(self.main_view, "s_playlist_obj", None)

        for index, item in enumerate(results_array):
            rowData: Optional[List[str]] = None
            item_metadata: Any = item
            track_id_for_download_check: Optional[str] = None
            
            # Variables for indicator column (candidates)
            has_candidates = False
            candidates_list = []
            linked_tidal_id = None

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

                        persisted_tidal_track_id_raw = link_info.get("tidal_track_id")
                        if persisted_tidal_track_id_raw:
                            linked_tidal_id = str(persisted_tidal_track_id_raw)
                            track_id_for_download_check = linked_tidal_id
                        
                        # 1. Deserialize Tidal Track
                        tidal_track_obj = None
                        tdata = link_info.get("tidal_track_details")
                        if tdata:
                            try:
                                tidal_track_obj = aigmodel.dictToModel(tdata, Track())
                            except Exception:
                                tidal_track_obj = None
                        
                        # 2. Retrieve Candidates and Score
                        candidates_list = link_info.get("candidates") or []
                        score = link_info.get("score")
                        
                        # 3. Determine Status
                        derived_status = "not_linked"
                        
                        if tidal_track_obj:
                            linked_tidal_id = str(tidal_track_obj.id)
                            track_id_for_download_check = linked_tidal_id
                            
                            if score is not None and score <= 1:
                                link_status_text = f"Linked (Certainty score: {score}): {tidal_track_obj.id}"
                                derived_status = "auto_linked"
                            elif score is not None and score > 1:
                                if candidates_list:
                                    link_status_text = f"Manual linking required (Certainty score: {score}): {tidal_track_obj.id}"
                                    derived_status = "manual_review_needed"
                                    has_candidates = True
                                else:
                                    link_status_text = f"Linked (Uncertain, Score: {score}): {tidal_track_obj.id}"
                                    derived_status = "found_uncertain"
                            else:
                                # Fallback if score missing but track exists
                                link_status_text = f"Linked: {tidal_track_obj.id}"
                                derived_status = "cached_linked"
                        
                        elif candidates_list:
                            # No track selected, but candidates exist
                            link_status_text = "Manual linking required (Candidates available)"
                            derived_status = "candidates_only"
                            has_candidates = True
                        
                        # 4. Update Metadata
                        link_status_data_for_title.update(
                            {
                                "link_status": derived_status,
                                "tidal_track_id": linked_tidal_id,
                                "tidal_track": tidal_track_obj,
                                "score": score,
                                "candidates": candidates_list
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
                        (
                            self._get_cached_quality_threadsafe(linked_tidal_id)
                            if linked_tidal_id
                            else "-"
                        )
                        or (QUALITY_PLACEHOLDER_TEXT if linked_tidal_id else "-"),
                        link_status_text,
                    ]
                    item_metadata = link_status_data_for_title

                elif isinstance(item, Track):
                    track_id_for_download_check = str(item.id)
                    quality_string = (
                        self._get_cached_quality_threadsafe(
                            track_id_for_download_check
                        )
                        or QUALITY_PLACEHOLDER_TEXT
                    )
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
                    
                    # Set Indicator Data (Column 0) for Candidates
                    if has_candidates:
                        indicator_item = table.item(index, 0)
                        if indicator_item:
                            indicator_data = {
                                "has_candidates": True,
                                "expanded": False,
                                "candidate_count": len(candidates_list),
                                "candidates_list": candidates_list,
                                "linked_tidal_track_id": linked_tidal_id,
                            }
                            indicator_item.setData(QtCore.Qt.ItemDataRole.UserRole, indicator_data)
                            indicator_item.setText(f"+ ({len(candidates_list)})")
                    
                    # Update row appearance (colors) based on status
                    table._update_row_appearance_for_row(index)

                    status_col_idx = self.column_indices.get("Status")
                    if status_col_idx is not None:
                        status_item = table.item(index, status_col_idx)
                        if not status_item:
                            status_item = QTableWidgetItem()
                            table.setItem(index, status_col_idx, status_item)

                        has_active_state = bool(
                            track_id_for_download_check
                            and track_id_for_download_check in self.download_handler.active_downloads
                        )

                        if has_active_state:
                            download_state = self.download_handler.active_downloads[
                                cast(str, track_id_for_download_check)
                            ]
                            status = download_state.get("status", "unknown")
                            progress = download_state.get("progress", 0)

                            if status == "pending":
                                status_item.setText("Pending for download")
                                status_item.setToolTip(download_state.get("tooltip", ""))
                            elif status == "downloading":
                                self._set_progress_bar_widget(index, status_col_idx, int(progress))
                            elif status == "completed":
                                completed_quality = self._normalize_completed_quality_text(
                                    cast(Optional[str], download_state.get("completed_quality"))
                                    or cast(Optional[str], download_state.get("requested_quality"))
                                )
                                if not completed_quality:
                                    track_for_completion = self._extract_tidal_track_from_item_data(item_metadata)
                                    if track_for_completion:
                                        completed_quality = (
                                            self.get_completed_quality_for_track(
                                                track_for_completion,
                                                cast(
                                                    Optional[Union[Playlist, Album, Dict[str, Any]]],
                                                    download_state.get("playlist_context", playlist_context_for_display),
                                                ),
                                            )
                                            or ""
                                        )
                                status_item.setText(self._format_completed_status_text(completed_quality))
                                status_item.setToolTip(completed_quality)
                            elif status == "failed":
                                status_item.setText("Failed")
                                status_item.setToolTip(download_state.get("error", ""))
                        else:
                            track_for_completion = self._extract_tidal_track_from_item_data(item_metadata)
                            if track_for_completion:
                                completed_quality = self.get_completed_quality_for_track(
                                    track_for_completion,
                                    cast(Optional[Union[Playlist, Album, Dict[str, Any]]], playlist_context_for_display),
                                )
                                if completed_quality:
                                    status_item.setText(self._format_completed_status_text(completed_quality))
                                    status_item.setToolTip(completed_quality)

            except Exception as e:
                logger.error(
                    f"Error processing item at index {index}: {e}", exc_info=True
                )
                table.addRow([str(index + 1), f"Error: {e}"], None)

        table.adjustColumnWidths()
        table.update()
        self._schedule_visible_quality_resolution()
        logger.info(f"Table populated with {table.rowCount()} items.")
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
                if tidal_track is not None:
                    item_data["tidal_track_id"] = str(tidal_track.id)
                elif status in {
                    "not_linked",
                    "not_found",
                    "error",
                    "linking",
                    "candidates_only",
                    "manual_review_needed",
                }:
                    item_data["tidal_track_id"] = None
                item_data["score"] = score
                if error_message:
                    item_data["error_message"] = error_message
                title_item.setData(Qt.ItemDataRole.UserRole, item_data)

        if tidal_track:
            tidal_track_id = str(tidal_track.id)
            cached_quality = self._get_cached_quality_threadsafe(
                tidal_track_id
            )
            if cached_quality:
                self._set_row_quality_text(row_index, cached_quality)
            else:
                self._set_row_quality_text(row_index, QUALITY_PLACEHOLDER_TEXT)
                self._enqueue_track_quality_resolution(tidal_track_id)
        elif status in {
            "not_linked",
            "not_found",
            "error",
            "linking",
            "candidates_only",
            "manual_review_needed",
        }:
            self._set_row_quality_text(row_index, "-")

        indicator_item = table.item(row_index, 0)
        if not indicator_item:
            indicator_item = QTableWidgetItem()
            table.setItem(row_index, 0, indicator_item)

        # Extract ID safely to a local variable to avoid scope/undefined issues
        linked_id = tidal_track.id if tidal_track else None

        if candidates and len(candidates) > 0:
            indicator_data = {
                "has_candidates": True,
                "expanded": False,
                "candidate_count": len(candidates),
                "candidates_list": candidates,
                "linked_tidal_track_id": linked_id,
            }
            indicator_item.setData(QtCore.Qt.ItemDataRole.UserRole, indicator_data)
            logger.debug(f"[TableHandler] Set indicator data for row {row_index} with {len(candidates)} candidates.")
        else:
            indicator_data = {
                "has_candidates": False,
                "expanded": False,
                "candidate_count": 0,
                "candidates_list": [],
                "linked_tidal_track_id": linked_id,
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
        
        # s_playlist_obj is the item_data dict from the tree item
        playlist_context = self.main_view.s_playlist_obj
        playlist_id = None

        if isinstance(playlist_context, dict):
            # Check type to determine how to extract ID
            p_type = playlist_context.get("type")
            p_data = playlist_context.get("data")
            
            if p_type == "spotify" and isinstance(p_data, dict):
                playlist_id = p_data.get("id")
            elif p_type == "tidal" and isinstance(p_data, Playlist):
                playlist_id = p_data.uuid
            elif isinstance(p_data, dict): # Fallback for generic dict data
                playlist_id = p_data.get("id")
                
        elif isinstance(playlist_context, Playlist):
             # Direct Playlist object (legacy or direct assignment)
            playlist_id = playlist_context.uuid

        self._populate_table_generic(
            self.main_view.s_array, self.main_view.s_type or Type.Null, playlist_id
        )
        self._schedule_visible_quality_resolution()

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

    def _find_row_for_spotify_id(self, spotify_id_to_find: Optional[str]) -> Optional[int]:
        """
        Finds the row index for a given Spotify Track ID.
        This is robust against table sorting/filtering as it scans current rows.
        """
        if not self.table_widget or not spotify_id_to_find:
            return None
            
        for row in range(self.table_widget.rowCount()):
            title_item = self.table_widget.item(row, 1)
            if not title_item:
                continue
                
            item_data = title_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict) and item_data.get("type") == "spotify_track":
                data = item_data.get("data", {})
                if data.get("id") == spotify_id_to_find:
                    return row
                    
        return None

    @pyqtSlot(str)
    def setup_progress_bar_for_download(self, track_id: str):
        # Added check for column_indices to prevent AttributeError
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

            self._set_progress_bar_widget(row, status_col, 0)
            logger.debug(f"Setup progress bar for track {track_id} at row {row}")

    @pyqtSlot(str, int)
    def update_track_progress(self, track_id: str, percentage: int):
        # Added check for column_indices to prevent AttributeError
        if not self.table_widget or "Status" not in self.column_indices:
            return
        row = self._find_row_for_track_id(track_id)
        if row is None:
            return
        status_col = self.column_indices["Status"]
        widget = self._get_progress_bar_widget(row, status_col)
        if widget is None:
            self._set_progress_bar_widget(row, status_col, int(percentage))
            widget = self._get_progress_bar_widget(row, status_col)

        if widget is not None:
            widget.setValue(int(percentage))
            widget.setFormat(f"{percentage}%")

    def mark_track_completed(
        self,
        track_id: str,
        ok: bool,
        error_msg: str = "",
        quality_text: Optional[str] = None,
    ):
        # Added check for column_indices to prevent AttributeError
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

        if ok:
            normalized_quality = self._normalize_completed_quality_text(quality_text)
            if not normalized_quality:
                normalized_quality = (
                    self._resolve_completed_quality_for_row(
                        row,
                        cast(
                            Optional[Union[Playlist, Album, Dict[str, Any]]],
                            getattr(self.main_view, "s_playlist_obj", None),
                        ),
                    )
                    or ""
                )
            item.setText(self._format_completed_status_text(normalized_quality))
            item.setToolTip(normalized_quality)
        else:
            item.setText("Failed")
            item.setToolTip(error_msg)

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
            self._schedule_visible_quality_resolution()
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
        current_playlist_context = cast(
            Optional[Union[Playlist, Album, Dict[str, Any]]],
            getattr(self.main_view, "s_playlist_obj", None),
        )
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
            track_obj: Optional[Track] = None
            title_item = self.table_widget.item(row, 1)
            if title_item:
                data = title_item.data(Qt.ItemDataRole.UserRole)
                track_obj = self._extract_tidal_track_from_item_data(data)
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
                if track_obj:
                    completed_quality = self.get_completed_quality_for_track(
                        track_obj,
                        current_playlist_context,
                    )
                    if completed_quality:
                        status_item.setText(self._format_completed_status_text(completed_quality))
                        status_item.setToolTip(completed_quality)
                        continue

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
                self._set_progress_bar_widget(row, status_col, int(progress))
            elif status == "completed":
                completed_quality = self._normalize_completed_quality_text(
                    cast(Optional[str], state.get("completed_quality"))
                    or cast(Optional[str], state.get("requested_quality"))
                )
                if not completed_quality and track_obj:
                    completed_quality = (
                        self.get_completed_quality_for_track(
                            track_obj,
                            cast(
                                Optional[Union[Playlist, Album, Dict[str, Any]]],
                                state.get("playlist_context", current_playlist_context),
                            ),
                        )
                        or ""
                    )
                status_item.setText(self._format_completed_status_text(completed_quality))
                status_item.setToolTip(completed_quality)
            elif status in ("failed", "cancelled"):
                status_item.setText("Failed" if status == "failed" else "Cancelled")
                if error:
                    status_item.setToolTip(error)
