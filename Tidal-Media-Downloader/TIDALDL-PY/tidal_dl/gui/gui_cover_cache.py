#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_cover_cache.py
@Time    :   2025/04/28
@Author  :   Roo
@Version :   1.0
@Contact :
@Desc    :   Cover art cache for the Tidal-DL GUI
"""

import logging
import tempfile
import pickle
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional
import os

import requests
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QObject, pyqtSignal, QRunnable
from PyQt6.QtGui import QPixmap
from requests.exceptions import RequestException

from tidal_dl import TIDAL_API
from tidal_dl.settings import SETTINGS
from tidal_dl.paths import getProfilePath  # Import getProfilePath directly

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# Set up GUI logging with INFO level for this module (more verbose GUI output for downloads)
# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from tidal_dl.gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

CACHE_FILE_NAME = "cover_cache.pkl"
CACHE_SAVE_DEBOUNCE_SECONDS = 0.75
CACHE_REPLACE_MAX_ATTEMPTS = 6
CACHE_REPLACE_BASE_DELAY_SECONDS = 0.15

TIDAL_COVER_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}[-/][0-9a-fA-F]{4}[-/][0-9a-fA-F]{4}[-/][0-9a-fA-F]{4}[-/][0-9a-fA-F]{12}$"
)


def _looks_like_tidal_cover_id(value: Optional[str]) -> bool:
    text = str(value or "").strip()
    if not text or text.startswith(("http://", "https://")):
        return False
    return bool(TIDAL_COVER_ID_RE.match(text.replace("-", "/")))


class CoverCache:
    """A persistent, singleton, disk-based cache for cover art (QPixmap)."""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(CoverCache, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_size=500):
        if self._initialized:
            return
        
        # --- FIX: Use getProfilePath() to ensure we write to User Music folder ---
        # Previously: self.cache_path = Path(SETTINGS._path_).parent / CACHE_FILE_NAME
        profile_path = getProfilePath()
        self.cache_path = Path(profile_path) / CACHE_FILE_NAME
        
        logger.debug(f"[CoverCache] Initializing cache singleton instance at {self.cache_path}")
        self._lock = threading.Lock()
        # Cover workers finish concurrently. Keep disk replacement serialized and
        # coalesce bursts so a large playlist does not rewrite the pickle once per row.
        self._save_lock = threading.Lock()
        self._save_timer: Optional[threading.Timer] = None
        self.cache: Dict[str, bytes] = self._load_cache()
        self.max_size = max_size
        self._initialized = True

    def _load_cache(self) -> Dict[str, bytes]:
        with self._lock:
            if self.cache_path.exists():
                logger.debug(f"[CoverCache] Loading cache from {self.cache_path}")
                try:
                    with open(self.cache_path, "rb") as f:
                        cache = pickle.load(f)
                        logger.debug(f"[CoverCache] Loaded {len(cache)} items from disk.")
                        return cache
                except (
                    pickle.UnpicklingError,
                    EOFError,
                    FileNotFoundError,
                    ValueError,
                    TypeError,
                    AttributeError,
                ) as e:
                    logger.warning(
                        f"[CoverCache] Invalid cache file: {e}. Deleting it and creating a new cache."
                    )
                    try:
                        self.cache_path.unlink(missing_ok=True)
                    except OSError as delete_error:
                        logger.warning(
                            f"[CoverCache] Could not delete invalid cache file: {delete_error}"
                        )
                    return {}
            logger.debug("[CoverCache] No cache file found. Starting with an empty cache.")
            return {}

    def _save_cache(self):
        with self._lock:
            cache_copy = self.cache.copy()
            self._save_timer = None
        
        logger.debug(f"[CoverCache] Saving {len(cache_copy)} items to {self.cache_path}")
        with self._save_lock:
            temp_path = None
            try:
                # Ensure directory exists (just in case)
                os.makedirs(self.cache_path.parent, exist_ok=True)

                with tempfile.NamedTemporaryFile(
                    "wb",
                    dir=self.cache_path.parent,
                    prefix=f"{self.cache_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as f:
                    pickle.dump(cache_copy, f)
                    f.flush()
                    os.fsync(f.fileno())
                    temp_path = Path(f.name)

                for attempt in range(1, CACHE_REPLACE_MAX_ATTEMPTS + 1):
                    try:
                        if self.cache_path.exists():
                            try:
                                os.chmod(self.cache_path, 0o600)
                            except OSError:
                                pass
                        os.replace(temp_path, self.cache_path)
                        temp_path = None
                        logger.debug("[CoverCache] Successfully saved cache to disk.")
                        break
                    except PermissionError as replace_error:
                        if attempt >= CACHE_REPLACE_MAX_ATTEMPTS:
                            raise
                        delay = CACHE_REPLACE_BASE_DELAY_SECONDS * attempt
                        logger.debug(
                            "[CoverCache] Cache destination temporarily locked; retrying "
                            "atomic replace in %.2fs (attempt %d/%d): %s",
                            delay,
                            attempt,
                            CACHE_REPLACE_MAX_ATTEMPTS,
                            replace_error,
                        )
                        time.sleep(delay)
            except IOError as e:
                logger.warning(
                    "Cover cache could not be persisted after retries; continuing with the in-memory cache: %s",
                    e,
                )
            except Exception as e:
                logger.warning(
                    "Unexpected error saving cover cache; continuing with the in-memory cache: %s",
                    e,
                    exc_info=True,
                )
            finally:
                if temp_path and temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError as cleanup_error:
                        logger.debug(f"[CoverCache] Could not remove temporary cache file: {cleanup_error}")

    def _schedule_save(self) -> None:
        """Debounce persistent writes triggered by concurrent cover workers."""
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            timer = threading.Timer(CACHE_SAVE_DEBOUNCE_SECONDS, self._save_cache)
            timer.daemon = True
            self._save_timer = timer
            timer.start()

    def get(self, url: str) -> Optional[QPixmap]:
        with self._lock:
            logger.debug(f"[CoverCache] Attempting to get item with key: {url}")
            image_data = self.cache.get(url)
        
        if image_data:
            logger.debug(f"[CoverCache] Cache hit for key: {url}")
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            return pixmap
        logger.debug(f"[CoverCache] Cache miss for key: {url}")
        return None

    def set(self, url: str, pixmap: QPixmap):
        logger.debug(f"[CoverCache] Setting item for key: {url}")
        
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        image_data = byte_array.data()

        with self._lock:
            if len(self.cache) >= self.max_size:
                try:
                    # Evict the oldest item (first item in Python 3.7+ dicts)
                    oldest_url = next(iter(self.cache))
                    logger.debug(f"[CoverCache] Cache full. Evicting oldest item: {oldest_url}")
                    self.cache.pop(oldest_url)
                except StopIteration:
                    pass  # Cache was empty, nothing to evict

            self.cache[url] = image_data
        
        self._schedule_save()


class CoverArtWorkerSignals(QObject):
    """Defines signals for the CoverArtWorker."""

    finished = pyqtSignal()
    cover_ready = pyqtSignal(str, QPixmap)
    error = pyqtSignal(str, str)

    def __str__(self) -> str:
        return (
            f"CoverArtWorkerSignals({self.finished}, {self.cover_ready}, {self.error})"
        )


class CoverArtWorker(QRunnable):
    """
    Worker thread for downloading cover art.
    Fetches an image from a URL and emits a signal with the QPixmap.
    """

    def __init__(
        self,
        url: Optional[str],
        cache: CoverCache,
        type: str,
        item_id: str,
        item_name: Optional[str] = None,
    ):
        super().__init__()
        self.url = url
        self.cache = cache
        self.type = type
        self.item_id = item_id
        self.item_name = item_name or ""
        self.signals = CoverArtWorkerSignals()

    def _fetch_tidal_cover_data(self, cover_sid_or_url: str) -> tuple[bytes, str]:
        resolved_key = TIDAL_API.getCoverUrl(str(cover_sid_or_url), "320", "320")
        cache_key = resolved_key if resolved_key else str(cover_sid_or_url)

        for cover_size in ("320", "640", "1280"):
            image_data = TIDAL_API.getCoverData(
                str(cover_sid_or_url),
                cover_size,
                cover_size,
                suppress_logs=(self.type == "Artist"),
            )
            if image_data:
                resolved_size_key = TIDAL_API.getCoverUrl(
                    str(cover_sid_or_url),
                    cover_size,
                    cover_size,
                )
                return image_data, resolved_size_key if resolved_size_key else cache_key

        return b"", cache_key

    def run(self):
        logger.debug(
            "[CoverArtWorker] Starting run for URL: '%s', Type: %s, ID: %s, Name: %s",
            self.url,
            self.type,
            self.item_id,
            self.item_name,
        )
        
        # Signal/cache key strategy:
        # - TIDAL playlist collage (type='tidal', url=None): synthetic playlist key
        # - Other types: prefer URL/cover source; fallback to type+id key
        signal_key = self.url if self.url else f"{self.type}_{self.item_id}"
        if self.type == "tidal" and self.url is None:
            signal_key = f"tidal_playlist_{self.item_id}"

        try:
            # Check cache first
            cached_pixmap = self.cache.get(signal_key)
            if cached_pixmap:
                self.signals.cover_ready.emit(signal_key, cached_pixmap)
                return

            image_data = None
            
            if self.type == "tidal":
                # --- START OF MODIFICATION ---
                # If URL is None, it's a playlist needing a collage.
                # The item_id is the playlist's UUID.
                if self.url is None:
                    if not self.item_id:
                        raise Exception("Cannot generate playlist collage without a playlist ID.")
                    image_data = TIDAL_API.getPlaylistCoverData(self.item_id)
                # If URL is present, it's a direct fetch for an album cover.
                # The item_id is the image UUID.
                else:
                    if not self.item_id:
                        raise Exception("Cannot fetch cover without an image ID.")
                    image_data = TIDAL_API.getCoverData(self.item_id, "320", "320")
                # --- END OF MODIFICATION ---
            
            elif self.type in ["Track", "Album", "Artist"]:
                cover_sid_or_url: Optional[str] = self.url

                # Resolve from API only when no direct cover source is available.
                if not cover_sid_or_url:
                    if self.type == "Track":
                        if _looks_like_tidal_cover_id(self.item_id):
                            cover_sid_or_url = self.item_id
                        else:
                            track = TIDAL_API.getTrack(self.item_id)
                            if (
                                track
                                and hasattr(track, "album")
                                and track.album
                                and hasattr(track.album, "cover")
                            ):
                                cover_sid_or_url = getattr(track.album, "cover", None)
                    elif self.type == "Album":
                        if _looks_like_tidal_cover_id(self.item_id):
                            cover_sid_or_url = self.item_id
                        else:
                            album = TIDAL_API.getAlbum(self.item_id)
                            if album and hasattr(album, "cover"):
                                cover_sid_or_url = getattr(album, "cover", None)
                    elif self.type == "Artist":
                        artist = TIDAL_API.getArtist(self.item_id)
                        if artist and hasattr(artist, "picture"):
                            cover_sid_or_url = getattr(artist, "picture", None)

                if cover_sid_or_url:
                    resolved_key = TIDAL_API.getCoverUrl(str(cover_sid_or_url), "320", "320")
                    signal_key = resolved_key if resolved_key else str(cover_sid_or_url)

                    cached_pixmap = self.cache.get(signal_key)
                    if cached_pixmap:
                        self.signals.cover_ready.emit(signal_key, cached_pixmap)
                        return

                logger.debug(
                    "COVER_DIAG_WORKER type=%s item_id=%s item_name=%s resolved_source=%s signal_key=%s",
                    self.type,
                    self.item_id,
                    self.item_name,
                    cover_sid_or_url,
                    signal_key,
                )

                if self.type == "Artist" and not cover_sid_or_url:
                    self.signals.cover_ready.emit(signal_key, QPixmap())
                    return

                if cover_sid_or_url:
                    image_data, signal_key = self._fetch_tidal_cover_data(
                        str(cover_sid_or_url)
                    )

            else: # For other types like Spotify
                if self.url:
                    with requests.get(self.url, stream=True, timeout=10) as response:
                        response.raise_for_status()
                        image_data = response.content

            if not image_data:
                if self.type in {"Artist", "Track", "Album", "tidal"}:
                    logger.warning(
                        "COVER_DIAG_NO_IMAGE_DATA type=%s item_id=%s item_name=%s url=%s signal_key=%s",
                        self.type,
                        self.item_id,
                        self.item_name,
                        self.url,
                        signal_key,
                    )
                    self.signals.cover_ready.emit(signal_key, QPixmap())
                    return

                logger.warning(
                    "COVER_DIAG_NO_IMAGE_DATA type=%s item_id=%s item_name=%s url=%s signal_key=%s",
                    self.type,
                    self.item_id,
                    self.item_name,
                    self.url,
                    signal_key,
                )
                raise Exception("No image data could be fetched or generated.")

            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            if pixmap.isNull():
                logger.warning(
                    "COVER_DIAG_INVALID_IMAGE_DATA type=%s item_id=%s item_name=%s url=%s signal_key=%s",
                    self.type,
                    self.item_id,
                    self.item_name,
                    self.url,
                    signal_key,
                )
                self.signals.cover_ready.emit(signal_key, QPixmap())
                return

            cache_aliases = {signal_key}
            if self.url:
                cache_aliases.add(str(self.url))
            if _looks_like_tidal_cover_id(self.item_id):
                cache_aliases.add(str(self.item_id))
                cover_url_alias = TIDAL_API.getCoverUrl(str(self.item_id), "320", "320")
                if cover_url_alias:
                    cache_aliases.add(cover_url_alias)

            for cache_alias in cache_aliases:
                if cache_alias:
                    self.cache.set(cache_alias, pixmap)

            self.signals.cover_ready.emit(signal_key, pixmap)

        except RequestException as e:
            error_message = f"Failed to download cover art from {signal_key}: {e}"
            logger.warning(error_message)
            self.signals.error.emit(signal_key, str(e))
        except Exception as e:
            error_message = (
                f"An unexpected error occurred in CoverArtWorker for {signal_key}: {e}"
            )
            logger.error(error_message, exc_info=True)
            self.signals.error.emit(signal_key, str(e))
        finally:
            self.signals.finished.emit()
