#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_search_handler.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Manages search operations for the Tidal Media Downloader GUI.
"""

import logging
import threading

# import traceback # Removed unused import
# Import List, Dict, Optional, Any only if needed for explicit type hints beyond forward refs
from typing import TYPE_CHECKING, List, Any, Union, Tuple, Optional  # Added Optional

# Import only necessary Qt components
from PyQt6 import QtCore
from PyQt6.QtCore import (
    QObject,
    pyqtSignal,
    pyqtSlot,
    QThread,
)  # Keep pyqtSlot, Add QThread

# from PyQt6.QtCore import Qt # Removed unused import
# from PyQt6.QtCore import QPoint # Removed unused import
# from PyQt6.QtWidgets import QTableWidgetItem, QMenu # Removed unused import

# Import project components
# from .gui_table import SplitterTable # Removed unused import
# Import necessary models and API
from tidal_dl.tidal import (
    Type,
    Track,
    Album,
    Playlist,
    Artist,
    TIDAL_API,
    SearchResult,
)  # Keep models, add SearchResult

# from ..tidal import AudioQuality # Removed unused import
from tidal_dl.printf import Printf

# from .gui_utils import format_duration_ms # Removed unused import
# from ..persistence import LinkPersistenceManager # Removed unused import

# Forward declaration for type hinting
if TYPE_CHECKING:
    from tidal_dl.gui.gui import MainView

    # from .gui_download import DownloadHandler # Removed unused import
    # from .gui_linking_handler import LinkingGuiHandler # Removed unused import

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (search operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


# --- Worker Class for Live Search ---
class LiveSearchWorker(QObject):
    """Worker object to perform live search in a background thread."""

    # Emit search_id along with results/error
    resultsReady = pyqtSignal(list, int)
    error = pyqtSignal(str, int)

    def __init__(self, query: str, search_id: int, parent=None):
        super().__init__(parent)
        self.query = query
        self.search_id = search_id

    @pyqtSlot()
    def run(self):
        """Performs the live search API calls, checking for interruption."""
        thread = self.thread()  # Get the thread this worker runs in
        if not self.query:
            self.resultsReady.emit([], self.search_id)
            return

        combined_results = []
        limit_per_type = 3  # Limit results for each type in live search

        try:
            # Check for interruption before starting
            if thread and thread.isInterruptionRequested():
                return

            # Search for Tracks
            try:
                if thread and thread.isInterruptionRequested():
                    return
                s_result_tracks_union = TIDAL_API.search(
                    self.query, Type.Track, limit=limit_per_type
                )
                if thread and thread.isInterruptionRequested():
                    return  # Check after potentially blocking call
                if isinstance(s_result_tracks_union, SearchResult):
                    tracks = TIDAL_API.getSearchResultItems(
                        s_result_tracks_union, Type.Track
                    )
                    combined_results.extend(tracks)
                elif isinstance(s_result_tracks_union, tuple):
                    s_result_tracks: SearchResult = s_result_tracks_union[0]
                    tracks = TIDAL_API.getSearchResultItems(s_result_tracks, Type.Track)
                    combined_results.extend(tracks)
            except Exception as track_err:
                logger.error(
                    f"Error searching tracks live (ID:{self.search_id}): {track_err}",
                    exc_info=False,
                )

            # Search for Albums
            try:
                if thread and thread.isInterruptionRequested():
                    return
                s_result_albums_union = TIDAL_API.search(
                    self.query, Type.Album, limit=limit_per_type
                )
                if thread and thread.isInterruptionRequested():
                    return
                if isinstance(s_result_albums_union, SearchResult):
                    albums = TIDAL_API.getSearchResultItems(
                        s_result_albums_union, Type.Album
                    )
                    combined_results.extend(albums)
                elif isinstance(s_result_albums_union, tuple):
                    s_result_albums: SearchResult = s_result_albums_union[0]
                    albums = TIDAL_API.getSearchResultItems(s_result_albums, Type.Album)
                    combined_results.extend(albums)
            except Exception as album_err:
                logger.error(
                    f"Error searching albums live (ID:{self.search_id}): {album_err}",
                    exc_info=False,
                )

            # Search for Artists
            try:
                if thread and thread.isInterruptionRequested():
                    return
                s_result_artists_union = TIDAL_API.search(
                    self.query, Type.Artist, limit=limit_per_type
                )
                if thread and thread.isInterruptionRequested():
                    return
                if isinstance(s_result_artists_union, SearchResult):
                    artists = TIDAL_API.getSearchResultItems(
                        s_result_artists_union, Type.Artist
                    )
                    combined_results.extend(artists)
                elif isinstance(s_result_artists_union, tuple):
                    s_result_artists: SearchResult = s_result_artists_union[0]
                    artists = TIDAL_API.getSearchResultItems(
                        s_result_artists, Type.Artist
                    )
                    combined_results.extend(artists)
            except Exception as artist_err:
                logger.error(
                    f"Error searching artists live (ID:{self.search_id}): {artist_err}",
                    exc_info=False,
                )

            # Final check before emitting
            if thread and not thread.isInterruptionRequested():
                self.resultsReady.emit(combined_results, self.search_id)

        except Exception as e:
            # Catch any broader errors during the process
            error_msg = f"An unexpected error occurred during live search worker (ID:{self.search_id}): {e}"
            logger.error(error_msg, exc_info=True)
            if thread and not thread.isInterruptionRequested():
                self.error.emit(error_msg, self.search_id)  # Emit error signal with ID


class SearchHandler(QObject):
    """Manages search operations, interacting with the Tidal API and emitting results."""

    # Signals
    # Refine signal types: results_array is List[Any], search_context can be Album, Playlist, etc.
    searchResultsReady = pyqtSignal(
        list, Type, object
    )  # results_array, result_type, search_context
    searchFailed = pyqtSignal(str)  # error_message
    liveSearchResultsReady = pyqtSignal(list)  # For live search dropdown (only results)
    data_fetched = pyqtSignal(list, str)

    def __init__(self, parent: "MainView"):
        super().__init__(parent)
        self.main_view = parent  # Keep reference for context/messages
        self.live_search_thread: Optional[QThread] = None
        self.live_search_worker: Optional[LiveSearchWorker] = None
        self._latest_search_id = 0  # Counter for live searches

    def _fetch_data_thread(self, item_type: Type, item_id: str):
        """
        Fetches full data for a search item in a background thread.
        This method is designed to be called from a non-GUI thread.
        """
        logger.debug(f"Thread started for {item_type.name} {item_id}")
        results: List[Any] = []
        error_msg = ""
        try:
            if item_type == Type.Album:
                album_data = TIDAL_API.getItems(item_id, Type.Album)
                if album_data:
                    results, _ = album_data
            elif item_type == Type.Artist:
                results = TIDAL_API.getArtistTopTracks(
                    item_id, limit=50
                )  # Use new method
            elif item_type == Type.Track:
                track = TIDAL_API.getTrack(item_id)
                if track:
                    results = [track]
        except Exception as e:
            error_msg = f"Failed to fetch data for {item_type.name} {item_id}: {e}"
            logger.error(error_msg, exc_info=True)

        # Safely invoke the slot on the main thread to emit the final signal
        QtCore.QMetaObject.invokeMethod(
            self,
            "_on_data_fetched",  # Call the new slot
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(list, results),
            QtCore.Q_ARG(str, error_msg),
        )

    @pyqtSlot(list, str)
    def _on_data_fetched(self, results: list, error_msg: str):
        """
        This slot is called on the main GUI thread with data from the worker thread.
        It emits the final data_fetched signal.
        """
        self.data_fetched.emit(results, error_msg)

    @pyqtSlot(dict)
    def _on_result_item_clicked(self, result_data: dict):
        """
        Handles a click on a search result item by unpacking the data dict,
        showing a loading message, and starting a thread to fetch the full data.
        """
        item_type = result_data.get("type")
        item_id = result_data.get("id")

        if not item_type or not item_id:
            logger.error(f"Invalid search result data received by slot: {result_data}")
            self.searchFailed.emit("Clicked on an invalid search item.")
            return

        logger.debug(f"Item click slot activated: {item_type.name} {item_id}")
        self.main_view.table_handler.show_loading_message(
            f"Loading {item_type.name}..."
        )

        thread = threading.Thread(
            target=self._fetch_data_thread, args=(item_type, item_id)
        )
        thread.start()

    def perform_search(self, query: str):  # Removed search_type parameter
        """
        Performs a search based on the query (primarily for URLs), emitting results or errors.
        Keyword searches are primarily handled by the live search mechanism.
        """
        logger.debug(f"SearchHandler: Performing search. Query='{query}'")
        if not query:
            self.searchFailed.emit("Please enter a search query or URL.")
            logger.warning("Search attempt with empty input.")
            return

        search_context: Any = None  # To store album/playlist if URL points to tracks
        actual_result_type: Optional[Type] = None  # Initialize type

        try:
            results_array: List[Any] = []

            if query.startswith("http://") or query.startswith("https://"):
                logger.debug(f"Processing input as URL: {query}")
                logger.info(f"Fetching item from URL: '{query}'")
                tmpType, tmpId = TIDAL_API.parseUrl(query)

                if tmpType == Type.Null:
                    self.searchFailed.emit(
                        "The provided URL is not recognized or supported."
                    )
                    return

                tmpData = TIDAL_API.getTypeData(tmpId, tmpType)
                if tmpData is None:
                    self.searchFailed.emit(
                        "Could not retrieve data for the provided URL."
                    )
                    return

                # If URL points to Track, fetch Album context
                if (
                    tmpType == Type.Track
                    and hasattr(tmpData, "album")
                    and tmpData.album
                ):
                    if hasattr(tmpData.album, "id") and tmpData.album.id:
                        try:
                            search_context = TIDAL_API.getAlbum(str(tmpData.album.id))
                        except Exception as album_err:
                            logger.warning(
                                f"Failed to fetch album context {tmpData.album.id} for {tmpType.name} {tmpId}: {album_err}"
                            )
                            search_context = None
                    else:
                        logger.warning(
                            f"Item {tmpId} ({tmpType.name}) has album attribute but no valid album ID."
                        )
                elif tmpType == Type.Playlist:
                    search_context = tmpData

                results_array = [tmpData]
                actual_result_type = tmpType
                logger.info(f"Displaying item from URL: {actual_result_type.name}")

            else:
                # Handle non-URL input (keyword search) - Now primarily handled by live search.
                # Emit an error or perform a default search if desired.
                logger.warning(
                    f"Keyword search via Enter press ('{query}') is not the primary mechanism. Use live search suggestions or enter a URL."
                )
                # Option 1: Emit error
                self.searchFailed.emit(
                    "Enter a valid URL or use live search suggestions."
                )
                # Option 2: Perform a default search (e.g., for Tracks) - Uncomment if needed
                # logger.debug(f"Performing default keyword search (Track) for: '{query}'")
                # try:
                #     s_result_union = TIDAL_API.search(query, Type.Track, limit=50)
                #     if isinstance(s_result_union, tuple): s_result = s_result_union[0]
                #     elif isinstance(s_result_union, SearchResult): s_result = s_result_union
                #     else: raise TypeError(f"Unexpected return type: {type(s_result_union)}")
                #     results_array = TIDAL_API.getSearchResultItems(s_result, Type.Track)
                #     actual_result_type = Type.Track
                #     logger.info(f"Default search returned {len(results_array)} tracks.")
                # except Exception as default_search_err:
                #      logger.error(f"Default keyword search failed: {default_search_err}", exc_info=True)
                #      self.searchFailed.emit(f"Default search failed: {default_search_err}")
                #      results_array = [] # Ensure empty results on error
                # Option 3: Do nothing and let the live search results remain (if any)
                return  # Exit if handling as error or doing nothing

            if not results_array:
                # This case might be hit if URL parsing succeeded but yielded no data,
                # or if default search (if enabled) failed/returned nothing.
                self.searchFailed.emit("No results found.")
                # Emit empty results to clear table, use determined type or None
                self.searchResultsReady.emit(
                    [],
                    actual_result_type if actual_result_type else Type.Null,
                    search_context,
                )
                return

            # Emit results
            self.searchResultsReady.emit(
                results_array, actual_result_type, search_context
            )

        except Exception as e:
            error_msg = f"An error occurred during search: {e}"
            logger.error(error_msg, exc_info=True)
            self.searchFailed.emit(f"Search failed: {e}")
            # Emit empty results on error
            self.searchResultsReady.emit(
                [], Type.Null, None
            )  # Use Null type on general error

    @pyqtSlot(str)
    def perform_live_search(self, query: str):
        """
        Starts a background worker thread to perform live search, ensuring only
        the latest results are processed and old threads are cleaned up safely.
        """
        logger.debug(f"-> perform_live_search START (Query: '{query}')")

        # --- Request Interruption of Previous Search (if any) ---
        # Don't clear references or delete here, just signal it to stop.
        if self.live_search_thread and self.live_search_thread.isRunning():
            old_worker_id = (
                self.live_search_worker.search_id
                if self.live_search_worker
                else "unknown"
            )
            logger.debug(
                f"Requesting interruption for previous running thread (ID {old_worker_id})"
            )
            self.live_search_thread.requestInterruption()
            # The old thread's finished signal is already connected to _cleanup_finished_thread

        if not query:
            self.liveSearchResultsReady.emit([])  # Emit empty if query is cleared
            # No need to explicitly clear here, interruption was requested if running.
            return

        # --- Increment Search ID ---
        self._latest_search_id += 1
        current_search_id = self._latest_search_id
        logger.debug(
            f"Starting live search ID: {current_search_id} for query: '{query}'"
        )

        # --- Setup New Worker and Thread ---
        # Create new instances for the new search
        new_thread = QThread(self)
        new_worker = LiveSearchWorker(query, current_search_id)
        new_worker.moveToThread(new_thread)
        logger.debug(
            f"Created new worker (ID {current_search_id}) and thread ({hex(id(new_thread))})"
        )

        # --- Connect Signals (Passing Search ID and Objects for Cleanup) ---
        # Use lambda to capture the current_search_id for the slot connection
        new_worker.resultsReady.connect(
            lambda results, search_id: self._handle_live_results(results, search_id)
        )
        new_worker.error.connect(
            lambda error_msg, search_id: self._handle_live_error(error_msg, search_id)
        )
        new_thread.started.connect(new_worker.run)

        # --- Cleanup Connection ---
        # Connect the finished signal to trigger the final cleanup *after* this specific thread's event loop exits.
        # Pass the thread and worker objects themselves to the cleanup slot.
        new_thread.finished.connect(
            lambda thread=new_thread, worker=new_worker: self._cleanup_finished_thread(
                thread, worker
            )
        )

        # --- Overwrite References and Start Thread ---
        # Now safe to replace references with the new ones
        self.live_search_thread = new_thread
        self.live_search_worker = new_worker

        logger.debug(f"Starting thread (ID {current_search_id})")
        self.live_search_thread.start()
        logger.debug(f"Live search thread (ID: {current_search_id}) started.")

    # Slot now accepts search_id
    @pyqtSlot(list, int)
    def _handle_live_results(self, results: list, search_id: int):
        """Handles results received from the live search worker, checking search ID."""
        logger.debug(
            f"---> _handle_live_results START (ID {search_id}, Latest: {self._latest_search_id})"
        )  # ADDED LOG
        logger.debug(
            f"Live search worker finished (ID: {search_id}). Received {len(results)} results."
        )

        # Only process results if the search ID matches the latest requested search
        if search_id == self._latest_search_id:
            logger.debug(
                f"Emitting liveSearchResultsReady for latest search ID {search_id} with {len(results)} results."
            )
            self.liveSearchResultsReady.emit(results)
        else:
            logger.debug(
                f"Ignoring results from outdated live search worker (ID: {search_id}, Latest: {self._latest_search_id})."
            )

    # Slot now accepts search_id
    @pyqtSlot(str, int)
    def _handle_live_error(self, error_msg: str, search_id: int):
        """Handles errors received from the live search worker, checking search ID."""
        logger.debug(
            f"---> _handle_live_error START (ID {search_id}, Latest: {self._latest_search_id})"
        )  # ADDED LOG
        logger.error(f"Live search worker error (ID: {search_id}): {error_msg}")

        # Only process/log error if it's from the latest search attempt
        if search_id == self._latest_search_id:
            logger.warning(
                f"Error occurred for latest search ID {search_id}. Emitting empty results."
            )
            # Optionally emit empty results on error for the *latest* search
            self.liveSearchResultsReady.emit([])
        else:
            logger.debug(
                f"Ignoring error from outdated live search worker (ID: {search_id}, Latest: {self._latest_search_id})."
            )

    # --- NEW Cleanup Slot ---
    @pyqtSlot(QThread, LiveSearchWorker)
    def _cleanup_finished_thread(self, thread: QThread, worker: LiveSearchWorker):
        """
        Safely cleans up a specific worker and thread after the thread has finished.
        Called via the thread's finished signal.
        """
        worker_id = worker.search_id if worker else "unknown"
        logger.debug(
            f"--> _cleanup_finished_thread START (ID {worker_id}, Thread: {hex(id(thread))})"
        )

        # --- Handle Worker ---
        if worker:
            logger.debug(f"Disconnecting signals for worker {hex(id(worker))}")
            # Disconnect signals explicitly before scheduling deletion.
            try:
                worker.resultsReady.disconnect()
            except (TypeError, RuntimeError):  # Catch if already disconnected or error
                pass  # Ignore errors, goal is cleanup
            try:
                worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass

            logger.debug(f"Scheduling worker.deleteLater() for {hex(id(worker))}")
            worker.deleteLater()

        # --- Handle Thread ---
        # Thread is already finished, so just schedule deletion.
        if thread:
            logger.debug(f"Scheduling thread.deleteLater() for {hex(id(thread))}")
            thread.deleteLater()

        logger.debug(f"--> _cleanup_finished_thread END (ID {worker_id})")
