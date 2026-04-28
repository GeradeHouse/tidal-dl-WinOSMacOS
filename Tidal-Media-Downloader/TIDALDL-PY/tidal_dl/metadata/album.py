# tidal_dl/metadata/album.py

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from ..filepath_utils import clean_filename, clean_filepath
from .covers import Covers
from .util import get_quality_id, safe_get, typed

PHON_COPYRIGHT = "\u2117"
COPYRIGHT = "\u00a9"

logger = logging.getLogger("streamrip")

# Set up GUI logging with DEBUG level for this module (metadata operations)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger("streamrip", logging.DEBUG)


genre_clean = re.compile(r"([^\u2192\/]+)")


def extract_label_from_copyright(copyright_text: Optional[str]) -> Optional[str]:
    """Derive a probable label from TIDAL copyright text."""
    if not copyright_text:
        return None

    cleaned = str(copyright_text).strip()
    if not cleaned:
        return None

    copyright_match = re.match(
        r"^(?:(?:\([CP]\)|\[[CP]\]|[©℗])\s*|[CP]\s+)\d{4}\s*(.+)$",
        cleaned,
        re.IGNORECASE,
    )
    if copyright_match:
        return copyright_match.group(1).strip() or None

    label_match = re.match(
        r"^(?:(?:\([CP]\)|\[[CP]\]|[©℗])\s*|[CP]\s+)(.+)$",
        cleaned,
        re.IGNORECASE,
    )
    if label_match:
        return label_match.group(1).strip() or None

    return None


@dataclass(slots=True)
class AlbumInfo:
    id: str
    quality: int
    container: str
    label: Optional[str] = None
    explicit: bool = False
    sampling_rate: int | float | None = None
    bit_depth: int | None = None
    booklets: list[dict] | None = None
    streamable: bool = True  # Whether the album is available for streaming



@dataclass(slots=True)
class AlbumMetadata:
    info: AlbumInfo
    album: str
    albumartist: str
    year: str
    genre: list[str]
    covers: Covers
    tracktotal: int
    disctotal: int = 1
    albumcomposer: str | None = None
    comment: str | None = None
    compilation: str | None = None
    copyright: str | None = None
    date: str | None = None
    description: str | None = None
    encoder: str | None = None
    grouping: str | None = None
    lyrics: str | None = None
    purchase_date: str | None = None
    source_platform: str | None = None
    source_album_id: str | None = None
    source_artist_id: str | None = None
    # Additional Deezer tags
    bpm: int | None = None
    barcode: str | None = None  # UPC/Barcode
    replaygain_album_gain: str | None = None  # ReplayGain format: "+/-X.XX dB"
    releasetype: str | None = None  # Vorbis standard name
    # New standard tags
    album_artist_credit: str | None = None  # Different from album artist
    originaldate: str | None = None  # Vorbis standard name
    media_type: str | None = None  # "WEB" for streaming sources
    # RYM metadata
    rym_descriptors: list[str] | None = None  # RateYourMusic descriptors

    def get_genres(self) -> str:
        return ", ".join(self.genre)

    def _get_rym_album_type(self) -> str:
        """Map streaming metadata to RYM album type."""
        if not self.releasetype:
            return "album"  # Default

        release_type = self.releasetype.lower()

        # Map streaming service types to RYM types
        type_mapping = {
            "ep": "ep",
            "single": "single",
            "compilation": "compilation",
            "best of": "compilation",
            "album": "album"
        }

        return type_mapping.get(release_type, "album")

    async def enrich_with_rym(self, rym_service):
        """Enrich this album metadata with RateYourMusic data using comprehensive fallback strategy.

        Uses rym_descriptors as state indicator:
        - None: Not yet searched
        - []: Searched but not found
        - [...]: Searched and found with descriptors
        """
        # Skip if already enriched (rym_descriptors is not None means we already tried)
        if self.rym_descriptors is not None:
            logger.debug(f"RYM enrichment already attempted for: {self.albumartist} - {self.album}")
            return

        if not rym_service:
            return

        try:
            # Parse year as integer
            year = None
            if self.year and self.year != "Unknown":
                try:
                    year = int(self.year)
                except ValueError:
                    year = None

            # Determine album type from streaming metadata
            album_type = self._get_rym_album_type()

            # Single call with comprehensive fallback built-in
            # (album search with optimized flow → artist fallback if needed)
            rym_metadata = await rym_service.get_release_metadata(
                self.albumartist, self.album, year, album_type
            )

            if rym_metadata:
                # Log what type of metadata we got for debugging
                if hasattr(rym_metadata, 'album') and rym_metadata.album:
                    logger.debug(f"RYM album enrichment: {self.albumartist} - {self.album}")
                else:
                    logger.debug(f"RYM artist fallback enrichment: {self.albumartist} - {self.album}")

                # Apply genre enrichment policy through service
                self.genre = rym_service.enrich_genres(self.genre, rym_metadata)

                # Add descriptors directly from RYM metadata object
                if rym_metadata.descriptors:
                    self.rym_descriptors = rym_metadata.descriptors
                else:
                    # Found metadata but no descriptors
                    self.rym_descriptors = []
            else:
                # No metadata found - mark as searched with empty list
                self.rym_descriptors = []
                logger.debug(f"RYM enrichment found no results for: {self.albumartist} - {self.album}")

        except Exception as e:
            # Even on error, mark as attempted to avoid retrying on every track
            self.rym_descriptors = []
            logger.debug(f"Failed to enrich {self.albumartist} - {self.album} with RYM data: {e}")

    def get_copyright(self) -> str | None:
        if self.copyright is None:
            return None
        # Add special chars
        _copyright = re.sub(r"(?i)\(P\)", PHON_COPYRIGHT, self.copyright)
        _copyright = re.sub(r"(?i)\(C\)", COPYRIGHT, _copyright)
        return _copyright

    def format_folder_path(self, formatter: str) -> str:
        # Available keys: "albumartist", "title", "year", "bit_depth", "sampling_rate",
        # "id", "albumcomposer", "releasetype"

        none_str = "Unknown"
        # Format releasetype with title case, except keep EP uppercase
        releasetype_formatted = none_str
        if self.releasetype:
            rt = clean_filename(self.releasetype)
            if rt.upper() == "EP":
                releasetype_formatted = "EP"
            else:
                releasetype_formatted = rt.title()
        
        info: dict[str, str | int | float] = {
            "albumartist": clean_filename(self.albumartist),
            "albumcomposer": clean_filename(self.albumcomposer or "") or none_str,
            "bit_depth": self.info.bit_depth or none_str,
            "id": self.info.id,
            "sampling_rate": self.info.sampling_rate or none_str,
            "title": clean_filename(self.album),
            "year": self.year,
            "container": self.info.container,
            "releasetype": releasetype_formatted,
        }

        return clean_filepath(formatter.format(**info))

    @classmethod
    def from_tidal(cls, resp) -> AlbumMetadata:
        """
        Args:
        ----
            resp: An Album object from tidal_dl.model.
        Returns: AlbumMetadata instance with streamable attribute set.
        """
        # ADDED: Debug log to inspect the incoming object
        logger.debug(f"[from_tidal] Received Album object for metadata creation. Attributes: {vars(resp)}")

        # FIX: Access attributes directly, not dictionary keys.
        # Use getattr for safety with a default value.
        streamable = getattr(resp, "allowStreaming", True)

        item_id = str(resp.id)
        album = typed(getattr(resp, "title", "Unknown Album"), str)

        # --- FIX: Handle potential None value for numberOfTracks ---
        track_total_val = getattr(resp, "numberOfTracks", 1)
        if track_total_val is None:
            logger.debug(f"[from_tidal] 'numberOfTracks' was None for album ID {item_id}. Defaulting to 1.")
            track_total_val = 1
        tracktotal = typed(track_total_val, int)

        date_val = getattr(resp, "releaseDate", None)
        if date_val is None:
            logger.debug(f"[from_tidal] 'releaseDate' was None for album ID {item_id}. Defaulting to empty string.")
            date_val = ""
        date = typed(date_val, str)
        year = date[:4] if date else "Unknown"

        copyright_val = getattr(resp, "copyright", "")
        if copyright_val is None:
            logger.debug(f"[from_tidal] 'copyright' was None for album ID {item_id}. Defaulting to empty string.")
            copyright_val = ""
        _copyright = typed(copyright_val, str)

        albumartist = "Unknown Artist"
        artist_id = None
        
        # First, try to get the list of artists
        artists_val = getattr(resp, "artists", None)
        logger.debug(f"[from_tidal] Initial 'artists' attribute value: {artists_val} (type: {type(artists_val).__name__})")

        if artists_val: # This checks if it's not None and not an empty list
            artists = typed(artists_val, list)
            albumartist = artists[0].name
            artist_id = str(artists[0].id)
            logger.debug(f"[from_tidal] Successfully processed 'artists' list. Album Artist: '{albumartist}', Artist ID: {artist_id}")
        else:
            # If 'artists' is None or empty, fall back to the single 'artist' attribute
            artist_obj = getattr(resp, "artist", None)
            logger.debug(f"[from_tidal] 'artists' was empty or None. Falling back to 'artist' attribute. Value: {artist_obj}")
            if artist_obj:
                albumartist = typed(getattr(artist_obj, "name", "Unknown Artist"), str)
                artist_id = str(getattr(artist_obj, "id", None))
                # Create a list containing the single artist object for downstream compatibility
                artists = [artist_obj] 
                logger.debug(f"[from_tidal] Successfully processed 'artist' object. Album Artist: '{albumartist}', Artist ID: {artist_id}")
            else:
                # If both are None, set a default empty list
                artists = []
                logger.debug(f"[from_tidal] Both 'artists' and 'artist' are None for album ID {item_id}. Using default 'Unknown Artist'.")

        disc_total_val = getattr(resp, "numberOfVolumes", 1)
        if disc_total_val is None:
            logger.debug(f"[from_tidal] 'numberOfVolumes' was None for album ID {item_id}. Defaulting to 1.")
            disc_total_val = 1
        disctotal = typed(disc_total_val, int)
        
        # Extract label from copyright field since Tidal doesn't provide direct label field
        label = extract_label_from_copyright(_copyright)
        
        # Extract additional Tidal metadata
        barcode = getattr(resp, "upc", None)  # UPC/Barcode
        # Normalize release type casing: keep EP uppercase, others title case
        raw_type = getattr(resp, "type", None)
        releasetype = None
        if raw_type:
            if raw_type.upper() == "EP":
                releasetype = "EP"
            else:
                releasetype = raw_type.title()
        
        media_type = "Digital Media"

        explicit_val = getattr(resp, "explicit", False)
        if explicit_val is None:
            logger.debug(f"[from_tidal] 'explicit' was None for album ID {item_id}. Defaulting to False.")
            explicit_val = False
        explicit = typed(explicit_val, bool)

        covers = Covers.from_tidal(vars(resp))
        if covers is None:
            covers = Covers()

        quality_map: dict[str, int] = {
            "LOW": 0,
            "HIGH": 1,
            "LOSSLESS": 2,
            "HI_RES": 3,
        }

        tidal_quality = getattr(resp, "audioQuality", "LOW")
        quality = quality_map.get(tidal_quality, 0)
        
        container = "MP4"
        sampling_rate = None
        bit_depth = None
        if quality >= 2:
            sampling_rate = 44100
            container = "FLAC"
            if quality == 3:
                bit_depth = 24
            else:
                bit_depth = 16

        info = AlbumInfo(
            id=item_id,
            quality=quality,
            container=container,
            label=label,
            explicit=explicit,
            sampling_rate=sampling_rate,
            bit_depth=bit_depth,
            booklets=None,
            streamable=streamable,
        )
        return AlbumMetadata(
            info,
            album,
            albumartist,
            year,
            genre=[],
            covers=covers,
            albumcomposer=None,
            comment=None,
            compilation=None,
            copyright=_copyright,
            date=date,
            description=None,
            disctotal=disctotal,
            encoder=None,
            grouping=None,
            lyrics=None,
            purchase_date=None,
            tracktotal=tracktotal,
            source_platform="tidal",
            source_album_id=item_id,
            source_artist_id=artist_id,
            barcode=barcode,
            releasetype=releasetype,
            media_type=media_type,
        )

    @classmethod
    def from_tidal_playlist_track_resp(cls, resp: dict) -> AlbumMetadata:
        album_resp = resp["album"]
        streamable = resp.get("allowStreaming", True)

        item_id = str(resp["id"])
        album = typed(album_resp.get("title", "Unknown Album"), str)
        tracktotal = 1
        # genre not returned by API
        date = resp.get("streamStartDate")
        if date is not None:
            year = typed(date, str)[:4]
        else:
            year = "Unknown Year"

        _copyright = typed(resp.get("copyright", ""), str)
        artists = typed(resp.get("artists", []), list)
        artist_id = None
        if artists:
            # Get first artist as primary albumartist (MusicBrainz standard)
            albumartist = artists[0]["name"]
            # Get first artist ID for source_artist_id
            artist_id = str(artists[0]["id"])
        else:
            albumartist = typed(
                safe_get(resp, "artist", "name", default="Unknown Albumbartist"), str
            )
            if "artist" in resp and "id" in resp["artist"]:
                artist_id = str(resp["artist"]["id"])

        disctotal = typed(resp.get("volumeNumber", 1), int)
        
        # Extract label from copyright field since Tidal doesn't provide direct label field
        label = extract_label_from_copyright(_copyright)
        
        # Extract additional Tidal metadata
        # Normalize release type casing: keep EP uppercase, others title case
        raw_type = resp.get("type")
        if raw_type:
            if raw_type.upper() == "EP":
                releasetype = "EP"
            else:
                releasetype = raw_type.title()  # Album, Single, etc.
        else:
            releasetype = None
        media_type = "Digital Media"  # MusicBrainz standard for digital/streaming sources

        # non-embedded
        explicit = typed(resp.get("explicit", False), bool)
        covers = Covers.from_tidal(album_resp)
        if covers is None:
            covers = Covers()

        quality_map: dict[str, int] = {
            "LOW": 0,
            "HIGH": 1,
            "LOSSLESS": 2,
            "HI_RES": 3,
        }

        tidal_quality = resp.get("audioQuality", "LOW")
        quality = quality_map[tidal_quality]
        if quality >= 2:
            sampling_rate = 44100
            if quality == 3:
                bit_depth = 24
                container = "FLAC"
            else:
                bit_depth = 16
                container = "FLAC"
        else:
            sampling_rate = None
            bit_depth = None
            container = "MP4"  # AAC for lower qualities

        info = AlbumInfo(
            id=item_id,
            quality=quality,
            container=container,
            label=label,
            explicit=explicit,
            sampling_rate=sampling_rate,
            bit_depth=bit_depth,
            booklets=None,
            streamable=streamable,
        )
        return AlbumMetadata(
            info,
            album,
            albumartist,
            year,
            genre=[],
            covers=covers,
            albumcomposer=None,
            comment=None,
            compilation=None,
            copyright=_copyright,
            date=date,
            description=None,
            disctotal=disctotal,
            encoder=None,
            grouping=None,
            lyrics=None,
            purchase_date=None,
            tracktotal=tracktotal,
            source_platform="tidal",
            source_album_id=str(album_resp["id"]),
            source_artist_id=artist_id,
            releasetype=releasetype,
            media_type=media_type,
        )

    @classmethod
    def from_track_resp(cls, resp: dict, source: str) -> AlbumMetadata:
        if source == "tidal":
            return cls.from_tidal_playlist_track_resp(resp)
        raise Exception("Invalid source")

    @classmethod
    def from_album_resp(cls, resp: dict, source: str) -> AlbumMetadata:
        if source == "tidal":
            return cls.from_tidal(resp)
        raise Exception("Invalid source")
