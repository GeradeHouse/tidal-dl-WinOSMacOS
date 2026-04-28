# tidal_dl/gui/gui_linking_handler.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_linking_handler.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.6
@Desc    :   Handles GUI interactions related to Spotify track linking.
"""

import logging
from typing import List, Optional, Dict, TYPE_CHECKING, Any, Tuple
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import (
    QObject,
    pyqtSignal,
    pyqtSlot,
    QPoint,
    Qt,
    QItemSelection,
    QItemSelectionModel,
)
from ..model import Track, Playlist
from ..tidal import TidalAPI
from ..printf import Printf
from ..persistence import LinkPersistenceManager
from .gui_custom_dialog import CustomQMessageBox

if TYPE_CHECKING:
    from tidal_dl.gui.gui_main import MainView
    from tidal_dl.gui.gui_table_handler import TableHandler
    from tidal_dl.gui.gui_table import SplitterTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

# Set up GUI logging with INFO level for this module
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class LinkingGuiHandler(QObject):
    """
    Handles interactions between the MainView GUI and the track linking logic.
    Manages context menus, starting the linking process, and updating the UI.
    """

    requestLinkingStart = pyqtSignal(
        list, str, object  # tracks_to_link_data, playlist_id, callback
    )
    manualLinkApplied = pyqtSignal(
        int
    )  # Signal to notify MainView that a manual link has been applied
    linkProgress = pyqtSignal(str, int) # playlist_id, count
    
    # --- NEW SIGNALS for Tree Widget Progress ---
    linkingStarted = pyqtSignal(str, str, int) # playlist_id, action, total
    linkingFinished = pyqtSignal(str) # playlist_id
    playlistQueued = pyqtSignal(str) # playlist_id

    def __init__(
        self,
        api: TidalAPI,
        table_handler: "TableHandler",
        persistence_manager: LinkPersistenceManager,
        link_button: QtWidgets.QPushButton,
        parent: Optional["MainView"] = None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.table_handler = table_handler
        self.persistence_manager = persistence_manager
        self.link_button = link_button
        self.main_view = parent
        
        # Per-playlist counters to prevent cross-contamination
        self._processed_counters: Dict[str, int] = {}
        self._total_counts: Dict[str, int] = {} # Track total items to detect completion
        # Track which playlist is being processed
        self._current_processing_playlist_id: Optional[str] = None
        
        # Connect manual link application to sub-row collapse
        self.manualLinkApplied.connect(self.table_handler.collapse_sub_row)

        # --- Connect Link Button Click (Persistent Connection) ---
        self.link_button.clicked.connect(self._handle_link_button_click)
        
        # --- Connect Internal Signal to Capture State from External Triggers ---
        self.linkingStarted.connect(self._on_linking_started_internal)
        
        logger.debug(
            "Connected link_button clicked signal to _handle_link_button_click"
        )

    @pyqtSlot(str, str, int)
    def _on_linking_started_internal(self, playlist_id: str, action: str, total: int):
        """
        Internal slot to capture the start of a linking job, whether triggered internally
        or externally (e.g., by TaskQueueManager). Initializes counters.
        """
        logger.info(f"Linking job started for playlist {playlist_id}. Total tracks: {total}")
        self._processed_counters[playlist_id] = 0
        self._total_counts[playlist_id] = total
        
        # Set the current processing ID so table updates occur if this playlist is viewed
        self._current_processing_playlist_id = playlist_id

    def _get_current_table_playlist_id(self) -> Optional[str]:
        """
        Returns the ID of the playlist currently displayed in the main table.
        Used to prevent cross-talk where linking updates for one playlist
        appear on the table of another.
        """
        if not self.main_view:
            return None
        
        # s_playlist_obj is set in PlaylistTreeHandler.onPlaylistItemClicked
        # and represents the playlist currently loaded in the view/table.
        playlist_obj = getattr(self.main_view, "s_playlist_obj", None)
        
        if isinstance(playlist_obj, dict):
            # Spotify playlist data structure
            return playlist_obj.get("data", {}).get("id")
        elif isinstance(playlist_obj, Playlist):
            # Tidal playlist object
            return getattr(playlist_obj, "uuid", None)
        
        return None

    def _get_linking_status_from_row(
        self, row_index: int
    ) -> Tuple[Optional[str], bool]:
        """
        Helper to get the linking status and candidate status from a table row's metadata.
        """
        if (
            not self.main_view
            or not self.table_handler
            or not self.table_handler.table_widget
        ):
            return None, False

        table = self.table_handler.table_widget
        if row_index >= table.rowCount():
            return None, False

        # Link status is stored in the Title column's data (column 1)
        title_item = table.item(row_index, 1)
        title_data = (
            title_item.data(QtCore.Qt.ItemDataRole.UserRole) if title_item else {}
        )
        link_status = (
            title_data.get("link_status") if isinstance(title_data, dict) else None
        )

        # Candidate status is stored in column 0 (Indicator)
        indicator_item = table.item(row_index, 0)
        indicator_data = (
            indicator_item.data(QtCore.Qt.ItemDataRole.UserRole)
            if indicator_item
            else {}
        )
        has_candidates = (
            indicator_data.get("has_candidates", False)
            if isinstance(indicator_data, dict)
            else False
        )

        return link_status, has_candidates

    def update_link_button_state(self) -> None:
        """
        Updates the visibility and text of the 'Link Tracks' button.
        """
        if (
            not self.main_view
            or not self.table_handler
            or not self.table_handler.table_widget
        ):
            self.link_button.setVisible(False)
            return

        table = self.table_handler.table_widget
        current_playlist_obj = getattr(self.main_view, "s_playlist_obj", None)
        is_spotify_playlist_selected = (
            isinstance(current_playlist_obj, dict)
            and current_playlist_obj.get("type") == "spotify"
        )

        # --- Visibility ---
        self.link_button.setVisible(is_spotify_playlist_selected)

        if not is_spotify_playlist_selected:
            return

        # --- Text Logic ---
        selected_indices = table.getSelectedRows()
        button_text = "Link Tracks"

        if selected_indices:
            any_selected_linked = False
            num_selected = len(selected_indices)
            for row_index in selected_indices:
                link_status, _ = self._get_linking_status_from_row(row_index)
                if link_status in ["found", "cached_linked", "manual_linked", "auto_linked", "found_uncertain"]:
                    any_selected_linked = True
                    break

            if any_selected_linked:
                button_text = f"Relink {num_selected} Selected"
            else:
                button_text = f"Link {num_selected} Selected"
        else:
            any_all_linked = False
            num_total = table.rowCount()
            if num_total > 0:
                for row_index in range(num_total):
                    link_status, _ = self._get_linking_status_from_row(row_index)
                    if link_status in ["found", "cached_linked", "manual_linked", "auto_linked", "found_uncertain"]:
                        any_all_linked = True
                        break

                if any_all_linked:
                    button_text = "Relink All Tracks"
                else:
                    button_text = "Link All Tracks"
            else:
                button_text = "Link All Tracks"

        self.link_button.setText(button_text)
        self.link_button.setEnabled(
            not getattr(self.main_view, "linking_active", False)
        )

    @pyqtSlot(QtWidgets.QMenu, list)
    def spotifyLinkContextMenu(
        self, menu: QtWidgets.QMenu, selected_rows_indices: List[int]
    ) -> None:
        """Populates the given context menu with actions for linking Spotify tracks."""
        if not self.main_view:
            return

        any_selected_linked = False
        all_selected_linked_or_error = True

        valid_linked_statuses = [
            "found",
            "auto_linked",
            "manual_linked",
            "cached_linked",
            "found_uncertain",
            "cached_linked_full",
            "cached_linked_id_fetched",
        ]
        error_statuses = [
            "error_fetching_tidal",
            "error_no_match",
            "error_api_failed",
            "error_rate_limited",
        ]

        if selected_rows_indices:
            for row_index in selected_rows_indices:
                link_status, _ = self._get_linking_status_from_row(row_index)
                if link_status in valid_linked_statuses:
                    any_selected_linked = True
                elif link_status not in error_statuses:
                    all_selected_linked_or_error = False

            if not any_selected_linked and not all_selected_linked_or_error:
                pass
            elif not all_selected_linked_or_error:
                any_selected_linked = True

        num_selected = len(selected_rows_indices)

        if any_selected_linked:
            action_text_link = f"Relink/Link {num_selected} selected Spotify track(s)"
        else:
            action_text_link = f"Link {num_selected} selected Spotify track(s) to Tidal"

        linkAction = menu.addAction(action_text_link)
        if linkAction:
            linkAction.setEnabled(bool(selected_rows_indices))
            linkAction.triggered.connect(
                lambda: self.startLinkingSelectedTracks(selected_rows_indices)
            )

        if any_selected_linked:
            menu.addSeparator()
            unlinkAction = menu.addAction(
                f"Unlink {num_selected} selected Spotify track(s)"
            )
            if unlinkAction:
                unlinkAction.setEnabled(True)
                unlinkAction.triggered.connect(lambda: self.unlinkSelectedTracks(selected_rows_indices))

    def startLinkingSelectedTracks(self, selected_rows_indices: List[int]) -> None:
        """Initiates the track linking process for selected rows."""
        if not self.main_view:
            return
        if self.main_view.linking_active:
            logger.warning("Attempted to start linking while already active.")
            return

        logger.info(
            f"Initiating linking for {len(selected_rows_indices)} selected Spotify track(s)..."
        )

        tracks_to_link_data = []
        table = self.main_view.tableWidget
        for row_index in selected_rows_indices:
            item_widget = table.item(row_index, 1)
            item_data = (
                item_widget.data(QtCore.Qt.ItemDataRole.UserRole)
                if item_widget
                else None
            )
            if isinstance(item_data, dict) and item_data.get("type") == "spotify_track":
                spotify_metadata = item_data.get("data")
                if spotify_metadata and all(
                    k in spotify_metadata for k in ("name", "artists", "album", "id")
                ):
                    tracks_to_link_data.append((row_index, spotify_metadata))
                else:
                    logger.warning(f"Skipping row {row_index} due to missing metadata.")

        if not tracks_to_link_data:
            CustomQMessageBox.information(
                self.main_view,
                "Selection Error",
                "No Valid Tracks",
                "No valid Spotify tracks were found in your selection."
            )
            return

        # Store the playlist ID when linking starts
        playlist_id = None
        if self.main_view and isinstance(self.main_view.s_playlist_obj, dict):
            playlist_id = self.main_view.s_playlist_obj.get("data", {}).get("id")
            self._current_processing_playlist_id = playlist_id
        
        if playlist_id:
            # Initialize counters and emit start signal to setup progress bar
            self._processed_counters[playlist_id] = 0
            self._total_counts[playlist_id] = len(tracks_to_link_data)
            self.linkingStarted.emit(playlist_id, "Linking", len(tracks_to_link_data))
            
        self.requestLinkingStart.emit(tracks_to_link_data, playlist_id, None)

    def linkAllSpotifyTracks(self) -> None:
        """Initiates the track linking process for ALL Spotify tracks currently in the table."""
        if not self.main_view:
            return
        if self.main_view.linking_active:
            logger.warning("Attempted to start linking while already active.")
            return

        logger.info("Initiating linking for all Spotify tracks...")

        tracks_to_link_data = []
        table = self.main_view.tableWidget
        for row_index in range(table.rowCount()):
            item_widget = table.item(row_index, 1)
            item_data = (
                item_widget.data(QtCore.Qt.ItemDataRole.UserRole)
                if item_widget
                else None
            )
            if isinstance(item_data, dict) and item_data.get("type") == "spotify_track":
                spotify_metadata = item_data.get("data")
                if spotify_metadata and all(
                    k in spotify_metadata for k in ("name", "artists", "album", "id")
                ):
                    tracks_to_link_data.append((row_index, spotify_metadata))

        if not tracks_to_link_data:
            CustomQMessageBox.information(
                self.main_view,
                "No Tracks to Link",
                "No Spotify Tracks Found",
                "No Spotify tracks were found in the current table."
            )
            return

        # Store the playlist ID when linking starts
        playlist_id = None
        if self.main_view and isinstance(self.main_view.s_playlist_obj, dict):
            playlist_id = self.main_view.s_playlist_obj.get("data", {}).get("id")
            self._current_processing_playlist_id = playlist_id
        
        if playlist_id:
            # Initialize counters and emit start signal to setup progress bar
            self._processed_counters[playlist_id] = 0
            self._total_counts[playlist_id] = len(tracks_to_link_data)
            self.linkingStarted.emit(playlist_id, "Linking", len(tracks_to_link_data))
            
        self.requestLinkingStart.emit(tracks_to_link_data, playlist_id, None)

    @pyqtSlot()
    def _handle_link_button_click(self):
        """
        Determines whether to link all or selected tracks based on table selection.
        """
        if (
            not self.main_view
            or not self.table_handler
            or not self.table_handler.table_widget
        ):
            return

        table = self.table_handler.table_widget
        selected_indices = table.getSelectedRows()

        if selected_indices:
            self.startLinkingSelectedTracks(selected_indices)
        else:
            self.linkAllSpotifyTracks()

    @pyqtSlot(int, dict)
    def onLinkingStarted(self, row_index: int, spotify_data: dict) -> None:
        """Slot called when linking starts for a specific row."""
        try:
            if not self.main_view:
                return
            
            # Check context before updating UI
            current_table_id = self._get_current_table_playlist_id()
            if current_table_id != self._current_processing_playlist_id:
                return

            # Robustly find the row index using the Spotify ID
            spotify_id = spotify_data.get('id')
            sid_str = str(spotify_id) if spotify_id else None
            actual_row = self.table_handler._find_row_for_spotify_id(sid_str)
            
            if actual_row is not None:
                self.table_handler.update_linking_status(actual_row, "linking", "Linking...")
                logger.info(f"Linking started for row {actual_row + 1} (ID: {spotify_id})...")
            else:
                logger.warning(f"Could not find row for Spotify ID {spotify_id} to update status.")

        except Exception as e:
            logger.error(
                f"Error updating linking status for row {row_index}: {e}", exc_info=True
            )

    @pyqtSlot(int, object, object, object, object)
    def onLinkingFinished(
        self,
        row_index: int,
        tidal_track: Optional[Track],
        candidates: Optional[List[Dict]],
        score: Optional[int],
        spotify_data: Optional[Dict] = None,
    ) -> None:
        """Slot called when linking finishes for a specific row."""
        if not self.main_view:
            return

        status_text = ""
        link_status = "not_linked"

        if tidal_track:
            if score is not None and score <= 1:
                status_text = f"Linked (Certainty score: {score}): {tidal_track.id}"
                link_status = "auto_linked"
            elif score is not None and score > 1:
                if candidates:
                    status_text = f"Manual linking required (Certainty score: {score}): {tidal_track.id}"
                    link_status = "manual_review_needed"
                else:
                    status_text = f"Linked (Uncertain, Score: {score}): {tidal_track.id}"
                    link_status = "found_uncertain"
            else:
                status_text = f"Linked: {tidal_track.id}"
                link_status = "auto_linked"

        elif candidates:
            status_text = "Manual linking required (Candidates available)"
            link_status = "candidates_only"
        else:
            status_text = "Not Found"
            link_status = "not_found"

        # Check context before updating UI
        current_table_id = self._get_current_table_playlist_id()
        if current_table_id == self._current_processing_playlist_id:
            # Robustly find the row index using the Spotify ID
            actual_row = row_index
            if spotify_data:
                spotify_id = spotify_data.get('id')
                sid_str = str(spotify_id) if spotify_id else None
                found_row = self.table_handler._find_row_for_spotify_id(sid_str)
                if found_row is not None:
                    actual_row = found_row
            
            self.table_handler.update_linking_status(
                row_index=actual_row,
                status=link_status,
                status_text=status_text,
                tidal_track=tidal_track,
                candidates=candidates,
                score=score,
            )
            logger.info(f"Linking finished for row {actual_row + 1}: {status_text}")

        # --- Persist Link ---
        should_persist = link_status not in ["not_linked", "linking", "error"]
        
        playlist_id = self._current_processing_playlist_id
        if not playlist_id and self.main_view and isinstance(self.main_view.s_playlist_obj, dict):
            playlist_id = self.main_view.s_playlist_obj.get("data", {}).get("id")

        if should_persist and playlist_id:
            spotify_track_id = None
            spotify_metadata = {}
            
            if spotify_data:
                spotify_metadata = spotify_data
                spotify_track_id = spotify_data.get("id")
            else:
                # Fallback to table scraping ONLY if we are viewing the correct playlist
                if current_table_id == playlist_id:
                    table_widget = self.main_view.tableWidget
                    title_item = table_widget.item(row_index, 1)
                    title_item_data = (
                        title_item.data(QtCore.Qt.ItemDataRole.UserRole) if title_item else None
                    )
                    if (
                        isinstance(title_item_data, dict)
                        and title_item_data.get("type") == "spotify_track"
                    ):
                        spotify_metadata = title_item_data.get("data", {})
                        spotify_track_id = spotify_metadata.get("id")

            if spotify_track_id:
                pm = getattr(self.main_view, "link_persistence_manager", None)
                if pm:
                    pm.add_or_update_link(
                        playlist_id=playlist_id,
                        spotify_track_id=spotify_track_id,
                        spotify_track_details=spotify_metadata,
                        tidal_track_object=tidal_track,
                        candidates=candidates if candidates else None,
                        score=score
                    )
        # --- End Persist Link ---

        # Update progress counters and check for completion
        processing_playlist_id = self._current_processing_playlist_id
        if processing_playlist_id:
            current_count = self._processed_counters.get(processing_playlist_id, 0) + 1
            self._processed_counters[processing_playlist_id] = current_count
            self.linkProgress.emit(str(processing_playlist_id), current_count)
            
            # Check if all items are processed
            total = self._total_counts.get(processing_playlist_id, 0)
            if total > 0 and current_count >= total:
                logger.info(f"Playlist {processing_playlist_id} linking completed ({current_count}/{total}). Cleaning up.")
                self.linkingFinished.emit(str(processing_playlist_id))
                self._current_processing_playlist_id = None
                self.update_link_button_state()
                if self.main_view and hasattr(self.main_view, "download_handler") and self.main_view.download_handler:
                    self.main_view.download_handler._update_download_button_text()

    @pyqtSlot(int, str, dict)
    def onLinkingError(self, row_index: int, error_message: str, spotify_data: dict) -> None:
        """Slot called when an error occurs during linking for a specific row."""
        try:
            if not self.main_view:
                return
            
            current_table_id = self._get_current_table_playlist_id()
            if current_table_id == self._current_processing_playlist_id:
                # Robustly find the row index using the Spotify ID
                actual_row = row_index
                if spotify_data:
                    spotify_id = spotify_data.get('id')
                    sid_str = str(spotify_id) if spotify_id else None
                    found_row = self.table_handler._find_row_for_spotify_id(sid_str)
                    if found_row is not None:
                        actual_row = found_row

                self.table_handler.update_linking_status(
                    row_index=actual_row, status="error", status_text=f"Error: {error_message}", error_message=error_message
                )
                logger.error(f"Linking error for row {actual_row + 1}: {error_message}")
            
        except Exception as e:
            logger.error(
                f"Error updating linking error status for row {row_index}: {e}",
                exc_info=True,
            )
        
        # Update progress counters and check for completion
        processing_playlist_id = self._current_processing_playlist_id
        if processing_playlist_id:
            current_count = self._processed_counters.get(processing_playlist_id, 0) + 1
            self._processed_counters[processing_playlist_id] = current_count
            self.linkProgress.emit(str(processing_playlist_id), current_count)

            # Check if all items are processed
            total = self._total_counts.get(processing_playlist_id, 0)
            if total > 0 and current_count >= total:
                logger.info(f"Playlist {processing_playlist_id} linking completed ({current_count}/{total}) [Error path]. Cleaning up.")
                self.linkingFinished.emit(str(processing_playlist_id))
                self._current_processing_playlist_id = None
                self.update_link_button_state()
                if self.main_view and hasattr(self.main_view, "download_handler") and self.main_view.download_handler:
                    self.main_view.download_handler._update_download_button_text()

    @pyqtSlot()
    def onAllLinkingTasksFinished(self) -> None:
        """Slot called when the LinkingWorker has processed all tracks."""
        logger.info("All linking tasks finished.")
        
        # Capture ID before clearing
        finished_playlist_id = self._current_processing_playlist_id

        # Clean up the processing playlist ID if not already done
        if self._current_processing_playlist_id:
            self._current_processing_playlist_id = None
        
        # Emit finished signal to clean up progress bar if not already done
        if finished_playlist_id:
            self.linkingFinished.emit(finished_playlist_id)
        
        self.update_link_button_state()

        if (
            self.main_view
            and hasattr(self.main_view, "download_handler")
            and self.main_view.download_handler
        ):
            self.main_view.download_handler._update_download_button_text()

    @pyqtSlot()
    def onStopLinkingClicked(self):
        """Handles clicks on the 'Stop Linking' button."""
        if not self.main_view:
            return
        if self.main_view.linking_active:
            logger.info("Stop linking requested by user.")
            
            # Capture ID before clearing
            stopped_playlist_id = self._current_processing_playlist_id

            if self._current_processing_playlist_id:
                self._current_processing_playlist_id = None
            
            # Emit finished signal to clean up progress bar
            if stopped_playlist_id:
                self.linkingFinished.emit(stopped_playlist_id)
            
            self.main_view.linking_stop_event.set()
            self.link_button.setText("Stopping...")
            self.link_button.setEnabled(False)

    @pyqtSlot(int)
    def on_no_match_selected(self, main_row_index: int):
        """
        Handles the action when the user declares that none of the candidates are a match.
        """
        if not self.main_view or not self.table_handler or not self.table_handler.table_widget:
            return

        # Update the status to "Not Found"
        self.table_handler.update_linking_status(
            row_index=main_row_index,
            status="not_found",
            status_text="Not Found (Manual)",
            tidal_track=None,
            candidates=None,
            score=None
        )

        # Persist this "Not Found" state
        playlist_id = None
        playlist_obj = self.main_view.s_playlist_obj
        if isinstance(playlist_obj, dict):
            playlist_id = playlist_obj.get("data", {}).get("id")

        title_item = self.table_handler.table_widget.item(main_row_index, 1)
        title_item_data = title_item.data(QtCore.Qt.ItemDataRole.UserRole) if title_item else None
        spotify_track_id = None
        spotify_metadata = {}
        if isinstance(title_item_data, dict):
            spotify_metadata = title_item_data.get("data", {})
            spotify_track_id = spotify_metadata.get("id")

        if playlist_id and spotify_track_id:
            self.persistence_manager.add_or_update_link(
                playlist_id=playlist_id,
                spotify_track_id=spotify_track_id,
                spotify_track_details=spotify_metadata,
                tidal_track_object=None,
            )

        self.manualLinkApplied.emit(main_row_index)

    @pyqtSlot(int, object)
    def onManualLinkSelected(self, main_row_index: int, selected_track: Track) -> None:
        """
        Handles the signal emitted when a user manually selects a linking candidate.
        """
        if (
            not self.main_view
            or not self.table_handler
            or not self.table_handler.table_widget
        ):
            return

        table_widget = self.table_handler.table_widget

        # Update the main table row status
        status_text = f"Linked (Manual): {selected_track.id}"
        self.table_handler.update_linking_status(
            row_index=main_row_index,
            status="manual_linked",
            status_text=status_text,
            tidal_track=selected_track,
            candidates=None
        )

        title_item = table_widget.item(main_row_index, 1)
        spotify_track_id = None
        spotify_metadata = {}

        if title_item:
            title_item_data = title_item.data(QtCore.Qt.ItemDataRole.UserRole)
            if (
                isinstance(title_item_data, dict)
                and title_item_data.get("type") == "spotify_track"
            ):
                spotify_metadata = title_item_data.get("data", {})
                spotify_track_id = spotify_metadata.get("id")

        playlist_id = None
        playlist_obj = self.main_view.s_playlist_obj
        if isinstance(playlist_obj, dict):
            playlist_id = playlist_obj.get("data", {}).get("id")
        elif isinstance(playlist_obj, Playlist):
            playlist_id = playlist_obj.uuid

        if spotify_track_id and playlist_id and spotify_metadata and selected_track:
            self.persistence_manager.add_or_update_link(
                playlist_id=playlist_id,
                spotify_track_id=spotify_track_id,
                spotify_track_details=spotify_metadata,
                tidal_track_object=selected_track,
            )

        self.manualLinkApplied.emit(main_row_index)
        self.update_link_button_state()

    def unlinkSelectedTracks(self, selected_rows_indices: List[int]):
        """
        Removes the link for the selected Spotify tracks.
        """
        if not self.main_view or not self.table_handler or not self.table_handler.table_widget or not self.persistence_manager:
            return

        playlist_id = None
        playlist_obj = self.main_view.s_playlist_obj
        if isinstance(playlist_obj, dict):
            playlist_id = playlist_obj.get("data", {}).get("id")

        if not playlist_id:
            CustomQMessageBox.information(self.main_view, "Error", "Playlist Error", "Could not determine the current playlist.")
            return

        table = self.table_handler.table_widget
        for row_index in selected_rows_indices:
            title_item = table.item(row_index, 1)
            if not title_item:
                continue
            
            item_data = title_item.data(QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict) and item_data.get("type") == "spotify_track":
                spotify_track_id = item_data.get("data", {}).get("id")
                if spotify_track_id:
                    self.persistence_manager.remove_link(playlist_id, spotify_track_id)
                    
                    self.table_handler.update_linking_status(
                        row_index=row_index,
                        status="not_linked",
                        status_text="Not Linked",
                        tidal_track=None,
                        candidates=None,
                        score=None
                    )
        
        self.update_link_button_state()
        if self.main_view.download_handler:
            self.main_view.download_handler._update_download_button_text()
