import logging
import threading
from typing import TYPE_CHECKING, Any, Optional, List, Dict, Union

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, Qt

from tidal_dl.spotify import SpotifyAPI
from tidal_dl.printf import Printf
from tidal_dl.tidal import Type, Track, TIDAL_API
from tidal_dl.gui.gui_custom_dialog import ModernDarkDialog
from tidal_dl import paths

if TYPE_CHECKING:
    from tidal_dl.gui.gui import MainView

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (Spotify operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class SpotifyGuiHandler(QObject):
    def __init__(self, main_view: "MainView", spotify_api: SpotifyAPI):
        super().__init__()
        self.main_view = main_view
        self.spotify_api = spotify_api

    def loginSpotify(self, check_cache_only: bool = False):
        from tidal_dl.settings import SETTINGS

        if not SETTINGS.autoSpotifyLogin and check_cache_only:
            logger.info("Spotify auto-login is disabled. Skipping silent check.")
            self.main_view.s_spotifyLoginFinished.emit(False)
            return

        logger.info("Spotify login initiated...")

        def _spotify_auth_thread(initial_check_cache_only: bool):
            auth_result: Union[bool, str] = False
            try:
                logger.info(
                    "Attempting silent Spotify authentication (cache/refresh)..."
                )
                auth_result = self.spotify_api.authenticate(check_cache_only=True)

                if not auth_result and not initial_check_cache_only:
                    Printf.warning(
                        "Spotify cache/refresh failed. Please login via browser..."
                    )
                    auth_result = self.spotify_api.authenticate(check_cache_only=False)
            except Exception as e:
                auth_result = f"Error: {e}"
            finally:
                self.main_view.s_spotifyLoginFinished.emit(auth_result)

        thread = threading.Thread(
            target=_spotify_auth_thread, args=(check_cache_only,), daemon=True
        )
        thread.start()

    def onSpotifyLoginFinished(self, auth_result: Union[bool, str]):
        logger.debug(
            f"SpotifyGuiHandler.onSpotifyLoginFinished called with auth_result: {auth_result}"
        )

        if isinstance(auth_result, str) and "CREDENTIALS_MISSING" in auth_result.upper():
            was_user_triggered = getattr(
                self.main_view.auth_handler,
                "_last_spotify_trigger_was_interactive",
                False,
            )
            if was_user_triggered:
                logger.info(
                    "Spotify Client ID and Secret are missing. Please configure them in Settings."
                )
                if self.main_view.navigation_handler:
                    self.main_view.navigation_handler.show_settings()

                QtWidgets.QApplication.processEvents()

                if self.main_view.settingsPage:
                    self.main_view.settingsPage.expandSpotifySection()

                try:
                    info_icon_path = paths.resource_path("assets/icons/info_icon.png")
                except Exception:
                    info_icon_path = None

                informative_text_for_dialog = (
                    "After entering them, click 'Save' at the bottom of the settings page, then try connecting to Spotify again. "
                    "For setup help, click 'How to get Spotify Client ID and Secret?' in the settings."
                )
                custom_dialog = ModernDarkDialog(
                    title="Spotify Credentials Missing",
                    main_message="Please enter Spotify Client ID and Secret in the 'Spotify Account Settings' section.",
                    informative_text=informative_text_for_dialog,
                    icon_path=info_icon_path,
                    parent=self.main_view,
                )
                custom_dialog.exec()

                if hasattr(
                    self.main_view.auth_handler, "_last_spotify_trigger_was_interactive"
                ):
                    self.main_view.auth_handler._last_spotify_trigger_was_interactive = (
                        False
                    )
            else:
                Printf.warning(
                    "Spotify auto-login failed: Credentials missing. Configure in Settings to enable Spotify features."
                )

            self.main_view.tree_handler.update_spotify_root_item(logged_in=False)
            return

        self.main_view.tree_handler.update_spotify_root_item(
            logged_in=(auth_result is True)
        )

        if auth_result is True:
            logger.info("Spotify login successful!")
            self.fetchSpotifyPlaylists()
        elif isinstance(auth_result, str):
            logger.warning(f"Spotify login failed. Reason: {auth_result}.")
        else:
            Printf.warning(
                "Spotify login failed. Please check credentials or network connection."
            )

    def refreshSpotifyPlaylists(self):
        logger.info("Refreshing Spotify playlists...")
        self.fetchSpotifyPlaylists()

    def fetchSpotifyPlaylists(self):
        logger.info("Fetching Spotify playlists...")

        def _spotify_playlist_thread():
            fetched_playlists: Optional[list] = None
            try:
                fetched_playlists = self.spotify_api.get_user_playlists()
            except Exception as e:
                logger.error(
                    f"Exception in Spotify playlist fetch thread: {e}", exc_info=True
                )
            finally:
                self.main_view.s_spotifyPlaylistsFetched.emit(fetched_playlists or [])

        thread = threading.Thread(target=_spotify_playlist_thread, daemon=True)
        thread.start()

    def fetchSpotifyTracks(self, playlist_id: str):
        logger.info(f"Fetching tracks for Spotify playlist ID: {playlist_id}")
        # Show loading row in table
        table_widget = self.main_view.tableWidget
        table_widget.clearRows()
        table_widget.addRow(["Loading Spotify tracks..."], None)

        def _spotify_track_thread():
            fetched_tracks: Optional[list] = None
            try:
                fetched_tracks = self.spotify_api.get_playlist_tracks(playlist_id)
            except Exception as e:
                logger.error(
                    f"Exception in Spotify track fetch thread for playlist {playlist_id}: {e}",
                    exc_info=True,
                )
            finally:
                self.main_view.s_spotifyTracksFetched.emit(
                    playlist_id, fetched_tracks or []
                )

        thread = threading.Thread(target=_spotify_track_thread, daemon=True)
        thread.start()
