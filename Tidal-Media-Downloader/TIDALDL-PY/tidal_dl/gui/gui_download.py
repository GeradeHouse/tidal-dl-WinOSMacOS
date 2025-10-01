# --- START OF FILE gui_download.py ---

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_download.py
@Time    :   2025/04/14
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Handles download logic and state for the Tidal Media Downloader GUI.
"""

import logging
import time
import threading
import traceback
import os
from functools import partial

from typing import (
    cast,
    List,
    Optional,
    Union,
    Any,
    Dict,
)  # Added cast and other required types
from typing import Tuple
from .gui_table import SplitterTable  # Import SplitterTable

# Assuming these are needed based on the methods being moved
from PyQt6 import QtWidgets

# from PyQt6 import QtGui # Removed unused import
from PyQt6.QtCore import (
    Qt,
    QTimer,
    QPoint,
    QModelIndex,
    QObject,
    pyqtSignal,
)  # Added QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QTableWidget,
    QMenu,
    QTableWidgetItem,
    QMessageBox,
    QProgressBar,
)  # Added QProgressBar
from PyQt6.QtGui import QAction  # Import QAction

# Import necessary components from the project
from ..printf import Printf
from ..tidal import (
    TIDAL_API,
    Type,
    AudioQuality,
    Track,
    Album,
    Playlist,
    start_type,
    StreamUrl,
)

# from ..tidal import VideoQuality # Removed unused import
from ..settings import SETTINGS  # Removed unused import

# Import the actual download functions from download.py
from ..download import downloadTrack as core_downloadTrack
from ..format import getAlbumPath, getPlaylistPath, getTrackPath
from ..settings import SETTINGS

# Import the utility function for showing messages
from .gui_utils import show_info_message, show_in_folder
from .gui_custom_dialog import ModernDarkDialog  # Import custom dialog
from ..paths import resource_path  # For icon path

# Forward declaration for type hinting MainView without circular import
# Import List and Any for type hinting
from typing import (
    TYPE_CHECKING,
    Optional,
    List,
    Any,
    Dict,
    Union,
    cast,
)  # Add Optional, Union, cast

if TYPE_CHECKING:
    from .gui import MainView

    # from PyQt6.QtGui import QAction # Moved import to top level
    # from PyQt6.QtCore import QPoint, QModelIndex # Moved import to top level
    # from PyQt6.QtWidgets import QTableWidgetItem # Moved import to top level


# Define a type alias for the complex data structure stored in table items
# This helps clarify the expected dictionary structure.
TableItemData = Dict[
    str, Any
]  # Example: {'link_status': 'found', 'tidal_track': Track(...)} or {'type': 'spotify_track', ...}

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set specific level for this module


class TrackProgressHandler(QObject):
    """Handles progress updates from aigpy and emits a signal for the GUI."""

    progressUpdated = pyqtSignal(str, int)  # track_id, percentage
    setupProgressBarUI = pyqtSignal(str, int)  # NEW: track_id, original_row_index

    def __init__(self, track_id: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._track_id = track_id
        self._last_emit_percent = (
            -1
        )  # Avoid emitting too frequently if value hasn't changed
        # Add a flag to track if finish has been called
        self._finished = False

    def update(self, currentSize: int, totalSize: int):
        """Called by aigpy's DownloadTool during download."""
        # --- ADD LOGGING HERE ---
        logger.debug(
            f"PROGRESS HANDLER UPDATE: TrackID={self._track_id}, Current={currentSize}, Total={totalSize}"
        )
        # --- END LOGGING ---
        if self._finished:  # Don't update if finish was already called
            return
        if totalSize > 0:
            percent = int((currentSize / totalSize) * 100)
            # Only emit if the percentage has changed to avoid flooding the GUI thread
            if percent != self._last_emit_percent:
                # Clamp percentage between 0 and 100
                percent = max(0, min(percent, 100))
                # --- ADD LOGGING HERE ---
                logger.debug(
                    f"PROGRESS HANDLER EMITTING: TrackID={repr(self._track_id)} (Type: {type(self._track_id)}), Percent={percent}"
                )
                # --- END LOGGING ---
                self.progressUpdated.emit(self._track_id, percent)
                self._last_emit_percent = percent
        # Add logging if needed: logger.debug(f"Track {self._track_id} progress: {currentSize}/{totalSize} ({percent}%)")

    def finish(self):
        """Called by aigpy's DownloadTool when download finishes (or fails)."""
        # Ensure 100% is emitted on successful completion, even if update didn't hit 100
        if not self._finished:
            self._finished = True  # Set flag first
            # --- ADD LOGGING HERE ---
            logger.debug(
                f"PROGRESS HANDLER FINISH: TrackID={repr(self._track_id)}. Last emitted percent: {self._last_emit_percent}"
            )
            # --- END LOGGING ---
            # Check if the last emitted was already 100 to avoid redundant signal
            if self._last_emit_percent != 100:
                # --- ADD LOGGING ---
                logger.debug(
                    f"PROGRESS HANDLER FINISH: Emitting final 100% for TrackID={repr(self._track_id)}"
                )
                # --- END LOGGING ---
                self.progressUpdated.emit(self._track_id, 100)
            logger.debug(f"Track {self._track_id} download finished signal processing.")

    def updateStream(self, stream):
        """Placeholder method called by downloadTrack before aigpy download starts."""
        # This method is called by downloadTrack to pass stream info.
        # We don't strictly need it for the QProgressBar, but it must exist.
        # We could log stream info here if desired.
        logger.debug(
            f"Track {self._track_id}: Received stream info: Quality={stream.soundQuality}, Codec={stream.codec}"
        )
        pass

    def setMaxNum(self, maxNum: int):
        """Placeholder method called by aigpy's DownloadTool."""
        # This method is likely called by aigpy to set the total number of steps/items.
        # We don't use it directly for the percentage calculation in update(),
        # but it needs to exist to satisfy the interface aigpy expects.
        logger.debug(f"Track {self._track_id}: setMaxNum called with {maxNum}")
        pass

    # Add start() method if aigpy requires it (check aigpy docs if needed)
    # def start(self):
    #     logger.debug(f"Track {self._track_id} download started.")


class DownloadHandler:
    """
    Manages download operations, state (active, paused, stopped),
    and interactions between the GUI (MainView) and the core download functions.
    """

    def __init__(
        self,
        main_view: "MainView",
        button_stack: QtWidgets.QStackedWidget,
        btn_download: QtWidgets.QPushButton,
        btn_pause_resume: QtWidgets.QPushButton,
        btn_stop: QtWidgets.QPushButton,
    ):
        """
        Initializes the DownloadHandler.

        Args:
            main_view (MainView): The instance of the main GUI window.
            button_stack (QStackedWidget): Reference to the download button stack.
            btn_download (QPushButton): Reference to the download button.
            btn_pause_resume (QPushButton): Reference to the pause/resume button.
            btn_stop (QPushButton): Reference to the stop button.
        """
        self.main_view = main_view
        self.button_stack = button_stack
        self.btn_download = btn_download
        self.btn_pause_resume = btn_pause_resume
        self.btn_stop = btn_stop
        # We don't duplicate state here; we access it via self.main_view
        # e.g., self.main_view.download_active, self.main_view.pause_event

        # Connect table selection change signal to update button text
        table_widget = getattr(self.main_view, "tableWidget", None)
        if table_widget:
            table_widget.itemSelectionChanged.connect(self._update_download_button_text)
        else:
            logger.error(
                "DownloadHandler init: tableWidget not found on main_view. Button text update on selection change will not work."
            )

        # Set initial button text
        self._update_download_button_text()

    def _update_download_button_text(self):
        """Updates the main download button text based on table selection."""
        table = getattr(self.main_view, "tableWidget", None)
        if not table:
            logger.warning("_update_download_button_text: tableWidget not found.")
            self.btn_download.setText("Download Selected")  # Default text
            return

        # Check if any items are selected
        selected_indices = table.getSelectedRows()  # Use the method from SplitterTable
        if not selected_indices:
            self.btn_download.setText("Download All")
        else:
            self.btn_download.setText("Download Selected")
        logger.debug(f"Download button text updated to: '{self.btn_download.text()}'")

    # --- Methods moved from MainView ---

    def _update_ui_for_download_start(self):
        """Sets the UI state when a download starts."""
        # Switch the button stack to show Pause/Stop.
        self.button_stack.setCurrentIndex(1)  # Index 1 is the Pause/Stop widget
        self.btn_pause_resume.setText("Pause")
        self.btn_pause_resume.setEnabled(True)
        self.btn_stop.setText("Stop")
        self.btn_stop.setEnabled(True)
        logger.debug("[GUI] Set download UI: Showing Pause/Stop")

    def onActuallyPaused(self):
        """Slot called via QueuedConnection when download thread confirms it has paused."""
        # Accessing main_view attributes - Pylance might warn due to forward ref
        logger.debug(
            f"[GUI {time.time():.3f}] Slot onActuallyPaused called. Current state: download_paused={getattr(self.main_view, 'download_paused', 'N/A')}"
        )
        if getattr(
            self.main_view, "download_paused", False
        ):  # Check if still paused (user might have resumed quickly)
            self.btn_pause_resume.setText("Resume")
            self.btn_pause_resume.setEnabled(True)
            logger.debug(
                f"[GUI {time.time():.3f}] Set button text to 'Resume' and enabled"
            )
        else:
            logger.warning(
                f"[GUI {time.time():.3f}] onActuallyPaused called but download_paused is False"
            )

    # Modify signature to accept list of tuples
    def startContextMenuDownload(
        self, tracks_with_rows: List[Tuple[int, Track]], quality_enum: AudioQuality
    ):
        """Initiates download for tracks via context menu, using main download flow."""
        # Accessing main_view attributes - Pylance might warn due to forward ref
        if getattr(self.main_view, "download_active", False):
            # Use imported show_info_message utility
            show_info_message(
                self.main_view,
                "Download In Progress",
                "Another download is already in progress.",
                "",
            )
            return

        if not tracks_with_rows:  # Check the new list name
            # Use imported show_info_message utility
            show_info_message(
                self.main_view,
                "Download Error",
                "Could not retrieve track information for download.",
                "Please select valid tracks.",
            )
            return

        # --- Save original table state before modifying for context menu download ---
        # Accessing main_view attributes - Pylance might warn due to forward ref
        self.main_view._original_s_array_before_ctx_dl = getattr(self.main_view, "s_array", [])[:]  # type: ignore
        self.main_view._original_s_type_before_ctx_dl = getattr(self.main_view, "s_type", None)  # type: ignore
        logger.debug(f"[GUI ContextMenu] Saved original state: {len(self.main_view._original_s_array_before_ctx_dl)} items, type {self.main_view._original_s_type_before_ctx_dl}")  # type: ignore

        # Set the main array and type using only the Track objects from the tuples
        tracks_only = [track for _, track in tracks_with_rows]
        logger.debug(
            f"[GUI ContextMenu] Setting self.main_view.s_array for download = {tracks_only} (type: {type(tracks_only)})"
        )
        self.main_view.s_array = tracks_only  # type: ignore
        # Explicitly set s_type to Track, as we are downloading specific tracks via context menu
        self.main_view.s_type = Type.Track  # type: ignore
        # Preserve original playlist context if applicable (might be needed by download function)
        self.main_view.s_playlist = self.main_view._original_s_type_before_ctx_dl == Type.Playlist  # type: ignore

        # Set the quality dropdown to match the selected quality
        # Accessing main_view attributes - Pylance might warn due to forward ref
        index = self.main_view.c_combTQuality.findData(quality_enum)  # type: ignore
        if index != -1:
            self.main_view.c_combTQuality.setCurrentIndex(index)  # type: ignore
        else:
            Printf.info(
                f"Could not find quality {quality_enum.name} in dropdown, download logic will use passed quality."
            )

        # Bypass the main 'download' method and call the thread function directly
        logger.debug(f"[GUI ContextMenu] Bypassing self.main_view.download(), calling __downloadFunc__ directly with items: {self.main_view.s_array}")  # type: ignore
        if getattr(self.main_view, "download_active", False):
            # Use imported show_info_message utility
            show_info_message(
                self.main_view,
                "Download In Progress",
                "A download is already in progress.",
                "",
            )
            return
        self._update_ui_for_download_start()  # Call internal UI setup
        # self.btn_stop.setEnabled(True) # Already handled by _update_ui_for_download_start
        # Log the playlist object being used for context menu download
        current_playlist_context = getattr(self.main_view, "s_playlist_obj", None)
        # Add more detailed logger for the context object
        logger.debug(
            f"[GUI ContextMenu] Playlist context for download: {current_playlist_context} (type: {type(current_playlist_context)})"
        )
        # Call the function that starts the download thread, passing the captured context
        # Pass the original list of tuples (tracks_with_rows) to __downloadFunc__
        self.__downloadFunc__(tracks_with_rows, playlist_context=current_playlist_context)  # type: ignore # Pass context explicitly

    # Add type hint for pos
    def downloadTableContextMenu(self, menu: QMenu, selected_rows_indices: List[int]):
        """Populates the given context menu with download options for selected tracks."""
        logger.debug("[GUI ContextMenu] downloadTableContextMenu executing.")

        table = cast(QTableWidget, getattr(self.main_view, "c_tableArea").widget())  # type: ignore

        tracks_with_rows: List[Tuple[int, Track]] = []
        menu_title_base = "Download"

        if not selected_rows_indices:
            logger.warning(
                "[GUI ContextMenu] No rows selected for download context menu."
            )
            # Add a disabled "No tracks selected" item?
            no_tracks_action = menu.addAction("No tracks selected for download")
            if no_tracks_action:
                no_tracks_action.setEnabled(False)
            return

        # Iterate through selected rows to get the *actual* Tidal Track objects
        for r_idx in selected_rows_indices:
            tidal_track: Optional[Track] = self._get_tidal_track_from_row(r_idx)
            if tidal_track:
                tracks_with_rows.append((r_idx, tidal_track))
                logger.debug(
                    f"[ContextMenu Multi] Row {r_idx}: Added valid Tidal track {tidal_track.id}"
                )
            else:
                logger.warning(
                    f"[ContextMenu Multi] Row {r_idx}: Skipping - could not retrieve valid Tidal track object."
                )

        if not tracks_with_rows:
            logger.warning(
                "Context menu: No valid Tidal tracks found for download from selection."
            )
            no_valid_tracks_action = menu.addAction(
                "No downloadable tracks in selection"
            )
            if no_valid_tracks_action:
                no_valid_tracks_action.setEnabled(False)
            return

        num_downloadable = len(tracks_with_rows)
        menu_title = f"{menu_title_base} {num_downloadable} Track"
        if num_downloadable > 1:
            menu_title += "s"

        # Determine available quality from the *first valid Tidal track*
        first_track = tracks_with_rows[0][1]
        available_quality_enum = None
        available = "Unknown"
        if hasattr(first_track, "audioQuality"):
            if isinstance(first_track.audioQuality, AudioQuality):
                available_quality_enum = first_track.audioQuality
            else:
                try:
                    available_quality_enum = AudioQuality(
                        str(first_track.audioQuality)
                    )  # Ensure string conversion
                except ValueError:
                    logger.warning(
                        f"Track has unknown audioQuality value: {first_track.audioQuality}"
                    )
                    available_quality_enum = None
            if available_quality_enum:
                available = Printf._map_quality(available_quality_enum)  # type: ignore
        else:
            logger.warning(
                f"Could not determine available quality for first track: {first_track.id if hasattr(first_track, 'id') else first_track}"
            )

        quality_rank = {
            "m4a - aac – high efficiency (96 kbps, 44.1 khz)": 0,
            "m4a - aac – full bandwidth (320 kbps, 44.1 khz)": 1,
            "flac – cd standard (16-bit, 44.1 khz)": 2,
            "flac – high resolution (24-bit, 96 khz)": 3,
            "highest available": 4,
        }

        downloadMenu = menu.addMenu(menu_title)
        if not downloadMenu:  # Should not happen with QMenu, but good practice
            logger.error("Failed to create submenu for download qualities.")
            return

        dlQualities = [
            ("M4a (High Efficiency)", AudioQuality.LOW),
            ("M4a (Full Bandwidth)", AudioQuality.HIGH),
            ("FLAC (CD Standard)", AudioQuality.LOSSLESS),
            ("FLAC (High Resolution)", AudioQuality.HI_RES_LOSSLESS),
            ("Highest Available", AudioQuality.HIGHEST),
        ]

        for text, qual_enum in dlQualities:
            action: Optional[QAction] = downloadMenu.addAction(text)
            if action is None:
                continue

            if qual_enum != AudioQuality.HIGHEST and available not in [
                "Loading...",
                "Error",
                "Unknown",
            ]:
                if available.lower() in quality_rank:
                    mapped_qual_string = Printf._map_quality(qual_enum).lower()  # type: ignore
                    if quality_rank.get(available.lower(), -1) < quality_rank.get(
                        mapped_qual_string, -1
                    ):
                        action.setEnabled(False)
            action.triggered.connect(
                partial(self.startContextMenuDownload, tracks_with_rows, qual_enum)
            )
        # The menu passed in is executed by the caller (TableHandler)

    def _get_tidal_track_from_row(self, row_index: int) -> Optional[Track]:
        """Helper function to extract the Tidal Track object from a table row."""
        table = getattr(self.main_view, "tableWidget", None)
        if not table or row_index >= table.rowCount():
            logger.warning(
                f"[_get_tidal_track_from_row Row {row_index}] Table not found or row index out of bounds."
            )
            return None

        # LINK_STATUS_COLUMN_INDEX = 6 # Not directly used for retrieval logic here
        TITLE_COLUMN_INDEX = 1

        title_item: Optional[QTableWidgetItem] = table.item(
            row_index, TITLE_COLUMN_INDEX
        )
        if not title_item:
            logger.warning(
                f"[_get_tidal_track_from_row Row {row_index}] Title item (column {TITLE_COLUMN_INDEX}) not found."
            )
            return None

        title_data: Optional[Any] = title_item.data(Qt.ItemDataRole.UserRole)
        logger.debug(
            f"[_get_tidal_track_from_row Row {row_index}] Raw title_data (type: {type(title_data)}): {str(title_data)[:500]}"
        )

        tidal_track: Optional[Track] = None

        if isinstance(title_data, dict) and title_data.get("type") == "spotify_track":
            logger.debug(
                f"[_get_tidal_track_from_row Row {row_index}] Identified as Spotify track. Full title_data: {title_data}"
            )
            tidal_track_candidate = title_data.get("tidal_track")
            logger.debug(
                f"[_get_tidal_track_from_row Row {row_index}] 'tidal_track' from title_data: {str(tidal_track_candidate)[:200]} (Type: {type(tidal_track_candidate)})"
            )
            if isinstance(tidal_track_candidate, Track):
                tidal_track = tidal_track_candidate
                logger.info(
                    f"[_get_tidal_track_from_row Row {row_index}] Spotify track, successfully extracted Tidal Track object: ID {tidal_track.id if tidal_track else 'None'}"
                )
            else:
                logger.warning(
                    f"[_get_tidal_track_from_row Row {row_index}] Spotify track, but 'tidal_track' in title_data is not a Track object. Type: {type(tidal_track_candidate)}."
                )

        elif isinstance(title_data, Track):
            tidal_track = title_data
            logger.info(
                f"[_get_tidal_track_from_row Row {row_index}] Identified as direct Tidal track. ID: {tidal_track.id if tidal_track else 'None'}"
            )

        else:
            logger.warning(
                f"[_get_tidal_track_from_row Row {row_index}] title_data is neither a Spotify track dict nor a Tidal Track object. Type: {type(title_data)}."
            )

        # Final check
        if tidal_track is None:
            logger.warning(
                f"[_get_tidal_track_from_row Row {row_index}] tidal_track is None before final return."
            )
            return None
        if not isinstance(
            tidal_track, Track
        ):  # This should catch if it's None or wrong type
            logger.error(
                f"[_get_tidal_track_from_row Row {row_index}] Final check: tidal_track is not a Track object. Type: {type(tidal_track)}. Value: {str(tidal_track)[:200]}"
            )
            return None

        logger.debug(
            f"[_get_tidal_track_from_row Row {row_index}] Successfully returning Track object: ID {tidal_track.id}"
        )
        return tidal_track

    def download(self):
        """Initiates download for selected tracks via the main Download button, deferring execution slightly."""
        logger.debug(
            "[GUI download Button] Clicked. Deferring selection check and start."
        )
        # Defer the actual download logic to allow the event loop to process the click first
        QTimer.singleShot(0, self._start_download_from_selection)

    def _start_download_from_selection(self):
        """Reads selection and starts the download thread. Called via QTimer."""
        logger.debug("[GUI download Button] _start_download_from_selection executing.")
        table = cast(QTableWidget, getattr(self.main_view, "c_tableArea").widget())  # type: ignore # Cast to help Pylance
        if not table:
            logger.error("_start_download_from_selection: Table widget not found.")
            return

        button_text = self.btn_download.text()
        # Change type hint to store tuples (original_row_index, Track)
        items_to_download: List[Tuple[int, Track]] = []
        current_playlist_obj = getattr(
            self.main_view, "s_playlist_obj", None
        )  # Get context

        if button_text == "Download Selected":
            selected_rows_indices: List[int] = cast(
                SplitterTable, table
            ).getSelectedRows()  # Use method
            logger.debug(
                f"[GUI download] 'Download Selected' clicked. Indices: {selected_rows_indices}"
            )
            if not selected_rows_indices:
                show_info_message(self.main_view, "Selection Error", "Please select rows first.", "")  # type: ignore
                return

            for row_index in selected_rows_indices:
                track = self._get_tidal_track_from_row(row_index)  # Use helper
                if track:
                    # Append tuple (original_row_index, Track)
                    items_to_download.append((row_index, track))
                else:
                    logger.warning(
                        f"[Download Selected] Skipping row {row_index} as no valid Tidal track found."
                    )

        elif button_text == "Download All":
            logger.debug("[GUI download] 'Download All' clicked.")
            # Check if it's a Spotify playlist context
            is_spotify_playlist = (
                isinstance(current_playlist_obj, dict)
                and current_playlist_obj.get("type") == "spotify"
            )
            unlinked_tracks_exist = False
            # Change type hint to store tuples (original_row_index, Track)
            linked_tracks_list: List[Tuple[int, Track]] = []

            for row_index in range(table.rowCount()):
                track = self._get_tidal_track_from_row(row_index)  # Use helper
                if track:
                    # Append tuple (original_row_index, Track)
                    linked_tracks_list.append((row_index, track))
                elif (
                    is_spotify_playlist
                ):  # Only flag unlinked if it's a Spotify context
                    # Check if the row *should* have had a track but didn't (i.e., unlinked Spotify)
                    title_item: Optional[QTableWidgetItem] = table.item(row_index, 1)
                    title_data: Optional[Any] = (
                        title_item.data(Qt.ItemDataRole.UserRole)
                        if title_item
                        else None
                    )
                    if (
                        isinstance(title_data, dict)
                        and title_data.get("type") == "spotify_track"
                    ):
                        unlinked_tracks_exist = True
                        logger.debug(
                            f"[Download All] Found unlinked Spotify track at row {row_index}."
                        )

            if is_spotify_playlist and unlinked_tracks_exist:
                msgBox = QMessageBox(self.main_view)  # Set parent
                msgBox.setWindowTitle("Unlinked Tracks Found")
                msgBox.setText(
                    "Some tracks in this playlist aren’t available for download."
                )
                msgBox.setInformativeText(
                    "Would you like to continue and download only the linked tracks?"
                )
                # Use standard buttons with roles
                continueButton = msgBox.addButton(
                    "Continue", QMessageBox.ButtonRole.YesRole
                )
                cancelButton = msgBox.addButton(
                    "Cancel", QMessageBox.ButtonRole.RejectRole
                )
                msgBox.setDefaultButton(continueButton)
                msgBox.setIcon(QMessageBox.Icon.Question)

                reply = msgBox.exec()

                if msgBox.clickedButton() == cancelButton:
                    logger.info(
                        "[Download All] User cancelled download due to unlinked tracks."
                    )
                    return  # User cancelled
                else:  # User clicked Continue
                    logger.info(
                        "[Download All] User chose to continue, downloading only linked tracks."
                    )
                    items_to_download = linked_tracks_list
            else:
                # Either not Spotify or all tracks were linked (or downloadable)
                items_to_download = linked_tracks_list  # Use all found tracks

        else:  # Should not happen
            logger.error(f"Unexpected download button text: '{button_text}'")
            return

        # --- Final checks and start download ---
        logger.debug(
            f"[GUI download] Final items_to_download list (count: {len(items_to_download)}): {[(idx, t.id if t else 'None') for idx, t in items_to_download[:5]]}..."
        )

        if not items_to_download:
            # Message depends on the button clicked
            if button_text == "Download Selected":
                show_info_message(self.main_view, "Selection Error", "Please select valid (and linked, if applicable) rows first.", "")  # type: ignore
            else:  # Download All
                show_info_message(self.main_view, "Download Error", "No downloadable tracks found in the current view.", "")  # type: ignore
            return

        if getattr(self.main_view, "download_active", False):
            show_info_message(self.main_view, "Download In Progress", "A download is already in progress.", "")  # type: ignore
            return

        # --- Start Download State Setup ---
        self._update_ui_for_download_start()
        # --- End Download State Setup ---

        # --- Set s_type correctly ---
        # If downloading tracks (most common case), ensure s_type is Track
        # If the original context was Album/Playlist and "Download All" was clicked,
        # the items_to_download will still be Tracks, so s_type should be Track.
        self.main_view.s_type = Type.Track  # type: ignore
        logger.debug(f"[GUI download] Setting s_type to Type.Track for download.")

        logger.debug(
            f"[GUI download] Passing playlist context to __downloadFunc__: {current_playlist_obj} (type: {type(current_playlist_obj)})"
        )
        self.__downloadFunc__(items_to_download, playlist_context=current_playlist_obj)

    def onPauseResumeClicked(self):
        """Handles clicks on the Pause/Resume button."""
        # Accessing main_view attributes - Pylance might warn due to forward ref
        if not getattr(self.main_view, "download_active", False):
            return

        if getattr(self.main_view, "download_paused", False):
            logger.debug(f"[GUI {time.time():.3f}] Branch: Resuming")
            self.main_view.download_paused = False  # type: ignore
            self.btn_pause_resume.setText("Pause")
            self.btn_pause_resume.setEnabled(True)
            getattr(self.main_view, "pause_event").set()  # type: ignore
            Printf.info("Resume requested.")
        else:
            logger.debug(f"[GUI {time.time():.3f}] Branch: Pausing")
            self.main_view.download_paused = True  # type: ignore
            self.btn_pause_resume.setText("Pausing...")
            self.btn_pause_resume.setEnabled(False)
            getattr(self.main_view, "pause_event").clear()  # type: ignore
            Printf.info("Pause requested. Download will pause after current track.")

    def onStopClicked(self):
        """Handles clicks on the Stop button."""
        # Accessing main_view attributes - Pylance might warn due to forward ref
        if not getattr(self.main_view, "download_active", False):
            return

        if getattr(self.main_view, "stop_requested", False):
            self.main_view.cancel_requested = True  # type: ignore
            getattr(self.main_view, "stop_event").set()  # type: ignore
            self.btn_stop.setText("Cancelling...")
            self.btn_stop.setEnabled(False)
            self.btn_pause_resume.setEnabled(False)
            Printf.info(
                "Immediate cancellation requested (may not be supported mid-track)."
            )
        else:
            self.main_view.stop_requested = True  # type: ignore
            getattr(self.main_view, "stop_event").set()  # type: ignore
            self.btn_stop.setText("Stopping...")
            self.btn_pause_resume.setEnabled(
                False
            )  # Disable pause/resume when stopping
            Printf.info("Stop requested. Finishing current track...")

    # Modify __downloadFunc__ signature to accept playlist_context
    # Modify signature to accept list of tuples
    def __downloadFunc__(
        self,
        items: List[Tuple[int, Track]],
        playlist_context: Optional[Union[Playlist, Album, Dict]] = None,
    ):  # Allow Dict for Spotify playlist
        """Starts the download thread, accepting explicit playlist context."""

        # Pass 'self.main_view' and the explicit playlist_context to the thread target function
        # Modify inner function signature to accept list of tuples
        def __thread_download__(
            main_view_instance: "MainView",
            items_to_download: List[Tuple[int, Track]],
            explicit_playlist_obj: Optional[Union[Playlist, Album, Dict]],
        ):
            logger.debug(
                f"[Thread __downloadFunc__] Received items (tuples): {[(idx, t.id if t else 'None') for idx, t in items_to_download[:5]]} (type: {type(items_to_download)})"
            )
            if not items_to_download:
                logger.warning(
                    "[Thread __downloadFunc__] Received empty items list. Aborting download."
                )
                getattr(main_view_instance, "s_downloadEnd").emit("Download Error", False, "No valid items selected or found.")  # type: ignore
                return

            # Validate items are Track objects before proceeding if expecting tracks
            # If downloading albums/playlists directly, this validation needs adjustment
            # Assuming for now that if s_type is Track, items should be Tracks
            if getattr(main_view_instance, "s_type") == Type.Track:  # type: ignore
                # Validation now happens on the tuple list
                valid_items_with_rows = []
                for (
                    item_tuple
                ) in items_to_download:  # items_to_download is List[Tuple[int, Track]]
                    if (
                        isinstance(item_tuple, tuple)
                        and len(item_tuple) == 2
                        and isinstance(item_tuple[1], Track)
                    ):
                        valid_items_with_rows.append(item_tuple)
                    else:
                        logger.error(
                            f"[Thread __downloadFunc__] Invalid item found: {item_tuple}. Expected Tuple[int, Track]."
                        )
                if not valid_items_with_rows:
                    logger.error(
                        "[Thread __downloadFunc__] No valid Tuple[int, Track] items found in list."
                    )
                    getattr(main_view_instance, "s_downloadEnd").emit("Download Error", False, "Internal error: Invalid item types for download.")  # type: ignore
                    return
                items_to_process = (
                    valid_items_with_rows  # Use the validated list of tuples
                )
            else:
                # This branch might need adjustment if non-track downloads also need row indices
                logger.warning(
                    "[Thread __downloadFunc__] Processing non-track type download. Row index mapping might be incorrect."
                )
                # Assuming items_to_download is still List[Tuple[int, Any]]? Or just List[Any]?
                # For now, let's assume it's still tuples if it gets here, but this needs review if used.
                items_to_process = items_to_download  # This line might need changing if non-track types are tuples

            logger.debug("[Thread] __thread_download__ started")
            try:
                # Accessing main_view attributes - Pylance might warn due to forward ref
                selected_quality_enum = getattr(main_view_instance, "c_combTQuality").currentData()  # type: ignore
                quality_arg_str: Optional[str] = None
                if selected_quality_enum != AudioQuality.HIGHEST:
                    quality_arg_str = Printf._map_quality(selected_quality_enum)  # type: ignore
                Printf.info(
                    f"Download triggered. Target quality enum: {selected_quality_enum.name}, Passing downloadQuality='{quality_arg_str}'"
                )

                current_playlist_obj = (
                    explicit_playlist_obj  # Use the passed object directly
                )
                logger.debug(
                    f"[Thread] Playlist context inside thread (explicitly passed): {current_playlist_obj} (type: {type(current_playlist_obj)})"
                )

                # --- Modified Download Loop ---
                if getattr(main_view_instance, "s_type") == Type.Track:  # type: ignore
                    Printf.info(
                        f"Downloading {len(items_to_process)} selected tracks. Effective quality arg: {quality_arg_str}"
                    )
                    # Modify loop to unpack the tuple
                    for original_row_index, track_item in items_to_process:
                        # === Pause/Stop Check (Inside Loop) ===
                        if main_view_instance.stop_event.is_set():
                            Printf.info(
                                "Stop request detected. Aborting download queue."
                            )
                            break  # Exit the loop
                        if main_view_instance.download_paused:
                            Printf.info(
                                "Download queue paused. Waiting for resume signal..."
                            )
                            main_view_instance.signal_actually_paused.emit()
                            main_view_instance.pause_event.wait()
                            Printf.info("Download queue resumed.")
                            if (
                                main_view_instance.stop_event.is_set()
                            ):  # Re-check stop after pause
                                Printf.info(
                                    "Stop request detected after pause. Aborting download queue."
                                )
                                break
                        # === End Pause/Stop Check ===

                        # Use track_item from the loop
                        if not isinstance(track_item, Track) or track_item.id is None:
                            logger.warning(
                                f"Skipping invalid track item at original row {original_row_index}: {track_item}"
                            )
                            continue

                        # --- START: Get Track ID ---
                        track_id_str = str(track_item.id)  # Use track_item
                        table_handler = getattr(
                            main_view_instance, "table_handler", None
                        )
                        # --- END: Get Track ID ---

                        # Create and connect progress handler for this specific track
                        progress_handler = TrackProgressHandler(track_id=track_id_str)
                        if (
                            table_handler
                            and hasattr(table_handler, "update_track_progress")
                            and hasattr(
                                table_handler, "setup_progress_bar_for_download"
                            )
                        ):  # Check for new slot
                            progress_handler.setupProgressBarUI.connect(
                                table_handler.setup_progress_bar_for_download
                            )  # Connect NEW signal
                            progress_handler.progressUpdated.connect(
                                table_handler.update_track_progress
                            )
                            logger.debug(
                                f"Connected progress handler signals for track {track_id_str}"
                            )
                        else:
                            logger.error(
                                f"Could not connect progress handler signals for track {track_id_str}: table_handler or required slots not found."
                            )
                            progress_handler = None  # Don't pass if connection failed

                        # --- MODIFICATION: Emit signal to setup UI BEFORE download ---
                        if (
                            progress_handler
                        ):  # Only emit if handler was successfully set up
                            logger.debug(
                                f"DOWNLOAD THREAD: Emitting setupProgressBarUI for track {track_id_str}, row {original_row_index}"
                            )
                            progress_handler.setupProgressBarUI.emit(
                                track_id_str, original_row_index
                            )

                        # --- START: Set Progress Bar Widget in Table (Using original_row_index) ---
                        # THIS BLOCK WILL BE MOVED TO THE NEW SLOT IN TableHandler
                        # For now, we comment it out or remove it from here, as the signal will handle it.
                        # However, the original analysis suggested keeping the dictionary population logic
                        # in update_linking_status and _populate_table_generic.
                        # The setCellWidget part is what moves.
                        # For clarity, I will comment out the direct setCellWidget call here.
                        # The actual progress bar container should still be prepared by TableHandler.update_linking_status
                        # or TableHandler._populate_table_generic.

                        # --- START REMOVAL OF OBSOLETE BLOCK ---
                        # The following block that tries to access table_handler.progress_containers
                        # and setCellWidget directly from the thread is now obsolete and causes the error.
                        # It has been removed. The setupProgressBarUI signal handles this on the main thread.
                        # --- END REMOVAL OF OBSOLETE BLOCK ---

                        # Use original_row_index for logging if needed, or just track title/ID
                        # Corrected logging to use track_item and original_row_index
                        logger.debug(
                            f"Initiating download for track: {track_item.title} (ID: {track_id_str}) at original row {original_row_index}"
                        )
                        # Call core_downloadTrack directly, passing the handler and track_item
                        core_downloadTrack(
                            track=track_item,  # Use track_item
                            main_view_instance=main_view_instance,
                            album=None,
                            playlist_context=current_playlist_obj,
                            userProgress=progress_handler,  # Pass the specific handler
                            downloadQuality=quality_arg_str,
                        )
                        # Disconnect handler after download attempt to prevent memory leaks?
                        # if progress_handler and table_handler and hasattr(table_handler, 'update_track_progress'):
                        #     try:
                        #         progress_handler.progressUpdated.disconnect(table_handler.update_track_progress)
                        #         logger.debug(f"Disconnected progress handler for track {item.id}")
                        #     except TypeError: # Signal might not be connected if slot was missing
                        #         logger.warning(f"Could not disconnect progress handler for track {item.id}, might not have been connected.")

                else:  # Album, Artist
                    Printf.info(f"Downloading selected {getattr(main_view_instance, 's_type').name}(s) (quality '{quality_arg_str}' applies if relevant in start_type)")  # type: ignore
                    for item in items_to_process:
                        # Need to check stop/pause here too
                        start_type(getattr(main_view_instance, "s_type"), item)  # type: ignore # start_type likely needs main_view_instance for pause/stop too
                # --- End Original Logic ---

                logger.debug(
                    "[Thread] Finished processing items. Emitting s_downloadEnd"
                )
                # Emit download end signal based on final state
                if getattr(main_view_instance, "stop_requested", False) or getattr(
                    main_view_instance, "cancel_requested", False
                ):
                    status_msg = (
                        "Download cancelled by user."
                        if getattr(main_view_instance, "cancel_requested", False)
                        else "Download stopped by user."
                    )
                    status_title = (
                        "Download Cancelled"
                        if getattr(main_view_instance, "cancel_requested", False)
                        else "Download Stopped"
                    )
                    getattr(main_view_instance, "s_downloadEnd").emit(
                        status_title, False, status_msg, None
                    )
                else:
                    final_path = None
                    if items_to_process:
                        # Get the first track object from the download list
                        first_track_item = items_to_process[0][1]

                        # Determine the highest-level context for the path
                        if explicit_playlist_obj:
                            if isinstance(explicit_playlist_obj, Album):
                                # Get artist names for path generation
                                album_artists = getattr(
                                    explicit_playlist_obj, "artists", []
                                )
                                artist_name_str = ", ".join(
                                    [
                                        artist.name
                                        for artist in album_artists
                                        if artist and hasattr(artist, "name")
                                    ]
                                )
                                album_artist_name_str = (
                                    (album_artists[0].name)
                                    if (
                                        album_artists
                                        and hasattr(album_artists[0], "name")
                                    )
                                    else artist_name_str
                                )
                                final_path = getAlbumPath(
                                    explicit_playlist_obj,
                                    artist_name_str,
                                    album_artist_name_str,
                                    "",
                                )
                            elif isinstance(explicit_playlist_obj, Playlist):
                                final_path = getPlaylistPath(explicit_playlist_obj)
                            else:  # Handle Spotify dict case
                                final_path = getPlaylistPath(explicit_playlist_obj)

                        # If no playlist/album context, use the track's album
                        if not final_path:
                            track_album = getattr(first_track_item, "album", None)
                            if track_album:
                                # Get artist names for path generation
                                album_artists = getattr(track_album, "artists", [])
                                artist_name_str = ", ".join(
                                    [
                                        artist.name
                                        for artist in album_artists
                                        if artist and hasattr(artist, "name")
                                    ]
                                )
                                album_artist_name_str = (
                                    (album_artists[0].name)
                                    if (
                                        album_artists
                                        and hasattr(album_artists[0], "name")
                                    )
                                    else artist_name_str
                                )
                                final_path = getAlbumPath(
                                    track_album,
                                    artist_name_str,
                                    album_artist_name_str,
                                    "",
                                )

                        # If still no path, construct it from the track itself (will save in 'Tracks' folder)
                        if not final_path:
                            # Get artist names for path generation
                            track_artists_list = getattr(
                                first_track_item, "artists", []
                            )
                            artists_str = ", ".join(
                                [
                                    artist.name
                                    for artist in track_artists_list
                                    if artist and hasattr(artist, "name")
                                ]
                            )
                            artist_str = (
                                getattr(
                                    getattr(first_track_item, "artist", None),
                                    "name",
                                    "",
                                )
                                or artists_str
                            )
                            final_path = getTrackPath(
                                first_track_item, None, artist_str, artists_str
                            )  # Get full file path
                            if final_path:
                                final_path = os.path.dirname(
                                    final_path
                                )  # Get parent dir

                    # Final fallback to settings download path
                    if not final_path or not os.path.isdir(final_path):
                        final_path = SETTINGS.downloadPath

                    getattr(main_view_instance, "s_downloadEnd").emit(
                        "Download Success!", True, "", final_path
                    )

            except Exception as e:
                logger.error(
                    "[Thread] Exception caught in __thread_download__", exc_info=True
                )
                Printf.err(f"Error in download thread: {traceback.format_exc()}")
                getattr(main_view_instance, "s_downloadEnd").emit("Download Error", False, str(e), None)  # type: ignore

        # Store thread reference in MainView
        # Pass the explicit playlist_context to the thread target function
        # Accessing main_view attributes - Pylance might warn due to forward ref
        self.main_view.download_thread = threading.Thread(target=__thread_download__, args=(self.main_view, items, playlist_context))  # type: ignore
        getattr(self.main_view, "download_thread").start()  # type: ignore

    # Add type hints for parameters
    def downloadEnd(
        self, title: str, result: bool, msg: str, path: Optional[str] = None
    ):
        """Handles the end of a download operation (called via signal)."""
        logger.debug(
            f"[GUI] Entering downloadEnd: title='{title}', result={result}, msg='{msg}', path='{path}'"
        )
        # Accessing main_view attributes - Pylance might warn due to forward ref
        logger.debug(
            f"[GUI] State before reset: active={getattr(self.main_view, 'download_active', 'N/A')}, paused={getattr(self.main_view, 'download_paused', 'N/A')}, stop_req={getattr(self.main_view, 'stop_requested', 'N/A')}, cancel_req={getattr(self.main_view, 'cancel_requested', 'N/A')}"
        )

        # Reset state variables in MainView
        self.main_view.download_active = False  # type: ignore
        self.main_view.download_paused = False  # type: ignore
        self.main_view.stop_requested = False  # type: ignore
        self.main_view.cancel_requested = False  # type: ignore
        self.main_view.download_thread = None  # type: ignore

        # Reset UI elements using stored references
        self.button_stack.setCurrentIndex(0)  # Show Download button
        self.btn_download.setEnabled(True)
        self.btn_pause_resume.setText("Pause")  # Reset text
        self.btn_pause_resume.setEnabled(True)  # Re-enable
        self.btn_stop.setText("Stop")  # Reset text
        self.btn_stop.setEnabled(True)  # Re-enable

        # Update button text based on current selection state AFTER download finishes
        self._update_download_button_text()

        logger.debug(
            f"[GUI] Exiting downloadEnd: active={getattr(self.main_view, 'download_active', 'N/A')}, paused={getattr(self.main_view, 'download_paused', 'N/A')}, stop_req={getattr(self.main_view, 'stop_requested', 'N/A')}, cancel_req={getattr(self.main_view, 'cancel_requested', 'N/A')}"
        )

        # --- Restore original table view if download was initiated from context menu ---
        # Accessing main_view attributes - Pylance might warn due to forward ref
        original_array = getattr(
            self.main_view, "_original_s_array_before_ctx_dl", None
        )
        original_type = getattr(self.main_view, "_original_s_type_before_ctx_dl", None)
        if original_array is not None:
            logger.debug(
                f"[GUI downloadEnd] Restoring original table view with {len(original_array)} items of type {original_type}"
            )
            # Add logger to inspect the saved state before restoring
            logger.debug(
                f"[GUI downloadEnd] _original_s_array_before_ctx_dl (first 5 items): {original_array[:5]}"
            )
            logger.debug(
                f"[GUI downloadEnd] _original_s_type_before_ctx_dl: {original_type}"
            )

            self.main_view.s_array = original_array  # type: ignore
            self.main_view.s_type = original_type  # type: ignore
            self.main_view.s_playlist = original_type == Type.Playlist  # type: ignore
            logger.debug(f"[GUI downloadEnd] Calling setSearchResults with s_array (len={len(self.main_view.s_array)}) and s_type={self.main_view.s_type}")  # type: ignore
            # Accessing main_view attributes - Pylance might warn due to forward ref
            playlist_obj = getattr(self.main_view, "s_playlist_obj", None)
            playlist_id = (
                getattr(playlist_obj, "id", None)
                if isinstance(playlist_obj, dict)
                else getattr(playlist_obj, "uuid", None)
            )
            getattr(self.main_view, "table_handler")._populate_table_generic(self.main_view.s_array, self.main_view.s_type, playlist_id=playlist_id)  # type: ignore

            # Clean up temporary variables in MainView
            del self.main_view._original_s_array_before_ctx_dl  # type: ignore
            del self.main_view._original_s_type_before_ctx_dl  # type: ignore

        # Show appropriate message based on outcome using the imported utility function
        if result:
            info_icon_path = resource_path("assets/icons/success_icon.png")
            custom_dialog = ModernDarkDialog(
                title="Info",
                main_message="Download finished successfully.",
                informative_text=f"Saved to: {path if path else 'N/A'}",
                icon_path=info_icon_path,
                parent=self.main_view,
                show_folder_path=path,
                show_in_folder_func=show_in_folder,
            )
            custom_dialog.exec()
        elif title == "Download Stopped" or title == "Download Cancelled":
            show_info_message(self.main_view, title, msg, "")
        elif title == "Download Error":
            show_info_message(
                self.main_view, "Download Error", f"Download failed: {msg}", ""
            )
        else:
            show_info_message(
                self.main_view, "Download Failed", f"Download failed: {msg}", ""
            )


# --- END OF FILE gui_download.py ---
