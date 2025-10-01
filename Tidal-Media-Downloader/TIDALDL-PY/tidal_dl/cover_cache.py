# -*- coding: utf-8 -*-
"""
Handles caching of playlist cover art.
"""
import os
import json
import time
import logging
import threading
from typing import Optional, Dict

import io
from PyQt6.QtCore import QRunnable, QThreadPool
from PIL import Image

import aigpy

from tidal_dl.settings import SETTINGS
from tidal_dl.paths import getProfilePath

# Initialize logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set specific level for this module


class SaveIconRunnable(QRunnable):
    """Runnable task for saving an icon asynchronously."""

    def __init__(
        self,
        cache_instance: "PlaylistCoverCache",
        service: str,
        playlist_id: str,
        image_bytes: bytes,
    ):
        super().__init__()
        self.cache = cache_instance
        self.service = service
        self.playlist_id = playlist_id
        self.image_bytes = image_bytes

    def run(self):
        """Execute the save worker."""
        logger.debug(f"SaveIconRunnable started for {self.service}-{self.playlist_id}")
        try:
            self.cache.save_worker(self.service, self.playlist_id, self.image_bytes)
            logger.debug(
                f"SaveIconRunnable finished for {self.service}-{self.playlist_id}"
            )
        except Exception as e:
            logger.error(
                f"Exception in SaveIconRunnable for {self.service}-{self.playlist_id}: {e}",
                exc_info=True,
            )


class PlaylistCoverCache:
    """
    Manages a local cache for playlist cover images to reduce API calls and speed up loading.
    """

    def __init__(self):
        """
        Initializes the PlaylistCoverCache.
        Determines the cache directory, loads metadata, and sets up the TTL.
        """
        logger.info("Initializing PlaylistCoverCache...")

        # Determine cache directory
        custom_path = SETTINGS.playlistCoverCachePath
        if custom_path and custom_path.strip():
            self.cache_dir = custom_path.strip()
            logger.info(f"Using custom playlist cover cache path: {self.cache_dir}")
        else:
            self.cache_dir = os.path.join(getProfilePath(), "cache", "playlist_covers")
            logger.info(f"Using default playlist cover cache path: {self.cache_dir}")

        # Ensure cache directory exists
        try:
            aigpy.path.mkdirs(self.cache_dir)
            logger.debug(f"Ensured cache directory exists: {self.cache_dir}")
        except Exception as e:
            logger.error(
                f"Failed to create cache directory {self.cache_dir}: {e}", exc_info=True
            )
            # If directory creation fails, we might want to handle this more gracefully,
            # maybe disable caching or raise an error. For now, log and continue.

        # Metadata file path
        self.metadata_path = os.path.join(self.cache_dir, ".cache_metadata.json")
        self.metadata: Dict[str, float] = {}
        self._metadata_lock = threading.Lock()  # Lock for thread-safe metadata access

        # Load existing metadata
        logger.debug(f"Attempting to load cache metadata from: {self.metadata_path}")
        try:
            with self._metadata_lock:  # Ensure thread safety during load
                # Check if file exists before opening
                if os.path.exists(self.metadata_path):
                    with open(self.metadata_path, "r", encoding="utf-8") as f:
                        loaded_data = json.load(f)
                        # Add robustness check for loaded metadata type
                        if not isinstance(loaded_data, dict):
                            logging.warning(
                                f"Loaded metadata from {self.metadata_path} is not a dictionary. Resetting to empty."
                            )
                            self.metadata = {}
                        else:
                            self.metadata = loaded_data
                    logger.info(
                        f"Successfully loaded cache metadata for {len(self.metadata)} items."
                    )
                else:
                    logger.info(
                        "Cache metadata file not found. Starting with empty metadata."
                    )
        except json.JSONDecodeError:
            logger.warning(
                f"Cache metadata file {self.metadata_path} is corrupted. Starting with empty metadata."
            )
            # Consider backing up the corrupted file here
            self.metadata = {}  # Reset metadata
        except Exception as e:
            logger.error(
                f"An unexpected error occurred loading cache metadata: {e}",
                exc_info=True,
            )
            self.metadata = {}  # Reset metadata on other errors too

        # Set TTL
        try:
            # Ensure TTL is treated as an integer
            self.ttl_seconds = int(SETTINGS.playlistCoverCacheTTL) * 24 * 60 * 60
            logger.info(
                f"Playlist cover cache TTL set to {SETTINGS.playlistCoverCacheTTL} days ({self.ttl_seconds} seconds)."
            )
        except (TypeError, ValueError):
            logger.warning(
                f"Invalid TTL value '{SETTINGS.playlistCoverCacheTTL}'. Using default TTL (7 days)."
            )
            # Define a default TTL if conversion fails, e.g., 7 days
            default_ttl_days = 7
            self.ttl_seconds = default_ttl_days * 24 * 60 * 60
            logger.info(
                f"Using default playlist cover cache TTL: {default_ttl_days} days ({self.ttl_seconds} seconds)."
            )

        logger.info("PlaylistCoverCache initialized.")

    def _save_metadata(self):
        """Saves the current metadata dictionary to the JSON file."""
        logger.debug(f"Attempting to save metadata to: {self.metadata_path}")
        with self._metadata_lock:
            # Ensure the directory exists before attempting to write
            metadata_dir = os.path.dirname(self.metadata_path)
            try:
                if not os.path.exists(metadata_dir):
                    aigpy.path.mkdirs(metadata_dir)
                    logger.debug(f"Created directory for metadata file: {metadata_dir}")
            except Exception as e:
                logger.error(
                    f"Failed to ensure metadata directory exists {metadata_dir}: {e}",
                    exc_info=True,
                )
                return  # Cannot proceed if directory cannot be ensured

            # Now attempt to write the file
            try:
                with open(self.metadata_path, "w", encoding="utf-8") as f:
                    json.dump(self.metadata, f, indent=4)
                logger.info(
                    f"Successfully saved metadata for {len(self.metadata)} items to {self.metadata_path}."
                )
            except (IOError, OSError) as e:
                logger.error(
                    f"Failed to save metadata to {self.metadata_path}: {e}",
                    exc_info=True,
                )
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred saving metadata: {e}", exc_info=True
                )

    def _update_metadata(self, filename: str, timestamp: float):
        """Updates the timestamp for a given filename in the metadata and saves."""
        logger.debug(f"Attempting to update metadata for: {filename}")
        updated = False
        with self._metadata_lock:
            # Only update if the value is different or new? For simplicity, always update.
            self.metadata[filename] = timestamp
            updated = True
            logger.debug(
                f"In-memory metadata updated for {filename} with timestamp {timestamp}."
            )

        if updated:
            self._save_metadata()  # Save changes immediately after lock release
            logger.info(f"Metadata update triggered save for: {filename}")
        else:
            logger.debug(f"No metadata update needed for {filename}.")

    def _remove_metadata_entry(self, filename: str):
        """
        Removes a specific entry from the metadata dictionary and saves the changes.
        Acquires lock only for dictionary access, then saves metadata afterwards if needed.
        """
        logger.debug(f"Attempting to remove metadata entry: {filename}")
        removed = False
        try:
            # Acquire lock only for the dictionary operation
            with self._metadata_lock:
                if filename in self.metadata:
                    del self.metadata[filename]
                    removed = True
                    logger.debug(f"Removed in-memory metadata entry: {filename}")
                else:
                    logger.debug(f"Metadata entry {filename} not found for removal.")
                    # If not found, no need to save later
                    return  # Exit early

        except Exception as e:
            logger.error(
                f"Unexpected error accessing metadata to remove {filename}: {e}",
                exc_info=True,
            )
            # If an error occurred during dictionary access, do not proceed to save
            return

        # Save updated metadata *after* the lock is released, only if an entry was removed
        if removed:
            self._save_metadata()  # This method handles its own locking for saving
            logger.info(f"Metadata removal triggered save for: {filename}")

    def get_icon_data(
        self, service: str, playlist_id: str
    ) -> tuple[bytes | None, float | None]:
        """
        Retrieves cached icon data and its timestamp for a given service and playlist ID.
        This method now returns the data even if it's past TTL, along with its timestamp.
        The caller is responsible for checking staleness if needed.

        Args:
            service (str): The service name (e.g., 'tidal', 'spotify').
            playlist_id (str): The unique identifier for the playlist.

        Returns:
            Tuple[Optional[bytes], Optional[float]]: A tuple containing:
                - The cached icon data as bytes if found and readable, otherwise None.
                - The timestamp (float) of the cache entry if metadata exists, otherwise None.
        """
        fname = f"{service}-{playlist_id}.jpg"
        logger.debug(f"Attempting to get cache data for: {fname}")

        metadata_entry_exists = False
        timestamp = 0.0
        remove_entry_flag = False  # Flag to remove entry outside the lock
        retrieved_timestamp: Optional[float] = None

        # Check metadata within lock
        with self._metadata_lock:
            if fname in self.metadata:
                metadata_entry_exists = True
                timestamp = self.metadata[fname]
                retrieved_timestamp = float(timestamp)  # Store timestamp
            else:
                logger.debug(f"Cache miss (no metadata) for: {fname}")
                return None, None  # Cache miss (no data, no timestamp)

        # Perform checks outside the initial metadata lock
        if metadata_entry_exists:
            # File existence and readability check
            fpath = os.path.join(self.cache_dir, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, "rb") as f:
                        data = f.read()
                    logger.info(
                        f"Cache hit and file read successful for: {fname}. Timestamp: {retrieved_timestamp}"
                    )
                    return data, retrieved_timestamp
                except (IOError, OSError) as e:
                    logger.error(
                        f"Cache hit, but failed to read file {fpath}: {e}",
                        exc_info=True,
                    )
                    logger.debug(
                        f"Setting remove_entry_flag for {fname} due to file read error."
                    )
                    remove_entry_flag = True  # Mark for removal
            else:
                # File doesn't exist, but metadata does - inconsistency
                logger.warning(
                    f"Cache metadata inconsistency: Entry for {fname} exists, but file {fpath} is missing."
                )
                logger.debug(
                    f"Setting remove_entry_flag for {fname} due to missing file."
                )
                remove_entry_flag = True  # Mark for removal

        # Perform removal if flagged (due to read error or missing file)
        if remove_entry_flag:
            logger.debug(
                f"Calling _remove_metadata_entry for {fname} due to previous error/inconsistency."
            )
            self._remove_metadata_entry(fname)  # This method handles its own locking

        return (
            None,
            retrieved_timestamp,
        )  # Return None data, but timestamp if metadata existed

    def save_worker(self, service: str, playlist_id: str, image_bytes: bytes):
        """Worker method to save image data and update metadata."""
        fname = f"{service}-{playlist_id}.jpg"
        fpath = os.path.join(self.cache_dir, fname)
        logger.debug(f"Worker starting save process for: {fname} to {fpath}")

        try:
            # Use Pillow to open, convert, and save the image
            img = Image.open(io.BytesIO(image_bytes))
            img = img.convert("RGB")  # Ensure RGB format for JPEG saving
            img.save(fpath, format="JPEG", quality=85)
            logger.info(f"Successfully saved image to cache: {fpath}")

            # Update metadata with current timestamp
            self._update_metadata(fname, time.time())

        except Exception as e:
            logger.error(f"Error saving icon to cache ({fname}): {e}", exc_info=True)
            # Optionally, attempt to clean up partially written file if error occurred
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    logger.debug(f"Cleaned up partially written file: {fpath}")
                except OSError as remove_error:
                    logger.error(
                        f"Failed to clean up file {fpath} after save error: {remove_error}"
                    )

    def save_icon_data(self, service: str, playlist_id: str, image_bytes: bytes):
        """Asynchronously saves icon data to the cache."""
        logger.debug(f"Received request to save icon data for {service}-{playlist_id}")
        if not image_bytes:
            logger.warning(
                f"Attempted to save empty image data for {service}-{playlist_id}. Skipping."
            )
            return

        # Create a runnable task
        runnable = SaveIconRunnable(self, service, playlist_id, image_bytes)

        # Submit the task to the global thread pool
        thread_pool = QThreadPool.globalInstance()
        if thread_pool is not None:
            thread_pool.start(runnable)
            logger.info(
                f"Submitted save task to thread pool for {service}-{playlist_id}"
            )
        else:
            # Log an error if the global thread pool instance is not available.
            # This indicates a more significant issue with the Qt application's state.
            logger.error(
                f"QThreadPool.globalInstance() returned None. Cannot save icon for {service}-{playlist_id} asynchronously. Icon will not be cached."
            )
            # Optionally, you could run the task synchronously as a fallback,
            # but be mindful that this will block the calling thread.
            # Example fallback (consider implications):
            # logger.warning(f"Running icon save synchronously for {service}-{playlist_id} due to missing thread pool.")
            # runnable.run()

    # Placeholder for future methods
    # def get_icon_data(self, playlist_uuid: str) -> bytes | None:
    #     pass

    # def save_icon_data(self, playlist_uuid: str, data: bytes):
    #     pass
