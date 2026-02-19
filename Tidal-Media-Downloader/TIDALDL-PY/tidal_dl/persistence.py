# --- START OF FILE persistence.py ---

# -*- coding: utf-8 -*-
"""
Manages the persistence of links between Spotify and Tidal tracks within playlists.

This module provides the `LinkPersistenceManager` class, responsible for loading,
saving, and manipulating track link data stored in a JSON file. It handles the
mapping between Spotify track IDs and their corresponding Tidal track IDs for
specific playlists, facilitating synchronization or lookup tasks.

Key Functionality:
- Loads link data from a JSON file (`spotify_to_tidal_links.json`) located in
  the user's profile directory.
- Saves the current link data back to the JSON file.
- Provides methods to add, update, retrieve, and remove track links for specific
  playlists.
- Automatically handles file creation if it doesn't exist.
- Uses lazy loading for the link data to optimize performance.
- Includes timestamps for link creation/modification.

Dependencies:
- json: For reading and writing JSON data.
- os: For path manipulation (joining paths).
- datetime: For generating timestamps.
- logging: For logging information, warnings, and errors.
- .paths: For retrieving the user's profile path.

Usage:
Instantiate `LinkPersistenceManager` and use its methods to interact with the
persisted link data. Changes are automatically saved to the file upon modification.

Example:
    manager = LinkPersistenceManager()
    links = manager.get_links_for_playlist("some_playlist_id")
    manager.add_or_update_link("some_playlist_id", "spotify_id_1", "tidal_id_1")
    manager.remove_link("some_playlist_id", "spotify_id_2")
"""

# ########## IMPORTS ##########
import json
import os
import time
from datetime import datetime, timezone
import logging
from typing import Dict, Optional, List, Any, cast  # Added cast for type narrowing
from . import paths
import aigpy  # type: ignore
from .model import Track  # Added for type hints and checks

# ########## LOGGING SETUP ##########
logger = logging.getLogger(__name__)
logger.setLevel(
    logging.WARNING
)  # Set specific level for this module to only receive warnings

# Set up GUI logging with DEBUG level for this module (debugging persistence operations)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.DEBUG)

# ########## CLASS DEFINITIONS ##########


class LinkPersistenceManager:
    """
    Manages loading, saving, and modifying Spotify-Tidal track links.

    This class handles the persistence of track mappings between Spotify and Tidal
    for different playlists, storing the data in a JSON file within the user's
    profile directory. It employs lazy loading for efficiency.
    """

    def __init__(self):
        """
        Initializes the LinkPersistenceManager.

        Sets up the path to the persistence file and initializes the links data
        to None for lazy loading upon first access.
        """
        self.file_path: str = self._get_file_path()
        # Initialize links_data as None to enable lazy loading.
        # Data will be loaded from the file only when first needed.
        self.links_data: Optional[Dict[str, Any]] = None  # Use more specific type hint

    # --- Private Helper Methods ---

    def _get_file_path(self) -> str:
        """
        Constructs the full path to the persistence JSON file.

        Returns:
            str: The absolute path to 'spotify_to_tidal_links.json'.
        """
        # Retrieve the base profile directory using the paths module.
        profile_path = paths.getProfilePath()
        # Combine the profile path and the filename.
        return os.path.join(profile_path, "spotify_to_tidal_links.json")

    def _get_current_timestamp(self) -> str:
        """
        Gets the current timestamp in UTC ISO 8601 format with 'Z' suffix.

        Returns:
            str: The current UTC timestamp as an ISO 8601 formatted string.
        """
        # Get current time in UTC, format it, and append 'Z' for UTC indication.
        return datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"

    # --- Core Data Handling Methods ---

    # Return type annotation remains Dict[str, Any] as the function guarantees it
    def load_links(self) -> Dict[str, Any]:
        """
        Loads the links data from the JSON file into memory.

        If the file doesn't exist or is invalid JSON, it returns a default
        empty structure and logs a warning/error. This method populates
        `self.links_data`.

        Returns:
            Dict[str, Any]: The loaded link data, or `{"playlists": {}}` on error/not found.
        """
        # Check if data is already loaded to avoid redundant file reads.
        if self.links_data is not None:
            return self.links_data

        # Default structure in case of errors
        default_structure: Dict[str, Any] = {"playlists": {}}

        # Block: Attempt to read and parse the JSON file.
        try:
            # Open the file in read mode with UTF-8 encoding.
            with open(self.file_path, "r", encoding="utf-8") as f:
                # Parse the JSON content.
                raw_data = json.load(f)
                # Ensure the basic structure exists, default if necessary.
                if "playlists" not in raw_data or not isinstance(
                    raw_data.get("playlists"), dict
                ):
                    logger.warning(
                        f"File '{self.file_path}' lacks 'playlists' key or it's not a dict. Initializing."
                    )
                    loaded_data: Dict[str, Any] = default_structure
                else:
                    loaded_data = raw_data
                logger.debug(f"Successfully loaded links from '{self.file_path}'.")
        # Handle case where the file does not exist.
        except FileNotFoundError:
            logger.warning(
                f"Links file '{self.file_path}' not found. Initializing with empty structure."
            )
            loaded_data = default_structure
            logger.debug(
                f"[DIAGNOSIS] load_links returning default structure due to FileNotFoundError: {loaded_data}"
            )
        # Handle case where the file contains invalid JSON.
        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to decode JSON from '{self.file_path}': {e}. Initializing with empty structure."
            )
            loaded_data = default_structure
        # Handle other potential file reading errors.
        except IOError as e:
            logger.error(
                f"Error reading links file '{self.file_path}': {str(e)}. Initializing with empty structure."
            )
            loaded_data = default_structure

        # Assign loaded data and return
        self.links_data = loaded_data
        return loaded_data

    def save_links(self) -> bool:
        """
        Saves the current in-memory links data (`self.links_data`) to the JSON file.

        Ensures data is loaded before attempting to save. If `self.links_data` is
        None (meaning it was never loaded or loading failed), it attempts to load
        it first. If still None, saving fails.
        
        Includes a retry mechanism to handle transient file locks (e.g., OneDrive syncing).

        Returns:
            bool: True if saving was successful, False otherwise.
        """
        # Ensure data is loaded before saving.
        if self.links_data is None:
            logger.warning("Attempted to save links before loading. Loading first.")
            self.load_links()

        # Now self.links_data is guaranteed to be a Dict[str, Any]
        links_data_to_save = cast(Dict[str, Any], self.links_data)

        # Retry logic for file locking issues
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Ensure the directory exists before writing.
                os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
                # Open the file in write mode with UTF-8 encoding.
                with open(self.file_path, "w", encoding="utf-8") as f:
                    # Dump the current links data to the file.
                    json.dump(
                        links_data_to_save,  # Use the guaranteed dict variable
                        f,
                        indent=4,  # Pretty-print with 4 spaces.
                        ensure_ascii=False,  # Allow non-ASCII characters.
                        sort_keys=False,  # Maintain insertion order where possible.
                    )
                logger.debug(f"Successfully saved links to '{self.file_path}'.")
                return True
            
            except IOError as e:
                # Check if it's a permission error (often caused by file locking)
                # Errno 13 is Permission denied
                if attempt < max_retries - 1:
                    logger.warning(f"Save attempt {attempt + 1} failed ({e}). Retrying in 0.2s...")
                    time.sleep(0.2)
                    continue
                else:
                    logger.error(f"Error saving links file '{self.file_path}' after {max_retries} attempts: {str(e)}")
                    return False
            
            except Exception as e:  # Catch unexpected errors during save
                logger.error(f"An unexpected error occurred during save: {str(e)}")
                return False
        
        return False

    # --- Public Data Access and Modification Methods ---

    def get_links_for_playlist(self, playlist_id: str) -> Dict[str, Any]:
        """
        Retrieves all track links for a given playlist ID.

        Loads data from the file if it hasn't been loaded yet (lazy loading).

        Args:
            playlist_id (str): The unique identifier of the playlist.

        Returns:
            dict: A dictionary containing track links for the specified playlist,
                  or an empty dictionary if the playlist is not found.
                  The structure is: `{"tracks": {spotify_id: {"tidal_track_id": ..., "timestamp": ..., "candidates": [...]}}, "last_modified": ...}`
        """
        # Ensure data is loaded before access.
        if self.links_data is None:
            self.load_links()

        # Safely cast to Dict[str, Any] for Pylance.
        links_data_dict = cast(Dict[str, Any], self.links_data)

        logger.debug(
            f"[DIAGNOSIS] Value of links_data_dict before get: {links_data_dict}"
        )
        # Safely access the playlist data using .get() to avoid KeyErrors.
        playlists = links_data_dict.get("playlists", {})
        return playlists.get(playlist_id, {})  # Return playlist data or empty dict.

    def get_cached_track_quality(self, tidal_track_id: str) -> Optional[str]:
        """
        Retrieves a cached quality label for a Tidal track ID.

        Args:
            tidal_track_id (str): Tidal track identifier.

        Returns:
            Optional[str]: Cached quality text if available, otherwise None.
        """
        if not tidal_track_id:
            return None

        if self.links_data is None:
            self.load_links()

        links_data_dict = cast(Dict[str, Any], self.links_data)
        quality_cache = links_data_dict.get("track_quality_cache", {})
        if not isinstance(quality_cache, dict):
            return None

        entry = quality_cache.get(str(tidal_track_id), {})
        if not isinstance(entry, dict):
            return None

        quality_text = entry.get("quality_text")
        if isinstance(quality_text, str) and quality_text.strip():
            return quality_text.strip()
        return None

    def set_cached_track_quality(self, tidal_track_id: str, quality_text: str) -> None:
        """
        Stores (or updates) a cached quality label for a Tidal track ID.

        Args:
            tidal_track_id (str): Tidal track identifier.
            quality_text (str): Human-readable quality label.
        """
        track_id = str(tidal_track_id or "").strip()
        cleaned_quality = str(quality_text or "").strip()
        if not track_id or not cleaned_quality:
            return

        if self.links_data is None:
            self.load_links()

        links_data_dict = cast(Dict[str, Any], self.links_data)

        quality_cache = links_data_dict.get("track_quality_cache")
        if not isinstance(quality_cache, dict):
            quality_cache = {}
            links_data_dict["track_quality_cache"] = quality_cache

        existing_entry = quality_cache.get(track_id)
        if isinstance(existing_entry, dict):
            existing_quality = existing_entry.get("quality_text")
            if isinstance(existing_quality, str) and existing_quality.strip() == cleaned_quality:
                return

        quality_cache[track_id] = {
            "quality_text": cleaned_quality,
            "timestamp": self._get_current_timestamp(),
        }

        if not self.save_links():
            logger.error(
                f"Failed to save track quality cache for track {track_id}"
            )

    def add_or_update_link(
        self,
        playlist_id: str,
        spotify_track_id: str,
        spotify_track_details: Dict[str, Any],
        tidal_track_object: Optional[Track],
        candidates: Optional[List[Dict[str, Any]]] = None,
        score: Optional[int] = None,
    ):
        """
        Adds a new track link or updates an existing one for a specific playlist.
        Stores full Spotify track details and serializes the full Tidal Track object.
        Optionally stores candidate tracks if the link is uncertain.

        Loads data if necessary. Creates the playlist entry if it doesn't exist.
        Saves the changes to the JSON file immediately after modification.

        Args:
            playlist_id (str): The ID of the playlist.
            spotify_track_id (str): The Spotify track ID (used as key).
            spotify_track_details (Dict[str, Any]): Full dictionary of Spotify track details.
            tidal_track_object (Optional[Track]): The corresponding Tidal Track object, or None if no match.
            candidates (Optional[List[Dict[str, Any]]]): A list of candidate match dictionaries,
                                               each containing 'tidal_track' (Track object),
                                               'score', and 'mismatch_reasons'. Defaults to None.
            score (Optional[int]): The certainty score for the link. Defaults to None.
        """
        # Ensure data is loaded before modification.
        if self.links_data is None:
            self.load_links()

        # Safely cast to Dict[str, Any] for Pylance.
        links_data_dict = cast(Dict[str, Any], self.links_data)

        # Ensure the top-level "playlists" key exists.
        if "playlists" not in links_data_dict:
            links_data_dict["playlists"] = {}

        # Block: Create or access the specific playlist's data structure.
        if playlist_id not in links_data_dict["playlists"]:
            links_data_dict["playlists"][playlist_id] = {
                "tracks": {},
                "last_modified": self._get_current_timestamp(),
            }
            logger.info(f"Created new playlist entry for ID: {playlist_id}")

        tracks: Dict[str, Any] = links_data_dict["playlists"][playlist_id]["tracks"]

        # Serialize Tidal track object
        serialized_tidal_track: Optional[Dict[str, Any]] = None
        if isinstance(tidal_track_object, Track):
            try:
                # Attempt to serialize the object to a dictionary
                temp_serialized_track = aigpy.model.modelToDict(tidal_track_object)
                
                # Ensure the result is a dictionary before proceeding
                if isinstance(temp_serialized_track, dict):
                    serialized_tidal_track = temp_serialized_track
                    # Add the __CLASS__ key for successful deserialization with dictToModel
                    serialized_tidal_track["__CLASS__"] = "Track"
                else:
                    logger.error(
                        f"Serialization of Track object {tidal_track_object.id} did not return a dictionary. Type: {type(temp_serialized_track)}"
                    )
                    serialized_tidal_track = None  # Ensure it remains None if serialization fails to produce a dict
                    
            except Exception as e:
                logger.error(
                    f"[Persistence] Failed to serialize main Tidal Track object {tidal_track_object.id}: {e}",
                    exc_info=True,
                )
                # Fallback to None if serialization fails to prevent storing corrupt data
                serialized_tidal_track = None
        elif tidal_track_object is None:
            # This is the case for "None of these match"
            serialized_tidal_track = None
        else:
            logger.warning(
                f"tidal_track_object passed to add_or_update_link is not a Track object, but {type(tidal_track_object)}. Cannot serialize."
            )


        # Prepare link data with all details
        link_data: Dict[str, Any] = {
            "spotify_track_details": spotify_track_details,
            "tidal_track_details": serialized_tidal_track,  # Store the serialized Tidal track
            "timestamp": self._get_current_timestamp(),
            "score": score,  # Store the certainty score
        }

        # Store the Tidal track ID directly for easier access and fallback
        if tidal_track_object and tidal_track_object.id is not None:
            link_data["tidal_track_id"] = str(tidal_track_object.id)
        else:
            # Ensure the key exists even if the ID is None, to maintain structure
            link_data["tidal_track_id"] = None
        # Add candidates if provided and not empty
        if candidates:
            serializable_candidates = []
            for cand_dict in candidates:
                track_obj = cand_dict.get("tidal_track")
                if isinstance(track_obj, Track):
                    try:
                        # Attempt to serialize the candidate track object
                        cand_track_dict = aigpy.model.modelToDict(track_obj)
                        # Ensure it's a dictionary before proceeding
                        if isinstance(cand_track_dict, dict):
                            # Add the __CLASS__ key for proper deserialization
                            cand_track_dict["__CLASS__"] = "Track"
                            serializable_candidates.append(
                                {
                                    "tidal_track": cand_track_dict,
                                    "score": cand_dict.get("score"),
                                    "mismatch_reasons": cand_dict.get(
                                        "mismatch_reasons"
                                    ),
                                }
                            )
                        else:
                            logger.error(
                                f"Serialization of candidate Track object {track_obj.id} did not return a dictionary."
                            )
                    except Exception as e:
                        logger.error(
                            f"[Persistence] Failed to serialize Track object for candidate {track_obj.id}: {e}",
                            exc_info=True,
                        )
                else:
                    logger.warning(
                        f"[Persistence] Candidate data for Spotify track {spotify_track_id} in "
                        f"playlist {playlist_id} contained non-Track object: {type(track_obj)}"
                    )

            if serializable_candidates:
                link_data["candidates"] = serializable_candidates
                logger.debug(
                    f"[Persistence] Storing {len(serializable_candidates)} candidates for link: "
                    f"{playlist_id} | {spotify_track_id}"
                )

        tracks[spotify_track_id] = link_data
        tidal_id_for_log = tidal_track_object.id if tidal_track_object else "None"
        logger.info(
            f"[Persistence] Added/Updated detailed link{' with candidates' if 'candidates' in link_data else ''}: "
            f"{playlist_id} | {spotify_track_id} -> Tidal ID {tidal_id_for_log}"
        )

        links_data_dict["playlists"][playlist_id][
            "last_modified"
        ] = self._get_current_timestamp()

        if not self.save_links():
            logger.error(
                f"Failed to save updated link for playlist {playlist_id}, track {spotify_track_id}"
            )

    def remove_link(self, playlist_id: str, spotify_track_id: str):
        """
        Removes a specific track link from a playlist.

        Loads data if necessary. Saves the changes to the JSON file immediately
        after removal.

        Args:
            playlist_id (str): The ID of the playlist containing the link.
            spotify_track_id (str): The Spotify track ID of the link to remove.
        """
        # Ensure data is loaded before modification.
        if self.links_data is None:
            self.load_links()

        # Safely cast to Dict[str, Any] for Pylance.
        links_data_dict = cast(Dict[str, Any], self.links_data)

        # Block: Attempt to find and remove the specified track link.
        # Check if 'playlists' key exists and the specific playlist ID exists.
        if (
            "playlists" in links_data_dict
            and playlist_id in links_data_dict["playlists"]
        ):
            # Get the tracks dictionary for the playlist, default to empty if 'tracks' key is missing.
            tracks = links_data_dict["playlists"][playlist_id].get("tracks", {})
            # Check if the specific Spotify track ID exists within the tracks.
            if spotify_track_id in tracks:
                # Remove the track link entry.
                del tracks[spotify_track_id]
                logger.info(
                    f"Removed link for track {spotify_track_id} from playlist {playlist_id}"
                )

                # Update the 'last_modified' timestamp for the playlist.
                links_data_dict["playlists"][playlist_id][
                    "last_modified"
                ] = self._get_current_timestamp()

                # Persist the changes to the file system.
                if not self.save_links():
                    # Log an error if saving fails.
                    logger.error(
                        f"Failed to save removal of link for playlist {playlist_id}, track {spotify_track_id}"
                    )
            else:
                # Log if the track link to be removed was not found.
                logger.warning(
                    f"Track link {spotify_track_id} not found in playlist {playlist_id} for removal."
                )
        else:
            # Log if the playlist itself was not found.
            logger.warning(f"Playlist {playlist_id} not found for link removal.")

    def remove_playlist(self, playlist_id: str):
        """
        Removes all links associated with a specific playlist ID.

        Loads data if necessary. Saves the changes to the JSON file immediately
        after removal.

        Args:
            playlist_id (str): The ID of the playlist to remove.
        """
        # Ensure data is loaded before modification.
        if self.links_data is None:
            self.load_links()

        # Safely cast to Dict[str, Any] for Pylance.
        links_data_dict = cast(Dict[str, Any], self.links_data)

        # Block: Attempt to find and remove the specified playlist entry.
        # Check if 'playlists' key exists and the specific playlist ID exists.
        if (
            "playlists" in links_data_dict
            and playlist_id in links_data_dict["playlists"]
        ):
            # Remove the entire entry for the playlist ID.
            del links_data_dict["playlists"][playlist_id]
            logger.info(f"Removed all links for playlist {playlist_id}.")

            # Persist the changes to the file system.
            if not self.save_links():
                # Log an error if saving fails.
                logger.error(f"Failed to save removal of playlist {playlist_id}")
        else:
            # Log if the playlist to be removed was not found.
            logger.warning(f"Playlist {playlist_id} not found for removal.")

# --- END OF FILE persistence.py ---
