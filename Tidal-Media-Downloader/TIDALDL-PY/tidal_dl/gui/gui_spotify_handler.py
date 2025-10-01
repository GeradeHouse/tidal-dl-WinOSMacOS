import logging
import threading
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import (
    QObject,
    pyqtSignal,
    pyqtSlot,
    QThreadPool,
    QRunnable,
    Qt,
)  # Keep threading imports, Add Qt, pyqtSlot
from PyQt6.QtGui import QPixmap, QIcon, QFont  # Keep GUI imports
from PyQt6.QtWidgets import QTreeWidgetItem, QMessageBox  # Removed unused QWidget
from ..spotify import SpotifyAPI  # Import the backend API class
from ..printf import Printf
from ..tidal import (
    Type,
    Track,
    TIDAL_API,
)  # Import TIDAL components, Removed unused AudioQuality
from .gui_utils import format_duration_ms  # Import utility function
from .gui_custom_dialog import ModernDarkDialog  # Import the new custom dialog
from .. import paths  # Import paths for resource loading

# Import workers/signals from gui_cover_cache ONLY
# Removed unused CoverSignals, CoverWorker
# from ..settings import SETTINGS # Removed unused import
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    List,
    Dict,
    Union,
)  # Keep typing, add Dict, List, Optional, Any, Union

if TYPE_CHECKING:
    from .gui import MainView  # Add MainView hint

    # from .gui_playlist_tree_handler import PlaylistTreeHandler # Removed unused import
    # from .gui_table_handler import TableHandler # Removed unused import
    from PyQt6.QtCore import QPoint  # Import QPoint for type hint
    from PyQt6.QtGui import QAction  # Import QAction for type hint
    from PyQt6.QtWidgets import (
        QTableWidgetItem,
    )  # Import QTableWidgetItem for type hint

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set specific level for this module

# --- Worker for Asynchronous Quality Fetching ---


class QualityFetchSignals(QObject):
    """Defines signals available from the QualityFetchWorker."""

    finished = pyqtSignal(int, str)  # row_index, quality_string
    error = pyqtSignal(int, str)  # row_index, error_message


class QualityFetchWorker(QRunnable, QObject):
    """
    Worker thread for fetching Tidal track quality asynchronously.
    Inherits from QRunnable for thread pool usage and QObject for signals.
    """

    def __init__(self, row_index: int, tidal_track_id: str):
        super().__init__()
        QObject.__init__(self)  # Initialize QObject part
        self.row_index = row_index
        self.tidal_track_id = tidal_track_id
        self.signals = QualityFetchSignals()

    @pyqtSlot()
    def run(self):
        """Execute the quality fetching task."""
        quality_str = "-"  # Default quality
        try:
            logger.debug(
                f"[Worker-{self.row_index}] Fetching quality for Tidal ID {self.tidal_track_id}"
            )
            # Use the global TIDAL_API instance
            tidal_track = TIDAL_API.getTrack(self.tidal_track_id)
            # Redundant check removed: isinstance(tidal_track, Track)
            if tidal_track and hasattr(tidal_track, "audioQuality"):
                # Access Printf via the main_view instance if needed, or make _map_quality static/global
                # Assuming Printf is accessible globally or _map_quality is static/class method
                quality_str = Printf.map_quality(tidal_track)
                logger.debug(
                    f"[Worker-{self.row_index}] Fetched quality for Tidal ID {self.tidal_track_id}: {quality_str}"
                )
            else:
                logger.warning(
                    f"[Worker-{self.row_index}] Could not fetch valid Tidal track or quality for ID {self.tidal_track_id}"
                )
                # Keep quality_str as "-"
            self.signals.finished.emit(self.row_index, quality_str)

        except Exception as e:
            error_msg = f"Error fetching quality in worker for Tidal ID {self.tidal_track_id}: {e}"
            logger.error(
                error_msg, exc_info=False
            )  # Log less verbosely for background errors
            # Emit error or just finish with default quality? Let's finish with default.
            self.signals.finished.emit(self.row_index, "-")  # Emit default on error
            # Optionally emit an error signal too: self.signals.error.emit(self.row_index, str(e))


# --- Spotify GUI Interaction Handler ---


class SpotifyGuiHandler(QObject):
    """
    Handles interactions between the MainView GUI and the SpotifyAPI.
    Manages login flow, data fetching triggered by UI, and UI updates.
    """

    def __init__(self, main_view: "MainView", spotify_api: SpotifyAPI):
        super().__init__()  # Initialize QObject base class
        self.main_view = main_view
        self.spotify_api = spotify_api

    # Add type hint for parameter
    def loginSpotify(self, check_cache_only: bool = False):
        """
        Initiates Spotify login process in a separate thread.

        Args:
            check_cache_only (bool): If True, attempts authentication using only
                                     the cached token without user interaction.
                                     Defaults to False.
        """
        # logger.info(f"Spotify login initiated by GUI (check_cache_only={check_cache_only})...")
        from ..settings import SETTINGS  # Import here for access

        # --- Fix: Check autoSpotifyLogin setting before proceeding ---
        if not SETTINGS.autoSpotifyLogin:
            logger.info(
                "Spotify auto-login is disabled in settings. Skipping Spotify login attempt and emitting failure signal."
            )
            # --- Fix: Emit failure signal when skipping ---
            self.main_view.s_spotifyLoginFinished.emit(False)  # type: ignore # Explicitly signal failure
            return  # Do not proceed with login if auto-login is off
            # --- End Fix ---
        # --- End Fix --- # Remove duplicate comment
        Printf.info("Spotify login initiated...")  # type: ignore

        def _spotify_auth_thread(initial_check_cache_only: bool):
            """Worker thread for Spotify authentication."""
            logger.debug(
                f"Spotify auth thread started (initial_check_cache_only={initial_check_cache_only})."
            )
            auth_result: Union[bool, str] = (
                False  # Default to False, can be string on error
            )
            try:
                # --- Step 1: Attempt silent authentication first ---
                logger.info(
                    "Attempting silent Spotify authentication (cache/refresh)..."
                )
                auth_result = self.spotify_api.authenticate(check_cache_only=True)

                if auth_result is True:  # Explicit check for True
                    logger.info("Silent Spotify authentication successful.")
                else:
                    # --- Step 2: If silent failed, attempt interactive authentication ---
                    logger.warning(
                        "Silent Spotify authentication failed. Attempting interactive login..."
                    )
                    Printf.warning("Spotify cache/refresh failed. Please login via browser...")  # type: ignore
                    # Only proceed to interactive if the initial call was meant to be interactive
                    if not initial_check_cache_only:
                        auth_result = self.spotify_api.authenticate(
                            check_cache_only=False
                        )
                    else:
                        # If the initial call was silent-only, don't proceed to interactive
                        logger.info(
                            "Initial call was silent-only, not proceeding to interactive login."
                        )
                        auth_result = False  # Ensure result is False

            except Exception as e:
                logger.debug(
                    f"_spotify_auth_thread: Emitting s_spotifyLoginFinished with error: {e}"
                )  # Inserted log
                logger.error(f"Exception in Spotify auth thread: {e}", exc_info=True)
                # Ensure auth_result remains False on exception
                auth_result = f"Error: {e}"  # Pass error message as string
            finally:
                logger.debug(
                    f"Spotify auth thread finished. Emitting result: {auth_result}"
                )
                # Emit the signal from MainView
                logger.debug(
                    f"_spotify_auth_thread: Emitting s_spotifyLoginFinished with result: {auth_result}"
                )  # Corrected log location
                self.main_view.s_spotifyLoginFinished.emit(auth_result)  # type: ignore

        # Pass the check_cache_only flag from the initial call to the thread
        thread = threading.Thread(
            target=_spotify_auth_thread, args=(check_cache_only,), daemon=True
        )
        logger.debug(
            f"SpotifyGuiHandler: Starting _spotify_auth_thread (check_cache_only={check_cache_only})..."
        )  # Corrected log location
        thread.start()

    # Add type hint for parameter
    def onSpotifyLoginFinished(self, auth_result: Union[bool, str]):
        """Handles the result of the Spotify authentication attempt (Slot for MainView signal)."""
        logger.debug(
            f"SpotifyGuiHandler.onSpotifyLoginFinished called with auth_result: {auth_result} (type: {type(auth_result)})"
        )

        if (
            isinstance(auth_result, str)
            and "CREDENTIALS_MISSING" in auth_result.upper()
        ):
            was_user_triggered = getattr(
                self.main_view.auth_handler,
                "_last_spotify_trigger_was_interactive",
                False,
            )
            logger.debug(f"Credentials missing. User triggered: {was_user_triggered}")

            if was_user_triggered:
                logger.info(
                    "Credentials missing after user-triggered Spotify login attempt. Navigating to settings."
                )
                Printf.info(
                    "Spotify Client ID and Secret are missing. Please configure them in Settings."
                )

                if (
                    hasattr(self.main_view, "navigation_handler")
                    and self.main_view.navigation_handler
                ):
                    self.main_view.navigation_handler.show_settings()
                else:
                    logger.error(
                        "MainView.navigation_handler not found. Cannot navigate to settings."
                    )
                    QtWidgets.QMessageBox.critical(
                        self.main_view,
                        "Navigation Error",
                        "Could not open settings page.",
                    )
                    self.main_view.tree_handler.update_spotify_root_item(
                        logged_in=False
                    )
                    return

                QtWidgets.QApplication.processEvents()

                if (
                    hasattr(self.main_view, "settingsPage")
                    and self.main_view.settingsPage
                ):
                    self.main_view.settingsPage.expandSpotifySection()
                else:
                    logger.error(
                        "MainView.settingsPage not found. Cannot expand Spotify section."
                    )

                # --- MODIFICATION START: Use Custom Dialog ---
                try:
                    # User specified icon path: 'Tidal-Media-Downloader\TIDALDL-PY\tidal_dl\assets\icons\info_icon.png'
                    # paths.resource_path expects 'assets/icons/info_icon.png'
                    info_icon_path = paths.resource_path("assets/icons/info_icon.png")
                except Exception as e:
                    logger.error(f"Could not resolve path for info icon: {e}")
                    info_icon_path = None  # Fallback if path resolution fails

                informative_text_for_dialog = (
                    "After entering them, click 'Save' at the bottom of the settings page, then try connecting to Spotify again. "
                    "For setup help, click 'How to get Spotify Client ID and Secret?' in the settings."
                )

                custom_dialog = ModernDarkDialog(
                    title="Spotify Credentials Missing",
                    main_message="Please enter Spotify Client ID and Secret in the 'Spotify Account Settings' section.",
                    informative_text=informative_text_for_dialog,
                    icon_path=info_icon_path,  # Pass the path to your 'i' icon
                    parent=self.main_view,
                )
                custom_dialog.exec()  # Show the custom dialog modally
                # --- MODIFICATION END ---

                if hasattr(
                    self.main_view.auth_handler, "_last_spotify_trigger_was_interactive"
                ):
                    self.main_view.auth_handler._last_spotify_trigger_was_interactive = (
                        False
                    )

                self.main_view.tree_handler.update_spotify_root_item(logged_in=False)
                # Ensure the "Connect to Spotify" button is visible if it exists
                if hasattr(self.main_view, "playlist_tree_widget") and hasattr(
                    self.main_view.playlist_tree_widget, "spotify_connect_button"
                ):
                    self.main_view.playlist_tree_widget.spotify_connect_button.setVisible(
                        True
                    )
                return
            else:
                logger.warning("Spotify auto-login failed: Credentials missing.")
                Printf.warning(
                    "Spotify auto-login failed: Credentials missing. Configure in Settings to enable Spotify features."
                )
                # Still update the root item to show not logged in
                self.main_view.tree_handler.update_spotify_root_item(logged_in=False)
                # Ensure the "Connect to Spotify" button is visible if it exists
                if hasattr(self.main_view, "playlist_tree_widget") and hasattr(
                    self.main_view.playlist_tree_widget, "spotify_connect_button"
                ):
                    self.main_view.playlist_tree_widget.spotify_connect_button.setVisible(
                        True
                    )
                # For auto-login failures due to missing credentials, we don't show popups, just log.
                # The UI will reflect "not logged in".
                return  # Prevent further processing for this specific auto-login failure case

        # --- Original logic for other cases (successful login or other types of failures) ---
        self.main_view.tree_handler.update_spotify_root_item(
            logged_in=(auth_result is True)
        )

        if auth_result is True:
            logger.info("Spotify login successful! Fetching playlists.")
            Printf.success("Spotify login successful!")
            self.fetchSpotifyPlaylists()
        elif auth_result is False:  # Generic failure
            logger.warning("Spotify login failed (generic).")
            Printf.warning(
                "Spotify login failed. Please check credentials or network connection. See console for details."
            )
        elif isinstance(
            auth_result, str
        ):  # Error string (but not "CREDENTIALS_MISSING" handled above)
            logger.warning(f"Spotify login failed. Reason: {auth_result}")
            Printf.warning(
                f"Spotify login failed. Reason: {auth_result}. Check console output."
            )

        # Ensure the "Connect to Spotify" button state is updated if login failed for any reason not handled above
        if auth_result is not True:
            if hasattr(self.main_view, "playlist_tree_widget") and hasattr(
                self.main_view.playlist_tree_widget, "spotify_connect_button"
            ):
                self.main_view.playlist_tree_widget.spotify_connect_button.setVisible(
                    True
                )
            # Update spotifyButtonStack if it exists (though its direct use for connect/disconnect seems to have changed)
            if hasattr(self.main_view, "spotifyButtonStack"):
                try:
                    getattr(self.main_view, "spotifyButtonStack").setCurrentIndex(0)
                except Exception as e:
                    logger.error(f"Error setting spotifyButtonStack index: {e}")

    def refreshSpotifyPlaylists(self):
        """Slot called when the 'Refresh Spotify Account' button is clicked."""
        logger.info("Spotify refresh requested by user.")
        Printf.info("Refreshing Spotify playlists...")  # type: ignore
        # Disable button while refreshing
        # Call the existing fetch method
        self.fetchSpotifyPlaylists()

    def fetchSpotifyPlaylists(self):
        """Fetches Spotify playlists in a separate thread."""
        logger.info("Fetching Spotify playlists...")
        Printf.info("Fetching Spotify playlists...")  # type: ignore
        # Update GUI elements via main_view
        logger.debug(
            f"Inspecting main_view before potential error point. Type: {type(self.main_view)}"
        )
        logger.debug(
            f"Does main_view have 'spotify_root_item'? {hasattr(self.main_view, 'spotify_root_item')}"
        )
        # logger.debug(f"Does main_view have 'spotify_playlists_root'? {hasattr(self.main_view, 'spotify_playlists_root')}") # Removed as attribute was removed
        # The spotify_playlists_root item was removed in UI refactoring.
        # Playlist population is handled in onSpotifyPlaylistsFetched using the header widget.

        def _spotify_playlist_thread():
            logger.debug("Spotify playlist fetch thread started.")
            fetched_playlists: Optional[list] = None  # Add type hint
            try:
                # Call the core API's method
                fetched_playlists = self.spotify_api.get_user_playlists()
            except Exception as e:
                logger.error(
                    f"Exception in Spotify playlist fetch thread: {e}", exc_info=True
                )
            finally:
                logger.debug(
                    f"Spotify playlist fetch thread finished. Emitting {len(fetched_playlists) if fetched_playlists else 0} playlists."
                )
                # Emit the signal from MainView
                self.main_view.s_spotifyPlaylistsFetched.emit(fetched_playlists if fetched_playlists is not None else [])  # type: ignore

        thread = threading.Thread(target=_spotify_playlist_thread, daemon=True)
        thread.start()

    # Add type hint for parameter
    def onSpotifyPlaylistsFetched(self, playlists: list):
        """Populates the tree with fetched Spotify playlists (Slot for MainView signal)."""
        logger.debug(
            f"SpotifyGuiHandler.onSpotifyPlaylistsFetched called with {len(playlists)} playlists."
        )
        try:
            # Access GUI elements via main_view
            spotify_root: QTreeWidgetItem = (
                self.main_view.tree_handler.spotify_root_item
            )  # Access via tree_handler
            # header_label = self.main_view.spotify_header_label # Might be redundant
            # header_widget = self.main_view.spotify_header_widget # Might be redundant

            # Clear existing children from the Spotify root item
            logger.debug(
                "Clearing existing Spotify playlist items from spotify_root_item."
            )
            while spotify_root.childCount() > 0:
                spotify_root.removeChild(spotify_root.child(0))

            playlist_count = len(playlists) if playlists else 0
            spotify_root.setText(0, f"Spotify Playlists ({playlist_count})")
            spotify_root.setHidden(False)  # Make sure the root item is visible

            if playlists:
                # header_widget.setVisible(True) # Might be redundant

                for p_data in playlists:
                    if isinstance(p_data, dict) and "name" in p_data and "id" in p_data:
                        item = QTreeWidgetItem(
                            spotify_root
                        )  # Add as child of spotify_root
                        item_text = f"{p_data.get('name', 'Unknown Name')} ({p_data.get('tracks_total', '?')})"
                        item.setText(0, item_text)

                        # Set font for child item (Medium)
                        font_child_spotify = item.font(0)
                        font_child_spotify.setFamily("Nationale")
                        font_child_spotify.setWeight(
                            QFont.Weight.Normal
                        )  # Normal = 400
                        item.setFont(0, font_child_spotify)

                        # Store data needed for click handling and lazy loading, including type
                        image_url: Optional[str] = None
                        images: list = p_data.get("images", [])
                        if images:
                            # Try to get the smallest image URL (usually last in the list)
                            image_url = images[-1].get("url")

                        item_data_dict: Dict[str, Any] = {
                            "type": "spotify",  # Mark item as Spotify type
                            "data": p_data,
                            "image_url": image_url,  # Store URL for lazy loading
                            "icon_loaded": False,  # Flag for lazy loading
                        }
                        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, item_data_dict)
                        # --- START: Immediate Cache Check ---
                        playlist_id: Optional[str] = p_data.get("id")
                        if playlist_id:
                            # get_icon_data returns a tuple: (icon_bytes, timestamp)
                            icon_bytes, icon_timestamp = (
                                self.main_view.playlist_cache_manager.get_icon_data(
                                    "spotify", playlist_id
                                )
                            )
                            # Check if icon_bytes (the actual image data) is not None
                            # icon_bytes is the first element of the tuple from get_icon_data, which should be bytes or None
                            # icon_timestamp is the second element
                            if (
                                icon_bytes is not None
                            ):  # Explicitly check if icon_bytes (the byte data) is not None
                                try:
                                    pixmap = QPixmap()
                                    # Pass only the icon_bytes to loadFromData
                                    if pixmap.loadFromData(
                                        icon_bytes
                                    ):  # This should now be correct if icon_bytes is indeed bytes
                                        icon = QIcon(pixmap)
                                        if not icon.isNull():
                                            item.setIcon(0, icon)
                                            item_data_dict["icon_loaded"] = True
                                        else:
                                            logger.warning(
                                                f"Loaded cached icon for spotify-{playlist_id}, but it is null."
                                            )
                                            # item_data_dict['icon_loaded'] = False # Already default
                                    else:
                                        logger.warning(
                                            f"Failed to load QPixmap from cached data for spotify-{playlist_id} (icon_bytes was not None but loadFromData failed). Will lazy load."
                                        )
                                        # item_data_dict['icon_loaded'] = False # Already default
                                except Exception as e:
                                    logger.error(
                                        f"Error processing cached icon during playlist population for spotify-{playlist_id}: {e}",
                                        exc_info=True,
                                    )
                                    # item_data_dict['icon_loaded'] = False # Already default
                            else:
                                logger.debug(
                                    f"No cached icon data (bytes) found for spotify-{playlist_id}."
                                )
                                # item_data_dict['icon_loaded'] = False # Already default
                        else:
                            logger.warning(
                                f"Missing playlist ID in p_data during cache check: {p_data.get('name')}. Cannot check cache."
                            )
                            # item_data_dict['icon_loaded'] = False # Already default

                        # Ensure the updated item_data_dict is set on the item
                        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, item_data_dict)
                        # --- END: Immediate Cache Check ---

                    else:
                        logger.warning(f"Skipping invalid playlist data item: {p_data}")
            else:
                logger.info(
                    "No Spotify playlists found or an error occurred during fetch."
                )
                # Access header_label via main_view if needed, but it might be handled by tree handler now
                # self.header_label.setText("Spotify Playlists (None found)")
                # self.header_widget.setVisible(True) # Keep header visible
        except Exception as e:
            logger.error(f"Error updating Spotify playlist tree: {e}", exc_info=True)
            Printf.err(f"GUI Error displaying Spotify playlists: {e}")  # type: ignore
            # Access header_label via main_view if needed
            # self.header_label.setText("Spotify Playlists (Error)") # Update header on error
            # self.header_widget.setVisible(True) # Keep header visible
            # Clear potentially existing spotify items on error - This should be handled by tree handler
            # items_to_remove = []
            # for i in range(self.tree.topLevelItemCount()):
            #      item = self.tree.topLevelItem(i)
            #      item_data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            #      if isinstance(item_data, dict) and item_data.get('type') == 'spotify':
            #          items_to_remove.append(item)
            # for item in items_to_remove:
            #      self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        finally:
            pass  # No specific cleanup needed here now

    # Add type hint for parameter
    def fetchSpotifyTracks(self, playlist_id: str):
        """Fetches tracks for a specific Spotify playlist ID (called by MainView)."""
        logger.info(f"Fetching tracks for Spotify playlist ID: {playlist_id}")
        Printf.info(f"Fetching tracks for Spotify playlist ID: {playlist_id}")  # type: ignore
        # Update GUI via main_view
        table_widget: QtWidgets.QTableWidget = getattr(self.main_view, "c_tableArea").widget()  # type: ignore
        getattr(table_widget, "clearRows")()  # type: ignore
        getattr(table_widget, "addRow")(["Loading Spotify tracks..."], None)  # type: ignore

        def _spotify_track_thread():
            logger.debug(
                f"Spotify track fetch thread started for playlist: {playlist_id}"
            )
            fetched_tracks: Optional[list] = None  # Add type hint
            try:
                # Call the core API's method
                fetched_tracks = self.spotify_api.get_playlist_tracks(playlist_id)
            except Exception as e:
                logger.error(
                    f"Exception in Spotify track fetch thread for playlist {playlist_id}: {e}",
                    exc_info=True,
                )
            finally:
                logger.debug(
                    f"Spotify track fetch thread finished for {playlist_id}. Emitting {len(fetched_tracks) if fetched_tracks else 0} tracks."
                )
                # Emit the signal from MainView
                self.main_view.s_spotifyTracksFetched.emit(playlist_id, fetched_tracks if fetched_tracks is not None else [])  # type: ignore

        thread = threading.Thread(target=_spotify_track_thread, daemon=True)
        thread.start()

    @pyqtSlot(int, str)
    def _update_quality_cell(self, row_index: int, quality_str: str):
        """Slot to update the quality cell in the table."""
        logger.debug(
            f"_update_quality_cell called for row {row_index} with quality '{quality_str}'"
        )  # Add log
        try:
            # Need to access the table widget via main_view
            table_widget: QtWidgets.QTableWidget = getattr(self.main_view, "c_tableArea").widget()  # type: ignore
            if row_index < table_widget.rowCount():  # Check if row still exists
                item: Optional[QtWidgets.QTableWidgetItem] = table_widget.item(
                    row_index, 5
                )  # Quality is column 5
                if not item:
                    # If item doesn't exist (shouldn't happen often with addRow), create it
                    item = QtWidgets.QTableWidgetItem(quality_str)
                    table_widget.setItem(row_index, 5, item)
                    logger.debug(
                        f"Created and set quality item for row {row_index} to '{quality_str}'"
                    )
                else:
                    # If item exists, just update text
                    item.setText(quality_str)
                    logger.debug(
                        f"Updated quality for row {row_index} to '{quality_str}'"
                    )
            else:
                logger.warning(
                    f"_update_quality_cell called for non-existent row {row_index} (table might have changed)"
                )
        except Exception as e:
            logger.error(
                f"Error updating quality cell for row {row_index}: {e}", exc_info=True
            )

    # Add type hints for parameters
    @pyqtSlot(str, list)
    def onSpotifyTracksFetched(self, playlist_id: str, tracks: list):
        """Displays fetched Spotify tracks in the main table (Slot for MainView signal)."""
        logger.debug(
            f"SpotifyGuiHandler.onSpotifyTracksFetched called for playlist {playlist_id} with {len(tracks)} tracks."
        )
        table_widget: Optional[QtWidgets.QTableWidget] = None  # Initialize
        try:
            # Access GUI elements via main_view
            table_widget = getattr(self.main_view, "c_tableArea").widget()  # type: ignore
            getattr(table_widget, "clearRows")()  # type: ignore

            # --- Sync with persisted links ---
            # links_changed = False # Removed unused variable
            persisted_links: Dict[str, Any] = {}  # Initialize before the inner try
            try:
                pm: LinkPersistenceManager = getattr(self.main_view, "link_persistence_manager")  # type: ignore
                persisted_links = pm.get_links_for_playlist(
                    playlist_id
                )  # Loads if needed
                # Correctly get IDs from the inner 'tracks' dictionary
                persisted_track_ids: set = set(persisted_links.get("tracks", {}).keys())
                fetched_track_ids: set = {
                    t["id"] for t in tracks if isinstance(t, dict) and "id" in t
                }

                # Remove stale links (in persisted but not in fetched)
                stale_ids: set = persisted_track_ids - fetched_track_ids
                if stale_ids:
                    logger.info(
                        f"Found {len(stale_ids)} stale links for playlist {playlist_id}. Removing..."
                    )
                    for stale_id in stale_ids:
                        pm.remove_link(
                            playlist_id, stale_id
                        )  # This loads and saves internally
                        logger.info(
                            f"Removed stale link for Spotify track ID: {stale_id} from playlist {playlist_id}"
                        )
                    # links_changed = True # Mark that changes were made # Removed unused variable
                    Printf.info(f"Removed {len(stale_ids)} stale links for playlist {playlist_id}.")  # type: ignore

                # Identify new/unlinked tracks (in fetched but not persisted)
                new_or_unlinked_ids: set = fetched_track_ids - persisted_track_ids
                if new_or_unlinked_ids:
                    logger.info(
                        f"Found {len(new_or_unlinked_ids)} new or unlinked tracks for playlist {playlist_id}."
                    )
                    # Optionally: Mark these rows specifically in the UI later, or auto-link?
                    # For now, just logger. Manual linking/relinking handles adding them.
                # Check for new tracks not yet linked (tracks in current Spotify playlist but not in JSON)
                new_spotify_ids: set = (
                    fetched_track_ids - persisted_track_ids
                )  # Use the defined variable
                if new_spotify_ids:
                    logger.info(
                        f"Detected {len(new_spotify_ids)} new Spotify tracks in playlist {playlist_id} not found in persisted links. Automatic re-linking for these is NOT currently implemented."
                    )  # Use the defined variable
                    # logger.debug(f"Example new IDs: {list(new_spotify_ids)[:5]}") # Optional: Log specific IDs # Use the defined variable

                # No explicit save needed here if remove_link saves internally
                # if links_changed:
                #     pm.save_links() # Save changes if links were removed

            except Exception as sync_e:
                logger.error(
                    f"Error syncing persisted links for playlist {playlist_id}: {sync_e}",
                    exc_info=True,
                )
                Printf.err(f"Error syncing links: {sync_e}")  # type: ignore
            # --- End Sync ---

            if not tracks:
                logger.info(
                    f"No tracks found or error fetching for Spotify playlist {playlist_id}."
                )
                getattr(table_widget, "addRow")(["No tracks found or error fetching."], None)  # type: ignore
                # Also update main_view state for empty results
                self.main_view.s_array = []  # type: ignore
                self.main_view.s_type = Type.Track  # type: ignore # Still track context, just empty
                return

            # Prepare the array for main_view state update
            spotify_track_array_for_mainview: list = []

            # Adapt table population for Spotify track data - Add 'Length', 'Quality', 'Link Status' columns
            column_headers = [
                "#",
                "Title",
                "Artists",
                "Album",
                "Length",
                "Quality",
                "Link Status",
            ]
            logger.debug(f"Setting table columns for Spotify tracks: {column_headers}")
            # Assuming setHorizontalHeaderLabels is the correct method
            # Explicitly set the column count FIRST
            getattr(table_widget, "setColumnCount")(len(column_headers))  # type: ignore
            logger.debug(
                f"Explicitly set column count to {len(column_headers)}"
            )  # Add log for confirmation
            # THEN set the header labels
            getattr(table_widget, "setHorizontalHeaderLabels")(column_headers)  # type: ignore
            # Adjust column widths after setting headers and count
            getattr(table_widget, "adjustColumnWidths")()  # type: ignore

            for index, track_data in enumerate(tracks):
                if (
                    isinstance(track_data, dict)
                    and "name" in track_data
                    and "id" in track_data
                ):
                    # Determine link status before creating rowData
                    link_status_text = ""
                    link_status_data: Dict[str, Any] = {"link_status": "not_linked"}
                    spotify_track_id: Optional[str] = track_data.get("id")
                    # Correctly check against the inner 'tracks' dictionary within persisted_links
                    persisted_tracks_dict: dict = persisted_links.get("tracks", {})
                    quality_str = (
                        "-"  # Initialize quality string with default placeholder
                    )
                    if spotify_track_id in persisted_tracks_dict:
                        # Safely get tidal_track_id, handle potential missing key
                        link_info: dict = persisted_tracks_dict.get(
                            spotify_track_id, {}
                        )
                        tidal_track_id: Optional[str] = link_info.get("tidal_track_id")
                        if tidal_track_id:
                            logger.debug(
                                f"Cached link found for Spotify ID {spotify_track_id}: Tidal ID {tidal_track_id}. Fetching quality..."
                            )
                            link_status_text = f"Linked (Cached): {tidal_track_id}"

                            # +++ Start Background Quality Fetch +++
                            # quality_str is already initialized above, worker will update it via signal/slot
                            try:
                                # Create worker
                                worker = QualityFetchWorker(index, tidal_track_id)
                                # Connect worker signal to the update slot
                                worker.signals.finished.connect(
                                    self._update_quality_cell
                                )
                                # Submit worker to the global thread pool
                                thread_pool = QThreadPool.globalInstance()
                                if thread_pool:
                                    thread_pool.start(worker)
                                else:
                                    logger.error(
                                        "Could not get QThreadPool global instance to start quality worker."
                                    )
                                logger.debug(
                                    f"Submitted quality fetch worker for row {index}, Tidal ID {tidal_track_id}"
                                )
                            except Exception as pool_err:
                                logger.error(
                                    f"Error submitting quality worker for row {index}: {pool_err}",
                                    exc_info=True,
                                )
                            # +++ End Background Fetch +++

                            link_status_data = {
                                "link_status": "cached_linked",
                                "tidal_track_id": tidal_track_id,
                                "status_str": link_status_text,
                            }
                        else:
                            logger.warning(
                                f"Persisted link for {spotify_track_id} found but missing 'tidal_track_id'."
                            )
                            link_status_text = "Link Data Error"  # Indicate issue
                            link_status_data = {
                                "link_status": "error",
                                "error_message": "Missing tidal_track_id in persisted data",
                            }

                    # Format duration
                    duration_str = format_duration_ms(
                        track_data.get("duration_ms")
                    )  # Use imported function

                    # Now create rowData with the determined status, length, and initial quality
                    rowData = [
                        str(index + 1),  # Index
                        track_data.get("name", "N/A"),  # Title
                        ", ".join(
                            track_data.get("artists", ["Unknown Artist"])
                        ),  # Artists
                        track_data.get("album", "N/A"),  # Album
                        duration_str,  # Length
                        quality_str,  # Quality (Fetched or default '-')
                        link_status_text,  # Link Status
                    ]
                    # Prepare item metadata for both table row and main_view array
                    item_metadata: Dict[str, Any] = {
                        "type": "spotify_track",
                        "data": track_data,
                    }
                    # Store Spotify track data with the row
                    getattr(table_widget, "addRow")(rowData, item_metadata)  # type: ignore
                    # Add the same metadata dict to the list for main_view state
                    spotify_track_array_for_mainview.append(item_metadata)

                    # Set data for the status item specifically (now column 6)
                    status_item: Optional[QtWidgets.QTableWidgetItem] = getattr(table_widget, "item")(index, 6)  # type: ignore
                    if status_item:
                        # Ensure status_item text matches link_status_text if addRow didn't set it
                        if status_item.text() != link_status_text:
                            status_item.setText(link_status_text)
                        status_item.setData(
                            QtCore.Qt.ItemDataRole.UserRole, link_status_data
                        )
                    else:  # Should not happen if addRow creates all items, but safety check
                        logger.warning(
                            f"Status item not found for row {index} after addRow"
                        )

                else:
                    logger.warning(
                        f"Skipping invalid track data item in playlist {playlist_id}: {track_data}"
                    )

            if table_widget:  # Ensure table_widget is not None before updating
                table_widget.update()
            logger.debug(
                f"Finished populating table with Spotify tracks for {playlist_id}."
            )

            # --- Update MainView State ---
            self.main_view.s_array = spotify_track_array_for_mainview  # type: ignore
            self.main_view.s_type = Type.Track  # type: ignore # Explicitly set type to Track for Spotify results
            logger.debug(f"Updated main_view state: s_array (len={len(self.main_view.s_array)}), s_type={self.main_view.s_type.name}")  # type: ignore
            # --- Call Table Handler to Populate UI ---
            self.main_view.table_handler.populate_spotify_tracks(playlist_id, tracks)  # type: ignore
            # --- End Update ---

        except Exception as e:
            logger.error(
                f"Error updating Spotify track table for playlist {playlist_id}: {e}",
                exc_info=True,
            )
            Printf.err(f"GUI Error displaying Spotify tracks: {e}")  # type: ignore
            try:
                if table_widget:  # Check if table_widget was assigned
                    getattr(table_widget, "clearRows")()  # type: ignore
                    getattr(table_widget, "addRow")([f"Error displaying tracks: {e}"], None)  # type: ignore
            except Exception as inner_e:
                logger.error(
                    f"Further error during error handling for Spotify tracks: {inner_e}"
                )

    @pyqtSlot(QTreeWidgetItem, QIcon)
    def _set_playlist_icon(self, item: QTreeWidgetItem, icon: QIcon):
        """Slot to set the fetched icon on the corresponding tree item."""
        try:
            if item:  # Check if item still exists
                item.setIcon(0, icon)
                logger.debug(f"Set icon for playlist item '{item.text(0)}'")
        except Exception as e:
            # Log error if setting icon fails (e.g., item deleted)
            logger.error(
                f"Error setting icon for item '{item.text(0) if item else 'N/A'}': {e}",
                exc_info=False,
            )

    @pyqtSlot(QTreeWidgetItem, str)
    def _handle_icon_error(self, item: QTreeWidgetItem, error_message: str):
        """Slot to handle errors during icon fetching (optional)."""
        logger.warning(
            f"Failed to fetch icon for item '{item.text(0) if item else 'N/A'}': {error_message}"
        )
        # Optionally set a default/error icon or do nothing
        # if item:
        #     item.setIcon(0, QIcon("path/to/error_icon.png"))
