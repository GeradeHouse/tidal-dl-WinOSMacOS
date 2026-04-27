#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_auth_handler.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.1
@Desc    :   Handles Tidal and Spotify authentication logic for the GUI using Qt-safe threading.
"""

import logging
import time

# Use QThread for background tasks
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QDateTime, QThread

# Import project components
from ..login import initialize_and_login, getLoginUrl, pollForToken, saveToken
from ..settings import SETTINGS
from ..tidal import TIDAL_API
from ..spotify import SpotifyAPI
from ..printf import Printf
from .gui_custom_dialog import CustomQMessageBox

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# Set up GUI logging with INFO level for this module (auth operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


# --- Worker for TIDAL Web Login ---
class TidalWebLoginWorker(QObject):
    """
    Worker object to handle the blocking network call for TIDAL web login.
    """

    # Signal to emit the retrieved login URL or an error message.
    finished = pyqtSignal(object)  # Use object to allow for None
    error = pyqtSignal(str)

    def run(self):
        """Perform the web login task to get the URL."""
        try:
            # Use the new non-blocking function
            logger.debug("[AuthHandler] TidalWebLoginWorker: Calling getLoginUrl()...")
            login_url = getLoginUrl()
            logger.debug(f"[AuthHandler] TidalWebLoginWorker: Got URL: {login_url}")
            self.finished.emit(login_url)
        except Exception as e:
            error_msg = f"Error getting TIDAL login URL: {e}"
            logger.error(error_msg, exc_info=True)
            self.error.emit(error_msg)


# --- Worker for TIDAL Token Polling ---
class TidalTokenPollingWorker(QObject):
    """
    Worker object to handle the blocking call for TIDAL token polling.
    """

    finished = pyqtSignal(bool)  # Emits True on success, False on failure

    def run(self):
        """Perform the token polling task."""
        try:
            # Get timeout and interval from the API object, which was configured by getLoginUrl
            timeout = TIDAL_API.key.authCheckTimeout
            interval = TIDAL_API.key.authCheckInterval
            start_time = time.time()

            # Defensive checks for timeout/interval values
            if timeout is None: timeout = 0
            if interval is None: interval = 5

            logger.debug(f"[AuthHandler] Starting polling loop. Timeout: {timeout}s, Interval: {interval}s")

            while time.time() - start_time < timeout:
                # pollForToken() returns a string status: "SUCCESS", "PENDING", etc.
                status = pollForToken()

                if status == "SUCCESS":
                    logger.debug("[AuthHandler] Polling returned SUCCESS.")
                    self.finished.emit(True)  # Emit boolean True on success
                    return  # Success, exit the worker

                elif status in ("PENDING", "SLOW_DOWN"):
                    time.sleep(interval)
                    if status == "SLOW_DOWN":
                        interval += 5  # Increase interval as requested by API
                        logger.debug(f"[AuthHandler] Polling slowed down. New interval: {interval}s")

                else:  # Any other status string is an unexpected failure
                    logger.error(f"TIDAL token polling failed with unexpected status: {status}")
                    self.finished.emit(False) # Emit boolean False on failure
                    return

            # If the while loop finishes, it means we timed out
            logger.error("TIDAL token polling timed out.")
            self.finished.emit(False) # Emit boolean False on timeout

        except Exception as e:
            logger.error(f"Exception during TIDAL token polling: {e}", exc_info=True)
            self.finished.emit(False) # Emit boolean False on exception


# --- Worker for Spotify Authentication ---
class SpotifyAuthWorker(QObject):
    """
    Worker object to handle the blocking network call for Spotify authentication.
    """

    # Signal to emit the authentication result (bool or str)
    finished = pyqtSignal(object)

    def __init__(self, spotify_api: SpotifyAPI, check_cache_only: bool):
        super().__init__()
        self.spotify_api = spotify_api
        self.check_cache_only = check_cache_only

    def run(self):
        """Perform the authentication task."""
        logger.debug("Spotify auth worker running.")
        auth_result = False
        try:
            auth_result = self.spotify_api.authenticate(
                check_cache_only=self.check_cache_only
            )
        except Exception as e:
            logger.error(f"Exception in Spotify auth worker: {e}", exc_info=True)
            auth_result = str(e)  # Pass error message back
        finally:
            logger.debug(
                f"Spotify auth worker finished. Emitting result: {auth_result}"
            )
            self.finished.emit(auth_result)


class AuthHandler(QObject):
    """
    Manages authentication flows for Tidal and Spotify using QThread workers.
    Emits signals upon completion or failure.
    """

    # Signals
    tidalLoginSuccess = pyqtSignal()
    tidalLoginFailure = pyqtSignal(str)
    spotifyLoginFinished = pyqtSignal(object)  # Re-emit signal from SpotifyAPI/Handler

    _thread: QThread | None = None

    def __init__(self, spotify_api: SpotifyAPI, parent=None):
        super().__init__(parent)
        self.spotify_api = spotify_api
        self._parent_widget = parent  # Store parent widget for message boxes
        self._last_spotify_trigger_was_interactive: bool = False
        # To prevent worker objects from being garbage collected prematurely
        self.thread_pool = []

    def check_initial_logins(self):
        """Checks initial login status for Tidal and Spotify."""
        logger.debug("[AuthHandler] Calling initialize_and_login() to set up API key...")
        initialize_and_login()
        logger.debug("[AuthHandler] initialize_and_login() finished.")

        from tidal_dl.login import loginByConfig

        logger.info("Checking initial login status...")

        # --- Check TIDAL Login ---
        tidal_logged_in = loginByConfig()

        if not tidal_logged_in:
            logger.info(
                "TIDAL token invalid or missing/expired. Initiating web login flow."
            )
            logger.info("TIDAL login required. Please follow the instructions...")
            self.start_tidal_web_login()
        else:
            logger.info("TIDAL token loaded successfully from config.")
            logger.info("TIDAL login successful.")
            self.tidalLoginSuccess.emit()  # Notify successful login

        # --- Check Spotify Auto-Login ---
        if SETTINGS.autoSpotifyLogin:
            logger.info(
                "Spotify auto-login enabled. Triggering SILENT Spotify login check..."
            )
            self.trigger_spotify_login(
                check_cache_only=True
            )  # Attempt SILENT login first
        else:
            logger.info("Spotify auto-login disabled.")
            self.spotifyLoginFinished.emit(
                False
            )  # Emit False initially if auto-login is off

    def start_tidal_web_login(self):
        """Initiates the Tidal web login flow using a QThread worker."""
        self._thread = QThread()
        self.worker = TidalWebLoginWorker()
        self.worker.moveToThread(self._thread)

        # Connect signals and slots
        self._thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_tidal_url_received)
        self.worker.error.connect(self._on_tidal_login_error)

        # Clean up
        self.worker.finished.connect(self._thread.quit)
        self.worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self.worker.finished.connect(self.worker.deleteLater)

        # Keep a reference
        self.thread_pool.append((self._thread, self.worker))

        self._thread.start()

    def _on_tidal_url_received(self, login_url: str):
        """Handle the successful retrieval of the TIDAL login URL."""
        full_url = login_url if login_url.startswith(("http://", "https://")) else f"https://{login_url}"
        CustomQMessageBox.information(
            self._parent_widget,
            "TIDAL Login Required",
            "Browser Login Required",
            f"Please visit the following URL in your browser to log in.\n\nURL: <a href='{full_url}'>{full_url}</a>\n\nYou have 5 minutes to complete the login."
        )
        # Start the dedicated polling worker in the background.
        self.start_tidal_token_polling()

    def _on_tidal_login_error(self, error_msg: str):
        """Handle errors during TIDAL login initiation."""
        CustomQMessageBox.information(
            self._parent_widget,
            "TIDAL Login Error",
            "Web Login Failed",
            f"An error occurred while trying to start the web login process.\n\nDetails: {error_msg}"
        )
        self.tidalLoginFailure.emit(f"Error starting login: {error_msg}")

    def start_tidal_token_polling(self):
        """Initiates the Tidal token polling flow using a dedicated QThread worker."""
        logger.info("Starting background worker for TIDAL token polling...")

        self.poll_thread = QThread()
        self.poll_worker = TidalTokenPollingWorker()
        self.poll_worker.moveToThread(self.poll_thread)

        # Connect signals and slots
        self.poll_thread.started.connect(self.poll_worker.run)
        self.poll_worker.finished.connect(self._on_tidal_polling_finished)

        # Clean up
        self.poll_worker.finished.connect(self.poll_thread.quit)
        self.poll_thread.finished.connect(self.poll_thread.deleteLater)
        self.poll_worker.finished.connect(self.poll_worker.deleteLater)

        # Keep a reference
        self.thread_pool.append((self.poll_thread, self.poll_worker))

        self.poll_thread.start()

    def _on_tidal_polling_finished(self, success: bool):
        """Handle the result of the token polling worker."""
        if success:
            logger.info("TIDAL token polling successful!")
            # Save the token to file
            saveToken()

            CustomQMessageBox.information(
                self._parent_widget,
                "TIDAL Login Successful",
                "Login Completed",
                "You have successfully logged into TIDAL.\n\nReady to use the application."
            )
            self.tidalLoginSuccess.emit()
        else:
            logger.error("TIDAL token polling failed or timed out.")
            CustomQMessageBox.information(
                self._parent_widget,
                "TIDAL Login Failed",
                "Authentication Failed",
                "The web login process failed or timed out after 5 minutes.\n\nPlease try logging in again."
            )
            self.tidalLoginFailure.emit("Login failed or timed out.")

    def trigger_spotify_login(self, check_cache_only=False):
        """
        Initiates Spotify login process in a QThread worker via SpotifyAPI.
        """

        logger.info(
            f"Triggering Spotify login (check_cache_only={check_cache_only})..."
        )
        self._last_spotify_trigger_was_interactive = (
            not check_cache_only
        )  # True if user-triggered
        logger.info("Attempting Spotify login...")

        self._thread = QThread()
        self.worker = SpotifyAuthWorker(self.spotify_api, check_cache_only)
        self.worker.moveToThread(self._thread)

        # Connect signals and slots
        self._thread.started.connect(self.worker.run)

        # This is where the magic happens: the worker's result is directly emitted
        # by the AuthHandler's signal. Since the AuthHandler lives on the main
        # thread, this signal emission is safe.
        self.worker.finished.connect(self.spotifyLoginFinished)

        # Clean up
        self.worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self.worker.finished.connect(self.worker.deleteLater)

        # Keep a reference
        self.thread_pool.append((self._thread, self.worker))

        self._thread.start()
