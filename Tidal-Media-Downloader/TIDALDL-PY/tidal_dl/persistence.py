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
import re
import shutil
import time
from datetime import datetime, timezone
import logging
from threading import RLock
from typing import Dict, Optional, List, Any, cast
from . import paths
import aigpy  # type: ignore
from .model import Track  # Added for type hints and checks

# ########## LOGGING SETUP ##########
logger = logging.getLogger(__name__)
logger.setLevel(
    logging.WARNING
)  # Set specific level for this module to only receive warnings

# Set up GUI logging with WARNING level for this module; cache debug noise stays hidden by default.
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.WARNING)

TRACK_METADATA_CACHE_VERSION = 1
TRACK_METADATA_CACHE_TTL_SECONDS = 90 * 24 * 60 * 60
TRACK_METADATA_CACHE_ALLOWED_KEYS = {
    "release_year",
    "bpm",
    "key",
    "genre",
    "label",
}
TRACK_METADATA_CACHE_PLACEHOLDER_VALUES = {
    "",
    "-",
    "Loading...",
    "Unknown",
    "Unknown Quality",
}

LINKS_BACKUP_KEEP_COUNT = 8
LINKS_FILE_BASENAME = "spotify_to_tidal_links.json"
LINKS_TEMP_SUFFIX = ".tmp"
LINKS_BACKUP_SUFFIX = ".bak"
LINKS_CORRUPT_SUFFIX = ".corrupt"

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
        self._io_lock = RLock()
        self._load_failed_due_to_unresolved_corruption = False
        self._last_recovery_source: Optional[str] = None
        self._deferred_save_depth = 0
        self._deferred_save_pending = False
        self._deferred_save_reason: Optional[str] = None

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
        return os.path.join(profile_path, LINKS_FILE_BASENAME)

    def _get_current_timestamp(self) -> str:
        """
        Gets the current timestamp in UTC ISO 8601 format with 'Z' suffix.

        Returns:
            str: The current UTC timestamp as an ISO 8601 formatted string.
        """
        # Store UTC timestamps in standard ISO-8601 Z form.
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _clean_track_metadata_cache_values(metadata: Dict[str, Any]) -> Dict[str, str]:
        """
        Keep only supported, non-placeholder display metadata values.
        """
        cleaned: Dict[str, str] = {}
        for raw_key, raw_value in (metadata or {}).items():
            key = str(raw_key or "").strip()
            if key not in TRACK_METADATA_CACHE_ALLOWED_KEYS:
                continue

            value = str(raw_value or "").strip()
            if value in TRACK_METADATA_CACHE_PLACEHOLDER_VALUES:
                continue

            cleaned[key] = value

        return cleaned

    @staticmethod
    def _is_track_metadata_cache_stale(timestamp: Any) -> bool:
        """
        Return True when a metadata-cache entry is too old or has an invalid timestamp.
        """
        if not isinstance(timestamp, str) or not timestamp.strip():
            return True

        try:
            normalized = timestamp.strip()

            # Accept legacy cache values accidentally written as "...+00:00Z".
            if normalized.endswith("+00:00Z"):
                normalized = normalized[:-1]
            elif normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"

            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            age_seconds = (
                datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
            ).total_seconds()
            return age_seconds > TRACK_METADATA_CACHE_TTL_SECONDS
        except Exception:
            return True

    @staticmethod
    def _default_links_structure() -> Dict[str, Any]:
        return {"playlists": {}}

    @staticmethod
    def _normalize_loaded_links_data(raw_data: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_data, dict):
            return None

        playlists = raw_data.get("playlists")
        if not isinstance(playlists, dict):
            return None

        normalized = dict(raw_data)
        normalized["playlists"] = playlists
        return normalized

    def _timestamp_for_filename(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    def _json_file_candidates(self, suffix: str) -> List[str]:
        directory = os.path.dirname(self.file_path)
        basename = os.path.basename(self.file_path)
        if not os.path.isdir(directory):
            return []

        candidates: List[str] = []
        prefix = f"{basename}{suffix}"
        for filename in os.listdir(directory):
            if filename == prefix or filename.startswith(f"{prefix}."):
                candidates.append(os.path.join(directory, filename))

        candidates.sort(
            key=lambda path: os.path.getmtime(path) if os.path.exists(path) else 0,
            reverse=True,
        )
        return candidates

    def _backup_candidates(self) -> List[str]:
        return self._json_file_candidates(LINKS_BACKUP_SUFFIX)

    def _load_and_validate_json_file(self, path: str) -> Optional[Dict[str, Any]]:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        return self._normalize_loaded_links_data(raw_data)

    def _copy_current_file_to_backup_if_valid(self) -> Optional[str]:
        if not os.path.exists(self.file_path):
            return None

        try:
            valid_data = self._load_and_validate_json_file(self.file_path)
            if valid_data is None:
                logger.warning(
                    "Current links file was not backed up because it does not contain a valid links structure."
                )
                return None

            backup_path = (
                f"{self.file_path}{LINKS_BACKUP_SUFFIX}.{self._timestamp_for_filename()}"
            )
            shutil.copy2(self.file_path, backup_path)
            self._rotate_link_backups()
            logger.info("Created links backup: %s", backup_path)
            return backup_path
        except Exception as exc:
            logger.warning(
                "Current links file was not backed up because validation failed: %s",
                exc,
            )
            return None

    def _copy_corrupt_file_as_preserved_copy(self, reason: str) -> Optional[str]:
        if not os.path.exists(self.file_path):
            return None

        try:
            corrupt_path = (
                f"{self.file_path}{LINKS_CORRUPT_SUFFIX}.{self._timestamp_for_filename()}"
            )
            shutil.copy2(self.file_path, corrupt_path)
            logger.error(
                "Preserved corrupt links file copy: %s | reason=%s",
                corrupt_path,
                reason,
            )
            return corrupt_path
        except Exception:
            logger.error(
                "Failed to preserve corrupt links file copy for '%s'.",
                self.file_path,
                exc_info=True,
            )
            return None

    def _rotate_link_backups(self) -> None:
        backups = self._backup_candidates()
        for old_backup in backups[LINKS_BACKUP_KEEP_COUNT:]:
            try:
                os.remove(old_backup)
                logger.debug("Removed old links backup: %s", old_backup)
            except Exception:
                logger.debug(
                    "Failed to remove old links backup: %s",
                    old_backup,
                    exc_info=True,
                )

    def _find_object_start_after_key(self, text: str, key: str) -> int:
        key_match = re.search(rf'"{re.escape(key)}"\s*:', text)
        if not key_match:
            return -1

        object_start = text.find("{", key_match.end())
        return object_start

    def _salvage_top_level_object(self, text: str, key: str) -> Optional[Dict[str, Any]]:
        object_start = self._find_object_start_after_key(text, key)
        if object_start < 0:
            return None

        decoder = json.JSONDecoder()
        try:
            parsed, _end = decoder.raw_decode(text[object_start:])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _salvage_playlists_from_corrupt_text(self, text: str) -> Dict[str, Any]:
        playlists_start = self._find_object_start_after_key(text, "playlists")
        if playlists_start < 0:
            return self._default_links_structure()

        decoder = json.JSONDecoder()
        index = playlists_start + 1
        recovered_playlists: Dict[str, Any] = {}
        skipped_entries = 0

        while index < len(text):
            while index < len(text) and text[index] in " \r\n\t,":
                index += 1

            if index >= len(text) or text[index] == "}":
                break

            try:
                playlist_id, key_end = decoder.raw_decode(text[index:])
                if not isinstance(playlist_id, str):
                    raise ValueError("playlist key is not a string")

                index += key_end
                while index < len(text) and text[index] in " \r\n\t":
                    index += 1
                if index >= len(text) or text[index] != ":":
                    raise ValueError("missing colon after playlist key")

                index += 1
                while index < len(text) and text[index] in " \r\n\t":
                    index += 1

                playlist_value, value_end = decoder.raw_decode(text[index:])
                if isinstance(playlist_value, dict):
                    recovered_playlists[playlist_id] = playlist_value

                index += value_end
            except Exception:
                skipped_entries += 1
                next_candidate = re.search(r',\s*"[^"]+"\s*:\s*\{', text[index:])
                if not next_candidate:
                    break
                index += next_candidate.start() + 1

        recovered: Dict[str, Any] = {"playlists": recovered_playlists}

        for optional_key in (
            "track_quality_cache",
            "track_metadata_cache",
            "track_metadata_cache_version",
        ):
            optional_value = self._salvage_top_level_object(text, optional_key)
            if optional_value is not None:
                recovered[optional_key] = optional_value

        logger.warning(
            "Corrupt links salvage completed | recovered_playlists=%d skipped_entries=%d",
            len(recovered_playlists),
            skipped_entries,
        )
        return recovered

    def _recover_links_data_after_decode_error(
        self,
        decode_error: json.JSONDecodeError,
    ) -> Optional[Dict[str, Any]]:
        self._copy_corrupt_file_as_preserved_copy(str(decode_error))

        for backup_path in self._backup_candidates():
            try:
                recovered = self._load_and_validate_json_file(backup_path)
                if recovered is not None:
                    self._last_recovery_source = backup_path
                    logger.warning(
                        "Recovered links data from backup '%s' after main JSON decode failure.",
                        backup_path,
                    )
                    if self._write_links_data_atomic(recovered, create_backup=False):
                        logger.warning(
                            "Repaired main links file from backup '%s'.",
                            backup_path,
                        )
                    return recovered
            except Exception:
                logger.warning(
                    "Skipping invalid links backup during recovery: %s",
                    backup_path,
                    exc_info=True,
                )

        try:
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                corrupt_text = f.read()

            recovered = self._salvage_playlists_from_corrupt_text(corrupt_text)
            normalized = self._normalize_loaded_links_data(recovered)
            if normalized and normalized.get("playlists"):
                self._last_recovery_source = "corrupt-file-salvage"
                logger.warning(
                    "Recovered links data by salvaging corrupt file | playlists=%d",
                    len(normalized.get("playlists", {})),
                )
                if self._write_links_data_atomic(normalized, create_backup=False):
                    logger.warning("Repaired main links file from salvaged data.")
                return normalized
        except Exception:
            logger.error(
                "Failed to salvage corrupted links file '%s'.",
                self.file_path,
                exc_info=True,
            )

        return None

    def _validate_temp_json_file(self, temp_path: str) -> bool:
        try:
            return self._load_and_validate_json_file(temp_path) is not None
        except Exception:
            return False

    def _write_links_data_atomic(
        self,
        links_data_to_save: Dict[str, Any],
        *,
        create_backup: bool = True,
    ) -> bool:
        directory = os.path.dirname(self.file_path)
        os.makedirs(directory, exist_ok=True)

        temp_path = (
            f"{self.file_path}{LINKS_TEMP_SUFFIX}."
            f"{os.getpid()}.{self._timestamp_for_filename()}"
        )

        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(
                    links_data_to_save,
                    f,
                    indent=4,
                    ensure_ascii=False,
                    sort_keys=False,
                )
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())

            if not self._validate_temp_json_file(temp_path):
                logger.error(
                    "Refusing to replace links file because temp JSON validation failed: %s",
                    temp_path,
                )
                return False

            if create_backup:
                self._copy_current_file_to_backup_if_valid()

            os.replace(temp_path, self.file_path)

            try:
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception:
                logger.debug(
                    "Directory fsync was not available for '%s'.",
                    directory,
                    exc_info=True,
                )

            logger.debug("Atomically saved links to '%s'.", self.file_path)
            return True
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    logger.debug(
                        "Failed to remove temp links file: %s",
                        temp_path,
                        exc_info=True,
                    )

    # --- Core Data Handling Methods ---

    # Return type annotation remains Dict[str, Any] as the function guarantees it
    def load_links(self) -> Dict[str, Any]:
        """
        Loads the links data from disk, with backup/salvage recovery for corrupt JSON.
        """
        if self.links_data is not None:
            return self.links_data

        default_structure = self._default_links_structure()
        loaded_data: Dict[str, Any] = default_structure

        with self._io_lock:
            try:
                raw_data = self._load_and_validate_json_file(self.file_path)
                if raw_data is None:
                    logger.warning(
                        "Links file '%s' lacks a valid 'playlists' dictionary. Initializing with empty structure.",
                        self.file_path,
                    )
                    loaded_data = default_structure
                else:
                    loaded_data = raw_data
                    self._load_failed_due_to_unresolved_corruption = False
                    logger.debug("Successfully loaded links from '%s'.", self.file_path)

            except FileNotFoundError:
                logger.warning(
                    "Links file '%s' not found. Initializing with empty structure.",
                    self.file_path,
                )
                loaded_data = default_structure

            except json.JSONDecodeError as exc:
                logger.error(
                    "Failed to decode JSON from '%s': %s. Attempting recovery before falling back to empty structure.",
                    self.file_path,
                    exc,
                )
                recovered_data = self._recover_links_data_after_decode_error(exc)
                if recovered_data is not None:
                    loaded_data = recovered_data
                    self._load_failed_due_to_unresolved_corruption = False
                else:
                    loaded_data = default_structure
                    self._load_failed_due_to_unresolved_corruption = True
                    logger.critical(
                        "Unable to recover links data from '%s'. Empty in-memory structure will be used, but automatic saving is protected.",
                        self.file_path,
                    )

            except IOError as exc:
                logger.error(
                    "Error reading links file '%s': %s. Initializing with empty structure.",
                    self.file_path,
                    exc,
                )
                loaded_data = default_structure

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
        with self._io_lock:
            if self.links_data is None:
                logger.warning("Attempted to save links before loading. Loading first.")
                self.load_links()

            links_data_to_save = cast(Dict[str, Any], self.links_data)

            if (
                self._load_failed_due_to_unresolved_corruption
                and links_data_to_save == self._default_links_structure()
                and os.path.exists(self.file_path)
            ):
                logger.critical(
                    "Refusing to overwrite unresolved corrupt links file with an empty default structure: %s",
                    self.file_path,
                )
                return False

            normalized = self._normalize_loaded_links_data(links_data_to_save)
            if normalized is None:
                logger.error(
                    "Refusing to save links because in-memory data does not contain a valid 'playlists' dictionary."
                )
                return False

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    if self._write_links_data_atomic(normalized, create_backup=True):
                        self._load_failed_due_to_unresolved_corruption = False
                        return True
                    raise IOError("atomic links write returned False")

                except IOError as exc:
                    if attempt < max_retries - 1:
                        logger.warning(
                            "Save attempt %d failed (%s). Retrying...",
                            attempt + 1,
                            exc,
                        )
                        time.sleep(1)
                    else:
                        logger.error(
                            "Failed to save links to '%s' after %d attempts: %s",
                            self.file_path,
                            max_retries,
                            exc,
                        )
                        return False

                except Exception as exc:
                    logger.error(
                        "Unexpected error saving links to '%s': %s",
                        self.file_path,
                        exc,
                        exc_info=True,
                    )
                    return False

            return False

    def get_recovery_status(self) -> Dict[str, Any]:
        """
        Returns diagnostic information about the most recent persistence recovery state.
        """
        return {
            "file_path": self.file_path,
            "load_failed_due_to_unresolved_corruption": self._load_failed_due_to_unresolved_corruption,
            "last_recovery_source": self._last_recovery_source,
            "backup_count": len(self._backup_candidates()),
        }

    def begin_deferred_save(self, reason: str = "bulk") -> None:
        """
        Defers disk writes while bulk link/cache mutations are in progress.
        Mutations still update the in-memory structure immediately.
        """
        with self._io_lock:
            self._deferred_save_depth += 1
            self._deferred_save_reason = str(reason or "bulk")
            logger.debug(
                "Deferred persistence save started | reason=%s depth=%d",
                self._deferred_save_reason,
                self._deferred_save_depth,
            )

    def end_deferred_save(self, reason: str = "bulk") -> bool:
        """
        Ends one deferred-save scope and flushes one atomic save if pending.
        """
        should_flush = False
        flush_reason = str(reason or self._deferred_save_reason or "bulk")

        with self._io_lock:
            if self._deferred_save_depth <= 0:
                logger.debug(
                    "Deferred persistence save end requested without active scope | reason=%s",
                    flush_reason,
                )
                return True

            self._deferred_save_depth -= 1
            if self._deferred_save_depth == 0 and self._deferred_save_pending:
                self._deferred_save_pending = False
                self._deferred_save_reason = None
                should_flush = True

        if not should_flush:
            return True

        start = time.perf_counter()
        result = self.save_links()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "Deferred persistence save flushed | reason=%s elapsed_ms=%.1f result=%s",
            flush_reason,
            elapsed_ms,
            result,
        )
        return result

    def _save_or_defer(self) -> bool:
        """
        Saves immediately unless a bulk/deferred-save scope is active.
        """
        with self._io_lock:
            if self._deferred_save_depth > 0:
                self._deferred_save_pending = True
                logger.debug(
                    "Persistence save deferred | reason=%s depth=%d",
                    self._deferred_save_reason,
                    self._deferred_save_depth,
                )
                return True

        return self.save_links()

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

        if not self._save_or_defer():
            logger.error(
                f"Failed to save track quality cache for track {track_id}"
            )

    def get_cached_track_metadata(self, tidal_track_id: str) -> Dict[str, str]:
        """
        Retrieves cached display metadata for a Tidal track ID.
        """
        track_id = str(tidal_track_id or "").strip()
        if not track_id:
            return {}

        if self.links_data is None:
            self.load_links()

        links_data_dict = cast(Dict[str, Any], self.links_data)
        cache_version = links_data_dict.get("track_metadata_cache_version")
        if cache_version is not None and cache_version != TRACK_METADATA_CACHE_VERSION:
            return {}

        metadata_cache = links_data_dict.get("track_metadata_cache", {})
        if not isinstance(metadata_cache, dict):
            return {}

        entry = metadata_cache.get(track_id, {})
        if not isinstance(entry, dict):
            return {}

        entry_version = entry.get("version")
        if entry_version is not None and entry_version != TRACK_METADATA_CACHE_VERSION:
            return {}

        if self._is_track_metadata_cache_stale(entry.get("timestamp")):
            return {}

        metadata = entry.get("metadata", {})
        if not isinstance(metadata, dict):
            return {}

        return self._clean_track_metadata_cache_values(metadata)

    def set_cached_track_metadata(self, tidal_track_id: str, metadata: Dict[str, str]) -> None:
        """
        Stores or updates cached display metadata for a Tidal track ID.
        """
        track_id = str(tidal_track_id or "").strip()
        cleaned_metadata = self._clean_track_metadata_cache_values(
            cast(Dict[str, Any], metadata or {})
        )
        if not track_id or not cleaned_metadata:
            return

        if self.links_data is None:
            self.load_links()

        links_data_dict = cast(Dict[str, Any], self.links_data)
        links_data_dict["track_metadata_cache_version"] = TRACK_METADATA_CACHE_VERSION

        metadata_cache = links_data_dict.get("track_metadata_cache")
        if not isinstance(metadata_cache, dict):
            metadata_cache = {}
            links_data_dict["track_metadata_cache"] = metadata_cache

        existing_entry = metadata_cache.get(track_id)
        existing_metadata_raw = (
            existing_entry.get("metadata", {})
            if isinstance(existing_entry, dict)
            else {}
        )
        existing_metadata = (
            self._clean_track_metadata_cache_values(existing_metadata_raw)
            if isinstance(existing_metadata_raw, dict)
            else {}
        )

        merged_metadata = dict(existing_metadata)
        merged_metadata.update(cleaned_metadata)

        existing_version = (
            existing_entry.get("version")
            if isinstance(existing_entry, dict)
            else None
        )
        if (
            isinstance(existing_entry, dict)
            and existing_version == TRACK_METADATA_CACHE_VERSION
            and existing_metadata == merged_metadata
            and not self._is_track_metadata_cache_stale(existing_entry.get("timestamp"))
        ):
            return

        metadata_cache[track_id] = {
            "version": TRACK_METADATA_CACHE_VERSION,
            "metadata": merged_metadata,
            "timestamp": self._get_current_timestamp(),
        }

        if not self._save_or_defer():
            logger.error(
                f"Failed to save track metadata cache for track {track_id}"
            )

    def get_playlist_folder_hint(self, playlist_id: str) -> Optional[str]:
        """
        Retrieves a cached playlist folder path hint for a playlist ID.

        The returned value is expected to be a path relative to the download root,
        e.g. ``Playlists/My Playlist``.
        """
        normalized_playlist_id = str(playlist_id or "").strip()
        if not normalized_playlist_id:
            return None

        if self.links_data is None:
            self.load_links()

        links_data_dict = cast(Dict[str, Any], self.links_data)
        folder_cache = links_data_dict.get("playlist_folder_cache", {})
        if not isinstance(folder_cache, dict):
            return None

        entry = folder_cache.get(normalized_playlist_id, {})
        if not isinstance(entry, dict):
            return None

        relative_path = entry.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            return None

        return os.path.normpath(relative_path.strip())

    def set_playlist_folder_hint(
        self,
        playlist_id: str,
        relative_folder_path: str,
        playlist_name: Optional[str] = None,
    ) -> None:
        """
        Stores (or updates) a cached playlist folder path hint.

        Args:
            playlist_id (str): Stable playlist identifier (Spotify/Tidal ID).
            relative_folder_path (str): Folder path relative to download root.
            playlist_name (Optional[str]): Human-readable playlist name for diagnostics.
        """
        normalized_playlist_id = str(playlist_id or "").strip()
        normalized_relative_path = os.path.normpath(
            str(relative_folder_path or "").strip().lstrip("/\\")
        )

        if (
            not normalized_playlist_id
            or not normalized_relative_path
            or normalized_relative_path in {".", ""}
        ):
            return

        if self.links_data is None:
            self.load_links()

        links_data_dict = cast(Dict[str, Any], self.links_data)

        folder_cache = links_data_dict.get("playlist_folder_cache")
        if not isinstance(folder_cache, dict):
            folder_cache = {}
            links_data_dict["playlist_folder_cache"] = folder_cache

        existing_entry = folder_cache.get(normalized_playlist_id)
        if isinstance(existing_entry, dict):
            existing_relative = str(existing_entry.get("relative_path", "")).strip()
            existing_name = str(existing_entry.get("playlist_name", "")).strip()
            incoming_name = str(playlist_name or "").strip()
            if (
                os.path.normpath(existing_relative) == normalized_relative_path
                and existing_name == incoming_name
            ):
                return

        folder_cache[normalized_playlist_id] = {
            "relative_path": normalized_relative_path,
            "playlist_name": str(playlist_name or "").strip() or None,
            "last_seen": self._get_current_timestamp(),
        }

        if not self._save_or_defer():
            logger.error(
                f"Failed to save playlist folder hint for playlist {normalized_playlist_id}"
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

        if not self._save_or_defer():
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
                if not self._save_or_defer():
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
            if not self._save_or_defer():
                # Log an error if saving fails.
                logger.error(f"Failed to save removal of playlist {playlist_id}")
        else:
            # Log if the playlist to be removed was not found.
            logger.warning(f"Playlist {playlist_id} not found for removal.")

# --- END OF FILE persistence.py ---
