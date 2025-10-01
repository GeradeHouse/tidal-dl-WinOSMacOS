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
from typing import Optional
from requests.exceptions import RequestException

from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool
from PyQt6.QtGui import QPixmap

from .. import TIDAL_API
from ..model import Track, Album

logger = logging.getLogger(__name__)


class CoverCache(object):
    """A simple in-memory cache for cover art."""

    def __init__(self, max_size=200):
        self.cache = {}
        self.max_size = max_size

    def get(self, url: str) -> Optional[QPixmap]:
        return self.cache.get(url)

    def set(self, url: str, pixmap: QPixmap):
        if len(self.cache) >= self.max_size:
            # Simple eviction strategy: remove the first added item
            try:
                self.cache.pop(next(iter(self.cache)))
            except StopIteration:
                pass  # Cache is empty, nothing to pop
        self.cache[url] = pixmap


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

    def __init__(self, url: str, cache: CoverCache, type: str, item_id: str):
        super().__init__()
        self.url = url
        self.cache = cache
        self.type = type
        self.item_id = item_id
        self.signals = CoverArtWorkerSignals()

    def run(self):
        try:
            # Check cache first
            cached_pixmap = self.cache.get(self.url)
            if cached_pixmap:
                self.signals.cover_ready.emit(self.url, cached_pixmap)
                return

            if self.type == "Track":
                track = TIDAL_API.getTrack(self.item_id)
                if (
                    track
                    and hasattr(track, "album")
                    and track.album
                    and hasattr(track.album, "id")
                ):
                    album_id = track.album.id
                    if album_id:
                        album = TIDAL_API.getAlbum(album_id)
                        if album and hasattr(album, "cover") and album.cover:
                            new_url = TIDAL_API.getCoverUrl(album.cover)
                            if new_url and new_url != self.url:
                                self.url = new_url
                                cached_pixmap = self.cache.get(self.url)
                else:
                    logger.warning(
                        f"Track {self.item_id} has no album info, cannot fetch cover."
                    )
                    return  # Exit if no album info
            elif self.type == "Album":
                album = TIDAL_API.getAlbum(self.item_id)
                new_url = (
                    TIDAL_API.getCoverUrl(album.cover)
                    if album and hasattr(album, "cover")
                    else None
                )
                if new_url and new_url != self.url:
                    self.url = new_url

            # Proceed with download if not in cache
            with requests.get(self.url, stream=True, timeout=5) as response:
                response.raise_for_status()
                image_data = response.content
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)

                # Cache the new pixmap
                self.cache.set(self.url, pixmap)

                self.signals.cover_ready.emit(self.url, pixmap)

        except RequestException as e:
            error_message = f"Failed to download cover art from {self.url}: {e}"
            logger.warning(error_message)
            self.signals.error.emit(self.url, str(e))
        except Exception as e:
            error_message = (
                f"An unexpected error occurred in CoverArtWorker for {self.url}: {e}"
            )
            logger.error(error_message, exc_info=True)
            self.signals.error.emit(self.url, str(e))
        finally:
            self.signals.finished.emit()
