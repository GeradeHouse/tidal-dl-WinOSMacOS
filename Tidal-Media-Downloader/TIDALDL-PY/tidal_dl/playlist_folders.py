"""Explicit local playlist destinations, independent of automatic folder hints.

Links are machine-local, keyed by service and stable playlist ID. Reads are
cached and thread-safe; a batch is published only after its atomic save succeeds.
This module deliberately has no GUI or network dependencies.
"""

import json
import os
import tempfile
import threading
from typing import Any, Optional

from .model import Playlist
from .paths import getProfilePath


def playlist_key(context: Any) -> Optional[str]:
    if isinstance(context, Playlist):
        identifier = str(getattr(context, "uuid", "") or "").strip()
        return f"tidal:{identifier}" if identifier else None
    if not isinstance(context, dict):
        return None
    service = context.get("type")
    data = context.get("data")
    if service not in {"spotify", "tidal"}:
        return None
    if service == "tidal" and isinstance(data, Playlist):
        return playlist_key(data)
    if not isinstance(data, dict):
        return None
    identifier = str(data.get("uuid" if service == "tidal" else "id") or data.get("id") or "").strip()
    return f"{service}:{identifier}" if identifier else None


def canonical_folder(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


class PlaylistFolderStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._links: Optional[dict[str, str]] = None
        self._path: Optional[str] = None

    def _load(self) -> dict[str, str]:
        if self._links is not None:
            return self._links
        self._path = os.path.join(getProfilePath(), "playlist-folder-links.json")
        try:
            with open(self._path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            self._links = {}
            return self._links
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Cannot read playlist folder links: {self._path}. "
                "Restore this file before downloading; no default destination was substituted."
            ) from exc
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("links"), dict):
            raise ValueError(f"Invalid playlist folder links file: {self._path}")
        links = payload["links"]
        for key, path in links.items():
            if (
                not isinstance(key, str)
                or not key.startswith(("tidal:", "spotify:"))
                or not key.partition(":")[2]
                or not isinstance(path, str)
                or not os.path.isabs(path)
            ):
                raise ValueError(f"Invalid playlist folder link in {self._path}")
        self._links = dict(links)
        return self._links

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._load())

    def get(self, context: Any) -> Optional[str]:
        key = playlist_key(context)
        if key is None:
            return None
        with self._lock:
            return self._load().get(key)

    def update(self, changes: dict[str, Optional[str]]) -> None:
        """Save all confirmed changes or none. Never modify any audio files."""
        with self._lock:
            updated = dict(self._load())
            for key, path in changes.items():
                if not key.startswith(("tidal:", "spotify:")) or not key.partition(":")[2]:
                    raise ValueError("A stable service and playlist ID are required.")
                if path is None:
                    updated.pop(key, None)
                    continue
                path = os.path.abspath(path)
                if not os.path.isdir(path):
                    raise ValueError(f"The selected folder is no longer available:\n{path}")
                if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
                    raise PermissionError(f"Read/write access is required for:\n{path}")
                updated[key] = path

            owners: dict[str, str] = {}
            for key, path in updated.items():
                normalized = canonical_folder(path)
                if normalized in owners and owners[normalized] != key:
                    raise ValueError(
                        f"This folder is already linked to {owners[normalized]}:\n{path}\n"
                        "Use a separate folder for each playlist to avoid mixing downloads."
                    )
                owners[normalized] = key

            assert self._path is not None
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=os.path.dirname(self._path),
                    prefix=".playlist-folders-", suffix=".tmp", delete=False,
                ) as handle:
                    temporary_path = handle.name
                    json.dump({"version": 1, "links": updated}, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self._path)
                self._links = updated
            finally:
                if temporary_path and os.path.exists(temporary_path):
                    os.unlink(temporary_path)


PLAYLIST_FOLDERS = PlaylistFolderStore()


def get_linked_playlist_folder(context: Any) -> Optional[str]:
    return PLAYLIST_FOLDERS.get(context)


def validate_linked_playlist_folder(context: Any) -> Optional[str]:
    """Download preflight: never recreate a missing linked folder or fall back."""
    folder = get_linked_playlist_folder(context)
    if folder:
        if not os.path.isdir(folder):
            raise FileNotFoundError(
                f"The linked playlist folder is unavailable:\n{folder}\n"
                "Reconnect the drive, or use the playlist's Local folder menu to relink or remove the link."
            )
        if not os.access(folder, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError(f"The linked playlist folder is not readable/writable:\n{folder}")
    return folder
