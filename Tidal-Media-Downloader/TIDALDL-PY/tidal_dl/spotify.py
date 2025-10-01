#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   spotify.py
@Time    :   14-04-2025
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Handles Spotify API interactions, authentication, and playlist fetching.
"""

import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import logging
import time
import requests
import threading
from io import BytesIO  # Needed for loading image data

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set specific level for this module
# logger.propagate = False # Removed: Allow propagation if level permits

# --- Monkey Patch spotipy.client logging ---
from spotipy.client import logger as spotipy_logger

_original_spotipy_debug = spotipy_logger.debug


def _spotipy_debug_wrapper(msg, *args, **kwargs):
    """
    Wrapper to conditionally suppress specific spotipy debug messages
    based on the effective level of the tidal_dl.spotify logger.
    """
    # Get the logger for the current module (tidal_dl.spotify)
    # This logger's level (set by logger.setLevel) determines suppression.
    module_logger = logging.getLogger(__name__)

    # Default: Assume we should call the original debug function
    should_call_original = True

    # Check if the message is one of the specific noisy types we might suppress
    if isinstance(msg, str):
        is_results_msg = msg.startswith("RESULTS:")
        is_sending_get_msg = msg.startswith(
            "Sending GET to https://api.spotify.com/v1/"
        )

        # Suppress ONLY if it's a noisy message AND the module logger is NOT enabled for DEBUG
        if (is_results_msg or is_sending_get_msg) and not module_logger.isEnabledFor(
            logging.DEBUG
        ):
            should_call_original = False  # Don't call original, effectively suppressing

    # Call the original debug function only if suppression rules didn't apply
    if should_call_original:
        _original_spotipy_debug(msg, *args, **kwargs)


# Apply the patch
spotipy_logger.debug = _spotipy_debug_wrapper
logger.info(
    "Applied monkey patch to spotipy.client.logger.debug"
)  # Confirm patch application
# --- End Monkey Patch ---

# Import necessary components from the project
from tidal_dl.settings import SETTINGS  # Import the singleton instance
from tidal_dl.paths import getProfilePath  # Import function to get profile directory
from tidal_dl.printf import Printf  # For logger/output
import aigpy  # For directory creation

# Import Qt components needed for the handler
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtGui import QPixmap, QIcon  # +++ Import QPixmap and QIcon +++
from PyQt6.QtWidgets import QTreeWidgetItem

from tidal_dl.tidal import TIDAL_API, Track  # +++ Import global TIDAL_API and Track +++
from PyQt6.QtCore import (
    QObject,
    QRunnable,
    pyqtSignal,
    pyqtSlot,
)  # +++ Import Qt threading components +++

# Forward declaration for type hinting MainView without circular import
from typing import Optional, List  # Keep Optional, List

# TODO: Define necessary scopes for reading playlists
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative"


class SpotifyAPI:
    """
    Manages connection and data retrieval from Spotify API.
    """

    def __init__(self):
        logger.debug("Initializing SpotifyAPI")
        self.sp = None
        self.auth_manager = None
        self.cache_path = os.path.join(getProfilePath(), ".spotify_token_cache.json")
        logger.debug(f"Spotify token cache path set to: {self.cache_path}")
        try:
            # Ensure the directory for the cache file exists
            aigpy.path.mkdirs(getProfilePath())
            logger.debug(f"Ensured profile directory exists: {getProfilePath()}")
        except Exception as e:
            logger.error(
                f"Failed to create profile directory for Spotify cache: {e}",
                exc_info=True,
            )
            Printf.err(f"Error setting up Spotify cache directory: {e}")

    def _initialize_auth_manager(self):
        """Initializes the SpotifyOAuth manager."""
        logger.debug("Initializing SpotifyOAuth manager")
        try:
            # Read credentials directly from the global SETTINGS object
            client_id = getattr(SETTINGS, "spotifyClientId", None)
            client_secret = getattr(SETTINGS, "spotifyClientSecret", None)
            # Default to the required loopback IP literal format
            redirect_uri = getattr(
                SETTINGS, "spotifyRedirectUri", "http://127.0.0.1:8888/callback"
            )

            # --- START: Added detailed credential logging ---
            logger.debug("Attempting to load Spotify credentials from SETTINGS object.")
            # Log the full values to the debug log for thorough inspection
            logger.debug(
                f"SETTINGS.spotifyClientId is: '{getattr(SETTINGS, 'spotifyClientId', 'Not Found')}'"
            )
            logger.debug(
                f"SETTINGS.spotifyClientSecret is: '{getattr(SETTINGS, 'spotifyClientSecret', 'Not Found')}'"
            )
            # --- END: Added detailed credential logging ---

            if not client_id or not client_secret:
                # Changed from error to info, as this is expected if user hasn't configured Spotify
                logger.info(
                    "Spotify Client ID or Secret not found in settings. Spotify features will be unavailable."
                )
                # Provide a more helpful message to the user in the main UI
                Printf.info(
                    "Spotify integration skipped: Please enter your Client ID and Secret in the 'Spotify Account Settings' section."
                )
                # Return specific indicator instead of None
                return "CREDENTIALS_MISSING"

            logger.debug(
                f"Spotify Client ID loaded: {client_id[:4]}..."
            )  # Log partial ID for privacy
            logger.debug(
                f"Spotify Client Secret loaded: {client_secret[:4]}..."
            )  # Log partial secret for privacy
            logger.debug(f"Spotify Redirect URI: {redirect_uri}")
            logger.debug(f"Spotify Scopes: {SPOTIFY_SCOPES}")

        except AttributeError as e:
            logger.error(
                f"Error accessing Spotify settings: {e}. Please ensure settings are loaded.",
                exc_info=True,
            )
            Printf.err(f"Error accessing Spotify settings: {e}")
            return None

        # Use the defined cache path and settings
        try:
            self.auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope=SPOTIFY_SCOPES,
                cache_path=self.cache_path,  # Use the defined cache path
                show_dialog=False,  # Set to False - might improve cache usage consistency
                open_browser=True,  # Allow spotipy to open the browser automatically
            )
            # --- UPDATED LOG MESSAGE ---
            logger.debug(
                "SpotifyOAuth manager created successfully with show_dialog=False and open_browser=True."
            )
            # --- END UPDATE ---
            return self.auth_manager
        except Exception as e:
            logger.error(f"Failed to create SpotifyOAuth manager: {e}", exc_info=True)
            Printf.err(f"Error initializing Spotify authentication: {e}")
            return None

    # --- NEW AUTHENTICATE METHOD ---
    def authenticate(self, check_cache_only=False):
        """
        Authenticates the user with Spotify. Handles silent and interactive flows.

        Args:
            check_cache_only (bool):
                If True, attempts to use cache or refresh token silently. Fails if
                user interaction would be required.
                If False, attempts cache/refresh first, but WILL open browser
                for user login/authorization if needed.
        """
        flow_type = "Silent" if check_cache_only else "Interactive"
        logger.info(f"Attempting {flow_type} Spotify authentication...")

        if not self.auth_manager:
            logger.debug(
                "Auth manager not initialized, calling _initialize_auth_manager()"
            )
            init_result = self._initialize_auth_manager()
            if init_result == "CREDENTIALS_MISSING":
                # Changed from error to info
                logger.info("Skipping Spotify authentication: Credentials missing.")
                return "CREDENTIALS_MISSING"
            elif not init_result:
                logger.error(
                    "Failed to initialize Spotify auth manager during authentication (generic error)."
                )
                return False

        if not self.auth_manager:
            logger.error("Spotify auth manager is None after initialization attempt.")
            return False

        token_info = None
        max_retries = 2  # Reduce retries slightly, maybe 2 is enough
        retry_delay = 3

        for attempt in range(max_retries):
            try:
                logger.info(
                    f"{flow_type} Authentication Attempt {attempt + 1}/{max_retries}..."
                )

                # --- Core Authentication Logic ---
                if check_cache_only:
                    # Use check_cache=True: tries cache AND refresh, but NO browser interaction.
                    # Returns None if both fail or require interaction.
                    logger.debug(
                        "Calling auth_manager.get_access_token(check_cache=True) for silent check."
                    )
                    token_info = self.auth_manager.get_access_token(check_cache=True)
                    if not token_info:
                        logger.warning(
                            "Silent authentication failed (cache/refresh unsuccessful or requires interaction)."
                        )
                        # No need to retry silent check, it won't change without interaction
                        return False
                else:
                    # Use check_cache=False: tries cache/refresh first, THEN opens browser if needed.
                    logger.debug(
                        "Calling auth_manager.get_access_token(check_cache=False) for interactive check."
                    )
                    token_info = self.auth_manager.get_access_token(check_cache=False)
                    if not token_info:
                        # This means the interactive flow (browser) was likely cancelled or failed.
                        logger.warning(
                            "Interactive authentication failed or was cancelled by user."
                        )
                        Printf.err(
                            "Spotify login required or failed. If a browser window opened, ensure you completed the authorization."
                        )
                        return False
                # --- End Core Authentication Logic ---

                # --- Token Handling (if successful) ---
                if token_info:
                    logger.info(
                        f"Successfully obtained/validated Spotify access token ({flow_type} flow)."
                    )
                    # Log token details (optional)
                    # logger.debug(f"Token Info: expires_at={token_info.get('expires_at')}, scope='{token_info.get('scope')}', has_refresh_token={token_info.get('refresh_token') is not None}")

                    # Explicitly save token to ensure persistence, especially after refresh
                    try:
                        import json

                        logger.debug(
                            f"Attempting to explicitly write token_info to cache file: {self.cache_path}"
                        )
                        with open(self.cache_path, "w") as f:
                            json.dump(token_info, f)
                        logger.info(f"Explicitly wrote token_info to {self.cache_path}")
                    except Exception as explicit_write_err:
                        logger.error(
                            f"Failed to explicitly write token_info to cache file: {explicit_write_err}",
                            exc_info=True,
                        )
                        Printf.err(
                            f"Warning: Failed to update Spotify token cache file: {explicit_write_err}"
                        )

                    # Configure spotipy client
                    self.sp = spotipy.Spotify(
                        auth_manager=self.auth_manager, requests_timeout=60
                    )
                    logger.debug("spotipy.Spotify client created.")
                    return True  # SUCCESS! Exit method.

                # If token_info is None after the attempt (should only happen in check_cache_only=True path now)
                logger.warning(
                    f"{flow_type} attempt {attempt + 1} resulted in no token_info."
                )
                # If it was check_cache_only, we already returned False. If interactive, it means failure.
                return (
                    False  # Should technically be unreachable if logic above is correct
                )

            # --- Exception Handling (Retry Logic) ---
            except spotipy.SpotifyOauthError as e:
                error_message = str(e).lower()
                is_server_error = (
                    "502" in error_message
                    or "503" in error_message
                    or "server error" in error_message
                    or "bad gateway" in error_message
                )
                logger.warning(
                    f"Spotify OAuth Error on {flow_type} attempt {attempt + 1}: {e}"
                )
                if is_server_error and attempt < max_retries - 1:
                    logger.warning(
                        f"Detected potential server error. Retrying in {retry_delay}s..."
                    )
                    Printf.warning(f"Spotify auth failed (Server Error). Retrying...")
                    time.sleep(retry_delay)
                    continue  # Go to next attempt
                else:
                    logger.error(
                        f"Spotify OAuth error during authentication (final attempt or non-retryable): {e}",
                        exc_info=True,
                    )
                    Printf.err(f"Spotify authentication error: {e}")
                    self.auth_manager = None  # Reset on definitive failure
                    self.sp = None
                    return False  # Failed

            except requests.exceptions.RequestException as req_e:
                logger.warning(
                    f"Network error during Spotify auth {flow_type} attempt {attempt + 1}: {req_e}",
                    exc_info=True,
                )
                if attempt < max_retries - 1:
                    Printf.warning(f"Spotify auth failed (Network Error). Retrying...")
                    time.sleep(retry_delay)
                    continue  # Go to next attempt
                else:
                    logger.error(
                        f"Spotify auth failed due to network error after {attempt + 1} attempts: {req_e}",
                        exc_info=True,
                    )
                    Printf.err(
                        f"Spotify authentication failed due to a network error: {req_e}"
                    )
                    self.auth_manager = None  # Reset on definitive failure
                    self.sp = None
                    return False  # Failed after retries

            except Exception as e:
                logger.error(
                    f"Unexpected error during Spotify {flow_type} authentication attempt {attempt + 1}: {e}",
                    exc_info=True,
                )
                if attempt < max_retries - 1:
                    Printf.warning(f"Unexpected error during Spotify auth. Retrying...")
                    time.sleep(retry_delay)
                    continue  # Retry on generic errors too
                else:
                    Printf.err(
                        f"Spotify authentication failed due to an unexpected error: {e}"
                    )
                    self.auth_manager = None  # Reset on definitive failure
                    self.sp = None
                    return False  # Failed after retries
            # --- End Exception Handling ---

        # If loop finishes without returning True (e.g., all retries failed)
        logger.error(
            f"Spotify {flow_type} authentication failed after {max_retries} attempts."
        )
        Printf.err("Spotify authentication failed after multiple attempts.")
        self.auth_manager = None  # Reset on definitive failure
        self.sp = None
        return False

    # --- END NEW AUTHENTICATE METHOD ---

    def get_user_playlists(self):
        """Fetches the current user's playlists."""
        logger.debug("get_user_playlists called")
        if not self.sp:
            logger.warning(
                "Not authenticated with Spotify in get_user_playlists. Attempting auth."
            )
            Printf.warning("Not authenticated with Spotify. Please authenticate first.")
            if not self.authenticate():
                logger.error("Authentication failed within get_user_playlists.")
                return None  # Return None or empty list on auth failure
            # Check again if self.sp was set after successful authenticate()
            if not self.sp:
                logger.error(
                    "self.sp is still None after successful authentication call."
                )
                Printf.err(
                    "Internal error: Spotify client not available after authentication."
                )
                return None

        playlists_data = []
        try:
            results = self.sp.current_user_playlists(
                limit=50
            )  # Max limit is 50 per page
            # +++ Add logger to inspect the raw API response +++
            logger.debug(f"Raw results from self.sp.current_user_playlists: {results}")
            if not results:
                logger.warning("current_user_playlists returned None or empty.")
                Printf.warning("Could not retrieve playlists from Spotify.")
                return []  # Return empty list

            playlists = results.get("items", [])
            # +++ Add logger to inspect the extracted 'items' list +++
            logger.debug(
                f"Extracted 'items' (playlists) list (first 5): {playlists[:5]}"
            )
            while results and results.get("next"):
                logger.debug(f"Fetching next page of playlists from {results['next']}")
                try:
                    results = self.sp.next(results)
                    if results and results.get("items"):
                        playlists.extend(results["items"])
                    else:
                        # Break if next page is empty or results are None
                        logger.warning(
                            "No more items found on next page or error fetching next page."
                        )
                        break
                except Exception as page_e:
                    logger.error(
                        f"Error fetching next page of playlists: {page_e}",
                        exc_info=True,
                    )
                    Printf.err(f"Error fetching subsequent playlist page: {page_e}")
                    # Decide whether to return partial list or None
                    break  # Stop pagination on error

            logger.debug(f"Total playlists fetched: {len(playlists)}")
            for item in playlists:
                # Add basic type checking for safety
                if isinstance(item, dict) and item.get("id") and item.get("name"):
                    owner_info = item.get("owner", {})
                    tracks_info = item.get("tracks", {})
                    playlists_data.append(
                        {
                            "id": item["id"],
                            "name": item["name"],
                            "owner": (
                                owner_info.get("display_name", "Unknown")
                                if isinstance(owner_info, dict)
                                else "Unknown"
                            ),
                            "tracks_total": (
                                tracks_info.get("total", 0)
                                if isinstance(tracks_info, dict)
                                else 0
                            ),
                            "images": item.get("images", []),  # +++ Add images list +++
                        }
                    )
                else:
                    logger.warning(f"Skipping invalid playlist item: {item}")

            logger.info(f"Processed {len(playlists_data)} valid playlists.")
            Printf.info(f"Found {len(playlists_data)} playlists.")
            return playlists_data
        except spotipy.SpotifyException as e:
            logger.error(
                f"Spotify API error fetching playlists: {e.http_status} - {e.msg}",
                exc_info=True,
            )
            Printf.err(f"Spotify API error fetching playlists: {e.msg}")
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error fetching Spotify playlists: {e}", exc_info=True
            )
            Printf.err(f"Error fetching Spotify playlists: {e}")
            Printf.err("Check log file for detailed traceback.")
            return None

    def get_playlist_tracks(self, playlist_id):
        """Fetches all tracks for a given playlist ID."""
        logger.debug(f"get_playlist_tracks called for playlist_id: {playlist_id}")
        if not self.sp:
            logger.warning(
                "Not authenticated with Spotify in get_playlist_tracks. Attempting auth."
            )
            Printf.warning("Not authenticated with Spotify. Please authenticate first.")
            if not self.authenticate():
                logger.error("Authentication failed within get_playlist_tracks.")
                return None
            if not self.sp:
                logger.error(
                    "self.sp is still None after successful authentication call."
                )
                Printf.err(
                    "Internal error: Spotify client not available after authentication."
                )
                return None

        tracks_data = []
        try:
            logger.info(f"Fetching tracks for Spotify playlist ID: {playlist_id}...")
            Printf.info(f"Fetching tracks for Spotify playlist ID: {playlist_id}...")
            offset = 0
            limit = 100  # Max limit per page

            while True:
                logger.debug(
                    f"Fetching playlist items for {playlist_id}, limit={limit}, offset={offset}"
                )
                # Request additional fields: duration_ms and external_ids (for ISRC)
                results = self.sp.playlist_items(
                    playlist_id,
                    fields="items(track(id, name, artists(name), album(name), duration_ms, external_ids)),next",
                    limit=limit,
                    offset=offset,
                    additional_types=["track"],
                )

                if not results:
                    logger.warning(
                        f"playlist_items returned None or empty for offset {offset}."
                    )
                    break

                items = results.get("items", [])
                if not items:
                    logger.debug(
                        f"No more items found for playlist {playlist_id} at offset {offset}."
                    )
                    break  # No more tracks

                logger.debug(f"Fetched {len(items)} items for offset {offset}.")
                for item in items:
                    track_info = item.get("track")
                    # Ensure track_info is a dict and has an ID before processing
                    if isinstance(track_info, dict) and track_info.get("id"):
                        artists = track_info.get("artists", [])
                        album_info = track_info.get("album", {})
                        external_ids = track_info.get(
                            "external_ids", {}
                        )  # Get external IDs dict
                        isrc = external_ids.get("isrc")  # Extract ISRC if available
                        duration_ms = track_info.get("duration_ms")  # Extract duration

                        tracks_data.append(
                            {
                                "id": track_info["id"],
                                "name": track_info.get("name", "Unknown Track"),
                                "artists": [
                                    artist.get("name", "Unknown Artist")
                                    for artist in artists
                                    if isinstance(artist, dict)
                                ],
                                "album": (
                                    album_info.get("name", "Unknown Album")
                                    if isinstance(album_info, dict)
                                    else "Unknown Album"
                                ),
                                "duration_ms": duration_ms,  # Add duration
                                "isrc": isrc,  # Add ISRC
                            }
                        )
                    # Removed misplaced font setting code from here
                    else:
                        logger.warning(
                            f"Skipping invalid track item in playlist {playlist_id}: {item}"
                        )

                if results.get("next"):
                    offset += limit
                else:
                    logger.debug(
                        f"No 'next' URL found, finished fetching tracks for {playlist_id}."
                    )
                    break  # No more pages

            logger.info(
                f"Processed {len(tracks_data)} tracks for playlist {playlist_id}."
            )
            Printf.info(f"Found {len(tracks_data)} tracks in playlist {playlist_id}.")
            return tracks_data
        except spotipy.SpotifyException as e:
            logger.error(
                f"Spotify API error fetching tracks for playlist {playlist_id}: {e.http_status} - {e.msg}",
                exc_info=True,
            )
            Printf.err(f"Spotify API error fetching tracks: {e.msg}")
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error fetching tracks for playlist {playlist_id}: {e}",
                exc_info=True,
            )
            Printf.err(f"Error fetching tracks for playlist {playlist_id}: {e}")
            Printf.err("Check log file for detailed traceback.")
            return None


# --- Spotify GUI Interaction Handler ---


def _format_duration_ms(ms: Optional[int]) -> str:
    """Formats duration in milliseconds to MM:SS string."""
    if ms is None:
        return "-"
    try:
        seconds = int(ms / 1000)
        minutes = seconds // 60
        seconds %= 60
        return f"{minutes:02}:{seconds:02}"
    except (ValueError, TypeError):
        logger.warning(f"Could not format duration from ms: {ms}")
        return "-"
