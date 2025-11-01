# tidal_dl/gui/gui_linking_handler.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_linking_handler.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Handles GUI interactions related to Spotify track linking.
"""

import logging
from typing import List, Optional, Dict, TYPE_CHECKING, Any, Tuple  # Added Tuple
from PyQt6 import QtWidgets, QtCore  # Keep existing PyQt6 imports
from PyQt6.QtCore import (
    QObject,
    pyqtSignal,
    pyqtSlot,
    QPoint,
    Qt,
    QItemSelection,
    QItemSelectionModel,
)  # Added Qt, QItemSelection, QItemSelectionModel
from ..model import Track, Playlist  # Keep Track import, add Playlist
from ..tidal import TidalAPI  # Keep TidalAPI
from ..printf import Printf
from ..persistence import LinkPersistenceManager  # Keep persistence
from .gui_utils import show_info_message  # Utility for showing messages
from .. import paths

if TYPE_CHECKING:
    from .gui import MainView  # Add MainView hint
    from .gui_table_handler import TableHandler  # Add TableHandler hint

    # Add SplitterTable import for type hinting
    from .gui_table import SplitterTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module


class LinkingGuiHandler(QObject):  # Inherit from QObject to use signals
    """
    Handles interactions between the MainView GUI and the track linking logic.
    Manages context menus, starting the linking process, and updating the UI.
    """

    requestLinkingStart = pyqtSignal(
        list, object # MODIFIED: Add callback
    )
    manualLinkApplied = pyqtSignal(
        int
    )  # Signal to notify MainView that a manual link has been applied
    linkProgress = pyqtSignal(str, int) # MODIFIED: Add progress signal (playlist_id, count)

    def __init__(
        self,
        api: TidalAPI,
        table_handler: "TableHandler",
        persistence_manager: LinkPersistenceManager,
        link_button: QtWidgets.QPushButton,
        parent: Optional["MainView"] = None,
    ) -> None:
        super().__init__(parent)  # Pass parent to QObject init
        self.api = api
        self.table_handler = table_handler
        self.persistence_manager = persistence_manager
        self.link_button = link_button
        self.main_view = parent  # Assuming parent is the MainView instance
        self._processed_count = 0 # MODIFIED: Add counter
        # Connect manual link application to sub-row collapse
        self.manualLinkApplied.connect(self.table_handler.collapse_sub_row)

        # --- Connect Link Button Click (Persistent Connection) ---
        # This connection is made once during initialization.
        self.link_button.clicked.connect(self._handle_link_button_click)
        logger.debug(
            "Connected link_button clicked signal to _handle_link_button_click"
        )
        # --- End Connection ---

    def _get_linking_status_from_row(
        self, row_index: int
    ) -> Tuple[Optional[str], bool]:
        """
        Helper to get the linking status and candidate status from a table row's metadata.

        Args:
            row_index (int): The index of the row to check.

        Returns:
            Tuple[Optional[str], bool]: A tuple containing:
                - The link status string ('found', 'cached_linked', 'manual_linked', 'not_linked', etc.) or None if error.
                - A boolean indicating if the row has candidates.
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
        Updates the visibility and text of the 'Link Tracks' button based
        on whether the current context is a Spotify playlist, the table selection,
        and the linking status of the relevant tracks.
        """
        if (
            not self.main_view
            or not self.table_handler
            or not self.table_handler.table_widget
        ):
            logger.error(
                "Cannot update link button state: MainView, TableHandler, or TableWidget not available."
            )
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
        logger.debug(
            f"Link Tracks button visibility set to: {is_spotify_playlist_selected}"
        )

        if not is_spotify_playlist_selected:
            return  # No need to update text if not visible

        # --- Text Logic ---
        selected_indices = sorted(
            list(set(idx.row() for idx in table.selectedIndexes()))
        )
        button_text = "Link Tracks"  # Default

        if selected_indices:
            # --- Selection Exists ---
            any_selected_linked = False
            num_selected = len(selected_indices)
            for row_index in selected_indices:
                link_status, _ = self._get_linking_status_from_row(row_index)
                # Consider 'found', 'cached_linked', 'manual_linked' as linked
                if link_status in ["found", "cached_linked", "manual_linked", "auto_linked", "found_uncertain"]:
                    any_selected_linked = True
                    break  # Found one linked, no need to check further

            if any_selected_linked:
                button_text = f"Relink {num_selected} Selected"
            else:
                button_text = f"Link {num_selected} Selected"
            logger.debug(f"Link button state (Selection): Text='{button_text}'")

        else:
            # --- No Selection ---
            any_all_linked = False
            num_total = table.rowCount()
            if num_total > 0:  # Only check if table has rows
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
                # Table is empty, default text is fine, but disable button?
                button_text = "Link All Tracks"  # Keep default text
                # Optionally disable if table is empty: self.link_button.setEnabled(False)
            logger.debug(f"Link button state (No Selection): Text='{button_text}'")

        self.link_button.setText(button_text)
        # Ensure button is enabled unless linking is active
        self.link_button.setEnabled(
            not getattr(self.main_view, "linking_active", False)
        )

    @pyqtSlot(QtWidgets.QMenu, list)  # Changed QPoint to QtWidgets.QMenu
    def spotifyLinkContextMenu(
        self, menu: QtWidgets.QMenu, selected_rows_indices: List[int]
    ) -> None:  # Changed pos to menu
        """Populates the given context menu with actions for linking Spotify tracks."""
        if not self.main_view:
            logger.error("main_view is None in spotifyLinkContextMenu.")
            return
        # table = self.main_view.tableWidget # Not needed if menu is passed in and executed by caller

        # --- Determine if ANY selected track is linked ---
        # This check needs to be more robust, similar to TableHandler, to include all valid linked statuses
        any_selected_linked = False
        all_selected_linked_or_error = (
            True  # Assume true, set to false if any are unlinked and not in an error
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
                elif (
                    link_status not in error_statuses
                ):  # If it's not linked and not an error status, then not all are linked/error
                    all_selected_linked_or_error = False

            if (
                not any_selected_linked and not all_selected_linked_or_error
            ):  # If no tracks are linked, and not all are in an error state, allow linking all
                pass  # Default behavior is to link
            elif (
                not all_selected_linked_or_error
            ):  # Some are linked, some are not (and not error)
                any_selected_linked = True  # Treat as if some are linked for "Relink" text, but still allow linking unlinked ones.
                # The actual linking logic in startLinkingSelectedTracks will skip already linked ones.
        # --- End Check ---

        num_selected = len(selected_rows_indices)

        # Action to Link/Relink
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

        # Action to Unlink (only if at least one is linked)
        if any_selected_linked:
            menu.addSeparator()
            unlinkAction = menu.addAction(
                f"Unlink {num_selected} selected Spotify track(s)"
            )
            if unlinkAction:
                unlinkAction.setEnabled(True)
                unlinkAction.triggered.connect(lambda: self.unlinkSelectedTracks(selected_rows_indices))

        # menu.exec(table.mapToGlobal(pos)) # Execution is handled by the caller (TableHandler)

    def startLinkingSelectedTracks(self, selected_rows_indices: List[int]) -> None:
        """Initiates the track linking process for selected rows."""
        if not self.main_view:
            return  # Existing check
        if self.main_view.linking_active:
            logger.warning("Attempted to start linking while already active.")
            return

        logger.info(
            f"Initiating linking for selected Spotify rows: {selected_rows_indices}"
        )
        logger.info(
            f"Initiating linking for {len(selected_rows_indices)} selected Spotify track(s)..."
        )

        tracks_to_link_data = []
        table = self.main_view.tableWidget
        for row_index in selected_rows_indices:
            # Get metadata from Title column (index 1)
            item_widget = table.item(row_index, 1)
            item_data = (
                item_widget.data(QtCore.Qt.ItemDataRole.UserRole)
                if item_widget
                else None
            )
            if isinstance(item_data, dict) and item_data.get("type") == "spotify_track":
                spotify_metadata = item_data.get("data")
                # Ensure essential keys exist before adding
                if spotify_metadata and all(
                    k in spotify_metadata for k in ("name", "artists", "album", "id")
                ):
                    tracks_to_link_data.append((row_index, spotify_metadata))
                else:
                    logger.warning(
                        f"Skipping row {row_index} due to missing essential metadata "
                        f"in retrieved data: {spotify_metadata}"
                    )
            else:
                logger.warning(
                    f"Skipping row {row_index} as it's not a Spotify track or data is missing."
                )

        if not tracks_to_link_data:
            show_info_message(
                self.main_view,
                "Selection Error",
                "No valid Spotify tracks were found in your selection.",
                "Please ensure the selected tracks have complete metadata.",
                icon_path=paths.resource_path("assets/icons/info_icon.png")
            )
            return

        logger.debug(
            f"Emitting requestLinkingStart signal with {len(tracks_to_link_data)} tracks."
        )
        self._processed_count = 0 # MODIFIED: Reset counter
        self.requestLinkingStart.emit(tracks_to_link_data, None)

    def linkAllSpotifyTracks(self) -> None:
        """Initiates the track linking process for ALL Spotify tracks currently in the table."""
        if not self.main_view:
            return  # Existing check
        if self.main_view.linking_active:
            logger.warning("Attempted to start linking while already active.")
            return

        logger.info("Initiating linking for ALL Spotify tracks in the table.")
        logger.info("Initiating linking for all Spotify tracks...")

        tracks_to_link_data = []
        table = self.main_view.tableWidget
        for row_index in range(table.rowCount()):
            # Get metadata from Title column (index 1)
            item_widget = table.item(row_index, 1)
            item_data = (
                item_widget.data(QtCore.Qt.ItemDataRole.UserRole)
                if item_widget
                else None
            )
            if isinstance(item_data, dict) and item_data.get("type") == "spotify_track":
                spotify_metadata = item_data.get("data")
                # Ensure essential keys exist before adding
                if spotify_metadata and all(
                    k in spotify_metadata for k in ("name", "artists", "album", "id")
                ):
                    tracks_to_link_data.append((row_index, spotify_metadata))
                else:
                    logger.warning(
                        f"Skipping row {row_index} due to missing essential metadata "
                        f"in retrieved data: {spotify_metadata}"
                    )

        if not tracks_to_link_data:
            show_info_message(
                self.main_view,
                "No Tracks to Link",
                "No Spotify tracks were found in the current table.",
                "Please load a Spotify playlist to begin linking.",
                icon_path=paths.resource_path("assets/icons/info_icon.png")
            )
            return

        logger.debug(
            f"Emitting requestLinkingStart signal with {len(tracks_to_link_data)} tracks for 'Link All'."
        )
        self._processed_count = 0 # MODIFIED: Reset counter
        self.requestLinkingStart.emit(tracks_to_link_data, None)

    @pyqtSlot()
    def _handle_link_button_click(self):
        """
        Determines whether to link all or selected tracks based on table selection
        when the 'Link Tracks' / 'Relink Tracks' button is clicked.
        """
        if (
            not self.main_view
            or not self.table_handler
            or not self.table_handler.table_widget
        ):
            logger.error(
                "Cannot handle link button click: MainView, TableHandler or TableWidget not available."
            )
            return

        table = self.table_handler.table_widget
        # Get selected row indices correctly using the table's method
        selected_indices = sorted(
            list(set(idx.row() for idx in table.selectedIndexes()))
        )

        if selected_indices:
            logger.debug(
                f"Link button clicked with {len(selected_indices)} rows selected. Calling startLinkingSelectedTracks."
            )
            self.startLinkingSelectedTracks(selected_indices)
        else:
            logger.debug(
                "Link button clicked with no rows selected. Calling linkAllSpotifyTracks."
            )
            self.linkAllSpotifyTracks()

    @pyqtSlot(int)
    def onLinkingStarted(self, row_index: int) -> None:
        """Slot called when linking starts for a specific row."""
        try:
            if not self.main_view:
                logger.error(
                    f"main_view is None in onLinkingStarted for row {row_index}."
                )
                return
            # Use table_handler to update the row status
            self.table_handler.update_linking_status(row_index, "linking", "Linking...")
            logger.info(f"Linking started for row {row_index + 1}...")
        except Exception as e:
            logger.error(
                f"Error updating linking status for row {row_index}: {e}", exc_info=True
            )

    @pyqtSlot(int, object, object, object)
    def onLinkingFinished(
        self,
        row_index: int,
        tidal_track: Optional[Track],
        candidates: Optional[List[Dict]],
        score: Optional[int],
    ) -> None:
        """Slot called when linking finishes for a specific row."""
        if not self.main_view:
            logger.error(f"main_view is None in onLinkingFinished for row {row_index}.")
            return

        status_text = ""
        link_status = "not_linked"  # Default

        # The 'candidates' variable here refers to alternative candidates if a best_match (tidal_track) was found,
        # or all potential candidates if no single best_match was identified by searchLinkTrack.

        logger.debug(
            f"[onLinkingFinished Row {row_index}] Received: BestMatchID='{getattr(tidal_track, 'id', 'N/A')}', Candidates (alternatives/all) type: {type(candidates)}, len: {len(candidates) if candidates else 0}, Score={score}"
        )
        if candidates:
            logger.debug(
                f"[onLinkingFinished Row {row_index}] First candidate details: TID='{getattr(candidates[0].get('tidal_track'),'id','N/A')}', Title='{getattr(candidates[0].get('tidal_track'),'title','N/A')}'"
            )

        if tidal_track:  # A best guess/match was found
            if (
                score is not None and score <= 1
            ):  # Confident automatic link (score 0 or 1)
                status_text = f"Linked (Certainty score: {score}): {tidal_track.id}"
                link_status = "auto_linked"
                logger.debug(
                    f"Row {row_index + 1}: Auto-linked. Tidal ID: {tidal_track.id}, Score: {score}"
                )
            elif score is not None and score > 1:  # Score is > 1
                if (
                    candidates
                ):  # Best guess found, score > 1, AND other candidates exist
                    status_text = f"Manual linking required (Certainty score: {score}): {tidal_track.id}"
                    link_status = "manual_review_needed"
                    logger.debug(
                        f"Row {row_index + 1}: Manual review needed. Best guess Tidal ID: {tidal_track.id}, Score: {score}, Alternatives available."
                    )
                else:  # Best guess found, score > 1, but NO other candidates exist
                    status_text = (
                        f"Linked (Uncertain, Score: {score}): {tidal_track.id}"
                    )
                    link_status = "found_uncertain"
                    logger.debug(
                        f"Row {row_index + 1}: Linked (uncertain). Tidal ID: {tidal_track.id}, Score: {score}, No other candidates."
                    )
            else:  # Score is None, but tidal_track (best guess) was present
                status_text = f"Linked: {tidal_track.id}"  # Fallback if score is None
                link_status = "auto_linked"  # Treat as auto_linked
                logger.debug(
                    f"Row {row_index + 1}: Linked (score was None). Tidal ID: {tidal_track.id}"
                )

        elif (
            candidates
        ):  # No single best_match (tidal_track is None), but candidates exist
            status_text = "Manual linking required (Candidates available)"
            link_status = "candidates_only"
            logger.debug(
                f"Row {row_index + 1}: Not auto-linked, but {len(candidates)} candidates found."
            )
        else:  # No best_match (tidal_track is None), and no candidates
            status_text = "Not Found"
            link_status = "not_found"
            logger.debug(f"Row {row_index + 1}: Not linked, no candidates found.")

        # Update table via TableHandler
        logger.debug(
            f"[onLinkingFinished Row {row_index}] About to call update_linking_status. Status='{link_status}', TidalTrackID='{getattr(tidal_track, 'id', 'N/A')}', Candidates type: {type(candidates)}, len: {len(candidates) if candidates else 0}, Score={score}"
        )
        self.table_handler.update_linking_status(
            row_index=row_index,
            status=link_status,
            status_text=status_text,
            tidal_track=tidal_track,
            candidates=candidates,  # Pass the candidates list (alternatives or all)
            score=score,
        )

        logger.info(f"Linking finished for row {row_index + 1}: {status_text}")

        # --- Persist Link ---
        # Persist only if it's an automatic or manually confirmed link.
        # For "auto_linked", "found_uncertain", and "manual_linked" (handled elsewhere)
        should_persist = link_status in ["auto_linked", "found_uncertain"]
        
        # MODIFIED: Correctly get playlist_id for persistence and progress signal
        playlist_id = None
        if self.main_view and isinstance(self.main_view.s_playlist_obj, dict):
            playlist_id = self.main_view.s_playlist_obj.get("data", {}).get("id")

        if (
            should_persist
            and tidal_track
            and playlist_id
        ):
            table_widget = self.main_view.tableWidget
            title_item = table_widget.item(row_index, 1)  # Title is column 1
            title_item_data = (
                title_item.data(QtCore.Qt.ItemDataRole.UserRole) if title_item else None
            )
            spotify_track_id = None
            spotify_metadata = {}  # Initialize spotify_metadata
            if (
                isinstance(title_item_data, dict)
                and title_item_data.get("type") == "spotify_track"
            ):
                spotify_metadata = title_item_data.get(
                    "data", {}
                )  # This should be the full Spotify track details
                spotify_track_id = spotify_metadata.get("id")

            if (
                playlist_id and spotify_track_id and spotify_metadata and tidal_track
            ):  # Ensure all necessary data is present
                pm = getattr(self.main_view, "link_persistence_manager", None)
                if pm:
                    # For auto_linked or found_uncertain, we persist the best_match.
                    # If there were alternatives (candidates), they are stored with the link.
                    pm.add_or_update_link(
                        playlist_id=playlist_id,
                        spotify_track_id=spotify_track_id,
                        spotify_track_details=spotify_metadata,  # Pass full Spotify details
                        tidal_track_object=tidal_track,  # Pass full Tidal Track object
                        candidates=candidates if candidates else None,
                    )
                    logger.debug(
                        f"Persisted detailed link ({link_status}){' with alternatives' if candidates else ''}: {playlist_id} | {spotify_track_id} -> {tidal_track.id}"
                    )
                else:
                    logger.error(
                        "Link persistence manager not found on main_view. Cannot persist link."
                    )
            else:
                logger.warning(
                    f"Could not persist link for row {row_index}: Missing playlist_id ({playlist_id}) or spotify_track_id ({spotify_track_id})"
                )
        # --- End Persist Link ---

        # MODIFIED: Increment counter and emit progress
        self._processed_count += 1
        if playlist_id:
            self.linkProgress.emit(str(playlist_id), self._processed_count)

    @pyqtSlot(int, str)
    def onLinkingError(self, row_index: int, error_message: str) -> None:
        """Slot called when an error occurs during linking for a specific row."""
        logger.error(f"Linking error for row {row_index + 1}: {error_message}")
        try:
            if not self.main_view:
                logger.error(
                    f"main_view is None in onLinkingError for row {row_index}."
                )
                return
            # Update table via TableHandler
            self.table_handler.update_linking_status(
                row_index=row_index, status="error", status_text=f"Error: {error_message}", error_message=error_message
            )
            Printf.err(f"Linking error for row {row_index + 1}: {error_message}")
        except Exception as e:
            logger.error(
                f"Error updating linking error status for row {row_index}: {e}",
                exc_info=True,
            )
        
        # MODIFIED: Increment counter and emit progress even on error
        self._processed_count += 1
        if self.main_view and isinstance(self.main_view.s_playlist_obj, dict):
            playlist_id = self.main_view.s_playlist_obj.get("data", {}).get("id")
            if playlist_id:
                self.linkProgress.emit(str(playlist_id), self._processed_count)

    @pyqtSlot()
    def onAllLinkingTasksFinished(self) -> None:
        """Slot called when the LinkingWorker has processed all tracks."""
        logger.info("All linking tasks finished.")
        logger.info("Finished linking process for all selected/queued tracks.")
        # Update button state after linking finishes
        self.update_link_button_state()  # This updates the "Link Tracks" button

        # --- NEW: Update the "Download" button text as well ---
        if (
            self.main_view
            and hasattr(self.main_view, "download_handler")
            and self.main_view.download_handler
        ):
            logger.debug(
                "onAllLinkingTasksFinished: Triggering download button text update."
            )
            self.main_view.download_handler._update_download_button_text()
        # --- END NEW ---

    @pyqtSlot()
    def onStopLinkingClicked(self):
        """Handles clicks on the 'Stop Linking' button."""
        if not self.main_view:
            return
        if self.main_view.linking_active:
            logger.info("Stop linking requested by user.")
            logger.info("Stop linking requested...")
            self.main_view.linking_stop_event.set()  # Signal the worker thread
            self.link_button.setText("Stopping...")
            self.link_button.setEnabled(False)  # Disable button while stopping
        else:
            logger.warning("onStopLinkingClicked called but linking is not active.")

    @pyqtSlot(int)
    def on_no_match_selected(self, main_row_index: int):
        """
        Handles the action when the user declares that none of the candidates are a match.
        """
        logger.info(f"[LinkingGuiHandler] 'No Match' selected for row {main_row_index}.")

        if not self.main_view or not self.table_handler or not self.table_handler.table_widget:
            return

        # Update the status to "Not Found"
        self.table_handler.update_linking_status(
            row_index=main_row_index,
            status="not_found",
            status_text="Not Found (Manual)",
            tidal_track=None,
            candidates=None, # Clear candidates as none matched
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
                tidal_track_object=None, # Explicitly link to None
            )
            logger.debug(f"Persisted 'No Match' for Spotify track {spotify_track_id} in playlist {playlist_id}.")

        # Finally, collapse the sub-row
        self.manualLinkApplied.emit(main_row_index)

    @pyqtSlot(int, object)
    def onManualLinkSelected(self, main_row_index: int, selected_track: Track) -> None:
        """
        Handles the signal emitted when a user manually selects a linking candidate.
        Updates the table, persists the link, and triggers sub-row collapse.
        """
        logger.info(
            f"[LinkingGuiHandler] Manual link selected for row {main_row_index}: Tidal Track ID {selected_track.id}"
        )

        if (
            not self.main_view
            or not self.table_handler
            or not self.table_handler.table_widget
        ):
            logger.error(
                f"Cannot process manual link: MainView, TableHandler or TableWidget not available."
            )
            return

        table_widget = self.table_handler.table_widget

        # Update the main table row status
        status_text = f"Linked (Manual): {selected_track.id}"
        self.table_handler.update_linking_status(
            row_index=main_row_index,
            status="manual_linked",
            status_text=status_text,
            tidal_track=selected_track,
            candidates=None # Clear candidates after manual selection
        )

        # Retrieve Spotify track ID from the main table row data (Title column)
        title_item = table_widget.item(main_row_index, 1)
        spotify_track_id = None
        spotify_metadata = {}  # Ensure spotify_metadata is defined

        if title_item:
            title_item_data = title_item.data(QtCore.Qt.ItemDataRole.UserRole)
            if (
                isinstance(title_item_data, dict)
                and title_item_data.get("type") == "spotify_track"
            ):
                spotify_metadata = title_item_data.get(
                    "data", {}
                )  # This should be the full Spotify track details
                spotify_track_id = spotify_metadata.get("id")

        # Retrieve playlist ID, handling both dict and Playlist object types
        playlist_id = None
        playlist_obj = self.main_view.s_playlist_obj
        if isinstance(playlist_obj, dict):
            # Assuming Spotify playlist data is stored directly
            playlist_id = playlist_obj.get("data", {}).get("id")
        elif isinstance(
            playlist_obj, Playlist
        ):  # Make sure Playlist is imported if not already
            playlist_id = playlist_obj.uuid
        elif playlist_obj:
            logger.warning(f"Unexpected type for s_playlist_obj: {type(playlist_obj)}")

        if (
            spotify_track_id and playlist_id and spotify_metadata and selected_track
        ):  # Ensure all necessary data
            # Persist the manual link
            self.persistence_manager.add_or_update_link(
                playlist_id=playlist_id,
                spotify_track_id=spotify_track_id,
                spotify_track_details=spotify_metadata,  # Pass full Spotify details
                tidal_track_object=selected_track,  # Pass full Tidal Track object
                # No candidates for manual link, so it defaults to None
            )
            logger.debug(
                f"[LinkingGuiHandler] Persisted detailed manual link: Spotify {spotify_track_id} -> Tidal {selected_track.id} for playlist {playlist_id}"
            )
        else:
            logger.error(
                f"[LinkingGuiHandler] Could not persist manual link for row {main_row_index}: Missing Spotify Track ID ({spotify_track_id}) or Playlist ID ({playlist_id})."
            )

        # --- THIS IS THE FIX ---
        # Emit signal to trigger sub-row collapse
        self.manualLinkApplied.emit(main_row_index)
        # --- END OF FIX ---

        # Update the main link button state after applying a manual link
        self.update_link_button_state()

    def unlinkSelectedTracks(self, selected_rows_indices: List[int]):
        """
        Removes the link for the selected Spotify tracks.
        """
        logger.info(f"Unlinking {len(selected_rows_indices)} selected tracks.")
        if not self.main_view or not self.table_handler or not self.table_handler.table_widget or not self.persistence_manager:
            logger.error("Cannot unlink tracks: critical components are missing (MainView, TableHandler, TableWidget, or PersistenceManager).")
            return

        playlist_id = None
        playlist_obj = self.main_view.s_playlist_obj
        if isinstance(playlist_obj, dict):
            playlist_id = playlist_obj.get("data", {}).get("id")

        if not playlist_id:
            logger.error("Cannot unlink tracks: could not determine playlist ID.")
            show_info_message(self.main_view, "Error", "Could not determine the current playlist.", "", icon_path=paths.resource_path("assets/icons/error_icon.png"))
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
                    # Remove from persistence
                    self.persistence_manager.remove_link(playlist_id, spotify_track_id)
                    
                    # Update UI
                    self.table_handler.update_linking_status(
                        row_index=row_index,
                        status="not_linked",
                        status_text="Not Linked",
                        tidal_track=None,
                        candidates=None,
                        score=None
                    )
        
        logger.info(f"Finished unlinking {len(selected_rows_indices)} track(s).")
        self.update_link_button_state()
        if self.main_view.download_handler:
            self.main_view.download_handler._update_download_button_text()