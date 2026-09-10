# --- START OF FILE track.py ---

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .album import AlbumMetadata
from .enrichment import MISSING_METADATA_TEXT, format_track_key
from .util import typed

logger = logging.getLogger("streamrip")

# Set up GUI logging with DEBUG level for this module (metadata operations)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger("streamrip", logging.DEBUG)


@dataclass(slots=True)
class TrackInfo:
    id: str
    quality: int

    streamable: bool = True  # Whether the track is available for streaming
    bit_depth: Optional[int] = None
    explicit: bool = False
    sampling_rate: Optional[int | float] = None
    work: Optional[str] = None
    container: Optional[str] = None


@dataclass(slots=True)
class TrackMetadata:
    info: TrackInfo
    title: str
    album: AlbumMetadata
    artist: str  # Primary/first artist only (MusicBrainz standard)
    tracknumber: int
    discnumber: int
    composer: str | None
    author: str | None  # Songwriter/lyricist
    key: str | None = None
    # Fields with defaults must come after non-default fields
    artists: list[str] | None = None  # All contributing artists (MusicBrainz standard)
    isrc: str | None = None
    lyrics: str | None = ""
    source_platform: str | None = None  # e.g. "Tidal"
    source_track_id: str | None = None  # Platform-specific track ID
    source_album_id: str | None = None  # Platform-specific album ID
    source_artist_id: str | None = None # Platform-specific artist ID
    spotify_key: str | None = None
    tidal_key: str | None = None
    key_source: str | None = None
    spotify_track_id: str | None = None
    tidal_dl_id: str | None = None
    # Additional Deezer tags
    bpm: int | None = None
    replaygain_track_gain: str | None = None  # ReplayGain format: "+/-X.XX dB"
    # New standard tags
    track_artist_credit: str | None = None  # Different from track artist
    media_type: str | None = None  # "WEB" for streaming sources

    @classmethod
    def from_tidal(cls, album: AlbumMetadata, track) -> TrackMetadata:
        # FIX: Access attributes directly from the 'track' object instead of dict keys
        title = typed(track.title, str).strip()
        item_id = str(track.id)
        isrc = typed(track.isrc, str)
        version = getattr(track, "version", None)
        explicit = getattr(track, "explicit", False)
        if version:
            title = f"{title} ({version})"

        tracknumber = typed(getattr(track, "trackNumber", 1), int)
        discnumber = typed(getattr(track, "volumeNumber", 1), int)

        tidal_artists = getattr(track, "artists", [])
        artist_id = None
        if tidal_artists and len(tidal_artists) > 0:
            all_artist_names = [a.name for a in tidal_artists]
            artist = all_artist_names[0]  # Primary artist (first one)
            artists = all_artist_names  # All artists
            # Get first artist ID for source_artist_id
            artist_id = str(tidal_artists[0].id)
        else:
            artist = track.artist.name
            artists = [artist]  # Single artist list
            # Get artist ID from single artist object
            artist_id = str(track.artist.id)

        # Check if track is streamable from Tidal API
        streamable = getattr(track, "allowStreaming", True)

        # Lyrics are handled separately in the download process, not from this object
        lyrics = ""

        key = format_track_key(track, use_camelot_key=False)
        if key == MISSING_METADATA_TEXT:
            key = None
        
        # Extract additional Tidal metadata
        bpm = getattr(track, "bpm", None)
        if bpm == 0:
            bpm = None

        logger.debug(f"[from_tidal] Found BPM for track '{track.title}': {bpm} (type: {type(bpm).__name__})")
        
        # Convert replayGain to standard format
        replaygain_track_gain = None
        if hasattr(track, "replayGain") and track.replayGain is not None:
            replaygain_track_gain = f"{track.replayGain:+.2f} dB"
        
        # Standard streaming source metadata
        media_type = "Digital Media"  # MusicBrainz standard for digital/streaming sources

        # Tidal returns single quality based on request, not all available qualities
        # Use the album's quality which comes from config
        quality = album.info.quality
        
        # Set bit depth and sampling rate based on quality
        bit_depth = None
        sampling_rate = None
        if quality >= 2:
            sampling_rate = 44100
            if quality == 3:
                bit_depth = 24
            else:
                bit_depth = 16

        info = TrackInfo(
            id=item_id,
            quality=quality,
            streamable=streamable,
            bit_depth=bit_depth,
            explicit=explicit,
            sampling_rate=sampling_rate,
            work=None,
        )
        return cls(
            info=info,
            title=title,
            album=album,
            artist=artist,
            tracknumber=tracknumber,
            discnumber=discnumber,
            composer=None,
            author=None,
            key=key,
            artists=artists,
            isrc=isrc,
            lyrics=lyrics,
            source_platform=album.source_platform,
            source_track_id=item_id,
            source_album_id=album.source_album_id,
            source_artist_id=artist_id,
            bpm=bpm,
            replaygain_track_gain=replaygain_track_gain,
            tidal_key=key,
            key_source="tidal" if key else None,
            media_type=media_type,
        )

    @classmethod
    def from_resp(cls, album: AlbumMetadata, source, resp) -> TrackMetadata:
        if source == "tidal":
            return cls.from_tidal(album, resp)
        raise Exception("Invalid source")

    def format_track_path(self, format_string: str) -> str:
        # Available keys: "tracknumber", "artist", "artists", "albumartist", "composer", "title",
        # "explicit", "albumcomposer", "album", "source_platform", "container"
        none_text = "Unknown"
        # artist = primary artist only (MusicBrainz standard)
        # artists = all artists comma-separated (MusicBrainz standard)
        artists_str = ", ".join(self.artists) if self.artists else self.artist
        
        info = {
            "title": self.title,
            "tracknumber": self.tracknumber,
            "artist": self.artist,  # Primary artist only
            "artists": artists_str,  # All artists comma-separated
            "albumartist": self.album.albumartist,
            "albumcomposer": self.album.albumcomposer or none_text,
            "composer": self.composer or none_text,
            "explicit": " (Explicit) " if self.info.explicit else "",
            "album": self.album.album,
            "source_platform": self.source_platform or none_text,
            "container": self.info.container or none_text,
        }
        return format_string.format(**info)

# --- END OF FILE track.py ---
