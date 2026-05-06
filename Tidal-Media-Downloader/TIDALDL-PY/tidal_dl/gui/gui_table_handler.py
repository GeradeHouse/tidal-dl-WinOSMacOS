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
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import List, Dict, Optional, Any, Union, cast, TYPE_CHECKING, Set

from PyQt6 import QtCore, QtWidgets, QtGui, sip
from PyQt6.QtCore import QTimer, QObject, pyqtSlot, Qt, QPoint, pyqtSignal, QEvent
from PyQt6.QtWidgets import QTableWidgetItem, QProgressBar, QMenu

from tidal_dl.gui.gui_table import SplitterTable
from tidal_dl.tidal import Type, Track, Playlist, Album, AudioQuality, TIDAL_API, SETTINGS
from tidal_dl.printf import Printf
from tidal_dl.gui.gui_utils import format_duration_ms
from tidal_dl.gui.gui_custom_dialog import CustomQMessageBox
from tidal_dl.persistence import LinkPersistenceManager
from tidal_dl.format import getAudioTypeFolder, getTrackPath
from tidal_dl.paths import get_user_download_path
from tidal_dl.model import Artist, StreamUrl
from tidal_dl.metadata.enrichment import MISSING_METADATA_TEXT, get_track_display_metadata

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
REQUESTED_METADATA_COLUMNS = [
    ("Release Year", "release_year"),
    ("BPM", "bpm"),
    ("Key", "key"),
    ("Genre", "genre"),
    ("Label", "label"),
]

METADATA_REFRESH_IDLE_MS = 450
METADATA_MAX_ROWS_PER_TICK = 12
METADATA_MAX_API_REQUESTS_PER_TICK = 3
METADATA_MAX_INFLIGHT_REQUESTS = 1
METADATA_OPENAPI_BATCH_SIZE_FALLBACK = 20
METADATA_CACHE_FLUSH_IDLE_MS = 12000
METADATA_CACHE_FLUSH_SLOW_WARNING_MS = 3000.0
SPOTIFY_ROW_DRAG_MIME = "application/x-tidal-dl-spotify-playlist-rows"
TABLE_TRACKS_DRAG_MIME = "application/x-tidal-dl-table-track-rows"


class TableHandler(QObject):
    """
    Manages the results table (SplitterTable), including populating it
    with data and handling context menus specific to the table content.
    """

    trackMetadataResolved = pyqtSignal(str, str, object)
    completedStatusResolved = pyqtSignal(str, str, str)

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
        self._spotify_drag_start_pos: Optional[QPoint] = None
        self._spotify_drag_start_row: Optional[int] = None
        self._spotify_drop_indicator_row: Optional[int] = None
        self._is_shutting_down = False
        self._table_viewport_ref: Optional[QtWidgets.QWidget] = None
        
        # Initialize column_indices to prevent AttributeError if accessed before population
        self.column_indices: Dict[str, int] = {}

        # Lazy track metadata resolution state. Keep this deliberately low-priority:
        # quality/metadata enrichment must never compete with scrolling or table painting.
        self._metadata_executor = ThreadPoolExecutor(max_workers=1)
        self._metadata_cache_flush_executor = ThreadPoolExecutor(max_workers=1)
        self._metadata_state_lock = threading.Lock()
        self._quality_cache_access_lock = threading.Lock()
        self._track_metadata_cache_access_lock = threading.Lock()
        self._metadata_inflight_track_ids: Set[str] = set()
        self._metadata_pending_track_ids: Set[str] = set()
        self._metadata_attempted_track_ids: Set[str] = set()
        self._metadata_api_budget_this_tick: int = 0
        self._metadata_cache_deferred_save_active: bool = False
        self._metadata_cache_flush_inflight: bool = False
        self._track_id_to_rows: Dict[str, Set[int]] = {}
        self._completed_status_executor = ThreadPoolExecutor(max_workers=2)
        self._completed_status_lock = threading.Lock()
        self._completed_status_inflight_keys: Set[str] = set()
        self._completed_status_cache: Dict[str, Optional[str]] = {}
        self._active_table_playlist_context_key: str = ""
        self._executors_shutdown: bool = False
        self._visible_metadata_refresh_timer = QTimer(self)
        self._visible_metadata_refresh_timer.setSingleShot(True)
        self._visible_metadata_refresh_timer.setInterval(METADATA_REFRESH_IDLE_MS)
        self._visible_metadata_refresh_timer.timeout.connect(
            self._resolve_visible_rows_metadata
        )

        self._metadata_cache_flush_timer = QTimer(self)
        self._metadata_cache_flush_timer.setSingleShot(True)
        self._metadata_cache_flush_timer.setInterval(METADATA_CACHE_FLUSH_IDLE_MS)
        self._metadata_cache_flush_timer.timeout.connect(
            self._flush_deferred_metadata_cache_save
        )

        self.trackMetadataResolved.connect(self._on_track_metadata_resolved)
        self.completedStatusResolved.connect(self._on_completed_status_resolved)

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

            def _connect_once(signal, slot):
                try:
                    signal.disconnect(slot)
                except TypeError:
                    pass
                signal.connect(slot)

            _connect_once(self.table_widget.noMatchSelectedInSubRow, handler.on_no_match_selected)
            _connect_once(self.table_widget.candidateSelectedInSubRow, handler.onManualLinkSelected)
            _connect_once(
                self.table_widget.candidateAutoReviewAcceptedInSubRow,
                handler.onAutoReviewAccepted,
            )
            _connect_once(
                self.table_widget.candidateUnlinkRequestedInSubRow,
                handler.onCandidateUnlinkRequested,
            )
            _connect_once(
                self.table_widget.candidatePreviewRequestedInSubRow,
                self._on_candidate_preview_requested,
            )

    def _on_candidate_preview_requested(self, main_row_index: int, tidal_track: object) -> None:
        if not isinstance(tidal_track, Track):
            return

        player_logic = getattr(self.main_view, "player_logic", None)
        if player_logic is None:
            logger.warning(
                "Candidate preview requested, but player_logic is not available."
            )
            return

        try:
            artists = TIDAL_API.getArtistsName(
                cast(List[Artist], getattr(tidal_track, "artists", []) or [])
            )
        except Exception:
            artists = getattr(getattr(tidal_track, "artist", None), "name", "") or ""

        album_obj = getattr(tidal_track, "album", None)
        album_title = getattr(album_obj, "title", "") if album_obj else ""
        duration_seconds = getattr(tidal_track, "duration", 0) or 0

        player_logic.play_track(
            {
                "id": str(getattr(tidal_track, "id", "") or ""),
                "title": getattr(tidal_track, "title", "Unknown Title") or "Unknown Title",
                "artist": artists,
                "album": album_title,
                "duration_ms": int(duration_seconds) * 1000,
            }
        )


    def _connect_table_signals(self):
        if not self.table_widget:
            logger.error(
                "Attempted to connect table signals, but table_widget is None."
            )
            return
        self.table_widget.customContextMenuRequested.connect(
            self.handle_table_context_menu
        )
        self.table_widget.setAcceptDrops(True)
        self.table_widget.setDropIndicatorShown(True)
        self.table_widget.setDragDropOverwriteMode(False)
        self.table_widget.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.NoDragDrop)
        self.table_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        viewport = self.table_widget.viewport()
        if viewport:
            self._table_viewport_ref = viewport
            viewport.setAcceptDrops(True)
            viewport.installEventFilter(self)
 
        # The connection for noMatchSelectedInSubRow is now moved to set_linking_handler
        # for better robustness, ensuring the handler exists when the connection is made.

        header = self.table_widget.horizontalHeader()
        if header:
            header.sortIndicatorChanged.connect(self.on_sort_indicator_changed)

        vertical_scrollbar = self.table_widget.verticalScrollBar()
        if vertical_scrollbar:
            vertical_scrollbar.valueChanged.connect(
                self._schedule_visible_metadata_resolution
            )

    def _is_qobject_alive(self, obj: Optional[QObject]) -> bool:
        if obj is None:
            return False
        try:
            return not sip.isdeleted(obj)
        except RuntimeError:
            return False

    def _table_widget_alive(self) -> bool:
        return self._is_qobject_alive(self.table_widget)

    def _table_viewport_alive(self) -> bool:
        return self._is_qobject_alive(self._table_viewport_ref)

    def _detach_spotify_drag_event_filter(self) -> None:
        viewport = self._table_viewport_ref
        if self._is_qobject_alive(viewport):
            with suppress(RuntimeError, TypeError):
                viewport.removeEventFilter(self)
        self._table_viewport_ref = None
        self._spotify_drag_start_pos = None
        self._spotify_drag_start_row = None
        self._spotify_drop_indicator_row = None

    def prepare_for_shutdown(self) -> None:
        self._is_shutting_down = True
        self._detach_spotify_drag_event_filter()

    def _active_spotify_playlist_can_reorder(self) -> bool:
        if self._is_shutting_down or not self._table_widget_alive():
            return False

        playlist_obj = getattr(self.main_view, "s_playlist_obj", None)
        if not (isinstance(playlist_obj, dict) and playlist_obj.get("type") == "spotify" and isinstance(playlist_obj.get("data"), dict)):
            return False
        return bool(playlist_obj["data"].get("can_modify_items"))

    def _set_spotify_reorder_visual_state(self, active: bool) -> None:
        if not self._table_widget_alive():
            return

        self.table_widget.setProperty("spotifyReorderActive", bool(active))
        self.table_widget.style().unpolish(self.table_widget)
        self.table_widget.style().polish(self.table_widget)
        self.table_widget.update()

    def _get_row_from_event_position(self, event: QtCore.QEvent) -> Optional[int]:
        if self._is_shutting_down or not self._table_widget_alive():
            return None

        pos = event.position().toPoint() if hasattr(event, "position") else event.pos() if hasattr(event, "pos") else None
        if pos is None:
            return None

        index = self.table_widget.indexAt(pos)
        return index.row() if index.isValid() else self.table_widget.rowCount()

    def _selected_table_rows_for_drag(self, fallback_row: int) -> List[int]:
        if self._is_shutting_down or not self._table_widget_alive():
            return []

        selected_rows = list(self.table_widget.getSelectedRows())
        if fallback_row not in selected_rows:
            selected_rows = [fallback_row]

        payloads = self.get_table_track_payloads_for_rows(selected_rows)
        payload_row_set = {
            int(payload.get("row_index"))
            for payload in payloads
            if isinstance(payload.get("row_index"), int)
        }
        return [row for row in selected_rows if row in payload_row_set]

    def _selected_spotify_rows_for_drag(self, fallback_row: int) -> List[int]:
        if self._is_shutting_down or not self._table_widget_alive():
            return []

        selected_rows = list(self.table_widget.getSelectedRows())
        if fallback_row not in selected_rows:
            selected_rows = [fallback_row]

        payloads = self.get_spotify_track_payloads_for_rows(selected_rows)
        payload_row_set = {
            int(payload.get("row_index"))
            for payload in payloads
            if isinstance(payload.get("row_index"), int)
        }
        return [row for row in selected_rows if row in payload_row_set]

    def _build_table_track_drag_pixmap(self, rows: List[int]) -> QtGui.QPixmap:
        count = len(rows)
        title = "Selected tracks"
        if count == 1 and self._table_widget_alive():
            title_item = self.table_widget.item(rows[0], 1)
            if title_item and title_item.text():
                title = title_item.text()

        subtitle = f"{count} track{'s' if count != 1 else ''} ready for Spotify playlist drop"
        pixmap = QtGui.QPixmap(360, 58)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(QtGui.QColor(18, 18, 22, 235))
        painter.setPen(QtGui.QColor(0, 200, 200, 130))
        painter.drawRoundedRect(0, 0, 359, 57, 12, 12)

        painter.setPen(QtGui.QColor("#ffffff"))
        title_font = QtGui.QFont("Segoe UI Variable", 10, QtGui.QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.drawText(QtCore.QRect(16, 9, 328, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

        painter.setPen(QtGui.QColor("#b9c7c7"))
        subtitle_font = QtGui.QFont("Segoe UI Variable", 8)
        painter.setFont(subtitle_font)
        painter.drawText(QtCore.QRect(16, 31, 328, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, subtitle)
        painter.end()
        return pixmap

    def get_spotify_drop_insert_position(self, drop_row: int) -> int:
        if self._is_shutting_down or not self._table_widget_alive():
            return 0

        if drop_row >= self.table_widget.rowCount():
            active_playlist = getattr(self.main_view, "s_playlist_obj", {})
            if isinstance(active_playlist, dict):
                data = active_playlist.get("data", {})
                if isinstance(data, dict):
                    return int(data.get("tracks_total") or self.table_widget.rowCount())
            return self.table_widget.rowCount()
        payloads = self.get_spotify_track_payloads_for_rows([drop_row])
        if payloads and isinstance(payloads[0].get("playlist_position"), int):
            return int(payloads[0]["playlist_position"])
        return max(0, int(drop_row))

    def _start_table_row_drag(self, rows: List[int]) -> bool:
        if self._is_shutting_down or not self._table_widget_alive() or not rows:
            return False

        payloads = self.get_table_track_payloads_for_rows(rows)
        if not payloads:
            return False

        mime = QtCore.QMimeData()
        mime.setData(
            TABLE_TRACKS_DRAG_MIME,
            QtCore.QByteArray(json.dumps({"rows": rows}).encode("utf-8")),
        )

        spotify_payloads = self.get_spotify_track_payloads_for_rows(rows)
        if self._active_spotify_playlist_can_reorder() and len(spotify_payloads) == len(rows):
            mime.setData(
                SPOTIFY_ROW_DRAG_MIME,
                QtCore.QByteArray(",".join(str(row) for row in rows).encode("utf-8")),
            )

        drag = QtGui.QDrag(self.table_widget)
        drag.setMimeData(mime)
        drag.setPixmap(self._build_table_track_drag_pixmap(rows))
        drag.setHotSpot(QPoint(18, 18))
        result = drag.exec(
            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
            Qt.DropAction.CopyAction,
        )
        return result in (Qt.DropAction.CopyAction, Qt.DropAction.MoveAction)

    def eventFilter(self, watched: QObject, event: Optional[QEvent]) -> bool:
        if self._is_shutting_down or event is None or not self._table_widget_alive():
            return False

        viewport = self._table_viewport_ref
        if not self._is_qobject_alive(viewport) or watched is not viewport:
            return super().eventFilter(watched, event)

        event_type = event.type()

        if event_type == QEvent.Type.MouseButtonPress and isinstance(event, QtGui.QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                row = self._get_row_from_event_position(event)
                if (
                    row is not None
                    and row < self.table_widget.rowCount()
                    and self._selected_table_rows_for_drag(row)
                ):
                    self._spotify_drag_start_pos = event.pos()
                    self._spotify_drag_start_row = row
            return super().eventFilter(watched, event)

        if event_type == QEvent.Type.MouseMove and isinstance(event, QtGui.QMouseEvent):
            if (
                self._spotify_drag_start_pos is not None
                and self._spotify_drag_start_row is not None
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                if (event.pos() - self._spotify_drag_start_pos).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                    rows = self._selected_table_rows_for_drag(self._spotify_drag_start_row)
                    self._spotify_drag_start_pos = None
                    self._spotify_drag_start_row = None
                    if rows:
                        self._start_table_row_drag(rows)
                        return True
            return super().eventFilter(watched, event)

        if event_type in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if isinstance(event, (QtGui.QDragEnterEvent, QtGui.QDragMoveEvent)) and event.mimeData().hasFormat(SPOTIFY_ROW_DRAG_MIME):
                if self._active_spotify_playlist_can_reorder():
                    self._spotify_drop_indicator_row = self._get_row_from_event_position(event)
                    event.setDropAction(Qt.DropAction.MoveAction)
                    event.accept()
                    return True
                event.ignore()
                return True

        if event_type == QEvent.Type.DragLeave:
            self._spotify_drop_indicator_row = None
            return super().eventFilter(watched, event)

        if event_type == QEvent.Type.Drop and isinstance(event, QtGui.QDropEvent):
            if event.mimeData().hasFormat(SPOTIFY_ROW_DRAG_MIME) and self._active_spotify_playlist_can_reorder():
                raw_rows = bytes(event.mimeData().data(SPOTIFY_ROW_DRAG_MIME)).decode("utf-8")
                rows = [int(part) for part in raw_rows.split(",") if part.strip().isdigit()]
                drop_row = self._get_row_from_event_position(event)
                insert_before = self.get_spotify_drop_insert_position(drop_row if drop_row is not None else self.table_widget.rowCount())
                spotify_handler = getattr(self.main_view, "spotify_gui_handler", None)
                if spotify_handler:
                    spotify_handler.moveSelectedRowsToPlaylistPosition(rows, insert_before)
                self._spotify_drop_indicator_row = None
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def clear_table(self):
        if not self.table_widget:
            return
        self._visible_metadata_refresh_timer.stop()
        self._track_id_to_rows.clear()
        if self._metadata_cache_deferred_save_active:
            self._metadata_cache_flush_timer.start()
        with self._metadata_state_lock:
            self._metadata_inflight_track_ids.clear()
            self._metadata_pending_track_ids.clear()
            self._metadata_attempted_track_ids.clear()
            self._metadata_api_budget_this_tick = 0
        with self._completed_status_lock:
            self._completed_status_inflight_keys.clear()
            self._completed_status_cache.clear()
        self._active_table_playlist_context_key = ""
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

    def _set_row_text_by_header(
        self,
        row: int,
        header: str,
        text: str,
        tooltip: Optional[str] = None,
    ) -> None:
        if not self.table_widget:
            return

        col = self.column_indices.get(header)
        if col is None:
            return

        item = self.table_widget.item(row, col)
        if not item:
            item = QTableWidgetItem()
            self.table_widget.setItem(row, col, item)

        final_text = str(text or MISSING_METADATA_TEXT)
        item.setText(final_text)
        item.setToolTip(str(tooltip or final_text))

    def _format_tidal_artists_for_display(self, track: Track) -> str:
        artists_raw = getattr(track, "artists", None)
        artists_list = artists_raw if isinstance(artists_raw, list) else []
        if not artists_list:
            single_artist = getattr(track, "artist", None)
            if single_artist is not None:
                artists_list = [single_artist]

        try:
            artists_text = TIDAL_API.getArtistsName(cast(List[Any], artists_list))
        except Exception:
            artists_text = getattr(getattr(track, "artist", None), "name", "") or ""

        return artists_text or MISSING_METADATA_TEXT

    def _set_row_tidal_identity_text(self, row: int, track: Optional[Track]) -> None:
        if not self.table_widget or not isinstance(track, Track):
            return

        album_obj = getattr(track, "album", None)
        album_title = getattr(album_obj, "title", None) or MISSING_METADATA_TEXT
        duration_seconds = getattr(track, "duration", 0) or 0
        duration_text = (
            Printf.formatDuration(duration_seconds)
            if duration_seconds
            else MISSING_METADATA_TEXT
        )

        self._set_row_text_by_header(
            row,
            "Title",
            getattr(track, "title", None) or MISSING_METADATA_TEXT,
        )
        self._set_row_text_by_header(
            row,
            "Artists",
            self._format_tidal_artists_for_display(track),
        )
        self._set_row_text_by_header(row, "Album", album_title)
        self._set_row_text_by_header(row, "Length", duration_text)

    @staticmethod
    def _is_missing_metadata_value(value: Optional[str]) -> bool:
        cleaned = str(value or "").strip()
        return not cleaned or cleaned == MISSING_METADATA_TEXT

    @staticmethod
    def _compact_metadata_display_text(header: str, value: str) -> str:
        text = str(value or "").strip()
        if header not in {"Genre", "Label"}:
            return text

        max_length = 34 if header == "Genre" else 30
        if len(text) <= max_length:
            return text

        if header == "Genre":
            parts = [part.strip() for part in text.split(",") if part.strip()]
            if len(parts) > 2:
                compact = ", ".join(parts[:2])
                if len(compact) <= max_length:
                    return f"{compact}, …"

        return f"{text[: max_length - 1].rstrip()}…"

    def _get_requested_metadata_values(self, track: Optional[Track]) -> Dict[str, str]:
        enriched_track = self._apply_cached_track_metadata(track)
        use_camelot_key = bool(getattr(SETTINGS, "useCamelotKeyNotation", True))
        metadata_values = get_track_display_metadata(
            enriched_track,
            use_camelot_key=use_camelot_key,
        )

        if isinstance(enriched_track, Track):
            track_id = str(getattr(enriched_track, "id", "") or "").strip()
            if track_id:
                cached_metadata = self._get_cached_track_metadata_threadsafe(track_id)
                for _header, key in REQUESTED_METADATA_COLUMNS:
                    cached_value = cached_metadata.get(key, "").strip()
                    if cached_value and self._is_missing_metadata_value(
                        metadata_values.get(key)
                    ):
                        metadata_values[key] = cached_value

        return metadata_values

    def _merge_metadata_with_existing_row_values(
        self,
        row: int,
        metadata_values: Dict[str, str],
    ) -> Dict[str, str]:
        if not self.table_widget:
            return metadata_values

        merged = dict(metadata_values)
        for header, key in REQUESTED_METADATA_COLUMNS:
            if not self._is_missing_metadata_value(merged.get(key)):
                continue

            col = self.column_indices.get(header)
            if col is None:
                continue

            existing_item = self.table_widget.item(row, col)
            existing_text = existing_item.text().strip() if existing_item else ""
            if not self._is_missing_metadata_value(existing_text):
                merged[key] = existing_text

        return merged

    def _set_row_requested_metadata_text(
        self,
        row: int,
        track: Optional[Track],
    ) -> None:
        if not self.table_widget:
            return

        metadata_values = self._get_requested_metadata_values(track)
        if isinstance(track, Track):
            metadata_values = self._merge_metadata_with_existing_row_values(
                row,
                metadata_values,
            )
        self._replace_row_tidal_track_metadata(row, track)
        for header, key in REQUESTED_METADATA_COLUMNS:
            col = self.column_indices.get(header)
            if col is None:
                continue

            full_text = metadata_values.get(key, MISSING_METADATA_TEXT) or MISSING_METADATA_TEXT
            display_text = self._compact_metadata_display_text(header, full_text)
            item = self.table_widget.item(row, col)
            if not item:
                item = QTableWidgetItem()
                self.table_widget.setItem(row, col, item)
            item.setText(display_text)
            item.setToolTip(full_text)
            item.setData(Qt.ItemDataRole.ToolTipRole, full_text)
            item.setData(Qt.ItemDataRole.StatusTipRole, full_text)

        self.table_widget._update_row_appearance_for_row(row)

    def _apply_metadata_tooltips_for_row(
        self,
        row: int,
        metadata_values: Dict[str, str],
    ) -> None:
        if not self.table_widget:
            return

        for header, key in REQUESTED_METADATA_COLUMNS:
            col = self.column_indices.get(header)
            if col is None:
                continue

            item = self.table_widget.item(row, col)
            if not item:
                continue

            full_text = metadata_values.get(key, MISSING_METADATA_TEXT) or MISSING_METADATA_TEXT
            display_text = self._compact_metadata_display_text(header, full_text)
            item.setText(display_text)
            item.setToolTip(full_text)
            item.setData(Qt.ItemDataRole.ToolTipRole, full_text)
            item.setData(Qt.ItemDataRole.StatusTipRole, full_text)

    def _replace_row_tidal_track_metadata(
        self,
        row: int,
        track: Optional[Track],
    ) -> None:
        if not self.table_widget or not isinstance(track, Track):
            return

        title_item = self.table_widget.item(row, 1)
        if not title_item:
            return

        item_data = title_item.data(Qt.ItemDataRole.UserRole)
        if isinstance(item_data, Track):
            if str(getattr(item_data, "id", "")) == str(getattr(track, "id", "")):
                title_item.setData(Qt.ItemDataRole.UserRole, track)
        elif isinstance(item_data, dict):
            existing_track = item_data.get("tidal_track")
            existing_track_id = item_data.get("tidal_track_id")
            if isinstance(existing_track, Track):
                existing_track_id = getattr(existing_track, "id", existing_track_id)
            if existing_track_id and str(existing_track_id) == str(getattr(track, "id", "")):
                item_data["tidal_track"] = track
                item_data["tidal_track_id"] = str(track.id)
                title_item.setData(Qt.ItemDataRole.UserRole, item_data)

    def _schedule_visible_metadata_resolution(self, *_args: Any) -> None:
        if not self.table_widget or "Quality" not in self.column_indices:
            return
        self._visible_metadata_refresh_timer.start()

    def _get_cached_quality_threadsafe(self, track_id: str) -> Optional[str]:
        with self._quality_cache_access_lock:
            return self.persistence_manager.get_cached_track_quality(track_id)

    def _set_cached_quality_threadsafe(self, track_id: str, quality_text: str) -> None:
        with self._quality_cache_access_lock:
            self._start_deferred_metadata_cache_save()
            self.persistence_manager.set_cached_track_quality(track_id, quality_text)

    def _get_cached_track_metadata_threadsafe(self, track_id: str) -> Dict[str, str]:
        with self._track_metadata_cache_access_lock:
            return self.persistence_manager.get_cached_track_metadata(track_id)

    def _set_cached_track_metadata_threadsafe(
        self,
        track_id: str,
        metadata: Dict[str, str],
    ) -> None:
        with self._track_metadata_cache_access_lock:
            self._start_deferred_metadata_cache_save()
            self.persistence_manager.set_cached_track_metadata(track_id, metadata)

    def _start_deferred_metadata_cache_save(self) -> None:
        if self._metadata_cache_deferred_save_active:
            self._metadata_cache_flush_timer.start()
            return

        self.persistence_manager.begin_deferred_save("lazy_table_metadata_cache")
        self._metadata_cache_deferred_save_active = True
        self._metadata_cache_flush_timer.start()

    def _flush_deferred_metadata_cache_save(self, blocking: bool = False) -> None:
        if not self._metadata_cache_deferred_save_active:
            return
        if self._metadata_cache_flush_inflight:
            if not blocking:
                self._metadata_cache_flush_timer.start()
            return

        self._metadata_cache_deferred_save_active = False
        if blocking:
            self._flush_deferred_metadata_cache_save_in_background()
            return

        self._metadata_cache_flush_inflight = True
        try:
            self._metadata_cache_flush_executor.submit(
                self._flush_deferred_metadata_cache_save_in_background
            )
        except RuntimeError:
            self._metadata_cache_flush_inflight = False
            self._metadata_cache_deferred_save_active = True
            self._metadata_cache_flush_timer.start()

    def _flush_deferred_metadata_cache_save_in_background(self) -> None:
        start = time.perf_counter()
        try:
            self.persistence_manager.end_deferred_save("lazy_table_metadata_cache")
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._metadata_cache_flush_inflight = False
            if elapsed_ms >= METADATA_CACHE_FLUSH_SLOW_WARNING_MS:
                logger.warning(
                    "Lazy metadata cache background flush was slow | elapsed_ms=%.1f",
                    elapsed_ms,
                )

    def _apply_cached_track_metadata(self, track: Optional[Track]) -> Optional[Track]:
        if not isinstance(track, Track):
            return track

        track_id = str(getattr(track, "id", "") or "").strip()
        if not track_id:
            return track

        cached_metadata = self._get_cached_track_metadata_threadsafe(track_id)
        cached_genre = cached_metadata.get("genre", "").strip()
        if cached_genre:
            logger.debug(
                "Applied cached track genre metadata track_id=%s genre=%s",
                track_id,
                cached_genre,
            )
            if not getattr(track, "genre", None):
                setattr(track, "genre", cached_genre)
            if not getattr(track, "genres", None):
                setattr(
                    track,
                    "genres",
                    [part.strip() for part in cached_genre.split(",") if part.strip()],
                )

        return track

    def _row_needs_genre_resolution(self, row: int) -> bool:
        if not self.table_widget:
            return False

        genre_col = self.column_indices.get("Genre")
        if genre_col is None:
            return False

        genre_item = self.table_widget.item(row, genre_col)
        genre_text = genre_item.text().strip() if genre_item else ""
        return not genre_text or genre_text == MISSING_METADATA_TEXT

    def _get_openapi_quality_batch_size(self) -> int:
        raw_batch_size = getattr(
            SETTINGS,
            "openApiQualityBatchSize",
            METADATA_OPENAPI_BATCH_SIZE_FALLBACK,
        )
        try:
            batch_size = int(raw_batch_size)
        except (TypeError, ValueError):
            batch_size = METADATA_OPENAPI_BATCH_SIZE_FALLBACK
        return max(1, min(20, batch_size))

    def _enqueue_track_metadata_resolution(self, track_id: str) -> None:
        track_id = str(track_id or "").strip()
        if not track_id:
            return

        if bool(getattr(SETTINGS, "enableOpenApiBatchQualityFetching", True)):
            with self._metadata_state_lock:
                if self._executors_shutdown:
                    return
                if track_id in self._metadata_attempted_track_ids:
                    return
                if track_id in self._metadata_inflight_track_ids:
                    return
                if track_id in self._metadata_pending_track_ids:
                    return
                self._metadata_pending_track_ids.add(track_id)
            return

        with self._metadata_state_lock:
            if self._executors_shutdown:
                return
            if track_id in self._metadata_attempted_track_ids:
                return
            if track_id in self._metadata_inflight_track_ids:
                return
            if len(self._metadata_inflight_track_ids) >= METADATA_MAX_INFLIGHT_REQUESTS:
                return
            if self._metadata_api_budget_this_tick >= METADATA_MAX_API_REQUESTS_PER_TICK:
                return
            self._metadata_attempted_track_ids.add(track_id)
            self._metadata_inflight_track_ids.add(track_id)
            self._metadata_api_budget_this_tick += 1

        try:
            self._metadata_executor.submit(self._resolve_track_metadata_in_background, track_id)
        except RuntimeError:
            with self._metadata_state_lock:
                self._metadata_inflight_track_ids.discard(track_id)

    def _submit_pending_metadata_batch(self) -> None:
        if not bool(getattr(SETTINGS, "enableOpenApiBatchQualityFetching", True)):
            return

        with self._metadata_state_lock:
            if self._executors_shutdown:
                return
            if len(self._metadata_inflight_track_ids) >= METADATA_MAX_INFLIGHT_REQUESTS:
                return

            batch_size = self._get_openapi_quality_batch_size()
            available_track_ids = [
                track_id
                for track_id in sorted(self._metadata_pending_track_ids)
                if track_id not in self._metadata_attempted_track_ids
                and track_id not in self._metadata_inflight_track_ids
            ][:batch_size]
            if not available_track_ids:
                return

            for track_id in available_track_ids:
                self._metadata_pending_track_ids.discard(track_id)
                self._metadata_attempted_track_ids.add(track_id)
                self._metadata_inflight_track_ids.add(track_id)

        try:
            self._metadata_executor.submit(
                self._resolve_track_metadata_batch_in_background,
                available_track_ids,
            )
        except RuntimeError:
            with self._metadata_state_lock:
                for track_id in available_track_ids:
                    self._metadata_inflight_track_ids.discard(track_id)

    def _quality_text_from_openapi_quality_result(
        self,
        quality_result: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not isinstance(quality_result, dict):
            return None

        def string_values(value: Any) -> List[str]:
            if value is None:
                return []
            if isinstance(value, str):
                cleaned = value.strip()
                return [cleaned] if cleaned else []
            if isinstance(value, (list, tuple, set)):
                values: List[str] = []
                for item in value:
                    values.extend(string_values(item))
                return list(dict.fromkeys(values))
            cleaned = str(value).strip()
            return [cleaned] if cleaned else []

        def numeric_value(value: Any) -> Optional[float]:
            if value in (None, "", [], {}):
                return None
            try:
                return float(str(value).strip())
            except (TypeError, ValueError):
                return None

        media_metadata = quality_result.get("mediaMetadata")
        tags: List[str] = []
        if isinstance(media_metadata, dict):
            tags.extend(string_values(media_metadata.get("tags")))
        tags.extend(string_values(quality_result.get("mediaTags")))
        normalized_tags = {tag.upper() for tag in tags}

        if "HIRES_LOSSLESS" in normalized_tags or "HI_RES_LOSSLESS" in normalized_tags:
            return Printf.map_quality_enum(AudioQuality.HI_RES_LOSSLESS)
        if "LOSSLESS" in normalized_tags:
            return Printf.map_quality_enum(AudioQuality.LOSSLESS)

        sample_rate = numeric_value(quality_result.get("sampleRate"))
        bit_depth = numeric_value(quality_result.get("bitDepth"))
        if (bit_depth is not None and bit_depth > 16) or (
            sample_rate is not None and sample_rate > 48000
        ):
            return Printf.map_quality_enum(AudioQuality.HI_RES_LOSSLESS)

        codec_values = {codec.upper() for codec in string_values(quality_result.get("codec"))}
        if codec_values.intersection({"FLAC", "ALAC"}):
            return Printf.map_quality_enum(AudioQuality.LOSSLESS)

        raw_quality = str(quality_result.get("audioQuality") or "").strip().upper()
        if raw_quality in {"HI_RES_LOSSLESS", "HI_RES", "HIRES", "MAX", "MASTER"}:
            return Printf.map_quality_enum(AudioQuality.HI_RES_LOSSLESS)
        if raw_quality in {"LOSSLESS", "CD", "FLAC"}:
            return Printf.map_quality_enum(AudioQuality.LOSSLESS)
        if raw_quality in {"HIGH"}:
            return Printf.map_quality_enum(AudioQuality.HIGH)
        if raw_quality in {"LOW"}:
            return Printf.map_quality_enum(AudioQuality.LOW)
        return None

    def _resolve_track_metadata_batch_in_background(self, track_ids: List[str]) -> None:
        started_at = time.perf_counter()
        quality_results: Dict[str, Dict[str, Any]] = {}
        try:
            quality_results = TIDAL_API.getTrackQualityBatchOpenApi(track_ids)
        except Exception as ex:
            logger.debug(
                "OpenAPI batch quality lookup failed for ids=%s: %s",
                track_ids,
                ex,
                exc_info=True,
            )

        resolved_count = 0
        try:
            for track_id in track_ids:
                quality_text = self._quality_text_from_openapi_quality_result(
                    quality_results.get(str(track_id))
                )
                if quality_text:
                    resolved_count += 1
                self.trackMetadataResolved.emit(str(track_id), quality_text or "-", None)
        finally:
            with self._metadata_state_lock:
                for track_id in track_ids:
                    self._metadata_inflight_track_ids.discard(str(track_id))

            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            log_fn = logger.warning if elapsed_ms >= 1000.0 else logger.info
            log_fn(
                "OpenAPI batch quality table resolution finished | requested=%d resolved=%d elapsed_ms=%.1f",
                len(track_ids),
                resolved_count,
                elapsed_ms,
            )

    def _resolve_track_metadata_in_background(self, track_id: str) -> None:
        quality_text: Optional[str] = None
        track_obj: Optional[Track] = None
        try:
            fetched_track = TIDAL_API.getTrack(str(track_id))
            track_obj = fetched_track if isinstance(fetched_track, Track) else None
            if isinstance(track_obj, Track):
                quality_text = Printf.map_track_quality(track_obj)

            quality_text = (quality_text or "-").strip() or "-"

            self.trackMetadataResolved.emit(track_id, quality_text, track_obj)

        except Exception as ex:
            logger.debug(
                f"Failed to resolve track metadata for track {track_id}: {ex}",
                exc_info=True,
            )
            self.trackMetadataResolved.emit(track_id, "-", None)
        finally:
            with self._metadata_state_lock:
                self._metadata_inflight_track_ids.discard(track_id)

    def _resolve_visible_rows_metadata(self) -> None:
        if not self.table_widget or "Quality" not in self.column_indices:
            return

        table = self.table_widget
        if table.rowCount() <= 0:
            return

        with self._metadata_state_lock:
            self._metadata_api_budget_this_tick = 0

        for row in self._visible_row_indices(max_rows=METADATA_MAX_ROWS_PER_TICK):
            track_id = self._extract_tidal_track_id_from_row(row)
            if not track_id:
                continue

            quality_col = self.column_indices.get("Quality")
            if quality_col is None:
                continue

            quality_item = table.item(row, quality_col)
            current_text = quality_item.text().strip() if quality_item else ""

            if current_text and current_text not in ("-", QUALITY_PLACEHOLDER_TEXT):
                # Do not use the quality column as a reason to keep making API calls.
                # Metadata enrichment is best-effort and rate-limited below.
                continue

            cached_quality = self._get_cached_quality_threadsafe(track_id)
            if cached_quality:
                self._set_row_quality_text(row, cached_quality)
                continue

            loaded_track = self._extract_tidal_track_from_row(row)
            derived_quality = self._quality_text_from_track_object(loaded_track)
            if derived_quality:
                self._set_row_quality_text(row, derived_quality)
                self._set_cached_quality_threadsafe(track_id, derived_quality)
                continue

            self._set_row_quality_text(row, QUALITY_PLACEHOLDER_TEXT)
            self._enqueue_track_metadata_resolution(track_id)

        self._submit_pending_metadata_batch()

    @pyqtSlot(str, str, object)
    def _on_track_metadata_resolved(
        self,
        track_id: str,
        quality_text: str,
        resolved_track: Optional[Track] = None,
    ) -> None:
        if not self.table_widget or "Quality" not in self.column_indices:
            return

        if quality_text not in ("-", "Unknown", "Unknown Quality"):
            self._set_cached_quality_threadsafe(track_id, quality_text)

        rows = sorted(self._track_id_to_rows.get(str(track_id), set()))
        if not rows:
            rows = [
                row
                for row in self._visible_row_indices(max_rows=METADATA_MAX_ROWS_PER_TICK)
                if self._extract_tidal_track_id_from_row(row) == track_id
            ]

        for row in rows:
            if row < 0 or row >= self.table_widget.rowCount():
                continue
            if self._extract_tidal_track_id_from_row(row) != track_id:
                continue

            self._set_row_quality_text(row, quality_text)
            if isinstance(resolved_track, Track):
                metadata_values = self._get_requested_metadata_values(resolved_track)
                metadata_values = self._merge_metadata_with_existing_row_values(
                    row,
                    metadata_values,
                )
                cacheable_metadata = {
                    key: value.strip()
                    for _header, key in REQUESTED_METADATA_COLUMNS
                    for value in [metadata_values.get(key, "").strip()]
                    if value and value != MISSING_METADATA_TEXT
                }
                if cacheable_metadata:
                    logger.debug(
                        "Caching resolved track metadata track_id=%s metadata=%s",
                        track_id,
                        cacheable_metadata,
                    )
                    self._set_cached_track_metadata_threadsafe(
                        track_id,
                        cacheable_metadata,
                    )
                self._set_row_requested_metadata_text(row, resolved_track)

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

    def get_spotify_track_payloads_for_rows(self, rows: List[int]) -> List[Dict[str, Any]]:
        if not self.table_widget:
            return []

        payloads: List[Dict[str, Any]] = []
        for row in rows:
            title_item = self.table_widget.item(row, 1)
            if not title_item:
                continue
            item_metadata = title_item.data(Qt.ItemDataRole.UserRole)
            if not (
                isinstance(item_metadata, dict)
                and item_metadata.get("type") == "spotify_track"
            ):
                continue
            track_data = item_metadata.get("data", {})
            if not isinstance(track_data, dict):
                continue
            track_id = track_data.get("id")
            uri = track_data.get("uri")
            if not uri and track_id:
                uri = f"spotify:track:{track_id}"
            if not uri:
                continue
            payload = dict(track_data)
            payload["uri"] = uri
            payload["row_index"] = row
            if not isinstance(payload.get("playlist_position"), int):
                payload["playlist_position"] = row
            payloads.append(payload)
        return payloads

    def get_table_track_payloads_for_rows(self, rows: List[int]) -> List[Dict[str, Any]]:
        if not self.table_widget:
            return []

        payloads: List[Dict[str, Any]] = []
        for row in rows:
            title_item = self.table_widget.item(row, 1)
            if not title_item:
                continue

            item_metadata = title_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(item_metadata, dict) and item_metadata.get("type") == "spotify_track":
                track_data = item_metadata.get("data", {})
                if not isinstance(track_data, dict):
                    continue
                track_id = track_data.get("id")
                uri = track_data.get("uri") or (f"spotify:track:{track_id}" if track_id else None)
                payload = dict(track_data)
                payload["uri"] = uri
                payload["row_index"] = row
                payload["source_type"] = "spotify"
                payload["display_title"] = str(track_data.get("name") or title_item.text() or "Spotify track")
                payloads.append(payload)
                continue

            tidal_track = item_metadata if isinstance(item_metadata, Track) else None
            if tidal_track is None and isinstance(item_metadata, dict):
                candidate_track = item_metadata.get("tidal_track")
                if isinstance(candidate_track, Track):
                    tidal_track = candidate_track

            if isinstance(tidal_track, Track):
                payloads.append(
                    {
                        "row_index": row,
                        "source_type": "tidal",
                        "display_title": str(getattr(tidal_track, "title", "") or title_item.text() or "TIDAL track"),
                        "tidal_track": tidal_track,
                    }
                )

        return payloads

    def _spotify_track_url_from_payload(self, payload: Dict[str, Any]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None

        external_urls = payload.get("external_urls")
        if isinstance(external_urls, dict):
            spotify_url = str(external_urls.get("spotify") or "").strip()
            if spotify_url:
                return spotify_url

        for key in ("spotify_url", "external_url", "url"):
            candidate_url = str(payload.get(key) or "").strip()
            if "open.spotify.com/track/" in candidate_url:
                return candidate_url

        uri = str(payload.get("uri") or "").strip()
        if uri.startswith("spotify:track:"):
            track_id = uri.rsplit(":", 1)[-1].strip()
            if track_id:
                return f"https://open.spotify.com/track/{track_id}"

        track_id = str(
            payload.get("id")
            or payload.get("track_id")
            or payload.get("spotify_id")
            or ""
        ).strip()
        if track_id:
            return f"https://open.spotify.com/track/{track_id}"

        return None

    def _spotify_track_share_payload_for_row(self, row: int) -> Optional[Dict[str, Any]]:
        payloads = self.get_table_track_payloads_for_rows([row])
        if not payloads:
            return None

        payload = payloads[0]
        if payload.get("source_type") != "spotify":
            return None

        share_url = self._spotify_track_url_from_payload(payload)
        if not share_url:
            return None

        payload = dict(payload)
        payload["spotify_share_url"] = share_url
        return payload

    def copySpotifyTrackUrlForRow(self, row: int) -> None:
        payload = self._spotify_track_share_payload_for_row(row)
        if not payload:
            CustomQMessageBox.warning(
                self.main_view,
                "Spotify URL Unavailable",
                "Spotify track URL unavailable.",
                "This row does not contain a Spotify track ID or URI.",
            )
            return

        share_url = str(payload.get("spotify_share_url") or "").strip()
        if not share_url:
            CustomQMessageBox.warning(
                self.main_view,
                "Spotify URL Unavailable",
                "Spotify track URL unavailable.",
                "This row does not contain a shareable Spotify URL.",
            )
            return

        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard:
            clipboard.setText(share_url)

        display_title = str(payload.get("display_title") or payload.get("name") or "Spotify track")
        CustomQMessageBox.information(
            self.main_view,
            "Spotify URL Copied",
            f"Copied Spotify URL for: {display_title}",
            share_url,
        )

    @pyqtSlot(str, list)
    def populate_spotify_tracks(
        self, playlist_id: str, tracks: Optional[List[Dict[str, Any]]]
    ):
        if not self.table_widget:
            return

        current_playlist_obj = cast(
            Optional[Union[Playlist, Dict[str, Any]]],
            getattr(self.main_view, "s_playlist_obj", None),
        )
        current_playlist_id: Optional[str] = None
        if isinstance(current_playlist_obj, dict) and current_playlist_obj.get("type") == "spotify":
            current_data = current_playlist_obj.get("data")
            if isinstance(current_data, dict):
                current_playlist_id = str(current_data.get("id") or "").strip() or None

        normalized_received_id = str(playlist_id or "").strip() or None
        is_active_spotify_selection = bool(
            isinstance(current_playlist_obj, dict)
            and current_playlist_obj.get("type") == "spotify"
            and current_playlist_id
        )
        if normalized_received_id and (
            (not is_active_spotify_selection)
            or (current_playlist_id != normalized_received_id)
        ):
            logger.info(
                "Ignoring stale Spotify tracks payload (received playlist_id=%s, active playlist_id=%s)",
                normalized_received_id,
                current_playlist_id,
            )
            return

        if tracks is None:
            self.show_error_message("Error fetching Spotify tracks.")
            tracks = []

        spotify_track_array_for_mainview: List[Dict[str, Any]] = [
            {"type": "spotify_track", "data": t}
            for t in tracks
            if isinstance(t, dict)
        ]
        self.main_view.s_array = spotify_track_array_for_mainview
        self.main_view.s_type = Type.Track
        self._set_spotify_reorder_visual_state(
            self._active_spotify_playlist_can_reorder()
        )
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
        cleaned_quality = " ".join((quality_text or "").split())
        if cleaned_quality:
            return f"Completed: {cleaned_quality}"
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

    def _extract_tidal_track_from_row(self, row: int) -> Optional[Track]:
        if not self.table_widget:
            return None
        title_item = self.table_widget.item(row, 1)
        if not title_item:
            return None
        return self._extract_tidal_track_from_item_data(
            title_item.data(Qt.ItemDataRole.UserRole)
        )

    def _quality_text_from_track_object(self, track: Optional[Track]) -> Optional[str]:
        if not isinstance(track, Track):
            return None

        try:
            quality_text = Printf.map_track_quality(track)
        except Exception:
            logger.debug(
                "Failed to derive quality from already-loaded Track object.",
                exc_info=True,
            )
            return None

        cleaned = (quality_text or "").strip()
        if not cleaned or cleaned in ("-", "Unknown", "Unknown Quality"):
            return None
        return cleaned

    def _register_row_track_id(self, row: int, track_id: Optional[str]) -> None:
        cleaned_track_id = str(track_id or "").strip()
        if not cleaned_track_id:
            return
        self._track_id_to_rows.setdefault(cleaned_track_id, set()).add(row)

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
        extensions_in_priority_order = [".flac", ".mp3", ".m4a", ".mp4"]
        candidate_paths: List[str] = []
        for extension in extensions_in_priority_order:
            audio_type_folder = getAudioTypeFolder(
                dummy_stream,
                extension=extension,
            )
            base_path = getTrackPath(
                track,
                dummy_stream,
                artist,
                artists,
                album=None,
                playlist_context=playlist_context,
                audio_type_folder=audio_type_folder,
            )
            base_stem, _ = os.path.splitext(os.path.normpath(base_path))
            candidate_paths.append(f"{base_stem}{extension}")
        return candidate_paths

    def _extract_playlist_identity(
        self,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        playlist_id: Optional[str] = None
        playlist_name: Optional[str] = None

        if isinstance(playlist_context, Playlist):
            playlist_id = str(getattr(playlist_context, "uuid", "") or "").strip() or None
            playlist_name = str(getattr(playlist_context, "title", "") or "").strip() or None
            return playlist_id, playlist_name

        if isinstance(playlist_context, dict):
            p_type = playlist_context.get("type")
            p_data = playlist_context.get("data")

            if p_type == "spotify" and isinstance(p_data, dict):
                playlist_id = str(p_data.get("id", "") or "").strip() or None
                playlist_name = str(p_data.get("name", "") or "").strip() or None
                return playlist_id, playlist_name

            if p_type == "tidal" and isinstance(p_data, Playlist):
                playlist_id = str(getattr(p_data, "uuid", "") or "").strip() or None
                playlist_name = str(getattr(p_data, "title", "") or "").strip() or None
                return playlist_id, playlist_name

            if isinstance(p_data, dict):
                playlist_id = str(
                    p_data.get("id", p_data.get("uuid", "")) or ""
                ).strip() or None
                playlist_name = str(
                    p_data.get("name", p_data.get("title", "")) or ""
                ).strip() or None
                return playlist_id, playlist_name

        return None, None

    def _extract_download_root_and_relative_playlist_dir(
        self,
        candidate_path: str,
    ) -> tuple[Optional[str], Optional[str]]:
        normalized_path = os.path.normpath(os.path.abspath(candidate_path))
        configured_download_root = os.path.normpath(
            os.path.abspath(get_user_download_path(SETTINGS.downloadPath))
        )
        candidate_dir = os.path.dirname(normalized_path)
        audio_type_folders = {"flac", "mp3", "m4a", "mp4", "aac", "unknown"}
        relative_to_configured_root: Optional[str] = None
        try:
            maybe_relative = os.path.relpath(candidate_dir, configured_download_root)
            if maybe_relative not in {"", "."} and not maybe_relative.startswith(".."):
                relative_to_configured_root = os.path.normpath(maybe_relative)
        except ValueError:
            # Different drive letters (Windows): continue with playlist-segment fallback.
            relative_to_configured_root = None

        if relative_to_configured_root:
            relative_parts = relative_to_configured_root.split(os.sep)
            if relative_parts and relative_parts[0].lower() in audio_type_folders:
                return configured_download_root, relative_to_configured_root

        path_parts = normalized_path.split(os.sep)

        if "Playlists" in path_parts:
            playlists_index = path_parts.index("Playlists")
            if playlists_index >= len(path_parts) - 1:
                return None, None

            if playlists_index == 0:
                return None, None

            download_root = os.sep.join(path_parts[:playlists_index])
            if not download_root:
                return None, None

            relative_playlist_dir = os.path.join(*path_parts[playlists_index:-1])
            return download_root, relative_playlist_dir

        # Fallback for custom playlist folder formats that do not include a "Playlists" segment.
        # Example: F:\Muziek\Tidal-dl\flac\GeradeHouse - Deeper House\...
        if not relative_to_configured_root:
            return None, None

        return configured_download_root, relative_to_configured_root

    def _build_existing_playlist_file_index(
        self,
        tracks: List[Track],
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
        *,
        include_track_id_tags: bool = True,
    ) -> tuple[Set[str], Set[str]]:
        """
        Builds a one-pass file index for playlist existence checks.

        Important performance rule:
        - For queued "download missing only" flows, callers should pass
          include_track_id_tags=False. Filename/stem matching is fast.
        - Reading audio tags with mutagen can be very slow on OneDrive/network
          folders and can hold the Python GIL long enough to make the UI feel frozen.
        """
        if not playlist_context:
            return set(), set()

        sample_track = next((track for track in tracks if isinstance(track, Track)), None)
        if not sample_track:
            return set(), set()

        candidate_paths = self._build_candidate_track_paths(sample_track, playlist_context)
        if not candidate_paths:
            return set(), set()

        download_root, computed_relative_playlist_dir = self._extract_download_root_and_relative_playlist_dir(
            candidate_paths[0]
        )
        if not download_root or not computed_relative_playlist_dir:
            return set(), set()

        playlist_id, _ = self._extract_playlist_identity(playlist_context)
        persisted_relative_playlist_dir = (
            self.persistence_manager.get_playlist_folder_hint(playlist_id)
            if playlist_id
            else None
        )

        preferred_relative_dirs = [
            computed_relative_playlist_dir,
            persisted_relative_playlist_dir,
        ]

        target_dirs: List[str] = []
        searched_relative_dirs: Set[str] = set()
        for relative_dir in preferred_relative_dirs:
            if not relative_dir:
                continue

            normalized_relative_dir = os.path.normpath(relative_dir)
            if normalized_relative_dir in searched_relative_dirs:
                continue

            searched_relative_dirs.add(normalized_relative_dir)
            candidate_dir = os.path.join(download_root, normalized_relative_dir)
            if os.path.isdir(candidate_dir):
                target_dirs.append(candidate_dir)

        if not target_dirs:
            return set(), set()

        indexed_stems: Set[str] = set()
        indexed_track_ids: Set[str] = set()
        allowed_extensions = {".flac", ".mp3", ".m4a", ".mp4"}

        index_start = time.perf_counter()
        scanned_files = 0
        tag_reads = 0

        expected_normalized_stems: Set[str] = set()
        for track in tracks:
            if not isinstance(track, Track):
                continue

            for candidate_path in self._build_candidate_track_paths(track, playlist_context):
                candidate_stem = os.path.splitext(os.path.basename(candidate_path))[0]
                normalized_candidate_stem = self._normalize_stem_for_match(candidate_stem)
                if normalized_candidate_stem:
                    expected_normalized_stems.add(normalized_candidate_stem)

        for target_dir in target_dirs:
            with suppress(OSError):
                for entry in os.scandir(target_dir):
                    if not entry.is_file():
                        continue

                    stem, extension = os.path.splitext(entry.name)
                    if extension.lower() not in allowed_extensions:
                        continue
                    scanned_files += 1

                    normalized_stem = self._normalize_stem_for_match(stem)
                    if normalized_stem:
                        indexed_stems.add(normalized_stem)

                    if not include_track_id_tags:
                        continue

                    # Only read tags for plausible candidates.
                    # Avoid the old broad substring scan for bulk queued checks.
                    likely_candidate = normalized_stem in expected_normalized_stems

                    if not likely_candidate and len(expected_normalized_stems) <= 200:
                        likely_candidate = any(
                            normalized_stem
                            and (
                                normalized_stem in expected_stem
                                or expected_stem in normalized_stem
                            )
                            for expected_stem in expected_normalized_stems
                        )

                    if likely_candidate:
                        tag_reads += 1
                        tags_track_id = self._extract_track_id_from_audio_tags(entry.path)
                        if tags_track_id:
                            indexed_track_ids.add(tags_track_id)

        elapsed_ms = (time.perf_counter() - index_start) * 1000.0
        if elapsed_ms > 250.0 or len(tracks) >= 100:
            logger.info(
                "Existing playlist file index built | tracks=%d target_dirs=%d scanned_files=%d "
                "indexed_stems=%d indexed_track_ids=%d tag_reads=%d include_track_id_tags=%s elapsed_ms=%.1f",
                len(tracks),
                len(target_dirs),
                scanned_files,
                len(indexed_stems),
                len(indexed_track_ids),
                tag_reads,
                include_track_id_tags,
                elapsed_ms,
            )

        return indexed_stems, indexed_track_ids

    def _extract_track_id_from_audio_tags(self, file_path: str) -> Optional[str]:
        try:
            from mutagen import File as MutagenFile
        except Exception:
            return None

        with suppress(Exception):
            audio_file = MutagenFile(file_path)
            if not audio_file or not getattr(audio_file, "tags", None):
                return None

            tags = audio_file.tags
            candidate_values: List[Any] = []

            if isinstance(tags, dict):
                for tag_key in (
                    "TIDAL_TRACK",
                    "TXXX:TIDAL_TRACK",
                    "----:com.apple.iTunes:TIDAL_TRACK",
                    "TIDAL_TRACK_ID",
                    "TXXX:TIDAL_TRACK_ID",
                    "----:com.apple.iTunes:TIDAL_TRACK_ID",
                ):
                    if tag_key in tags:
                        candidate_values.append(tags.get(tag_key))

                for tag_key, tag_value in tags.items():
                    key_upper = str(tag_key).upper()
                    if "TIDAL_TRACK" in key_upper:
                        candidate_values.append(tag_value)

            for raw_value in candidate_values:
                if isinstance(raw_value, list) and raw_value:
                    raw_value = raw_value[0]

                if isinstance(raw_value, bytes):
                    raw_text = raw_value.decode("utf-8", errors="ignore").strip()
                else:
                    raw_text = str(raw_value).strip()

                if not raw_text:
                    continue

                if raw_text.isdigit():
                    return raw_text

        return None

    def _normalize_stem_for_match(self, stem: str) -> str:
        """Normalizes file stems to improve cross-version/path-format matching robustness."""
        raw_stem = str(stem or "")
        if not raw_stem:
            return ""

        # Normalize unicode accents and apostrophe variants.
        normalized = unicodedata.normalize("NFKD", raw_stem)
        normalized = "".join(
            char for char in normalized if not unicodedata.combining(char)
        )
        normalized = normalized.replace("’", "'").replace("`", "'")

        # Normalize common feature annotations and punctuation differences.
        normalized = re.sub(
            r"\((feat\.?|ft\.?)\s+[^)]*\)",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"[^a-zA-Z0-9]+", " ", normalized).strip().lower()
        return " ".join(normalized.split())

    def _build_playlist_context_key(
        self,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
    ) -> str:
        playlist_id, playlist_name = self._extract_playlist_identity(playlist_context)
        context_type = type(playlist_context).__name__ if playlist_context is not None else "None"

        if isinstance(playlist_context, dict):
            context_type = str(playlist_context.get("type") or context_type)

        if playlist_id:
            return f"{context_type}:{playlist_id}"
        if playlist_name:
            return f"{context_type}:name:{playlist_name.lower()}"
        return f"{context_type}:none"

    def _find_existing_track_file_path_by_playlist_scan(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        candidate_paths = self._build_candidate_track_paths(track, playlist_context)
        if not candidate_paths:
            return None

        download_root, computed_relative_playlist_dir = self._extract_download_root_and_relative_playlist_dir(
            candidate_paths[0]
        )
        if not download_root or not computed_relative_playlist_dir:
            return None

        expected_stems = {
            os.path.splitext(os.path.basename(candidate_path))[0]
            for candidate_path in candidate_paths
        }

        expected_extensions = {
            os.path.splitext(candidate_path)[1].lower() for candidate_path in candidate_paths
        }

        track_id_str = str(getattr(track, "id", "") or "").strip()
        if not track_id_str:
            return None

        playlist_id, playlist_name = self._extract_playlist_identity(playlist_context)
        persisted_relative_playlist_dir = (
            self.persistence_manager.get_playlist_folder_hint(playlist_id)
            if playlist_id
            else None
        )

        preferred_relative_dirs = [
            computed_relative_playlist_dir,
            persisted_relative_playlist_dir,
        ]
        searched_relative_dirs: Set[str] = set()

        for relative_dir in preferred_relative_dirs:
            if not relative_dir:
                continue
            normalized_relative_dir = os.path.normpath(relative_dir)
            if normalized_relative_dir in searched_relative_dirs:
                continue
            searched_relative_dirs.add(normalized_relative_dir)
            candidate_dir = os.path.join(download_root, normalized_relative_dir)
            if not os.path.isdir(candidate_dir):
                continue
            matched = self._scan_single_playlist_folder_for_track(
                candidate_dir,
                expected_stems,
                expected_extensions,
                track_id_str,
                allow_stem_match=True,
            )
            if matched:
                return matched

        candidate_playlist_roots = [os.path.join(download_root, "Playlists")]
        for audio_folder in ("flac", "mp3", "m4a", "mp4", "aac", "unknown"):
            candidate_playlist_roots.append(os.path.join(download_root, audio_folder, "Playlists"))

        playlist_roots: List[str] = []
        seen_playlist_roots: Set[str] = set()
        for playlists_root in candidate_playlist_roots:
            normalized_playlists_root = os.path.normpath(playlists_root)
            if normalized_playlists_root in seen_playlist_roots:
                continue
            seen_playlist_roots.add(normalized_playlists_root)
            if os.path.isdir(playlists_root):
                playlist_roots.append(playlists_root)

        if not playlist_roots:
            return None

        prioritized_dirs: List[str] = []
        remaining_dirs: List[str] = []
        playlist_name_key = (playlist_name or "").strip().lower()

        for playlists_root in playlist_roots:
            with suppress(OSError):
                for entry in os.scandir(playlists_root):
                    if not entry.is_dir():
                        continue

                    if playlist_name_key:
                        entry_name_key = entry.name.strip().lower()
                        if (
                            playlist_name_key == entry_name_key
                            or playlist_name_key in entry_name_key
                            or entry_name_key in playlist_name_key
                        ):
                            prioritized_dirs.append(entry.path)
                            continue

                    remaining_dirs.append(entry.path)

        for folder_path in prioritized_dirs:
            matched = self._scan_single_playlist_folder_for_track(
                folder_path,
                expected_stems,
                expected_extensions,
                track_id_str,
                allow_stem_match=True,
            )
            if matched:
                if playlist_id:
                    with suppress(ValueError):
                        relative_dir = os.path.relpath(folder_path, download_root)
                        self.persistence_manager.set_playlist_folder_hint(
                            playlist_id,
                            relative_dir,
                            playlist_name,
                        )
                return matched

        for folder_path in remaining_dirs:
            matched = self._scan_single_playlist_folder_for_track(
                folder_path,
                expected_stems,
                expected_extensions,
                track_id_str,
                allow_stem_match=False,
            )
            if matched:
                if playlist_id:
                    with suppress(ValueError):
                        relative_dir = os.path.relpath(folder_path, download_root)
                        self.persistence_manager.set_playlist_folder_hint(
                            playlist_id,
                            relative_dir,
                            playlist_name,
                        )
                return matched

        return None

    def _scan_single_playlist_folder_for_track(
        self,
        folder_path: str,
        expected_stems: Set[str],
        expected_extensions: Set[str],
        track_id_str: str,
        allow_stem_match: bool,
    ) -> Optional[str]:
        normalized_expected_stems = {
            self._normalize_stem_for_match(expected_stem)
            for expected_stem in expected_stems
            if expected_stem
        }

        with suppress(OSError):
            for entry in os.scandir(folder_path):
                if not entry.is_file():
                    continue

                _, extension = os.path.splitext(entry.name)
                extension_lower = extension.lower()
                if extension_lower not in expected_extensions:
                    continue

                if allow_stem_match:
                    file_stem = os.path.splitext(entry.name)[0]
                    normalized_file_stem = self._normalize_stem_for_match(file_stem)
                    if (
                        file_stem in expected_stems
                        or (
                            normalized_file_stem
                            and normalized_file_stem in normalized_expected_stems
                        )
                    ):
                        return entry.path

                tags_track_id = self._extract_track_id_from_audio_tags(entry.path)
                if tags_track_id and tags_track_id == track_id_str:
                    return entry.path

        return None

    def _find_existing_track_file_path(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        for candidate_path in self._build_candidate_track_paths(track, playlist_context):
            if os.path.exists(candidate_path):
                return candidate_path

        if playlist_context:
            robust_match = self._find_existing_track_file_path_by_playlist_scan(
                track,
                playlist_context,
            )
            if robust_match:
                return robust_match

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

    def _get_completed_quality_with_diagnostics(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
        *,
        reason: str,
        row: Optional[int] = None,
    ) -> Optional[str]:
        start = time.perf_counter()
        quality_text = self.get_completed_quality_for_track(track, playlist_context)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        if elapsed_ms >= 250.0:
            playlist_type = type(playlist_context).__name__ if playlist_context is not None else "None"
            track_id = str(getattr(track, "id", ""))
            logger.warning(
                "[DIAGNOSIS] Slow completed-quality lookup detected | reason=%s row=%s track_id=%s elapsed_ms=%.1f playlist_context_type=%s",
                reason,
                row,
                track_id,
                elapsed_ms,
                playlist_type,
            )

        return quality_text

    def _enqueue_completed_status_resolution(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
        *,
        row: Optional[int],
        reason: str,
    ) -> None:
        track_id = str(getattr(track, "id", "") or "").strip()
        if not track_id:
            return

        context_key = self._build_playlist_context_key(playlist_context)
        cache_key = f"{context_key}|{track_id}"

        with self._completed_status_lock:
            if self._executors_shutdown:
                return
            if cache_key in self._completed_status_cache:
                cached_quality = self._completed_status_cache.get(cache_key)
                if cached_quality:
                    self.completedStatusResolved.emit(track_id, context_key, cached_quality)
                return

            if cache_key in self._completed_status_inflight_keys:
                return

            self._completed_status_inflight_keys.add(cache_key)

        try:
            self._completed_status_executor.submit(
                self._resolve_completed_status_in_background,
                track,
                playlist_context,
                context_key,
                cache_key,
                row,
                reason,
            )
        except RuntimeError:
            with self._completed_status_lock:
                self._completed_status_inflight_keys.discard(cache_key)

    def shutdown_background_workers(self, wait: bool = False) -> None:
        self.prepare_for_shutdown()
        self._visible_metadata_refresh_timer.stop()
        self._metadata_cache_flush_timer.stop()
        self._flush_deferred_metadata_cache_save(blocking=True)

        with self._metadata_state_lock:
            self._executors_shutdown = True
            self._metadata_inflight_track_ids.clear()
            self._metadata_pending_track_ids.clear()

        with self._completed_status_lock:
            self._completed_status_inflight_keys.clear()

        try:
            self._metadata_executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            self._metadata_executor.shutdown(wait=wait)
        except Exception:
            logger.debug("Failed to shut down metadata executor cleanly.", exc_info=True)

        try:
            self._metadata_cache_flush_executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            self._metadata_cache_flush_executor.shutdown(wait=wait)
        except Exception:
            logger.debug("Failed to shut down metadata cache flush executor cleanly.", exc_info=True)

        try:
            self._completed_status_executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            self._completed_status_executor.shutdown(wait=wait)
        except Exception:
            logger.debug("Failed to shut down completed-status executor cleanly.", exc_info=True)

    def _visible_row_indices(self, max_rows: Optional[int] = None) -> List[int]:
        if not self.table_widget or self.table_widget.rowCount() <= 0:
            return []

        first_visible_row = self.table_widget.rowAt(0)
        if first_visible_row < 0:
            first_visible_row = max(0, self.table_widget.verticalScrollBar().value())

        viewport = self.table_widget.viewport()
        row_height = max(1, self.table_widget.verticalHeader().defaultSectionSize())
        estimated_visible_rows = max(1, (viewport.height() // row_height) + 2) if viewport else 12

        last_visible_row = self.table_widget.rowAt(
            self.table_widget.viewport().height() - 1
        )
        if last_visible_row < 0:
            last_visible_row = first_visible_row + estimated_visible_rows

        final_row = min(last_visible_row, self.table_widget.rowCount() - 1)
        if max_rows is not None:
            final_row = min(final_row, first_visible_row + max(1, max_rows) - 1)

        return list(range(first_visible_row, final_row + 1))

    def check_completed_status_for_rows(
        self,
        rows: List[int],
        *,
        reason: str = "manual_completed_status_check",
    ) -> None:
        if not self.table_widget:
            return

        current_playlist_context = cast(
            Optional[Union[Playlist, Album, Dict[str, Any]]],
            getattr(self.main_view, "s_playlist_obj", None),
        )
        self._active_table_playlist_context_key = self._build_playlist_context_key(
            current_playlist_context
        )

        for row in rows:
            title_item = self.table_widget.item(row, 1)
            if not title_item:
                continue

            track_obj = self._extract_tidal_track_from_item_data(
                title_item.data(Qt.ItemDataRole.UserRole)
            )
            if not track_obj:
                continue

            self._enqueue_completed_status_resolution(
                track_obj,
                current_playlist_context,
                row=row,
                reason=reason,
            )

    def check_completed_status_for_visible_rows(self) -> None:
        self.check_completed_status_for_rows(
            self._visible_row_indices(),
            reason="manual_visible_rows",
        )

    def _resolve_completed_status_in_background(
        self,
        track: Track,
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
        context_key: str,
        cache_key: str,
        row: Optional[int],
        reason: str,
    ) -> None:
        track_id = str(getattr(track, "id", "") or "").strip()
        resolved_quality: Optional[str] = None
        try:
            resolved_quality = self._get_completed_quality_with_diagnostics(
                track,
                playlist_context,
                reason=reason,
                row=row,
            )
        except Exception as ex:
            logger.debug(
                "Completed-status background resolution failed for track_id=%s: %s",
                track_id,
                ex,
                exc_info=True,
            )
        finally:
            cleaned_quality = (resolved_quality or "").strip() or None
            with self._completed_status_lock:
                self._completed_status_cache[cache_key] = cleaned_quality
                self._completed_status_inflight_keys.discard(cache_key)

            if cleaned_quality:
                self.completedStatusResolved.emit(track_id, context_key, cleaned_quality)

    @pyqtSlot(str, str, str)
    def _on_completed_status_resolved(
        self,
        track_id: str,
        context_key: str,
        quality_text: str,
    ) -> None:
        if (
            not self.table_widget
            or not self.download_handler
            or "Status" not in self.column_indices
        ):
            return

        if not quality_text:
            return

        if context_key != self._active_table_playlist_context_key:
            logger.debug(
                "Ignoring completed-status result for stale context: track_id=%s context=%s active=%s",
                track_id,
                context_key,
                self._active_table_playlist_context_key,
            )
            return

        status_col = self.column_indices["Status"]
        for row in range(self.table_widget.rowCount()):
            row_track_id = self._extract_tidal_track_id_from_row(row)
            if row_track_id != track_id:
                continue

            active_state = self.download_handler.active_downloads.get(track_id)
            if active_state and active_state.get("status") in {"pending", "downloading", "failed"}:
                continue

            status_item = self.table_widget.item(row, status_col)
            if not status_item:
                status_item = QTableWidgetItem()
                self.table_widget.setItem(row, status_col, status_item)

            status_item.setText(self._format_completed_status_text(quality_text))
            status_item.setToolTip(quality_text)

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
        *,
        allow_slow_fallback: bool = True,
        reason: str = "filter_non_completed_tracks",
    ) -> List[Track]:
        filter_start = time.perf_counter()
        index_start = time.perf_counter()
        indexed_stems, indexed_track_ids = self._build_existing_playlist_file_index(
            tracks,
            playlist_context,
            include_track_id_tags=allow_slow_fallback,
        )
        index_elapsed_ms = (time.perf_counter() - index_start) * 1000.0

        non_completed_tracks: List[Track] = []
        skipped_by_stem = 0
        skipped_by_track_id = 0
        slow_fallback_checks = 0

        for track in tracks:
            if not isinstance(track, Track):
                continue

            if indexed_stems:
                candidate_stems = {
                    self._normalize_stem_for_match(
                        os.path.splitext(os.path.basename(candidate_path))[0]
                    )
                    for candidate_path in self._build_candidate_track_paths(track, playlist_context)
                }
                if candidate_stems.intersection(indexed_stems):
                    skipped_by_stem += 1
                    continue

            track_id_str = str(getattr(track, "id", "") or "").strip()
            if track_id_str and track_id_str in indexed_track_ids:
                skipped_by_track_id += 1
                continue

            if not allow_slow_fallback:
                non_completed_tracks.append(track)
                continue

            slow_fallback_checks += 1
            if not self.is_track_completed(track, playlist_context):
                non_completed_tracks.append(track)

        elapsed_ms = (time.perf_counter() - filter_start) * 1000
        if (
            elapsed_ms > 500
            or len(tracks) >= 500
            or not allow_slow_fallback
        ):
            logger.info(
                "Non-completed filter result | reason=%s tracks=%d remaining=%d "
                "removed=%d skipped_by_stem=%d skipped_by_track_id=%d "
                "slow_fallback_checks=%d allow_slow_fallback=%s elapsed_ms=%.1f "
                "index_elapsed_ms=%.1f",
                reason,
                len(tracks),
                len(non_completed_tracks),
                len(tracks) - len(non_completed_tracks),
                skipped_by_stem,
                skipped_by_track_id,
                slow_fallback_checks,
                allow_slow_fallback,
                elapsed_ms,
                index_elapsed_ms,
            )

        return non_completed_tracks

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
        return self._get_completed_quality_with_diagnostics(
            track,
            playlist_context,
            reason="resolve_completed_quality_for_row",
            row=row,
        )

    def _populate_table_generic(
        self,
        results_array: List[Any],
        result_type: Type,
        playlist_id: Optional[str] = None,
    ):
        if not self.table_widget or not self.download_handler:
            return
        populate_start = time.perf_counter()
        table = self.table_widget
        sorting_was_enabled = table.isSortingEnabled()
        if sorting_was_enabled:
            table.setSortingEnabled(False)
        table.clearRows()
        self._track_id_to_rows.clear()
        with self._metadata_state_lock:
            self._metadata_inflight_track_ids.clear()
            self._metadata_pending_track_ids.clear()
            self._metadata_attempted_track_ids.clear()
            self._metadata_api_budget_this_tick = 0

        is_spotify_track_list = (
            result_type == Type.Track
            and results_array
            and isinstance(results_array[0], dict)
            and results_array[0].get("type") == "spotify_track"
        )

        base_headers = [
            "#",
            "Title",
            "Artists",
            "Album",
            "Release Year",
            "BPM",
            "Key",
            "Genre",
            "Label",
            "Length",
            "Quality",
        ]
        column_headers = base_headers + ["Status"]
        table.setColumnCount(len(column_headers))
        table.setHorizontalHeaderLabels(column_headers)
        self.column_indices = {header: i for i, header in enumerate(column_headers)}
        if hasattr(table, "apply_column_visibility_preferences"):
            table.apply_column_visibility_preferences()

        if not results_array:
            table.update()
            if self.download_handler:
                self.download_handler._update_download_button_text()
            if sorting_was_enabled:
                table.setSortingEnabled(True)
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
        self._active_table_playlist_context_key = self._build_playlist_context_key(
            cast(Optional[Union[Playlist, Album, Dict[str, Any]]], playlist_context_for_display)
        )

        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(len(results_array))
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
                        tidal_track_obj: Optional[Track] = None

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

                            # 2. Retrieve Candidates, Score, and persisted workflow state
                            candidates_list = link_info.get("candidates") or []
                            score = link_info.get("score")
                            persisted_link_status = link_info.get("link_status")
                            if not isinstance(persisted_link_status, str):
                                persisted_link_status = None

                            # 3. Determine Status
                            derived_status = "not_linked"

                            if tidal_track_obj:
                                linked_tidal_id = str(tidal_track_obj.id)
                                track_id_for_download_check = linked_tidal_id

                                if persisted_link_status == "candidate_confirmed":
                                    link_status_text = f"Linked (Confirmed): {tidal_track_obj.id}"
                                    derived_status = "candidate_confirmed"
                                    has_candidates = bool(candidates_list)
                                elif persisted_link_status == "manual_linked":
                                    link_status_text = f"Linked (Manual): {tidal_track_obj.id}"
                                    derived_status = "manual_linked"
                                    has_candidates = bool(candidates_list)
                                elif (
                                    persisted_link_status == "needs_review_confirm"
                                    or (
                                        score is not None
                                        and isinstance(score, (int, float))
                                        and score > 1
                                        and len(candidates_list) == 1
                                    )
                                ):
                                    score_text = score if score is not None else "N/A"
                                    link_status_text = (
                                        f"Linked (Review suggested match, Score: {score_text}): "
                                        f"{tidal_track_obj.id}"
                                    )
                                    derived_status = "needs_review_confirm"
                                    has_candidates = bool(candidates_list)
                                elif score is not None and score <= 1:
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
                                if persisted_link_status == "candidate_review_dismissed":
                                    link_status_text = "Candidate match dismissed"
                                    derived_status = "candidate_review_dismissed"
                                else:
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
                                    "candidates": candidates_list,
                                }
                            )

                        artists_str = ", ".join(
                            spotify_track_data_to_use.get("artists", [])
                        )
                        album_name = spotify_track_data_to_use.get("album", "N/A")
                        duration_str = format_duration_ms(
                            spotify_track_data_to_use.get("duration_ms")
                        )
                        metadata_values = self._get_requested_metadata_values(
                            tidal_track_obj
                        )
                        rowData = [
                            str(index + 1),
                            spotify_track_data_to_use.get("name", "N/A"),
                            artists_str,
                            album_name,
                            metadata_values["release_year"],
                            metadata_values["bpm"],
                            metadata_values["key"],
                            metadata_values["genre"],
                            metadata_values["label"],
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
                        metadata_values = self._get_requested_metadata_values(item)
                        rowData = [
                            str(index + 1),
                            str(item.title),
                            TIDAL_API.getArtistsName(artists),
                            str(album_title),
                            metadata_values["release_year"],
                            metadata_values["bpm"],
                            metadata_values["key"],
                            metadata_values["genre"],
                            metadata_values["label"],
                            Printf.formatDuration(item.duration),
                            str(quality_string),
                            "-",
                        ]
                        item_metadata = item

                    if rowData:
                        table.setRowData(
                            index,
                            rowData,
                            track=item_metadata,
                            apply_row_style=False,
                        )
                        self._register_row_track_id(
                            index,
                            self._extract_tidal_track_id_from_row(index),
                        )
                        current_track_for_metadata = self._extract_tidal_track_from_item_data(item_metadata)
                        if current_track_for_metadata:
                            self._register_row_track_id(
                                index,
                                str(getattr(current_track_for_metadata, "id", "") or ""),
                            )
                            self._set_row_tidal_identity_text(
                                index,
                                current_track_for_metadata,
                            )
                            self._apply_metadata_tooltips_for_row(
                                index,
                                self._get_requested_metadata_values(
                                    current_track_for_metadata
                                ),
                            )
                        
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
                                    "candidate_review_mode": (
                                        "auto_single_candidate_review"
                                        if derived_status == "needs_review_confirm"
                                        else "manual"
                                    ),
                                    "original_spotify_track": spotify_track_data_to_use,
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
                                            self._enqueue_completed_status_resolution(
                                                track_for_completion,
                                                cast(
                                                    Optional[Union[Playlist, Album, Dict[str, Any]]],
                                                    download_state.get("playlist_context", playlist_context_for_display),
                                                ),
                                                row=index,
                                                reason="populate_table_active_completed_fallback",
                                            )
                                    status_item.setText(self._format_completed_status_text(completed_quality))
                                    status_item.setToolTip(completed_quality)
                                elif status == "failed":
                                    status_item.setText("Failed")
                                    status_item.setToolTip(download_state.get("error", ""))
                            else:
                                # Avoid scanning local download folders for every row during initial table population.
                                # Completed-status lookup remains available through active download completion paths.
                                pass

                except Exception as e:
                    logger.error(
                        f"Error processing item at index {index}: {e}", exc_info=True
                    )
                    error_row_data = [str(index + 1), f"Error: {e}"] + [
                        ""
                    ] * max(0, table.columnCount() - 2)
                    table.setRowData(
                        index,
                        error_row_data,
                        track=None,
                        apply_row_style=False,
                    )

            table.adjustColumnWidths()
            table.update()
            self._schedule_visible_metadata_resolution()
            elapsed_ms = (time.perf_counter() - populate_start) * 1000.0
            logger.info(
                "Table populated with %s items in %.1fms (spotify=%s, playlist_id=%s)",
                table.rowCount(),
                elapsed_ms,
                is_spotify_track_list,
                playlist_id,
            )
            if elapsed_ms >= 800.0:
                logger.warning(
                    "[DIAGNOSIS] Slow _populate_table_generic detected | elapsed_ms=%.1f rows=%s spotify=%s playlist_id=%s sorting_was_enabled=%s",
                    elapsed_ms,
                    table.rowCount(),
                    is_spotify_track_list,
                    playlist_id,
                    sorting_was_enabled,
                )
            if self.download_handler:
                self.download_handler._update_download_button_text()
        finally:
            table.setUpdatesEnabled(True)
            if sorting_was_enabled:
                table.setSortingEnabled(True)

    def handle_table_context_menu(self, pos: QPoint):
        if not self.table_widget:
            return
        table = self.table_widget
        index = table.indexAt(pos)
        if not index.isValid():
            return

        selected_rows_indices = table.getSelectedRows()
        if index.row() not in selected_rows_indices:
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
            if len(selected_rows_indices) == 1:
                if not context_menu.isEmpty():
                    context_menu.addSeparator()

                share_payload = self._spotify_track_share_payload_for_row(selected_rows_indices[0])
                copy_spotify_url_action = context_menu.addAction("Copy Spotify Track URL")
                if copy_spotify_url_action:
                    copy_spotify_url_action.setEnabled(bool(share_payload))
                    copy_spotify_url_action.triggered.connect(
                        lambda _checked=False, row=selected_rows_indices[0]: self.copySpotifyTrackUrlForRow(row)
                    )
            spotify_handler = getattr(self.main_view, "spotify_gui_handler", None)
            if spotify_handler:
                if not context_menu.isEmpty():
                    context_menu.addSeparator()

                remove_spotify_action = context_menu.addAction(
                    f"Remove {len(selected_rows_indices)} Selected from Spotify Playlist"
                )
                if remove_spotify_action:
                    remove_spotify_action.triggered.connect(
                        lambda _checked=False, rows=list(selected_rows_indices): spotify_handler.removeSelectedRowsFromCurrentSpotifyPlaylist(rows)
                    )

                add_to_playlist_action = context_menu.addAction(
                    "Add Selected to Another Spotify Playlist"
                )
                if add_to_playlist_action:
                    add_to_playlist_action.triggered.connect(
                        lambda _checked=False, rows=list(selected_rows_indices): spotify_handler.addSelectedRowsToChosenSpotifyPlaylist(rows)
                    )

                create_playlist_action = context_menu.addAction(
                    "Create New Spotify Playlist from Selected"
                )
                if create_playlist_action:
                    create_playlist_action.triggered.connect(
                        lambda _checked=False, rows=list(selected_rows_indices): spotify_handler.createSpotifyPlaylistFromSelectedRows(rows)
                    )

                move_top_action = context_menu.addAction("Move Selected to Top")
                if move_top_action:
                    move_top_action.triggered.connect(
                        lambda _checked=False, rows=list(selected_rows_indices): spotify_handler.moveSelectedRowsToTop(rows)
                    )

                move_bottom_action = context_menu.addAction("Move Selected to Bottom")
                if move_bottom_action:
                    move_bottom_action.triggered.connect(
                        lambda _checked=False, rows=list(selected_rows_indices): spotify_handler.moveSelectedRowsToBottom(rows)
                    )
                review_playlist_action = context_menu.addAction(
                    "Create Review Playlist from Selected"
                )
                if review_playlist_action:
                    review_playlist_action.triggered.connect(
                        lambda _checked=False, rows=list(selected_rows_indices): spotify_handler.createReviewPlaylistFromSelectedRows(rows)
                    )

                sync_selected_action = context_menu.addAction(
                    "Sync Selected to Spotify Playlist..."
                )
                if sync_selected_action:
                    sync_selected_action.triggered.connect(
                        lambda _checked=False, rows=list(selected_rows_indices): spotify_handler.syncSelectedRowsToSpotifyPlaylist(rows)
                    )
        elif self.download_handler:
            self.download_handler.downloadTableContextMenu(
                context_menu, selected_rows_indices
            )

        if selected_rows_indices:
            if not context_menu.isEmpty():
                context_menu.addSeparator()

            check_selected_action = context_menu.addAction(
                f"Check completed status for {len(selected_rows_indices)} selected track"
                + ("s" if len(selected_rows_indices) != 1 else "")
            )
            if check_selected_action:
                check_selected_action.triggered.connect(
                    lambda _checked=False, rows=list(selected_rows_indices): self.check_completed_status_for_rows(
                        rows,
                        reason="manual_selected_rows",
                    )
                )

        visible_rows = self._visible_row_indices()
        if visible_rows:
            if not context_menu.isEmpty():
                context_menu.addSeparator()
            check_visible_action = context_menu.addAction(
                f"Check completed status for {len(visible_rows)} visible row"
                + ("s" if len(visible_rows) != 1 else "")
            )
            if check_visible_action:
                check_visible_action.triggered.connect(
                    lambda _checked=False: self.check_completed_status_for_visible_rows()
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
                    "candidate_review_dismissed",
                }:
                    item_data["tidal_track_id"] = None
                item_data["score"] = score
                if error_message:
                    item_data["error_message"] = error_message
                title_item.setData(Qt.ItemDataRole.UserRole, item_data)

        if tidal_track:
            self._register_row_track_id(row_index, str(tidal_track.id))
            self._set_row_tidal_identity_text(row_index, tidal_track)
            self._set_row_requested_metadata_text(row_index, tidal_track)
            tidal_track_id = str(tidal_track.id)
            cached_quality = self._get_cached_quality_threadsafe(
                tidal_track_id
            )
            if cached_quality:
                self._set_row_quality_text(row_index, cached_quality)
            else:
                self._set_row_quality_text(row_index, QUALITY_PLACEHOLDER_TEXT)
                self._enqueue_track_metadata_resolution(tidal_track_id)
        elif status in {
            "not_linked",
            "not_found",
            "error",
            "candidates_only",
            "candidate_review_dismissed",
        }:
            self._set_row_requested_metadata_text(row_index, None)
            self._set_row_quality_text(row_index, "-")
        elif status in {"linking", "manual_review_needed"}:
            # Preserve existing matched metadata during transient/manual-review updates.
            pass

        indicator_item = table.item(row_index, 0)
        if not indicator_item:
            indicator_item = QTableWidgetItem()
            table.setItem(row_index, 0, indicator_item)

        existing_indicator_data = (
            indicator_item.data(Qt.ItemDataRole.UserRole) if indicator_item else {}
        )
        if not isinstance(existing_indicator_data, dict):
            existing_indicator_data = {}

        original_spotify_track = existing_indicator_data.get("original_spotify_track")
        if not isinstance(original_spotify_track, dict):
            title_payload = (
                title_item.data(Qt.ItemDataRole.UserRole) if title_item else None
            )
            if (
                isinstance(title_payload, dict)
                and isinstance(title_payload.get("data"), dict)
            ):
                original_spotify_track = title_payload.get("data")
            else:
                original_spotify_track = None

        effective_candidates = candidates
        if effective_candidates is None and status in {
            "manual_linked",
            "needs_review_confirm",
            "candidate_confirmed",
            "candidate_review_dismissed",
        }:
            preserved_candidates = existing_indicator_data.get("candidates_list")
            if isinstance(preserved_candidates, list):
                effective_candidates = preserved_candidates

        has_candidates = bool(effective_candidates)
        linked_tidal_track_id = (
            str(tidal_track.id) if tidal_track and getattr(tidal_track, "id", None) is not None else None
        )

        indicator_data = {
            **existing_indicator_data,
            "has_candidates": has_candidates,
            "expanded": existing_indicator_data.get("expanded", False),
            "candidate_count": len(effective_candidates) if effective_candidates else 0,
            "candidates_list": effective_candidates,
            "linked_tidal_track_id": linked_tidal_track_id,
            "candidate_review_mode": (
                "auto_single_candidate_review"
                if status == "needs_review_confirm"
                else "manual"
            ),
            "original_spotify_track": original_spotify_track,
        }
        indicator_item.setData(Qt.ItemDataRole.UserRole, indicator_data)
        indicator_item.setText(f"+ ({len(effective_candidates)})" if has_candidates else "")

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
        self._schedule_visible_metadata_resolution()

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
                title_item = self.table_widget.item(row, 1)
                track_obj: Optional[Track] = None
                if title_item:
                    item_data = title_item.data(Qt.ItemDataRole.UserRole)
                    track_obj = self._extract_tidal_track_from_item_data(item_data)

                current_context = cast(
                    Optional[Union[Playlist, Album, Dict[str, Any]]],
                    getattr(self.main_view, "s_playlist_obj", None),
                )
                if track_obj:
                    self._enqueue_completed_status_resolution(
                        track_obj,
                        current_context,
                        row=row,
                        reason="mark_track_completed_fallback",
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
            self._schedule_visible_metadata_resolution()
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
        self._active_table_playlist_context_key = self._build_playlist_context_key(
            current_playlist_context
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
                            "needs_review_confirm": "Review Match",
                            "candidate_confirmed": "Linked",
                            "candidate_review_dismissed": "Not Linked",
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
                            "needs_review_confirm": "Review Match",
                            "candidate_confirmed": "Linked",
                            "candidate_review_dismissed": "Not Linked",
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
                    logger.debug(
                        "Completed quality missing for track %s; skipping automatic file scan during status rebuild.",
                        getattr(track_obj, "id", ""),
                    )
                status_item.setText(self._format_completed_status_text(completed_quality))
                status_item.setToolTip(completed_quality)
            elif status in ("failed", "cancelled"):
                status_item.setText("Failed" if status == "failed" else "Cancelled")
                if error:
                    status_item.setToolTip(error)
