# --- START OF FILE tagger.py ---

import logging
import os
from enum import Enum
from typing import List, Union

import aiofiles
from mutagen import id3
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,  # type: ignore
    ID3,
    ID3NoHeaderError,
)
from mutagen.mp4 import MP4, MP4Cover

from .track import TrackMetadata

logger = logging.getLogger("streamrip")

# Set up GUI logging with DEBUG level for this module (metadata operations)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger("streamrip", logging.DEBUG)

FLAC_MAX_BLOCKSIZE = 16777215  # 16.7 MB

MP4_KEYS = (
    "\xa9nam",
    "\xa9ART",
    "----:com.apple.iTunes:ARTISTS",
    "\xa9alb",
    r"aART",
    "\xa9wrt",  # composer
    "----:com.apple.iTunes:AUTHOR",  # author/songwriter
    "\xa9day",
    "\xa9cmt",
    "desc",
    "purd",
    "\xa9grp",
    "----:com.apple.iTunes:INITIALKEY", # key
    "\xa9gen",
    "\xa9lyr",
    "\xa9too",
    "cprt",
    "cpil",
    "trkn",
    "disk",
    None,
    None,
    None,
    "----:com.apple.iTunes:ISRC",
    "©pub",  # label/publisher
    "tmpo",  # bpm
    "----:com.apple.iTunes:BARCODE",  # was UPC
    "----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN",  # was GAIN
    "----:com.apple.iTunes:REPLAYGAIN_ALBUM_GAIN",  # new
    "----:com.apple.iTunes:RELEASETYPE",  # was RECORD_TYPE
    None,  # source_track_id (handled dynamically)
    None,  # source_album_id (handled dynamically)
    None,  # source_artist_id (handled dynamically)
    None,  # spotify_key (handled as freeform)
    None,  # tidal_key (handled as freeform)
    None,  # key_source (handled as freeform)
    None,  # spotify_track_id (handled as freeform)
    None,  # tidal_dl_id (handled as freeform)
    "----:com.apple.iTunes:TRACK_ARTIST_CREDIT",
    "----:com.apple.iTunes:ALBUM_ARTIST_CREDIT",
    "----:com.apple.iTunes:ORIGINALDATE",  # was ORIGINAL_RELEASE_DATE
    "----:com.apple.iTunes:MEDIA_TYPE",
    "----:com.apple.iTunes:RYM_DESCRIPTORS",
)

MP3_KEYS = (
    id3.TIT2,  # type: ignore
    id3.TPE1,  # type: ignore
    None,  # artists (handled as TXXX)
    id3.TALB,  # type: ignore
    id3.TPE2,  # type: ignore
    id3.TCOM,  # type: ignore
    id3.TEXT,  # author/lyricist/songwriter
    id3.TYER,  # type: ignore
    id3.COMM,  # type: ignore
    id3.TIT1,  # description (content group)
    None,  # purchase_date (handled as TXXX)
    id3.GP1,  # type: ignore
    id3.TKEY, # key
    id3.TCON,  # type: ignore
    id3.USLT,  # type: ignore
    id3.TEN,  # type: ignore
    id3.TCOP,  # type: ignore
    id3.TCMP,  # type: ignore
    id3.TRCK,  # type: ignore
    id3.TPOS,  # type: ignore
    None,
    None,
    None,
    id3.TSRC,
    id3.TPUB,  # label/publisher 
    id3.TBPM,  # bpm
    None,  # barcode (handled as TXXX)
    None,  # replaygain_track_gain (handled as TXXX)
    None,  # replaygain_album_gain (handled as TXXX)
    None,  # releasetype (handled as TXXX)
    None,  # source_track_id (handled dynamically)
    None,  # source_album_id (handled dynamically) 
    None,  # source_artist_id (handled dynamically)
    None,  # spotify_key (handled as TXXX)
    None,  # tidal_key (handled as TXXX)
    None,  # key_source (handled as TXXX)
    None,  # spotify_track_id (handled as TXXX)
    None,  # tidal_dl_id (handled as TXXX)
    None,  # track_artist_credit (handled as TXXX)
    None,  # album_artist_credit (handled as TXXX)
    id3.TDOR,  # originaldate
    None,  # media_type (handled as TXXX)
    None,  # rym_descriptors (handled as TXXX)
)

METADATA_TYPES = (
    "title",
    "artist",
    "artists",
    "album",
    "albumartist",
    "composer",
    "author",
    "year",
    "comment",
    "description",
    "purchase_date",
    "grouping",
    "key",
    "genre",
    "lyrics",
    "encoder",
    "copyright",
    "compilation",
    "tracknumber",
    "discnumber",
    "tracktotal",
    "disctotal", 
    "date",
    "isrc",
    "label",
    "bpm",
    "barcode",
    "replaygain_track_gain",
    "replaygain_album_gain", 
    "releasetype",
    "source_track_id",
    "source_album_id", 
    "source_artist_id",
    "spotify_key",
    "tidal_key",
    "key_source",
    "spotify_track_id",
    "tidal_dl_id",
    "track_artist_credit",
    "album_artist_credit",
    "originaldate",
    "media_type",
    "rym_descriptors",
)


FLAC_KEY = {v: v.upper() for v in METADATA_TYPES}
MP4_KEY = dict(zip(METADATA_TYPES, MP4_KEYS))
MP3_KEY = dict(zip(METADATA_TYPES, MP3_KEYS))


class Container(Enum):
    FLAC = 1
    AAC = 2
    MP3 = 3

    def get_mutagen_class(self, path: str):
        if self == Container.FLAC:
            return FLAC(path)
        elif self == Container.AAC:
            return MP4(path)
        elif self == Container.MP3:
            try:
                return ID3(path)
            except ID3NoHeaderError:
                return ID3()
        # unreachable
        return {}

    def get_tag_pairs(self, meta) -> list[tuple]:
        if self == Container.FLAC:
            return self._tag_flac(meta)
        elif self == Container.MP3:
            return self._tag_mp3(meta)
        elif self == Container.AAC:
            return self._tag_mp4(meta)
        # unreachable
        return []

    def _tag_flac(self, meta: TrackMetadata) -> list[tuple]:
        out = []
        for k, v in FLAC_KEY.items():
            tag = self._attr_from_meta(meta, k)
            if tag:
                if k in {
                    "tracknumber",
                    "discnumber",
                    "tracktotal",
                    "disctotal",
                }:
                    if isinstance(tag, str):
                        tag = f"{int(tag):02}"
                    else:
                        logger.warning(f"Unexpected type for tag '{k}': {type(tag)}. Expected str.")
                        continue
                elif k in ["source_track_id", "source_album_id", "source_artist_id"]:
                    # Format source tags dynamically based on platform
                    if meta.source_platform and tag:
                        formatted_key = f"{meta.source_platform}_{k.replace('source_', '')}".upper()
                        out.append((formatted_key, str(tag)))
                    continue
                elif k == "key":
                    # Mixed In Key uses the FLAC KEY field for its own Base64 metadata.
                    out.append(("INITIALKEY", str(tag)))
                    continue
                elif k in {
                    "spotify_key",
                    "tidal_key",
                    "key_source",
                    "spotify_track_id",
                    "tidal_dl_id",
                }:
                    out.append((k.upper(), str(tag)))
                    continue
                elif k == "artists":
                    # Handle multi-value artists for FLAC - return as list for mutagen
                    if isinstance(tag, list):
                        out.append((v, tag))  # Let mutagen handle the list natively
                    else:
                        out.append((v, str(tag)))
                    continue
                elif k == "genre":
                    # Handle multi-value genres for FLAC - return as list for mutagen
                    if isinstance(tag, list):
                        out.append((v, tag))  # Let mutagen handle the list natively
                    else:
                        out.append((v, str(tag)))
                    continue
                elif k == "rym_descriptors":
                    # Handle multi-value RYM descriptors for FLAC - return as list for mutagen
                    if isinstance(tag, list):
                        out.append((v, tag))  # Let mutagen handle the list natively
                    else:
                        out.append((v, str(tag)))
                    continue
                
                out.append((v, str(tag)))
        return out

    def _tag_mp3(self, meta: TrackMetadata):
        out = []
        for k, v in MP3_KEY.items():
            if k == "tracknumber":
                text = f"{meta.tracknumber}/{meta.album.tracktotal}"
            elif k == "discnumber":
                text = f"{meta.discnumber}/{meta.album.disctotal}"
            elif k in ["source_track_id", "source_album_id", "source_artist_id"]:
                # Format source tags dynamically based on platform
                if meta.source_platform and self._attr_from_meta(meta, k):
                    formatted_key = f"TXXX:{meta.source_platform}_{k.replace('source_', '')}".upper()
                    text = self._attr_from_meta(meta, k)
                    if text is not None:
                        out.append((formatted_key, text))
                continue
            elif k == "artists":
                # Handle artists as TXXX with comma-separated values
                artists = self._attr_from_meta(meta, k)
                if artists is not None:
                    text = ", ".join(artists) if isinstance(artists, list) else str(artists)
                    out.append((f"TXXX:{k.upper()}", text))
                continue
            elif k in [
                "barcode",
                "replaygain_track_gain",
                "replaygain_album_gain",
                "releasetype",
                "track_artist_credit",
                "album_artist_credit",
                "media_type",
                "purchase_date",
                "originaldate",
                "rym_descriptors",
                "spotify_key",
                "tidal_key",
                "key_source",
                "spotify_track_id",
                "tidal_dl_id",
            ]:
                # Handle as TXXX custom tags
                text = self._attr_from_meta(meta, k)
                if text is not None:
                    out.append(
                        (
                            f"TXXX:{k.upper()}",
                            str(text),
                        )
                    )
                continue
            else:
                text = self._attr_from_meta(meta, k)

            if text is not None and v is not None:
                out.append((v.__name__, v(encoding=3, text=text)))
        return out

    def _tag_mp4(self, meta: TrackMetadata):
        out = []
        for k, v in MP4_KEY.items():
            if k == "tracknumber":
                text = [(meta.tracknumber, meta.album.tracktotal)]
            elif k == "discnumber":
                text = [(meta.discnumber, meta.album.disctotal)]
            elif k == "bpm":
                # BPM (tmpo) must be an integer for MP4 tags
                bpm_value = self._attr_from_meta(meta, k)
                if bpm_value is not None and isinstance(bpm_value, str):
                    try:
                        text = [int(bpm_value)]
                    except (ValueError, TypeError):
                        text = None
                else:
                    text = None
            elif k == "isrc" and meta.isrc is not None:
                # because ISRC is an mp4 freeform value (not supported natively)
                # we have to pass in the actual bytes to mutagen
                # See mutagen.MP4Tags.__render_freeform
                text = meta.isrc.encode("utf-8")
            elif k in ["source_track_id", "source_album_id", "source_artist_id"]:
                # Format source tags dynamically based on platform
                if meta.source_platform and self._attr_from_meta(meta, k):
                    formatted_key = f"----:com.apple.iTunes:{meta.source_platform.upper()}_{k.replace('source_', '').upper()}"
                    text = self._attr_from_meta(meta, k)
                    if text is not None:
                        text = str(text).encode("utf-8")  # MP4 freeform tags need bytes
                        out.append((formatted_key, text))
                continue
            elif k == "artists" and v is not None:
                # Handle artists as MP4 freeform with byte encoding
                artists = self._attr_from_meta(meta, k)
                if artists is not None:
                    text = ", ".join(artists) if isinstance(artists, list) else str(artists)
                    text = text.encode("utf-8")
                    out.append((v, text))
                continue
            elif k in [
                "barcode",
                "replaygain_track_gain",
                "replaygain_album_gain",
                "releasetype",
                "track_artist_credit",
                "album_artist_credit",
                "originaldate",
                "media_type",
                "rym_descriptors",
                "spotify_key",
                "tidal_key",
                "key_source",
                "spotify_track_id",
                "tidal_dl_id",
            ]:
                # Handle custom MP4 freeform tags that need bytes encoding
                text = self._attr_from_meta(meta, k)
                if text is not None:
                    freeform_key = (
                        v
                        or "----:com.apple.iTunes:"
                        f"{k.upper()}"
                    )
                    text = str(text).encode("utf-8")
                    out.append(
                        (freeform_key, text)
                    )
                continue
            else:
                text = self._attr_from_meta(meta, k)

            if v is not None and text is not None:
                out.append((v, text))
        return out

    def _attr_from_meta(self, meta: TrackMetadata, attr: str) -> Union[str, List[str], None]:
        # TODO: verify this works
        in_trackmetadata = {
            "title",
            "album",
            "artist",
            "artists",
            "tracknumber",
            "discnumber",
            "composer",
            "author",
            "isrc",
            "lyrics",
            "key",
            # Track-specific source metadata
            "source_platform",
            "source_track_id",
            "source_album_id", 
            "source_artist_id",
            "spotify_key",
            "tidal_key",
            "key_source",
            "spotify_track_id",
            "tidal_dl_id",
            # Track-specific additional metadata
            "bpm",
            "replaygain_track_gain",
            "track_artist_credit",
            "media_type",
        }
        if attr in in_trackmetadata:
            if attr == "album":
                return meta.album.album
            elif attr == "artist":
                # Return primary artist only (string)
                return getattr(meta, attr)
            elif attr == "artists":
                # Return all artists as list for format handlers to process
                return getattr(meta, attr)
            val = getattr(meta, attr)
            if val is None:
                return None
            return str(val)
        else:
            if attr == "genre":
                # Return genres as list for FLAC to support multiple genre tags
                return meta.album.genre
            elif attr == "rym_descriptors":
                # Return RYM descriptors as list for FLAC to support multiple descriptor tags
                return meta.album.rym_descriptors
            elif attr == "copyright":
                return meta.album.get_copyright()
            elif attr == "label":
                return meta.album.info.label
            val = getattr(meta.album, attr)
            if val is None:
                return None
            return str(val)

    def tag_audio(self, audio, tags: list[tuple]):
        for k, v in tags:
            try:
                if k.startswith("TXXX:"):
                    # Handle TXXX frames for custom tags
                    description = k.split(":", 1)[1]
                    txxx = id3.TXXX(encoding=3, desc=description, text=v)
                    audio.add(txxx)
                else:
                    # Mutagen requires every MP4 freeform atom value to be bytes.
                    # Keep this final guard here so newly added custom fields cannot
                    # accidentally reach MP4Tags as str and fail during rendering.
                    if k.startswith("----:"):
                        if isinstance(v, str):
                            v = v.encode("utf-8")
                        elif isinstance(v, list):
                            v = [
                                item.encode("utf-8") if isinstance(item, str) else item
                                for item in v
                            ]
                    # Handle regular tags
                    audio[k] = v
            except Exception:
                logger.error(
                    "Metadata assignment failed | key=%r value_type=%s item_types=%s",
                    k,
                    type(v).__name__,
                    [type(item).__name__ for item in v] if isinstance(v, list) else "-",
                    exc_info=True,
                )
                raise

    async def embed_cover(self, audio, cover_path):
        if self == Container.FLAC:
            size = os.path.getsize(cover_path)
            if size > FLAC_MAX_BLOCKSIZE:
                raise Exception("Cover art too big for FLAC")
            cover = Picture()
            cover.type = 3
            cover.mime = "image/jpeg"
            async with aiofiles.open(cover_path, "rb") as img:
                cover.data = await img.read()
            audio.add_picture(cover)
        elif self == Container.MP3:
            cover = APIC()
            cover.type = 3
            cover.mime = "image/jpeg"
            async with aiofiles.open(cover_path, "rb") as img:
                cover.data = await img.read()
            audio.add(cover)
        elif self == Container.AAC:
            async with aiofiles.open(cover_path, "rb") as img:
                cover = MP4Cover(await img.read(), imageformat=MP4Cover.FORMAT_JPEG)
            audio["covr"] = [cover]

    def save_audio(self, audio, path):
        if self == Container.FLAC:
            audio.save()
        elif self == Container.AAC:
            audio.save()
        elif self == Container.MP3:
            audio.save(path, v2_version=3)


async def tag_file(path: str, meta: TrackMetadata, cover_path: str | None):
    """Async function to tag audio files with metadata and cover art."""
    ext = path.split(".")[-1].lower()
    if ext == "flac":
        container = Container.FLAC
    elif ext in ("m4a", "mp4"):
        container = Container.AAC
    elif ext == "mp3":
        container = Container.MP3
    else:
        raise Exception(f"Invalid extension {ext}")

    audio = container.get_mutagen_class(path)
    tags = container.get_tag_pairs(meta)
    # Preserve the actual audio provider ID even for Spotify-sourced downloads.
    # source_track_id is the Spotify ID in that case, not the TIDAL recording ID.
    if meta.info.id:
        if container == Container.FLAC:
            tags.append(("TIDAL_TRACK_ID", str(meta.info.id)))
        elif container == Container.MP3:
            tags.append(("TXXX:TIDAL_TRACK_ID", str(meta.info.id)))
        else:
            tags.append(("----:com.apple.iTunes:TIDAL_TRACK_ID", str(meta.info.id).encode("utf-8")))
    logger.debug("Tagging with %s", tags)
    container.tag_audio(audio, tags)
    if cover_path is not None:
        await container.embed_cover(audio, cover_path)
    container.save_audio(audio, path)
    return None  # Explicitly return None to satisfy type checker

# --- END OF FILE tagger.py ---
