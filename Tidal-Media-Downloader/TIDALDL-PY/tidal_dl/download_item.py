from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from tidal_dl.model import Track


@dataclass(slots=True)
class DownloadItem:
    """Download context that keeps source metadata with the linked TIDAL track."""

    tidal_track: Track
    source_platform: str = "tidal"
    source_track_id: Optional[str] = None
    spotify_key: Optional[int] = None
    spotify_mode: Optional[int] = None
    spotify_tempo: Optional[float] = None
    spotify_metadata: Optional[dict[str, Any]] = None

    @classmethod
    def from_track(cls, track: Track) -> "DownloadItem":
        return cls(tidal_track=track)

