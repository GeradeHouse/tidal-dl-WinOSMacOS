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

import requests
import logging
import pickle
import threading
from pathlib import Path
from typing import Optional, Dict
from requests.exceptions import RequestException

from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QPixmap

from .. import TIDAL_API
from ..model import Track, Album
from ..settings import SETTINGS

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# Set up GUI logging with INFO level for this module (more verbose GUI output for downloads)
# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from .gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

CACHE_FILE_NAME = "cover_cache.pkl"

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
        self.cache_path = Path(SETTINGS._path_).parent / CACHE_FILE_NAME
        logger.debug(f"[CoverCache] Initializing cache singleton instance at {self.cache_path}")
        self._lock = threading.Lock()
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
                except (pickle.UnpicklingError, EOFError, FileNotFoundError) as e:
                    logger.warning(f"[CoverCache] Failed to load cache file: {e}. Creating a new cache.")
                    return {}
            logger.debug("[CoverCache] No cache file found. Starting with an empty cache.")
            return {}

    def _save_cache(self):
        with self._lock:
            cache_copy = self.cache.copy()
        
        logger.debug(f"[CoverCache] Saving {len(cache_copy)} items to {self.cache_path}")
        try:
            with open(self.cache_path, "wb") as f:
                pickle.dump(cache_copy, f)
            logger.debug("[CoverCache] Successfully saved cache to disk.")
        except IOError as e:
            logger.error(f"Could not save cover cache: {e}")
        except Exception as e:
            logger.error(f"Unexpected error saving cover cache: {e}", exc_info=True)

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
        
        self._save_cache()


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

    def __init__(self, url: Optional[str], cache: CoverCache, type: str, item_id: str):
        super().__init__()
        self.url = url
        self.cache = cache
        self.type = type
        self.item_id = item_id
        self.signals = CoverArtWorkerSignals()

    def run(self):
        logger.debug(f"[CoverArtWorker] Starting run for URL: '{self.url}', Type: {self.type}, ID: {self.item_id}")
        
        # The URL is the primary key for caching and signals. For playlist collages, create a synthetic key.
        signal_key = self.url if self.url else f"tidal_playlist_{self.item_id}"

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
            
            elif self.type in ["Track", "Album"]:
                 # This logic is for non-Tidal items and remains unchanged.
                if self.type == "Track":
                    track = TIDAL_API.getTrack(self.item_id)
                    if track and hasattr(track, 'album') and track.album and hasattr(track.album, 'cover'):
                        self.url = TIDAL_API.getCoverUrl(track.album.cover)
                elif self.type == "Album":
                    album = TIDAL_API.getAlbum(self.item_id)
                    if album and hasattr(album, 'cover'):
                        self.url = TIDAL_API.getCoverUrl(album.cover)
                
                if self.url:
                    with requests.get(self.url, stream=True, timeout=10) as response:
                        response.raise_for_status()
                        image_data = response.content

            else: # For other types like Spotify
                if self.url:
                    with requests.get(self.url, stream=True, timeout=10) as response:
                        response.raise_for_status()
                        image_data = response.content

            if not image_data:
                raise Exception("No image data could be fetched or generated.")

            pixmap = QPixmap()
            pixmap.loadFromData(image_data)

            # Cache the new pixmap using the signal_key
            self.cache.set(signal_key, pixmap)

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