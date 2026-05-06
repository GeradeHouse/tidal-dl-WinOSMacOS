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

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (API operations)
# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from .gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

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
from tidal_dl.settings import SETTINGS
from tidal_dl.paths import getProfilePath
import aigpy

from tidal_dl.tidal import TIDAL_API, Track
from typing import Any, Dict, List, Optional, Sequence, Union

SPOTIFY_REQUIRED_SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
)
SPOTIFY_SCOPES = " ".join(SPOTIFY_REQUIRED_SCOPES)
SPOTIFY_MUTATION_BATCH_SIZE = 100


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
        self.current_user_id: Optional[str] = None
        self.current_user_display_name: Optional[str] = None
        self.current_user_profile: Dict[str, Any] = {}
        try:
            # Ensure the directory for the cache file exists
            aigpy.path.mkdirs(getProfilePath())
            logger.debug(f"Ensured profile directory exists: {getProfilePath()}")
        except Exception as e:
            logger.error(
                f"Failed to create profile directory for Spotify cache: {e}",
                exc_info=True,
            )
            logger.error(f"Error setting up Spotify cache directory: {e}")

    def _required_scope_set(self) -> set[str]:
        return set(SPOTIFY_REQUIRED_SCOPES)

    def _token_scope_set(self, token_info: Optional[Dict[str, Any]]) -> set[str]:
        if not isinstance(token_info, dict):
            return set()
        raw_scope = token_info.get("scope") or ""
        if isinstance(raw_scope, str):
            return {scope.strip() for scope in raw_scope.split() if scope.strip()}
        if isinstance(raw_scope, (list, tuple, set)):
            return {str(scope).strip() for scope in raw_scope if str(scope).strip()}
        return set()

    def _token_has_required_scopes(self, token_info: Optional[Dict[str, Any]]) -> bool:
        token_scopes = self._token_scope_set(token_info)
        missing_scopes = self._required_scope_set() - token_scopes
        if missing_scopes:
            logger.warning(
                "Spotify token is missing required scope(s): %s",
                ", ".join(sorted(missing_scopes)),
            )
            return False
        return True

    def _clear_cached_token(self) -> None:
        try:
            if os.path.exists(self.cache_path):
                os.remove(self.cache_path)
                logger.info("Removed Spotify token cache because reauthorization is required.")
        except Exception as exc:
            logger.warning("Failed to remove Spotify token cache: %s", exc, exc_info=True)

    def _refresh_current_user_profile(self) -> None:
        if not self.sp:
            return
        try:
            profile = self.sp.current_user() or {}
            self.current_user_profile = profile if isinstance(profile, dict) else {}
            self.current_user_id = self.current_user_profile.get("id")
            self.current_user_display_name = (
                self.current_user_profile.get("display_name")
                or self.current_user_profile.get("id")
            )
            logger.info("Spotify account profile loaded: %s", self.current_user_display_name)
        except Exception as exc:
            logger.warning("Failed to fetch Spotify account profile: %s", exc, exc_info=True)
            self.current_user_profile = {}
            self.current_user_id = None
            self.current_user_display_name = None

    def _ensure_client(self) -> bool:
        if self.sp:
            return True
        auth_result = self.authenticate(check_cache_only=True)
        if auth_result is True and self.sp:
            return True
        auth_result = self.authenticate(check_cache_only=False)
        return bool(auth_result is True and self.sp)

    def _chunked(self, values: Sequence[Any], size: int = SPOTIFY_MUTATION_BATCH_SIZE) -> List[List[Any]]:
        return [list(values[index:index + size]) for index in range(0, len(values), size)]

    def _spotify_success_result(
        self,
        message: str,
        playlist_id: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        playlist: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "success": True,
            "message": message,
            "playlist_id": playlist_id,
            "snapshot_id": snapshot_id,
            "playlist": playlist,
        }

    def _spotify_error_result(
        self,
        operation: str,
        exc: Exception,
        playlist_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        http_status = getattr(exc, "http_status", None)
        retry_after = None
        headers = getattr(exc, "headers", None)
        if isinstance(headers, dict):
            retry_after = headers.get("Retry-After")

        if http_status == 401:
            message = (
                f"{operation} failed because Spotify authentication expired or is invalid. "
                "Reconnect Spotify and retry the action."
            )
            self.sp = None
        elif http_status == 403:
            message = (
                f"{operation} failed because Spotify denied access. "
                "The playlist may require ownership, collaborator access, or additional granted scopes."
            )
        elif http_status == 429:
            retry_suffix = f" Retry after {retry_after} second(s)." if retry_after else ""
            message = f"{operation} failed because Spotify rate-limited the request.{retry_suffix}"
        else:
            message = f"{operation} failed: {getattr(exc, 'msg', str(exc))}"

        logger.error("%s", message, exc_info=True)
        return {
            "success": False,
            "message": message,
            "playlist_id": playlist_id,
            "snapshot_id": None,
            "playlist": None,
            "http_status": http_status,
            "retry_after": retry_after,
        }

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
                logger.info(
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
            logger.error(f"Error accessing Spotify settings: {e}")
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
            logger.error(f"Error initializing Spotify authentication: {e}")
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
                        logger.error(
                            "Spotify login required or failed. If a browser window opened, ensure you completed the authorization."
                        )
                        return False
                # --- End Core Authentication Logic ---

                if token_info:
                    logger.info(
                        f"Successfully obtained/validated Spotify access token ({flow_type} flow)."
                    )

                    if not self._token_has_required_scopes(token_info):
                        if check_cache_only:
                            logger.warning(
                                "Silent Spotify authentication found an outdated token cache. Interactive reauthorization is required."
                            )
                            return "SPOTIFY_SCOPE_UPGRADE_REQUIRED"

                        self._clear_cached_token()
                        self.auth_manager = None
                        init_result = self._initialize_auth_manager()
                        if init_result == "CREDENTIALS_MISSING":
                            return "CREDENTIALS_MISSING"
                        if not init_result:
                            return False

                        token_info = self.auth_manager.get_access_token(check_cache=False)
                        if not token_info or not self._token_has_required_scopes(token_info):
                            logger.error("Spotify reauthorization did not grant the required playlist scopes.")
                            return "SPOTIFY_SCOPE_UPGRADE_REQUIRED"

                    try:
                        import json

                        logger.debug(
                            f"Attempting to explicitly write token_info to cache file: {self.cache_path}"
                        )
                        with open(self.cache_path, "w", encoding="utf-8") as f:
                            json.dump(token_info, f)
                        logger.info(f"Explicitly wrote token_info to {self.cache_path}")
                    except Exception as explicit_write_err:
                        logger.error(
                            f"Failed to explicitly write token_info to cache file: {explicit_write_err}",
                            exc_info=True,
                        )
                        logger.error(
                            f"Warning: Failed to update Spotify token cache file: {explicit_write_err}"
                        )

                    self.sp = spotipy.Spotify(
                        auth_manager=self.auth_manager, requests_timeout=60
                    )
                    logger.debug("spotipy.Spotify client created.")
                    self._refresh_current_user_profile()
                    return True

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
                    logger.warning(f"Spotify auth failed (Server Error). Retrying...")
                    time.sleep(retry_delay)
                    continue  # Go to next attempt
                else:
                    logger.error(
                        f"Spotify OAuth error during authentication (final attempt or non-retryable): {e}",
                        exc_info=True,
                    )
                    logger.error(f"Spotify authentication error: {e}")
                    self.auth_manager = None  # Reset on definitive failure
                    self.sp = None
                    return False  # Failed

            except requests.exceptions.RequestException as req_e:
                logger.warning(
                    f"Network error during Spotify auth {flow_type} attempt {attempt + 1}: {req_e}",
                    exc_info=True,
                )
                if attempt < max_retries - 1:
                    logger.warning(f"Spotify auth failed (Network Error). Retrying...")
                    time.sleep(retry_delay)
                    continue  # Go to next attempt
                else:
                    logger.error(
                        f"Spotify auth failed due to network error after {attempt + 1} attempts: {req_e}",
                        exc_info=True,
                    )
                    logger.error(
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
                    logger.warning(f"Unexpected error during Spotify auth. Retrying...")
                    time.sleep(retry_delay)
                    continue  # Retry on generic errors too
                else:
                    logger.error(
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
        logger.error("Spotify authentication failed after multiple attempts.")
        self.auth_manager = None  # Reset on definitive failure
        self.sp = None
        return False

    # --- END NEW AUTHENTICATE METHOD ---

    def get_user_playlists(self):
        """Fetches the current Spotify account playlists."""
        logger.debug("get_user_playlists called")
        if not self._ensure_client():
            logger.error("Authentication failed within get_user_playlists.")
            return None

        if not self.current_user_id:
            self._refresh_current_user_profile()

        playlists_data = []
        try:
            results = self.sp.current_user_playlists(limit=50)
            logger.debug(f"Raw results from self.sp.current_user_playlists: {results}")
            if not results:
                logger.warning("current_user_playlists returned None or empty.")
                logger.warning("Could not retrieve playlists from Spotify.")
                return []

            playlists = results.get("items", [])
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
                        logger.warning(
                            "No more items found on next page or error fetching next page."
                        )
                        break
                except Exception as page_e:
                    logger.error(
                        f"Error fetching next page of playlists: {page_e}",
                        exc_info=True,
                    )
                    logger.error(f"Error fetching subsequent playlist page: {page_e}")
                    break

            logger.debug(f"Total playlists fetched: {len(playlists)}")
            for item in playlists:
                if isinstance(item, dict) and item.get("id") and item.get("name"):
                    owner_info = item.get("owner", {})
                    tracks_info = item.get("tracks", {})
                    owner_id = (
                        owner_info.get("id")
                        if isinstance(owner_info, dict)
                        else None
                    )
                    owner_name = (
                        owner_info.get("display_name")
                        or owner_info.get("id")
                        or "Unknown"
                        if isinstance(owner_info, dict)
                        else "Unknown"
                    )
                    is_owner = bool(owner_id and owner_id == self.current_user_id)
                    is_collaborative = bool(item.get("collaborative", False))
                    playlist_data = {
                        "id": item["id"],
                        "name": item["name"],
                        "owner": owner_name,
                        "owner_id": owner_id,
                        "owner_name": owner_name,
                        "public": item.get("public"),
                        "collaborative": is_collaborative,
                        "description": item.get("description") or "",
                        "uri": item.get("uri") or f"spotify:playlist:{item['id']}",
                        "snapshot_id": item.get("snapshot_id"),
                        "tracks_total": (
                            tracks_info.get("total", 0)
                            if isinstance(tracks_info, dict)
                            else 0
                        ),
                        "images": item.get("images", []),
                        "can_edit_details": is_owner,
                        "can_modify_items": bool(is_owner or is_collaborative),
                    }
                    playlists_data.append(playlist_data)
                else:
                    logger.warning(f"Skipping invalid playlist item: {item}")

            logger.info(f"Processed {len(playlists_data)} valid playlists.")
            logger.info(f"Found {len(playlists_data)} playlists.")
            return playlists_data
        except spotipy.SpotifyException as e:
            logger.error(
                f"Spotify API error fetching playlists: {e.http_status} - {e.msg}",
                exc_info=True,
            )
            logger.error(f"Spotify API error fetching playlists: {e.msg}")
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error fetching Spotify playlists: {e}", exc_info=True
            )
            logger.error(f"Error fetching Spotify playlists: {e}")
            logger.error("Check log file for detailed traceback.")
            return None

    def get_playlist_tracks(self, playlist_id):
        """Fetches all tracks for a given Spotify playlist ID."""
        logger.debug(f"get_playlist_tracks called for playlist_id: {playlist_id}")
        if not self._ensure_client():
            logger.error("Authentication failed within get_playlist_tracks.")
            return None

        tracks_data = []
        try:
            logger.info(f"Fetching tracks for Spotify playlist ID: {playlist_id}...")
            offset = 0
            limit = 100

            while True:
                logger.debug(
                    f"Fetching playlist items for {playlist_id}, limit={limit}, offset={offset}"
                )
                results = self.sp.playlist_items(
                    playlist_id,
                    fields=(
                        "items(added_at,added_by(id,display_name),is_local,"
                        "track(id,uri,name,artists(name),album(name),duration_ms,external_ids)),next"
                    ),
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
                    is_local = bool(item.get("is_local", False))
                    if isinstance(track_info, dict) and (track_info.get("id") or is_local):
                        artists = track_info.get("artists", [])
                        album_info = track_info.get("album", {})
                        external_ids = track_info.get("external_ids", {})
                        added_by = item.get("added_by", {})
                        track_id = track_info.get("id")
                        uri = track_info.get("uri")
                        if not uri and track_id:
                            uri = f"spotify:track:{track_id}"

                        tracks_data.append(
                            {
                                "id": track_id,
                                "uri": uri,
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
                                "duration_ms": track_info.get("duration_ms"),
                                "isrc": external_ids.get("isrc"),
                                "playlist_position": len(tracks_data),
                                "added_at": item.get("added_at"),
                                "added_by": (
                                    {
                                        "id": added_by.get("id"),
                                        "display_name": added_by.get("display_name") or added_by.get("id"),
                                    }
                                    if isinstance(added_by, dict)
                                    else None
                                ),
                                "is_local": is_local,
                            }
                        )
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
            logger.info(f"Found {len(tracks_data)} tracks in playlist {playlist_id}.")
            return tracks_data
        except spotipy.SpotifyException as e:
            logger.error(
                f"Spotify API error fetching tracks for playlist {playlist_id}: {e.http_status} - {e.msg}",
                exc_info=True,
            )
            logger.error(f"Spotify API error fetching tracks: {e.msg}")
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error fetching tracks for playlist {playlist_id}: {e}",
                exc_info=True,
            )
            logger.error(f"Error fetching tracks for playlist {playlist_id}: {e}")
            logger.error("Check log file for detailed traceback.")
            return None

    def search_tracks(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search Spotify tracks and return normalized track dictionaries."""
        if not self._ensure_client():
            logger.error("Authentication failed within search_tracks.")
            return []

        clean_query = str(query or "").strip()
        if not clean_query:
            return []

        try:
            results = self.sp.search(q=clean_query, type="track", limit=max(1, min(int(limit), 50)))
            items = (
                results.get("tracks", {}).get("items", [])
                if isinstance(results, dict)
                else []
            )
            tracks: List[Dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue

                album_info = item.get("album", {})
                external_ids = item.get("external_ids", {})
                artists = item.get("artists", [])
                track_id = item.get("id")
                uri = item.get("uri") or (f"spotify:track:{track_id}" if track_id else None)

                if not uri:
                    continue

                tracks.append(
                    {
                        "id": track_id,
                        "uri": uri,
                        "name": item.get("name", "Unknown Track"),
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
                        "duration_ms": item.get("duration_ms"),
                        "isrc": (
                            external_ids.get("isrc")
                            if isinstance(external_ids, dict)
                            else None
                        ),
                    }
                )
            return tracks
        except spotipy.SpotifyException as exc:
            logger.error(
                "Spotify API error searching tracks: %s - %s",
                exc.http_status,
                exc.msg,
                exc_info=True,
            )
            return []
        except Exception as exc:
            logger.error("Unexpected error searching Spotify tracks: %s", exc, exc_info=True)
            return []

    def create_playlist(
        self,
        name: str,
        public: bool = False,
        collaborative: bool = False,
        description: str = "",
    ) -> Dict[str, Any]:
        operation = "Create Spotify playlist"
        if not self._ensure_client():
            return {"success": False, "message": "Spotify authentication is required before creating playlists.", "playlist_id": None, "snapshot_id": None, "playlist": None}
        if not self.current_user_id:
            self._refresh_current_user_profile()
        if not self.current_user_id:
            return {"success": False, "message": "Spotify account ID could not be resolved.", "playlist_id": None, "snapshot_id": None, "playlist": None}
        try:
            playlist = self.sp.user_playlist_create(user=self.current_user_id, name=name, public=bool(public), collaborative=bool(collaborative), description=description or "")
            playlist_id = playlist.get("id") if isinstance(playlist, dict) else None
            return self._spotify_success_result(f"Created Spotify playlist: {name}", playlist_id=playlist_id, snapshot_id=playlist.get("snapshot_id") if isinstance(playlist, dict) else None, playlist=playlist if isinstance(playlist, dict) else None)
        except spotipy.SpotifyException as exc:
            return self._spotify_error_result(operation, exc)
        except Exception as exc:
            return self._spotify_error_result(operation, exc)

    def update_playlist_details(self, playlist_id: str, name: Optional[str] = None, public: Optional[bool] = None, collaborative: Optional[bool] = None, description: Optional[str] = None) -> Dict[str, Any]:
        operation = "Update Spotify playlist details"
        if not self._ensure_client():
            return {"success": False, "message": "Spotify authentication is required before updating playlist details.", "playlist_id": playlist_id, "snapshot_id": None, "playlist": None}
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if public is not None:
            payload["public"] = bool(public)
        if collaborative is not None:
            payload["collaborative"] = bool(collaborative)
        if description is not None:
            payload["description"] = description
        if not payload:
            return self._spotify_success_result("No Spotify playlist detail changes were submitted.", playlist_id=playlist_id)
        try:
            self.sp.playlist_change_details(playlist_id, **payload)
            refreshed = self.sp.playlist(playlist_id) if self.sp else None
            return self._spotify_success_result("Updated Spotify playlist details.", playlist_id=playlist_id, snapshot_id=refreshed.get("snapshot_id") if isinstance(refreshed, dict) else None, playlist=refreshed if isinstance(refreshed, dict) else None)
        except spotipy.SpotifyException as exc:
            return self._spotify_error_result(operation, exc, playlist_id)
        except Exception as exc:
            return self._spotify_error_result(operation, exc, playlist_id)

    def add_items_to_playlist(self, playlist_id: str, uris: Sequence[str], position: Optional[int] = None) -> Dict[str, Any]:
        operation = "Add Spotify playlist items"
        if not self._ensure_client():
            return {"success": False, "message": "Spotify authentication is required before adding playlist items.", "playlist_id": playlist_id, "snapshot_id": None, "playlist": None}
        clean_uris = [str(uri).strip() for uri in uris if str(uri).strip()]
        if not clean_uris:
            return {"success": False, "message": "No Spotify item URIs were available for adding.", "playlist_id": playlist_id, "snapshot_id": None, "playlist": None}
        latest_snapshot_id: Optional[str] = None
        try:
            for index, batch in enumerate(self._chunked(clean_uris)):
                add_kwargs: Dict[str, Any] = {}
                if index == 0 and position is not None:
                    add_kwargs["position"] = position

                result = self.sp.playlist_add_items(
                    playlist_id,
                    batch,
                    **add_kwargs,
                )
                if isinstance(result, dict):
                    latest_snapshot_id = result.get("snapshot_id") or latest_snapshot_id
            result = self._spotify_success_result(
                f"Added {len(clean_uris)} Spotify item(s) to playlist.",
                playlist_id=playlist_id,
                snapshot_id=latest_snapshot_id,
            )
            result["tracks_delta"] = len(clean_uris)
            return result
        except spotipy.SpotifyException as exc:
            return self._spotify_error_result(operation, exc, playlist_id)
        except Exception as exc:
            return self._spotify_error_result(operation, exc, playlist_id)

    def remove_items_from_playlist(self, playlist_id: str, items: Sequence[Union[str, Dict[str, Any]]], snapshot_id: Optional[str] = None) -> Dict[str, Any]:
        operation = "Remove Spotify playlist items"
        if not self._ensure_client():
            return {"success": False, "message": "Spotify authentication is required before removing playlist items.", "playlist_id": playlist_id, "snapshot_id": None, "playlist": None}
        clean_items = [item for item in items if item]
        if not clean_items:
            return {"success": False, "message": "No Spotify item URIs were available for removal.", "playlist_id": playlist_id, "snapshot_id": None, "playlist": None}
        latest_snapshot_id = snapshot_id
        try:
            for batch in self._chunked(clean_items):
                if any(isinstance(item, dict) and item.get("positions") for item in batch):
                    result = self.sp.playlist_remove_specific_occurrences_of_items(playlist_id, batch, snapshot_id=latest_snapshot_id)
                else:
                    uris = [item.get("uri") if isinstance(item, dict) else str(item) for item in batch]
                    result = self.sp.playlist_remove_all_occurrences_of_items(playlist_id, [str(uri).strip() for uri in uris if str(uri).strip()], snapshot_id=latest_snapshot_id)
                if isinstance(result, dict):
                    latest_snapshot_id = result.get("snapshot_id") or latest_snapshot_id
            return self._spotify_success_result(f"Removed {len(clean_items)} Spotify item(s) from playlist.", playlist_id=playlist_id, snapshot_id=latest_snapshot_id)
        except spotipy.SpotifyException as exc:
            return self._spotify_error_result(operation, exc, playlist_id)
        except Exception as exc:
            return self._spotify_error_result(operation, exc, playlist_id)

    def reorder_playlist_items(self, playlist_id: str, range_start: int, insert_before: int, range_length: int = 1, snapshot_id: Optional[str] = None) -> Dict[str, Any]:
        operation = "Reorder Spotify playlist items"
        if not self._ensure_client():
            return {"success": False, "message": "Spotify authentication is required before reordering playlist items.", "playlist_id": playlist_id, "snapshot_id": None, "playlist": None}
        try:
            result = self.sp.playlist_reorder_items(playlist_id, range_start=range_start, insert_before=insert_before, range_length=range_length, snapshot_id=snapshot_id)
            return self._spotify_success_result("Reordered Spotify playlist item(s).", playlist_id=playlist_id, snapshot_id=result.get("snapshot_id") if isinstance(result, dict) else None)
        except spotipy.SpotifyException as exc:
            return self._spotify_error_result(operation, exc, playlist_id)
        except Exception as exc:
            return self._spotify_error_result(operation, exc, playlist_id)

    def replace_playlist_items(self, playlist_id: str, uris: Sequence[str]) -> Dict[str, Any]:
        operation = "Replace Spotify playlist items"
        if not self._ensure_client():
            return {"success": False, "message": "Spotify authentication is required before replacing playlist items.", "playlist_id": playlist_id, "snapshot_id": None, "playlist": None}
        clean_uris = [str(uri).strip() for uri in uris if str(uri).strip()]
        latest_snapshot_id: Optional[str] = None
        try:
            result = self.sp.playlist_replace_items(playlist_id, clean_uris[:SPOTIFY_MUTATION_BATCH_SIZE])
            if isinstance(result, dict):
                latest_snapshot_id = result.get("snapshot_id") or latest_snapshot_id
            remaining = clean_uris[SPOTIFY_MUTATION_BATCH_SIZE:]
            if remaining:
                append_result = self.add_items_to_playlist(playlist_id, remaining)
                latest_snapshot_id = append_result.get("snapshot_id") or latest_snapshot_id
                if not append_result.get("success"):
                    return append_result
            result = self._spotify_success_result(
                f"Replaced Spotify playlist contents with {len(clean_uris)} item(s).",
                playlist_id=playlist_id,
                snapshot_id=latest_snapshot_id,
            )
            result["tracks_total"] = len(clean_uris)
            return result
        except spotipy.SpotifyException as exc:
            return self._spotify_error_result(operation, exc, playlist_id)
        except Exception as exc:
            return self._spotify_error_result(operation, exc, playlist_id)

    def unfollow_playlist(self, playlist_id: str) -> Dict[str, Any]:
        operation = "Remove Spotify playlist from library"
        if not self._ensure_client():
            return {"success": False, "message": "Spotify authentication is required before removing a playlist from the library.", "playlist_id": playlist_id, "snapshot_id": None, "playlist": None}
        try:
            self.sp.current_user_unfollow_playlist(playlist_id)
            return self._spotify_success_result("Removed Spotify playlist from library.", playlist_id=playlist_id)
        except spotipy.SpotifyException as exc:
            return self._spotify_error_result(operation, exc, playlist_id)
        except Exception as exc:
            return self._spotify_error_result(operation, exc, playlist_id)


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
