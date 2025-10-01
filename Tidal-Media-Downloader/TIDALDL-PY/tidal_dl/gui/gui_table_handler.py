# --- START OF FILE gui_table_handler.py ---

import sys

print("DEBUG_TRACE: gui_table_handler.py - Top level execution start", file=sys.stderr)
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
import traceback
from typing import List, Dict, Optional, Any, Union, cast


import aigpy
from aigpy import modelHelper  # Corrected import

from PyQt6 import QtCore, QtWidgets, QtGui  # Ensure QtGui is imported

# Added pyqtSignal back for potential future use
from PyQt6.QtCore import QObject, pyqtSlot, Qt, QPoint, pyqtSignal  # Keep existing
from PyQt6.QtWidgets import QTableWidgetItem, QProgressBar  # Keep existing
from PyQt6.QtWidgets import QMenu

from .gui_table import SplitterTable
from ..tidal import Type, Track, Album, Playlist, Artist, TIDAL_API
from ..printf import Printf
from .gui_utils import format_duration_ms
from ..persistence import LinkPersistenceManager

# Forward declaration for type hinting
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .gui import MainView
    from .gui_download import DownloadHandler
    from .gui_linking_handler import LinkingGuiHandler

print(
    f"DEBUG_TRACE: gui_table_handler.py - About to get logger '{__name__}'",
    file=sys.stderr,
)
logger = logging.getLogger(__name__)
print(f"DEBUG_TRACE: gui_table_handler.py - Got logger: {logger}. Current level: {logging.getLevelName(logger.level)}, Effective level: {logging.getLevelName(logger.getEffectiveLevel())}, Handlers: {logger.handlers}, Propagate: {logger.propagate}", file=sys.stderr)  # type: ignore[reportArgumentType]
# Set level back to WARNING or INFO if DEBUG is too verbose for normal use
print(
    f"DEBUG_TRACE: gui_table_handler.py - About to set logger level to DEBUG",
    file=sys.stderr,
)
logger.setLevel(logging.DEBUG)  # Changed to DEBUG for detailed logging
print(f"DEBUG_TRACE: gui_table_handler.py - Set logger level. New level: {logging.getLevelName(logger.level)}, Effective level: {logging.getLevelName(logger.getEffectiveLevel())}", file=sys.stderr)  # type: ignore[reportArgumentType]
# logger.propagate = True # Keep propagation enabled

MANUAL_LINK_REQUIRED_ROLE = (
    Qt.ItemDataRole.UserRole + 100
)  # Ensure it's a unique UserRole offset


class TableHandler(QObject):
    """
    Manages the results table (SplitterTable), including populating it
    with data and handling context menus specific to the table content.
    """

    # Signals (if needed for communication with other handlers)
    # e.g., requestContextMenuDownload = pyqtSignal(list, AudioQuality)

    def __init__(
        self,
        table_widget: Optional[SplitterTable],
        persistence_manager: LinkPersistenceManager,
        linking_handler: Optional["LinkingGuiHandler"],
        parent: "MainView",
    ):  # Allow table_widget and linking_handler to be None initially
        super().__init__(parent)
        self.main_view = parent  # Keep reference for context/messages
        self.table_widget = table_widget
        self.persistence_manager = persistence_manager
        self.linking_handler = (
            linking_handler  # Store reference (can be None initially)
        )
        self.download_handler: Optional["DownloadHandler"] = None  # Set later
        self.progress_bars: Dict[str, QProgressBar] = (
            {}
        )  # Dictionary to hold progress bars {track_id: QProgressBar}
        # self.progress_containers: Dict[str, QtWidgets.QWidget] = {} # REMOVE THIS LINE

        if self.table_widget:
            self._connect_table_signals()
        else:
            logger.warning(
                "TableHandler initialized with table_widget=None. Signals not connected yet."
            )

    def set_download_handler(self, handler: "DownloadHandler"):
        """Sets the download handler for triggering downloads from context menus."""
        self.download_handler = handler

    def set_linking_handler(self, handler: "LinkingGuiHandler"):
        """Sets the linking handler for triggering linking from context menus."""
        self.linking_handler = handler

    def _connect_table_signals(self):
        """Connects signals from the QTableWidget."""
        if not self.table_widget:
            logger.error(
                "Attempted to connect table signals, but table_widget is None."
            )
            return
        self.table_widget.customContextMenuRequested.connect(
            self.handle_table_context_menu
        )
        # Connect signal from SplitterTable when a candidate is manually selected
        # This signal is now emitted by LinkingGuiHandler after processing
        # self.table_widget.candidateSelectedInSubRow.connect(self.linking_handler.onManualLinkSelected) # Connection moved

    def clear_table(self):
        """Clears all rows and resets headers."""
        if not self.table_widget:
            return
        self.table_widget.clearRows()  # This detaches cell widgets
        # DO NOT CLEAR self.progress_bars or self.progress_containers here
        if self.download_handler:
            self.download_handler._update_download_button_text()

    def show_loading_message(self, message: str):
        """Displays a loading message in the table."""
        if not self.table_widget:
            return
        self.clear_table()
        self.table_widget.setColumnCount(1)
        self.table_widget.setHorizontalHeaderLabels([message])
        self.table_widget.addRow([message], None)
        if self.download_handler:
            self.download_handler._update_download_button_text()

    def show_error_message(self, message: str):
        """Displays an error message in the table."""
        if not self.table_widget:
            return
        self.clear_table()
        self.table_widget.setColumnCount(1)
        self.table_widget.setHorizontalHeaderLabels(["Error"])
        self.table_widget.addRow([message], None)
        if self.download_handler:
            self.download_handler._update_download_button_text()

    # Removed @pyqtSlot decorator - not needed for direct calls or invokeMethod
    def populate_search_results(
        self,
        results_array: List[Any],
        result_type: Type,
        search_context: Optional[object],
    ):
        """Populates the table with search results."""
        if not self.table_widget:
            return
        logger.debug(
            f"[TableHandler] Populating with search results. Type: {result_type.name}, Items: {len(results_array)}"
        )
        # Store context in MainView (needed by other handlers)
        self.main_view.s_array = results_array
        self.main_view.s_type = result_type
        self.main_view.s_playlist = isinstance(
            search_context, (Playlist, dict)
        )  # Check if context is playlist-like
        self.main_view.s_playlist_obj = cast(
            Optional[Union[Playlist, Dict]], search_context
        )  # Cast for type hint

        self._populate_table_generic(results_array, result_type)

        if self.linking_handler:
            self.linking_handler.update_link_button_state()
        else:
            logger.warning("Linking handler not available to update button state.")

        if self.download_handler:
            self.download_handler._update_download_button_text()

    @pyqtSlot(list, str)
    def populate_tidal_tracks(self, tracks: List[Track], error_msg: str):
        """Populates the table with tracks from a Tidal playlist."""
        if not self.table_widget:
            return
        logger.debug(
            f"[TableHandler] Populating with Tidal tracks. Items: {len(tracks)}, Error: {error_msg}"
        )
        if error_msg:
            self.show_error_message(error_msg)
        else:
            # Store context in MainView
            self.main_view.s_array = tracks
            self.main_view.s_type = Type.Track
            # s_playlist and s_playlist_obj should already be set by the tree click handler

            self._populate_table_generic(tracks, Type.Track)

        if self.linking_handler:
            self.linking_handler.update_link_button_state()
        else:
            logger.warning("Linking handler not available to update button state.")

        if self.download_handler:
            self.download_handler._update_download_button_text()

    # Removed @pyqtSlot decorator
    def populate_spotify_tracks(
        self, playlist_id: str, tracks: Optional[List[Dict[str, Any]]]
    ):  # Allow tracks to be None
        """Populates the table with tracks from a Spotify playlist."""
        if not self.table_widget:
            return
        logger.debug(
            f"[TableHandler] Populating with Spotify tracks for playlist {playlist_id}. Items: {len(tracks) if tracks else 'None'}"
        )
        if tracks is None:
            self.show_error_message("Error fetching Spotify tracks.")
            tracks = []

        # Prepare array for MainView state (list of dicts with type marker)
        spotify_track_array_for_mainview: List[Dict[str, Any]] = [
            {"type": "spotify_track", "data": t} for t in tracks
        ]

        # Store context in MainView
        self.main_view.s_array = spotify_track_array_for_mainview
        self.main_view.s_type = Type.Track  # Treat as track context
        # s_playlist and s_playlist_obj should already be set by the tree click handler

        self._populate_table_generic(
            spotify_track_array_for_mainview, Type.Track, playlist_id=playlist_id
        )

        if self.linking_handler:
            self.linking_handler.update_link_button_state()
        else:
            logger.warning("Linking handler not available to update button state.")

        if self.download_handler:
            self.download_handler._update_download_button_text()

    @pyqtSlot(list, str)
    def _populate_table_from_search(self, tracks: List[Track], error_msg: str):
        """Slot to receive data from SearchHandler and populate the table."""
        logger.debug(
            f"[TableHandler] Received data from search click. Items: {len(tracks)}, Error: {error_msg}"
        )
        if error_msg:
            self.show_error_message(error_msg)
            return

        # Clear any previous search context to avoid ambiguity
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
        print(
            "DEBUG_TRACE: _populate_table_generic - Entered function", file=sys.stderr
        )
        """
        Generic method to populate the results table widget.
        Handles different item types and Spotify track specifics.
        print(f"DEBUG_TRACE: gui_table_handler.py - Inside _populate_table_generic. Logger level: {logging.getLevelName(logger.level)}, Effective level: {logging.getLevelName(logger.getEffectiveLevel())}, Handlers: {logger.handlers}, Propagate: {logger.propagate}", file=sys.stderr)
        """
        print(
            "DEBUG_TRACE: gui_table_handler.py - About to call logger.debug...",
            file=sys.stderr,
        )
        print(
            "DEBUG_TRACE: gui_table_handler.py - About to call logger.debug in _populate_table_generic",
            file=sys.stderr,
        )
        logger.debug(
            "TEST DEBUG INSIDE _populate_table_generic START (using module logger)"
        )

        if not self.table_widget:
            logger.error("_populate_table_generic called but table_widget is None.")
            return
        table = self.table_widget
        table.clearRows()
        # --- START FIX for Progress Bar ---
        # Clear only the progress_bars dictionary
        self.progress_bars.clear()
        logger.debug("Cleared self.progress_bars dictionary.")
        # --- END FIX for Progress Bar ---

        # --- Determine Headers ---
        is_spotify_track_list = False
        if result_type == Type.Track and results_array:
            first_item = results_array[0]
            # Check if the first item is a dictionary and has the 'type' key set to 'spotify_track'
            if (
                isinstance(first_item, dict)
                and first_item.get("type") == "spotify_track"
            ):
                is_spotify_track_list = True

        base_headers = ["#", "Title", "Artists", "Album", "Length", "Quality"]
        # MERGED: Combine Link Status and Progress into 'Status'
        column_headers = base_headers + ["Status"]

        table.setColumnCount(len(column_headers))
        table.setHorizontalHeaderLabels(column_headers)
        logger.debug(
            f"AFTER setColumnCount/Labels: Count={table.columnCount()}, Headers=[(table.horizontalHeaderItem(i).text() if table.horizontalHeaderItem(i) is not None else 'None') for i in range(table.columnCount())]"
        )
        # Store column indices for easier access later
        self.column_indices = {header: i for i, header in enumerate(column_headers)}
        logger.debug(
            f"Set table headers for context ({'Spotify' if is_spotify_track_list else 'Tidal'}): {column_headers}"
        )

        if not results_array:
            logger.debug("Results array is empty, table cleared.")
            table.update()
            if self.download_handler:
                self.download_handler._update_download_button_text()
            return

        # --- Load Persisted Links (if Spotify context) ---
        persisted_links: Dict[str, Any] = {}
        if is_spotify_track_list and playlist_id:
            persisted_links = self.persistence_manager.get_links_for_playlist(
                playlist_id
            )
            logger.debug(
                f"Loaded {len(persisted_links.get('tracks', {}))} persisted links for Spotify playlist {playlist_id}."
            )

        # --- Populate Rows ---
        Printf.info(
            f"Populating table with {len(results_array)} items of type {result_type.name}..."
        )
        for index, item in enumerate(results_array):
            link_status_data: Optional[Dict[str, Any]] = None
            rowData: Optional[List[str]] = None
            item_metadata: Any = item  # This will be the data for the Title column item

            try:
                # --- Determine Row Data based on Type ---
                if is_spotify_track_list:
                    # Handle Spotify track dictionaries (item is already the dict with type marker)
                    # Original Spotify data from the input results_array
                    original_spotify_track_data = item.get("data", {})
                    spotify_track_id = original_spotify_track_data.get("id")

                    # Initialize with original data, may be overwritten by cache
                    spotify_track_data_to_use = original_spotify_track_data

                    link_status_text = "Not Linked"
                    link_status_data_for_title: Dict[str, Any] = {
                        "link_status": "not_linked"
                    }
                    persisted_candidates = None
                    has_candidates = False
                    fetched_tidal_track_object: Optional[Track] = None

                    persisted_tracks_dict = persisted_links.get("tracks", {})
                    if spotify_track_id and spotify_track_id in persisted_tracks_dict:
                        link_info = persisted_tracks_dict.get(spotify_track_id, {})

                        cached_spotify_details = link_info.get("spotify_track_details")
                        cached_tidal_details_dict = link_info.get("tidal_track_details")
                        persisted_tidal_id_from_cache = link_info.get(
                            "tidal_track_id"
                        )  # For backward compatibility or if full object fails
                        persisted_candidates = link_info.get("candidates")

                        if cached_spotify_details and isinstance(
                            cached_spotify_details, dict
                        ):
                            spotify_track_data_to_use = cached_spotify_details  # Use cached Spotify details if available
                            logger.debug(
                                f"Using cached Spotify details for {spotify_track_id}"
                            )

                        # --- START REVISED TIDAL TRACK OBJECT RETRIEVAL ---
                        current_fetched_tidal_track_object: Optional[Track] = (
                            None  # Use a local var for clarity
                        )

                        # 1. Try to deserialize full Tidal object from 'tidal_track_details'
                        if (
                            cached_tidal_details_dict
                            and isinstance(cached_tidal_details_dict, dict)
                            and cached_tidal_details_dict.get("id")
                        ):
                            try:
                                deserialized_track = aigpy.model.dictToModel(
                                    cached_tidal_details_dict, Track
                                )
                                if deserialized_track and deserialized_track.id:
                                    current_fetched_tidal_track_object = (
                                        deserialized_track
                                    )
                                    link_status_text = f"Linked (Cached Full): {current_fetched_tidal_track_object.id}"
                                    link_status_data_for_title = {
                                        "link_status": "cached_linked_full",
                                        "tidal_track_id": str(
                                            current_fetched_tidal_track_object.id
                                        ),
                                        "tidal_track": current_fetched_tidal_track_object,
                                        "status_str": link_status_text,
                                    }
                                    logger.debug(
                                        f"Successfully deserialized cached Tidal Track object {current_fetched_tidal_track_object.id}"
                                    )
                                else:
                                    logger.warning(
                                        f"Deserialization of cached_tidal_details for {spotify_track_id} resulted in invalid Track object."
                                    )
                            except Exception as e:
                                logger.error(
                                    f"Error deserializing cached Tidal track details for {spotify_track_id}: {e}",
                                    exc_info=True,
                                )

                        # 2. If full object not loaded, try fetching by 'tidal_track_id' (new direct field)
                        if (
                            not current_fetched_tidal_track_object
                            and persisted_tidal_id_from_cache
                        ):
                            link_status_text = (
                                f"Linked (Cached ID): {persisted_tidal_id_from_cache}"
                            )
                            logger.debug(
                                f"Populating cached link (ID fallback): Fetching Tidal Track object for ID {persisted_tidal_id_from_cache}"
                            )
                            try:
                                api_fetched_track = TIDAL_API.getTrack(
                                    str(persisted_tidal_id_from_cache)
                                )
                                if api_fetched_track and api_fetched_track.id:
                                    current_fetched_tidal_track_object = (
                                        api_fetched_track
                                    )
                                    link_status_data_for_title = {
                                        "link_status": "cached_linked_id_fetched",
                                        "tidal_track_id": str(
                                            persisted_tidal_id_from_cache
                                        ),
                                        "tidal_track": current_fetched_tidal_track_object,
                                        "status_str": link_status_text,
                                    }
                                    logger.debug(
                                        f"Successfully fetched Track object {persisted_tidal_id_from_cache} via API for cached link."
                                    )
                                else:
                                    logger.warning(
                                        f"Failed to fetch Track object for cached_linked ID {persisted_tidal_id_from_cache}."
                                    )
                                    link_status_data_for_title.update(
                                        {
                                            "link_status": "error",
                                            "error_message": f"Failed to fetch Tidal track {persisted_tidal_id_from_cache} for cached link.",
                                        }
                                    )
                            except Exception as e:
                                logger.error(
                                    f"Error fetching Track object for cached_linked ID {persisted_tidal_id_from_cache}: {e}",
                                    exc_info=True,
                                )
                                link_status_data_for_title.update(
                                    {
                                        "link_status": "error",
                                        "error_message": f"Error fetching Tidal track {persisted_tidal_id_from_cache}.",
                                    }
                                )

                        # 3. Fallback if 'tidal_track_id' was missing but 'tidal_track_details' (old cache) might have an ID
                        elif (
                            not current_fetched_tidal_track_object
                            and isinstance(cached_tidal_details_dict, dict)
                            and cached_tidal_details_dict.get("id")
                        ):
                            old_cache_tidal_id = cached_tidal_details_dict.get("id")
                            link_status_text = (
                                f"Linked (Old Cache ID): {old_cache_tidal_id}"
                            )
                            logger.debug(
                                f"Populating cached link (Old Cache ID fallback): Fetching Tidal Track object for ID {old_cache_tidal_id}"
                            )
                            try:
                                api_fetched_track = TIDAL_API.getTrack(
                                    str(old_cache_tidal_id)
                                )
                                if api_fetched_track and api_fetched_track.id:
                                    current_fetched_tidal_track_object = (
                                        api_fetched_track
                                    )
                                    link_status_data_for_title = {
                                        "link_status": "cached_linked_id_fetched",  # Still use this status
                                        "tidal_track_id": str(old_cache_tidal_id),
                                        "tidal_track": current_fetched_tidal_track_object,
                                        "status_str": link_status_text,
                                    }
                                    logger.debug(
                                        f"Successfully fetched Track object {old_cache_tidal_id} via API for old cached link."
                                    )
                                else:  # API fetch failed
                                    logger.warning(
                                        f"Failed to fetch Track object for old cached_linked ID {old_cache_tidal_id}."
                                    )
                                    link_status_data_for_title.update(
                                        {
                                            "link_status": "error",
                                            "error_message": f"Failed to fetch Tidal track {old_cache_tidal_id} for old cached link.",
                                        }
                                    )
                            except Exception as e:
                                logger.error(
                                    f"Error fetching Track object for old cached_linked ID {old_cache_tidal_id}: {e}",
                                    exc_info=True,
                                )
                                link_status_data_for_title.update(
                                    {
                                        "link_status": "error",
                                        "error_message": f"Error fetching Tidal track {old_cache_tidal_id}.",
                                    }
                                )

                        # 4. If still no Tidal track object, but we had some form of link info
                        elif not current_fetched_tidal_track_object and (
                            cached_tidal_details_dict or persisted_tidal_id_from_cache
                        ):
                            logger.warning(
                                f"Persisted link for {spotify_track_id} found but could not obtain a valid Tidal Track object."
                            )
                            if (
                                "link_status" not in link_status_data_for_title
                                or link_status_data_for_title["link_status"]
                                not in ["error"]
                            ):  # Avoid overwriting specific error
                                link_status_text = "Link Data Error"
                                link_status_data_for_title.update(
                                    {
                                        "link_status": "error",
                                        "error_message": "Could not resolve Tidal track from cache.",
                                    }
                                )

                        # Ensure 'tidal_track' in link_status_data_for_title is the final object or None
                        link_status_data_for_title["tidal_track"] = (
                            current_fetched_tidal_track_object
                        )
                        # --- END REVISED TIDAL TRACK OBJECT RETRIEVAL ---

                        if persisted_candidates:
                            if (
                                "candidates" not in link_status_data_for_title
                            ):  # Ensure 'candidates' key exists
                                link_status_data_for_title["candidates"] = []
                            link_status_data_for_title["candidates"] = (
                                persisted_candidates
                            )
                            has_candidates = True

                    # Use spotify_track_data_to_use for display
                    artists_list = spotify_track_data_to_use.get("artists", [])
                    artists_str = ", ".join(
                        [a for a in artists_list if isinstance(a, str)]
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
                    # item_metadata for Title column:
                    # 'data' should be the spotify_track_data_to_use (which could be from cache or original)
                    # and merge with link_status_data_for_title which contains tidal_track object if successfully loaded/fetched
                    item_metadata = {
                        "type": "spotify_track",
                        "data": spotify_track_data_to_use,
                        **link_status_data_for_title,
                        "has_candidates": has_candidates,
                    }
                    logger.debug(
                        f"Row {index} (Spotify): item_metadata for Title: { {k: (type(v).__name__ if k=='tidal_track' else v) for k,v in item_metadata.items()} }"
                    )

                elif isinstance(item, Track):
                    quality_string = Printf.map_quality(item) if hasattr(item, "audioQuality") else "N/A"  # type: ignore
                    album_title = getattr(getattr(item, "album", None), "title", "N/A")
                    rowData = [
                        str(index + 1),
                        getattr(item, "title", "N/A"),
                        TIDAL_API.getArtistsName(getattr(item, "artists", [])),
                        album_title,
                        Printf.formatDuration(getattr(item, "duration", 0)),
                        quality_string,
                        "-",
                    ]
                    item_metadata = item

                elif isinstance(item, Album):
                    rowData = [
                        str(index + 1),
                        getattr(item, "title", "N/A"),
                        TIDAL_API.getArtistsName(getattr(item, "artists", [])),
                        str(getattr(item, "numberOfTracks", "")) + " Tracks",
                        getattr(item, "audioQuality", ""),
                        "-",
                    ]
                    item_metadata = item

                elif isinstance(item, Playlist):
                    rowData = [
                        str(index + 1),
                        getattr(item, "title", "N/A"),
                        getattr(item, "creatorName", ""),
                        str(getattr(item, "numberOfTracks", "")) + " Tracks",
                        "",
                        "-",
                    ]
                    item_metadata = item

                elif isinstance(item, Artist):
                    rowData = [
                        str(index + 1),
                        getattr(item, "name", "N/A"),
                        "",
                        "",
                        "",
                        "-",
                    ]
                    item_metadata = item

                else:
                    logger.warning(
                        f"Cannot format row data for unknown item type at index {index}: {type(item)}"
                    )
                    rowData = [str(index + 1), "Unknown Item Type", "", "", "", "-"]
                    item_metadata = None

                if rowData is not None:
                    current_row_index = table.rowCount()
                    table.addRow(
                        rowData, item_metadata
                    )  # item_metadata is stored with Title item

                    # --- Progress Bar Logic ---
                    track_id_for_progress: Optional[str] = None
                    is_downloadable_track = False

                    # Check for direct Tidal Track
                    if (
                        isinstance(item_metadata, Track)
                        and item_metadata.id is not None
                    ):
                        track_id_for_progress = str(item_metadata.id)
                        is_downloadable_track = True
                        logger.debug(
                            f"Row {index} (Tidal): Marked as downloadable. Tidal ID: {track_id_for_progress}"
                        )
                    # Check for Spotify track that has been linked (and thus has a Track object in its metadata)
                    elif (
                        isinstance(item_metadata, dict)
                        and item_metadata.get("type") == "spotify_track"
                    ):
                        link_status = item_metadata.get("link_status")
                        # Crucially, check if 'tidal_track' (the Track object) is present and is a Track instance
                        if (
                            link_status
                            in [
                                "cached_linked",
                                "found",
                                "auto_linked",
                                "manual_linked",
                            ]
                            and isinstance(item_metadata.get("tidal_track"), Track)
                            and item_metadata.get("tidal_track_id") is not None
                        ):  # Ensure tidal_track_id also exists

                            # Use the tidal_track_id from the metadata for consistency
                            track_id_for_progress = str(item_metadata["tidal_track_id"])
                            is_downloadable_track = True
                            logger.debug(
                                f"Row {index} (Spotify Linked - Status: {link_status}): Marked as downloadable. Tidal ID: {track_id_for_progress}"
                            )

                    # --- Simplified Progress Bar Instance Creation ---
                    if is_downloadable_track and track_id_for_progress:
                        # Only ensure a QProgressBar instance exists in self.progress_bars
                        # We don't create/manage a container widget here anymore.
                        if track_id_for_progress not in self.progress_bars:
                            logger.debug(
                                f"POPULATE: Pre-creating QProgressBar instance for track_id: {repr(track_id_for_progress)}"
                            )
                            bar = QProgressBar()
                            bar.setRange(0, 100)
                            bar.setFixedHeight(14)
                            bar.setTextVisible(False)
                            self.progress_bars[track_id_for_progress] = bar
                        else:
                            # If it exists, reset its value
                            existing_bar = self.progress_bars[track_id_for_progress]
                            existing_bar.setValue(0)
                    # --- End Simplified Progress Bar Instance Creation ---

                    # --- Set UserRole data for the Status column item ---
                    if is_spotify_track_list and "Status" in self.column_indices:
                        status_col_index = self.column_indices["Status"]
                        status_item = table.item(current_row_index, status_col_index)
                        if status_item:
                            status_item.setData(Qt.ItemDataRole.UserRole, item_metadata)
                else:
                    logger.error(
                        f"rowData remained None for item at index {index}, type {result_type.name}. Item: {item}"
                    )

            except Exception as e:
                num_cols = table.columnCount()
                error_row_data = [str(index + 1), f"Error processing item: {e}"] + [
                    ""
                ] * (num_cols - 2)
                logger.error(
                    f"Error processing item at index {index} for table: {item}\n{traceback.format_exc()}"
                )
                Printf.err(f"Error displaying item #{index + 1}: {e}")
                try:
                    table.addRow(error_row_data, None)
                except Exception as add_row_e:
                    logger.error(f"Failed to add error row to table: {add_row_e}")
                continue

        table.resizeColumnsToContents()
        table.adjustColumnWidths()
        table.update()
        Printf.success(f"Table populated with {table.rowCount()} items.")

        if self.download_handler:
            self.download_handler._update_download_button_text()

    # Removed @pyqtSlot decorator
    def handle_table_context_menu(self, pos: QPoint):
        """Determines context and delegates context menu display."""
        if not self.table_widget:
            return
        table = self.table_widget
        index = table.indexAt(pos)
        if not index.isValid():
            logger.debug("Table context menu requested on invalid index.")
            return

        selected_rows_indices = sorted(
            list(set(idx.row() for idx in table.selectedIndexes()))
        )
        if not selected_rows_indices:
            selected_rows_indices = [index.row()]

        first_row_index = selected_rows_indices[0]

        # Primary metadata (Track object or Spotify dict) is on Title item (col 1)
        title_item = table.item(first_row_index, 1)  # TITLE_COLUMN_INDEX = 1
        item_metadata_raw = (
            title_item.data(Qt.ItemDataRole.UserRole) if title_item else None
        )

        # --- START FIX for Pylance reportOptionalMemberAccess ---
        is_spotify_track_from_meta = False
        link_status = None
        is_linked = False

        if isinstance(item_metadata_raw, dict):
            is_spotify_track_from_meta = (
                item_metadata_raw.get("type") == "spotify_track"
            )
            link_status = item_metadata_raw.get("link_status")

            # Add 'cached_linked_full' and 'cached_linked_id_fetched' to this list
            valid_linked_statuses = [
                "found",
                "auto_linked",
                "manual_linked",
                "cached_linked",
                "found_uncertain",
                "cached_linked_full",
                "cached_linked_id_fetched",  # Added new statuses
            ]
            is_linked = (link_status in valid_linked_statuses) and (
                isinstance(item_metadata_raw.get("tidal_track"), Track)
                or item_metadata_raw.get("tidal_track_id") is not None
            )
        elif isinstance(item_metadata_raw, Track):  # It's a Tidal track
            is_spotify_track_from_meta = False  # Not a spotify track
            is_linked = (
                True  # Tidal tracks are inherently "linked" for download purposes
            )
            link_status = (
                "tidal_native"  # A custom status to indicate it's a direct Tidal track
            )
        # --- END FIX ---

        logger.debug(
            f"Context Menu Check: Row={first_row_index}, IsSpotifyTrack={is_spotify_track_from_meta}, IsLinked={is_linked}, LinkStatus={link_status}"
        )

        context_menu = QMenu(table)
        context_menu.setStyleSheet(
            """
            QMenu {
                background-color: #333333; /* Dark grey background */
                color: white; /* White text */
                border: 1px solid #555555; /* Optional: a slightly lighter border */
            }
            QMenu::item:selected {
                background-color: #555555; /* Darker grey for selected item */
            }
        """
        )

        if is_spotify_track_from_meta:
            # Always show linking options for Spotify tracks
            if self.linking_handler:
                logger.debug("Populating Spotify link actions for context menu.")
                # This method signature will be changed in LinkingGuiHandler:
                # spotifyLinkContextMenu(self, menu: QMenu, selected_rows_indices: List[int])
                self.linking_handler.spotifyLinkContextMenu(context_menu, selected_rows_indices)  # type: ignore
            else:
                logger.error(
                    "Linking Handler not available to populate Spotify link actions."
                )

            # If the Spotify track is linked, also show download options
            if is_linked:
                if self.download_handler:
                    if (
                        not context_menu.isEmpty() and self.linking_handler
                    ):  # Add separator if linking actions were added
                        context_menu.addSeparator()
                    logger.debug(
                        "Populating download actions for linked Spotify track."
                    )
                    # This method signature will be changed in DownloadGuiHandler:
                    # downloadTableContextMenu(self, menu: QMenu, selected_rows_indices: List[int])
                    self.download_handler.downloadTableContextMenu(context_menu, selected_rows_indices)  # type: ignore
                else:
                    logger.error(
                        "Download Handler not available to populate download actions for linked Spotify track."
                    )
            elif (
                not self.linking_handler
            ):  # Unlinked Spotify track, and no linking handler was available earlier
                logger.debug(
                    "Context menu for unlinked Spotify track remains empty as no handlers are available."
                )

        else:  # It's a native Tidal track (is_linked is True, is_spotify_track_from_meta is False)
            if self.download_handler:
                logger.debug("Populating download actions for Tidal track.")
                # This method signature will be changed in DownloadGuiHandler:
                # downloadTableContextMenu(self, menu: QMenu, selected_rows_indices: List[int])
                self.download_handler.downloadTableContextMenu(context_menu, selected_rows_indices)  # type: ignore
            else:
                logger.error(
                    "Download Handler not available to populate download actions for Tidal track."
                )

        if not context_menu.isEmpty():
            viewport = table.viewport()
            if viewport:
                context_menu.exec(viewport.mapToGlobal(pos))
            else:
                logger.warning(
                    "Table viewport is not available, cannot show context menu at specific position. Showing at global mouse position."
                )
                context_menu.exec(
                    QtGui.QCursor.pos()
                )  # Fallback to global cursor position
        else:
            logger.debug("Context menu is empty, not showing.")

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
        """Updates the visual status of a row during/after linking."""
        if not self.table_widget:
            return
        table = self.table_widget
        if row_index >= table.rowCount():
            logger.warning(
                f"Attempted to update linking status for non-existent row {row_index}"
            )
            return

        logger.debug(
            f"[UpdateStatus Row {row_index}] Received: Status='{status}', BestMatchID='{getattr(tidal_track, 'id', 'N/A')}', Candidates (alternatives) type: {type(candidates)}, len: {len(candidates) if candidates else 0}, Score={score}"
        )
        if candidates:
            first_cand_track = candidates[0].get("tidal_track")
            logger.debug(
                f"[UpdateStatus Row {row_index}] First alternative candidate received: TID='{getattr(first_cand_track,'id','N/A')}', Title='{getattr(first_cand_track,'title','N/A')}'"
            )

        QUALITY_COLUMN_INDEX = 5
        STATUS_COLUMN_INDEX = 6
        INDICATOR_COLUMN_INDEX = 0
        TITLE_COLUMN_INDEX = 1  # Define for clarity

        status_item = table.item(row_index, STATUS_COLUMN_INDEX)
        if not status_item:
            status_item = QTableWidgetItem()
            table.setItem(row_index, STATUS_COLUMN_INDEX, status_item)

        quality_item = table.item(row_index, QUALITY_COLUMN_INDEX)
        if not quality_item:
            quality_item = QTableWidgetItem()
            table.setItem(row_index, QUALITY_COLUMN_INDEX, quality_item)

        indicator_item = table.item(row_index, INDICATOR_COLUMN_INDEX)
        if not indicator_item:
            indicator_item = QTableWidgetItem()
            table.setItem(row_index, INDICATOR_COLUMN_INDEX, indicator_item)

        title_item = table.item(row_index, TITLE_COLUMN_INDEX)  # Get title item

        # item_data will be the dictionary that gets updated and then set to UserRole
        # For Spotify tracks, it starts as the dict from the Title item, otherwise it's a new dict.
        current_title_item_data_raw = (
            title_item.data(Qt.ItemDataRole.UserRole) if title_item else None
        )
        if (
            isinstance(current_title_item_data_raw, dict)
            and current_title_item_data_raw.get("type") == "spotify_track"
        ):
            # Make a copy to avoid modifying a shared dict if it was shared (though it shouldn't be after initial population)
            item_data: Dict[str, Any] = current_title_item_data_raw.copy()
        else:
            # For Tidal tracks or if title item data is not as expected, start fresh for status column.
            # The title item's data (if it's a Track object) won't be modified here.
            item_data: Dict[str, Any] = {}

        final_status_text = status_text or ""
        quality_text = "-"

        current_indicator_data_raw = (
            indicator_item.data(QtCore.Qt.ItemDataRole.UserRole)
            if indicator_item
            else {}
        )
        current_indicator_data: Dict[str, Any] = (
            current_indicator_data_raw
            if isinstance(current_indicator_data_raw, dict)
            else {}
        )

        # Start new_indicator_data with a copy of current_indicator_data to preserve existing fields
        new_indicator_data: Dict[str, Any] = current_indicator_data.copy()
        # Ensure 'expanded' is present, defaulting to False if not in current_indicator_data
        new_indicator_data["expanded"] = current_indicator_data.get("expanded", False)
        is_expanded = new_indicator_data[
            "expanded"
        ]  # Use the value from new_indicator_data

        # Default other keys if they might not be in new_indicator_data and are expected by later logic
        if "has_candidates" not in new_indicator_data:
            new_indicator_data["has_candidates"] = False
        if "candidate_count" not in new_indicator_data:
            new_indicator_data["candidate_count"] = 0
        if "candidates_list" not in new_indicator_data:
            new_indicator_data["candidates_list"] = []
        # linked_tidal_track_id will be preserved if it exists, or set/removed by specific statuses

        indicator_text = ""

        if status == "linking":
            final_status_text = "Linking..."
            item_data.update(
                {
                    "link_status": "linking",
                    "tidal_track": None,
                    "candidates": None,
                    "score": None,
                }
            )

        elif status == "auto_linked":
            if tidal_track:
                quality_text = Printf.map_quality(tidal_track)
            item_data.update(
                {
                    "link_status": "auto_linked",
                    "tidal_track": tidal_track,
                    "candidates": candidates,
                    "score": score,
                    "status_str": final_status_text,
                }
            )
            if candidates:
                count = len(candidates)
                new_indicator_data.update(
                    {
                        "has_candidates": True,
                        "candidate_count": count,
                        "candidates_list": candidates,
                    }
                )
                indicator_text = "-" if is_expanded else f"+ ({count})"

        elif status == "manual_review_needed":
            if tidal_track:
                quality_text = Printf.map_quality(tidal_track)
            item_data.update(
                {
                    "link_status": "manual_review_needed",
                    "tidal_track": tidal_track,
                    "candidates": candidates,
                    "score": score,
                    "status_str": final_status_text,
                }
            )
            if candidates:
                count = len(candidates)
                new_indicator_data.update(
                    {
                        "has_candidates": True,
                        "candidate_count": count,
                        "candidates_list": candidates,
                    }
                )
                indicator_text = "-" if is_expanded else f"+ ({count})"

        elif status == "found_uncertain":
            if tidal_track:
                quality_text = Printf.map_quality(tidal_track)
            item_data.update(
                {
                    "link_status": "found_uncertain",
                    "tidal_track": tidal_track,
                    "candidates": None,
                    "score": score,
                    "status_str": final_status_text,
                }
            )

        elif status == "candidates_only":
            final_status_text = (
                status_text or "Manual linking required (Candidates available)"
            )
            item_data.update(
                {
                    "link_status": "candidates_only",
                    "tidal_track": None,
                    "candidates": candidates,
                    "score": None,
                    "status_str": final_status_text,
                }
            )
            if candidates:
                count = len(candidates)
                new_indicator_data.update(
                    {
                        "has_candidates": True,
                        "candidate_count": count,
                        "candidates_list": candidates,
                    }
                )
                indicator_text = "-" if is_expanded else f"+ ({count})"

        elif status == "manual_linked":
            final_status_text = (
                status_text or f"Linked (Manual): {getattr(tidal_track, 'id', 'N/A')}"
            )
            if tidal_track:
                quality_text = Printf.map_quality(tidal_track)
            item_data.update(
                {
                    "link_status": "manual_linked",
                    "tidal_track": tidal_track,
                    "candidates": None,
                    "score": None,
                    "status_str": final_status_text,
                }
            )  # Candidates set to None

            # --- FIX FOR ISSUE 1: Store the ID of the newly linked track in the indicator data ---
            if (
                tidal_track
                and hasattr(tidal_track, "id")
                and tidal_track.id is not None
            ):
                new_indicator_data["linked_tidal_track_id"] = str(tidal_track.id)
            else:
                # If no track or ID, remove any existing linked_tidal_track_id
                new_indicator_data.pop("linked_tidal_track_id", None)
            # --- END FIX ---

            # For indicator, if it was expanded showing candidates, now it should show just '-' or be cleared
            # if it had candidates before, but now they are conceptually "resolved" by manual link.
            # The new_indicator_data should reflect if there are still candidates to show for toggling.
            if (
                current_indicator_data.get("has_candidates", False)
                and current_indicator_data.get("candidate_count", 0) > 0
            ):
                original_candidate_count = current_indicator_data.get(
                    "candidate_count", 0
                )
                original_candidates_list_raw = current_indicator_data.get(
                    "candidates_list", []
                )  # This is the raw list

                new_indicator_data.update(
                    {
                        "has_candidates": True,  # It still has candidates to show on expand
                        "candidate_count": original_candidate_count,
                        "candidates_list": original_candidates_list_raw,  # Store the raw list for _toggle_expand
                    }
                )
                # is_expanded is already derived from current_indicator_data (line 570)
                indicator_text = (
                    "-" if is_expanded else f"+ ({original_candidate_count})"
                )
            else:
                # If there were no candidates to begin with (e.g. direct link without prior candidates), clear the indicator.
                new_indicator_data.update(
                    {
                        "has_candidates": False,
                        "candidate_count": 0,
                        "candidates_list": [],
                    }
                )
                indicator_text = ""

        elif status == "not_found":
            final_status_text = "Not Found"
            item_data.update(
                {
                    "link_status": "not_found",
                    "tidal_track": None,
                    "candidates": None,
                    "score": None,
                }
            )

        elif status == "error":
            final_status_text = "Error"
            quality_text = "-"
            item_data.update(
                {
                    "link_status": "error",
                    "error_message": error_message,
                    "tidal_track": None,
                    "candidates": None,
                    "score": None,
                }
            )

        else:  # Default / not_linked / cached_linked
            final_status_text = status_text or "Not Linked"
            current_link_status_in_item_data = item_data.get(
                "link_status", "not_linked"
            )
            if (
                status in ["not_linked", "cached_linked"]
                and current_link_status_in_item_data != status
            ):
                item_data.update({"link_status": status})  # Update if different
            # Ensure these keys exist if not already, especially for 'not_linked'
            if "tidal_track" not in item_data:
                item_data["tidal_track"] = None
            if "candidates" not in item_data:
                item_data["candidates"] = None
            if "score" not in item_data:
                item_data["score"] = None

            if status == "cached_linked" and item_data.get(
                "candidates"
            ):  # item_data here is from title item
                cached_candidates = item_data.get("candidates")
                if isinstance(cached_candidates, list) and len(cached_candidates) > 0:
                    count = len(cached_candidates)
                    new_indicator_data.update(
                        {
                            "has_candidates": True,
                            "candidate_count": count,
                            "candidates_list": cached_candidates,
                        }
                    )
                    indicator_text = "-" if is_expanded else f"+ ({count})"

        # --- Set Item Text and Store Updated Data ---
        if status_item:
            if table.cellWidget(row_index, STATUS_COLUMN_INDEX) is not None:
                table.removeCellWidget(row_index, STATUS_COLUMN_INDEX)
                logger.debug(
                    f"Removed cell widget from row {row_index}, col {STATUS_COLUMN_INDEX} before setting link status text."
                )
            status_item.setText(final_status_text)
            # Store the updated item_data with the Status column item
            status_item.setData(
                Qt.ItemDataRole.UserRole, item_data.copy()
            )  # Store a copy for the status column
            logger.debug(
                f"[UpdateStatus Row {row_index}] Set UserRole data for Status column item. Link status: {item_data.get('link_status')}"
            )

        # --- START: Update Title Column Item's UserRole Data ---
        if (
            title_item
            and isinstance(current_title_item_data_raw, dict)
            and current_title_item_data_raw.get("type") == "spotify_track"
        ):
            # current_title_item_data_raw is the dict from the Title item.
            # item_data is the dict that has been updated with linking results.
            # We update current_title_item_data_raw with the relevant fields from item_data.
            current_title_item_data_raw["link_status"] = item_data.get("link_status")
            current_title_item_data_raw["tidal_track"] = item_data.get("tidal_track")
            current_title_item_data_raw["candidates"] = item_data.get("candidates")
            current_title_item_data_raw["score"] = item_data.get("score")
            current_title_item_data_raw["status_str"] = item_data.get("status_str")
            if "error_message" in item_data:
                current_title_item_data_raw["error_message"] = item_data.get(
                    "error_message"
                )
            elif "error_message" in current_title_item_data_raw:
                del current_title_item_data_raw["error_message"]

            # If 'tidal_track' is None in item_data (e.g. not_found), ensure it's also None
            if "tidal_track" in item_data and item_data["tidal_track"] is None:
                current_title_item_data_raw["tidal_track"] = None

            title_item.setData(Qt.ItemDataRole.UserRole, current_title_item_data_raw)
            # --- MODIFIED LOG ---
            has_tidal_track_obj = isinstance(
                current_title_item_data_raw.get("tidal_track"), Track
            )
            logger.debug(
                f"[UpdateStatus Row {row_index}] Updated UserRole data for Title column item. New link_status: {current_title_item_data_raw.get('link_status')}, Has TidalTrack Object: {has_tidal_track_obj}"
            )
            # --- END MODIFIED LOG ---
        elif title_item and isinstance(
            item_data.get("tidal_track"), Track
        ):  # For direct Tidal tracks, ensure title item has the track
            title_item.setData(Qt.ItemDataRole.UserRole, item_data.get("tidal_track"))
            logger.debug(
                f"[UpdateStatus Row {row_index}] Updated UserRole data for Title column (Tidal track) with Track object."
            )
        # --- END: Update Title Column Item's UserRole Data ---

        if quality_item:
            quality_item.setText(quality_text)

        if indicator_item:
            indicator_item.setText(indicator_text)
            indicator_item.setData(Qt.ItemDataRole.UserRole, new_indicator_data)
            logger.debug(
                f"[UpdateStatus Row {row_index}] Final Indicator set to '{indicator_text}', Data: {new_indicator_data}"
            )
        else:
            logger.error(
                f"[UpdateStatus Row {row_index}] indicator_item is None. Cannot set text/data."
            )

        table._update_row_appearance_for_row(row_index)
        viewport = table.viewport()
        if viewport:
            viewport.update()
        else:
            logger.warning(
                f"Viewport not available for table, cannot force update for row {row_index}"
            )

        if status in ["manual_review_needed", "candidates_only"]:
            logger.debug(
                f"--- POST-UPDATE DEBUG FOR ROW {row_index} (Status: {status}) ---"
            )
            logger.debug(
                f"  Status Item Data (Col 6): {status_item.data(Qt.ItemDataRole.UserRole) if status_item else 'N/A'}"
            )
            logger.debug(
                f"  Title Item Data (Col 1): {title_item.data(Qt.ItemDataRole.UserRole) if title_item else 'N/A'}"
            )
            logger.debug(
                f"  Called table._update_row_appearance_for_row({row_index}) and viewport().update()"
            )
            logger.debug(f"--- END POST-UPDATE DEBUG FOR ROW {row_index} ---")

        if (
            self.main_view
            and hasattr(self.main_view, "download_handler")
            and self.main_view.download_handler
        ):
            logger.debug(
                f"update_linking_status for row {row_index}: Triggering download button text update."
            )
            self.main_view.download_handler._update_download_button_text()

    def update_quality_cell(self, row_index: int, quality_str: str):
        """Updates the quality cell text for a given row."""
        if not self.table_widget:
            return
        table = self.table_widget
        if row_index < table.rowCount():
            item = table.item(row_index, 5)  # Quality is column 5
            if not item:
                item = QTableWidgetItem(quality_str)
                table.setItem(row_index, 5, item)
            else:
                item.setText(quality_str)
            logger.debug(
                f"TableHandler updated quality for row {row_index} to '{quality_str}'"
            )
        else:
            logger.warning(
                f"TableHandler: Attempted to update quality for non-existent row {row_index}"
            )

    # Removed @pyqtSlot decorator
    def collapse_sub_row(self, main_row_index: int):
        """Collapses the sub-row after a manual link."""
        if not self.table_widget:
            return
        self.table_widget.collapseSubRow(main_row_index)

    @pyqtSlot(str, int)
    def update_track_progress(self, track_id: str, percentage: int):
        """Slot to update the progress bar for a specific track."""
        logger.debug(
            f"SLOT update_track_progress: Received track_id={repr(track_id)} (Type: {type(track_id)}), percentage={percentage}"
        )
        # Log current dictionary keys for comparison
        current_keys = list(self.progress_bars.keys())
        logger.debug(
            f"SLOT update_track_progress: Current keys in self.progress_bars: {current_keys}"
        )

        progressBar = self.progress_bars.get(track_id)
        if progressBar:
            logger.debug(
                f"SLOT update_track_progress: Found progress bar instance: {progressBar} for track_id: {repr(track_id)}. Current value: {progressBar.value()}"
            )
            percentage = max(0, min(percentage, 100))
            progressBar.setValue(percentage)
            logger.debug(
                f"SLOT update_track_progress: Set progress bar value for {repr(track_id)} to {percentage}"
            )
        else:
            logger.warning(
                f"SLOT update_track_progress: Progress bar NOT FOUND in dictionary for track_id: {repr(track_id)}"
            )

    @pyqtSlot(str, int)
    @pyqtSlot(str, int)
    @pyqtSlot(str, int)
    @pyqtSlot(str, int)
    def setup_progress_bar_for_download(self, track_id: str, row_index: int):
        """
        Sets up the progress bar widget in the table for a given track.
        This slot is called from the main GUI thread via a signal.
        """
        if not self.table_widget or not ("Status" in self.column_indices):
            logger.error(
                f"MainThread: Cannot setup progress bar for track {track_id}: table_widget or Status column missing."
            )
            return

        logger.debug(
            f"MainThread SLOT setup_progress_bar_for_download: track_id={track_id}, row_index={row_index}"
        )
        table = self.table_widget
        status_col_index = self.column_indices["Status"]

        if row_index < table.rowCount():
            # Try to get an existing progress bar for this track_id
            progressBar_to_use = self.progress_bars.get(track_id)

            if not progressBar_to_use:
                logger.warning(
                    f"MainThread: QProgressBar for track {track_id} not found in self.progress_bars. Creating new one."
                )
                progressBar_to_use = QProgressBar()
                progressBar_to_use.setRange(0, 100)
                progressBar_to_use.setFixedHeight(14)  # Or a height that fits your row
                progressBar_to_use.setTextVisible(False)
                # Apply QSS directly if needed, or rely on global stylesheet
                # progressBar_to_use.setStyleSheet("QProgressBar { qproperty-textVisible: false; /* other styles */ }")
                self.progress_bars[track_id] = progressBar_to_use

            progressBar_to_use.setValue(0)  # Reset value

            # --- Directly set the QProgressBar as the cell widget ---
            # Ensure it's not parented elsewhere before setting
            if (
                progressBar_to_use.parent() is not None
                and progressBar_to_use.parent() is not table.viewport()
            ):
                logger.warning(
                    f"MainThread: ProgressBar for track {track_id} had an unexpected parent ({progressBar_to_use.parent()}). Detaching."
                )
                progressBar_to_use.setParent(None)

            table.setCellWidget(row_index, status_col_index, progressBar_to_use)

            # Ensure the underlying QTableWidgetItem exists and clear its text
            status_item = table.item(row_index, status_col_index)
            if status_item:
                status_item.setText("")
            else:
                status_item = QTableWidgetItem("")
                table.setItem(row_index, status_col_index, status_item)

            logger.debug(
                f"MainThread: Successfully set QProgressBar widget directly for track {track_id} at row {row_index}"
            )
        else:
            logger.error(
                f"MainThread: Invalid row index {row_index} for track {track_id} when setting up progress bar."
            )


# --- END OF FILE gui_table_handler.py ---
