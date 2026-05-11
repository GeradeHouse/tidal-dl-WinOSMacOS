#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   format.py
@Time    :   2025/08/01
@Author  :   Roo
@Version :   1.0
@Desc    :   Functions for formatting paths and filenames, isolated to break circular dependencies.
"""
import re
import datetime
import logging
import os
from typing import Optional, Union, Dict, Any

import aigpy

# Import singleton and enums needed for path formatting
from .settings import SETTINGS
from .enums import AudioQuality, Type
from .model import Album, Playlist, Track, StreamUrl
from .paths import get_user_download_path


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (format operations) - LAZY LOADED
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


def __fixPath__(name: Any) -> str:
    """Replaces invalid path characters and strips whitespace."""
    if name is None:
        return ""
    if not isinstance(name, str):
        name = str(name)
    if name == "None":
        return ""
    original_name = name
    fixed_name = aigpy.path.replaceLimitChar(name, '-').strip()
    if original_name != fixed_name:
        logger.debug(f"__fixPath__: '{original_name}' -> '{fixed_name}'")
    return fixed_name


def __getYear__(releaseDate: Optional[str]) -> str:
    """Extracts the year from a YYYY-MM-DD date string."""
    if not releaseDate:
        return ''
    if not isinstance(releaseDate, str):
        releaseDate = str(releaseDate)
    parts = releaseDate.split('-')
    return parts[0] if parts else ''


def __getDurationStr__(seconds: Optional[Union[int, float, str]]) -> str:
    """Formats seconds into a MM:SS or H:MM:SS string."""
    if seconds is None:
        return ""
    try:
        seconds_int = int(seconds)
        time_delta = datetime.timedelta(seconds=seconds_int)
        time_string = str(time_delta)
        if time_string.startswith('0:'):
            time_string = time_string[2:]
        return time_string
    except (ValueError, TypeError):
        logger.warning(f"Could not format duration from seconds: {seconds}")
        return ""


def _get_artists_name(artists: Any) -> str:
    """Formats an album or track artist collection as a comma-separated string."""
    if isinstance(artists, list):
        return ", ".join(
            str(getattr(item, "name", ""))
            for item in artists
            if getattr(item, "name", None)
        )
    artist_name = getattr(artists, "name", None)
    return str(artist_name) if artist_name else ""


def _get_content_flag(data: Any, type: Type, short: bool = True, separator: str = " / ") -> str:
    """Formats quality/Atmos/explicit flags without importing the TIDAL API singleton."""
    max_quality = False
    atmos = False
    explicit = False

    if type == Type.Album or type == Type.Track:
        audio_quality = getattr(data, "audioQuality", None)
        if audio_quality == AudioQuality.HI_RES_LOSSLESS.value:
            max_quality = True
        audio_modes = getattr(data, "audioModes", [])
        if type == Type.Album and isinstance(audio_modes, list) and "DOLBY_ATMOS" in audio_modes:
            atmos = True
        if getattr(data, "explicit", False) is True:
            explicit = True

    if not max_quality and not atmos and not explicit:
        return ""

    flag_values: list[str] = []
    if max_quality:
        flag_values.append("M" if short else "Max")
    if atmos:
        flag_values.append("A" if short else "Dolby Atmos")
    if explicit:
        flag_values.append("E" if short else "Explicit")
    return separator.join(flag_values)


def _format_album_relative_path(
    album: Album,
    artistName: str,
    albumArtistName: str,
    flag: str,
) -> str:
    if flag:
        flag = f"[{flag}] "
    else:
        flag = ""

    albumName = __fixPath__(album.title)
    year = __getYear__(getattr(album, 'releaseDate', ''))

    relative_path = SETTINGS.albumFolderFormat or SETTINGS.getDefaultPathFormat(Type.Album)

    relative_path = relative_path.replace(R"{ArtistName}", __fixPath__(artistName))
    relative_path = relative_path.replace(R"{AlbumArtistName}", __fixPath__(albumArtistName))
    relative_path = relative_path.replace(R"{Flag}", flag)
    relative_path = relative_path.replace(R"{AlbumID}", str(getattr(album, 'id', '')))
    relative_path = relative_path.replace(R"{AlbumYear}", year)
    relative_path = relative_path.replace(R"{AlbumTitle}", albumName)
    audio_quality_name = getattr(getattr(album, 'audioQuality', None), 'name', '')
    relative_path = relative_path.replace(R"{AudioQuality}", audio_quality_name)
    relative_path = relative_path.replace(R"{DurationSeconds}", str(getattr(album, 'duration', 0)))
    relative_path = relative_path.replace(R"{Duration}", __getDurationStr__(getattr(album, 'duration', 0)))
    relative_path = relative_path.replace(R"{NumberOfTracks}", str(getattr(album, 'numberOfTracks', 0)))
    relative_path = relative_path.replace(R"{NumberOfVideos}", str(getattr(album, 'numberOfVideos', 0)))
    relative_path = relative_path.replace(R"{NumberOfVolumes}", str(getattr(album, 'numberOfVolumes', 0)))
    relative_path = relative_path.replace(R"{ReleaseDate}", str(getattr(album, 'releaseDate', '')))
    record_type_name = getattr(getattr(album, 'type', None), 'name', '')
    relative_path = relative_path.replace(R"{RecordType}", record_type_name)
    relative_path = relative_path.replace(R"{None}", "")

    if not artistName.strip():
        relative_path = relative_path.lstrip('/\\')
    if year == '' and '[{AlbumYear}]' in relative_path:
        relative_path = relative_path.replace(' [{AlbumYear}]', '')

    return relative_path.strip()


def __getExtension__(stream: StreamUrl) -> str:
    """Determines the file extension based on stream URL and codec."""
    stream_url = str(getattr(stream, 'url', '') or '') if stream else ''
    codec_lower = str(getattr(stream, 'codec', '') or '').lower() if stream else ''
    manifest_mime_lower = str(getattr(stream, 'manifestMimeType', '') or '').lower() if stream else ''
    sound_quality = str(getattr(stream, 'soundQuality', '') or '').upper() if stream else ''

    if stream_url:
        url_lower = stream_url.lower()
        if '.flac' in url_lower:
            return '.flac'
        if '.mp4' in url_lower:
            if 'ac4' in codec_lower or 'mha1' in codec_lower:
                return '.mp4'
            elif 'flac' in codec_lower:
                return '.mp4'
            return '.m4a'

    if 'flac' in codec_lower:
        if 'dash+xml' in manifest_mime_lower or 'vnd.tidal.bt' in manifest_mime_lower:
            return '.mp4'
        return '.flac'

    if sound_quality in {'LOSSLESS', 'HI_RES', 'HI_RES_LOSSLESS'}:
        if 'dash+xml' in manifest_mime_lower or 'vnd.tidal.bt' in manifest_mime_lower:
            return '.mp4'
        return '.flac'

    return '.m4a'


def _normalize_audio_type_folder(folder_name: Optional[str]) -> str:
    """Normalizes audio folder names to stable, safe lowercase directory names."""
    normalized = __fixPath__(folder_name or "").strip().strip(".").lower()
    return normalized or "unknown"


def getAudioTypeFolder(
    stream: Optional[StreamUrl] = None,
    requested_audio_type: Optional[Union[str, AudioQuality]] = None,
    extension: Optional[str] = None,
) -> str:
    """Determines the download subfolder name for an audio file type."""
    requested_value = ""
    if isinstance(requested_audio_type, AudioQuality):
        requested_value = requested_audio_type.name.lower()
    elif requested_audio_type is not None:
        requested_value = str(requested_audio_type).strip().lower()

    requested_value = requested_value.replace("-", "_").replace(" ", "_")
    if "mp3" in requested_value:
        return "mp3"
    if "aac" in requested_value or requested_value == AudioQuality.LOW.name.lower():
        return "m4a"
    if (
        "flac" in requested_value
        or "lossless" in requested_value
        or requested_value == AudioQuality.HIGH.name.lower()
        or requested_value == AudioQuality.HI_RES_LOSSLESS.name.lower()
    ):
        return "flac"
    if requested_value in {"m4a", "mp4"}:
        return requested_value

    explicit_extension = (extension or "").strip().lower()
    if explicit_extension and not explicit_extension.startswith("."):
        explicit_extension = f".{explicit_extension}"

    if explicit_extension in {".flac", ".mp3", ".m4a", ".mp4"}:
        return explicit_extension.lstrip(".")

    stream_url = str(getattr(stream, "url", "") or "") if stream else ""
    codec_lower = str(getattr(stream, "codec", "") or "").lower() if stream else ""
    sound_quality = str(getattr(stream, "soundQuality", "") or "").upper() if stream else ""

    if ".flac" in stream_url.lower() or "flac" in codec_lower:
        return "flac"
    if sound_quality in {"HIGH", "LOSSLESS", "HI_RES", "HI_RES_LOSSLESS"}:
        return "flac"

    inferred_extension = __getExtension__(stream) if stream else ".m4a"
    if inferred_extension in {".flac", ".m4a", ".mp4"}:
        return inferred_extension.lstrip(".")
    return _normalize_audio_type_folder(inferred_extension)


def getAlbumPath(album: Album, artistName: str, albumArtistName: str, flag: str) -> Optional[str]:
    """Generates the directory path for an album based on settings."""
    if not album or not hasattr(album, 'title'):
        logger.error("Invalid album object passed to getAlbumPath.")
        return None

    relative_path = _format_album_relative_path(album, artistName, albumArtistName, flag)
    base_path = get_user_download_path(SETTINGS.downloadPath)
    full_path = os.path.join(base_path, relative_path.strip())

    logger.debug(f"getAlbumPath result: artist='{artistName}', path='{full_path}'")

    return full_path


def getPlaylistPath(playlist: Union[Playlist, Dict[str, Any]], base_path: Optional[str] = None) -> Optional[str]:
    """Generates the directory path for a playlist based on settings."""
    playlistName = "Unknown Playlist"
    playlistUUID = "UnknownUUID"

    if isinstance(playlist, dict):
        spotify_data = playlist.get('data', {})
        playlistName = __fixPath__(spotify_data.get('name', 'Unknown Playlist'))
        playlistUUID = str(spotify_data.get('id', 'UnknownUUID'))
    elif hasattr(playlist, 'title') and hasattr(playlist, 'uuid'):
        playlistName = __fixPath__(playlist.title)
        playlistUUID = str(playlist.uuid)
    else:
        logger.warning(f"getPlaylistPath: Received unexpected playlist type: {type(playlist)}")
        return None

    relative_path = SETTINGS.playlistFolderFormat or SETTINGS.getDefaultPathFormat(Type.Playlist)
    relative_path = relative_path.replace(R"{PlaylistUUID}", playlistUUID)
    relative_path = relative_path.replace(R"{PlaylistName}", playlistName)

    resolved_base_path = base_path or get_user_download_path(SETTINGS.downloadPath)
    full_path = os.path.join(resolved_base_path, relative_path.strip())

    return full_path


def _resolve_playlist_path_from_context(
    playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
    base_path: str,
) -> Optional[str]:
    """Resolves an absolute playlist folder path from a mixed playlist context."""
    if not playlist_context:
        return None

    if isinstance(playlist_context, dict):
        context_type = playlist_context.get("type")
        context_data = playlist_context.get("data")

        if context_type == "single":
            return os.path.join(base_path, "Tracks")

        if context_type == "spotify":
            return getPlaylistPath(playlist_context, base_path)

        if isinstance(context_data, Playlist):
            return getPlaylistPath(context_data, base_path)

        if isinstance(context_data, dict):
            playlist_name = __fixPath__(
                context_data.get("name", context_data.get("title", "Unknown Playlist"))
            )
            playlist_uuid = str(
                context_data.get("id", context_data.get("uuid", "UnknownUUID"))
            )
            relative_path = (
                SETTINGS.playlistFolderFormat
                or SETTINGS.getDefaultPathFormat(Type.Playlist)
            )
            relative_path = relative_path.replace(R"{PlaylistUUID}", playlist_uuid)
            relative_path = relative_path.replace(R"{PlaylistName}", playlist_name)
            return os.path.join(base_path, relative_path.strip())

        return None

    if isinstance(playlist_context, Playlist):
        return getPlaylistPath(playlist_context, base_path)

    return None


def getTrackPath(track: Track, stream: Optional[StreamUrl], artist: str, artists: str, album: Optional[Album] = None, playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None, audio_type_folder: Optional[str] = None) -> str:
    """Generates the full file path for a track based on context and settings."""
    logger.debug(f"getTrackPath: artist='{artist}', artists='{artists}', track.title='{track.title if track else 'None'}'")
    if not track or not hasattr(track, 'title') or not stream:
        logger.error("Invalid track or stream object passed to getTrackPath.")
        return "Invalid_Track.m4a"

    if album is None and isinstance(playlist_context, Album):
        album = playlist_context

    # Determine track number string
    raw_number_val = getattr(track, 'trackNumberOnPlaylist', getattr(track, 'trackNumber', None)) if playlist_context and SETTINGS.usePlaylistFolder else getattr(track, 'trackNumber', None)
    number_for_format = str(int(raw_number_val)).rjust(2, '0') if raw_number_val and isinstance(raw_number_val, (int, str)) and str(raw_number_val).isdigit() and int(raw_number_val) > 0 else ""

    # Determine Album object for filename placeholders
    album_obj_for_filename = None
    if playlist_context and SETTINGS.usePlaylistFolder:
        album_obj_for_filename = getattr(track, "album", None)
    elif album:
        album_obj_for_filename = album
    else:
        album_obj_for_filename = getattr(track, "album", None)

    # Extract metadata for filename
    title = __fixPath__(track.title)
    version_attr = getattr(track, 'version', None)
    if version_attr:
        title += f' ({__fixPath__(version_attr)})'
    explicit = "(Explicit)" if getattr(track, 'explicit', False) else ''

    albumName = ''
    year = ''
    if album_obj_for_filename and isinstance(album_obj_for_filename, Album):
        albumName = __fixPath__(getattr(album_obj_for_filename, 'title', ''))
        year = __getYear__(getattr(album_obj_for_filename, 'releaseDate', ''))

    extension = __getExtension__(stream)

    filename_format = SETTINGS.trackFileFormat or SETTINGS.getDefaultPathFormat(Type.Track)

    # Replace all placeholders in the filename format
    filename_format = filename_format.replace(R"{TrackNumber}", number_for_format)
    filename_format = filename_format.replace(R"{ArtistName}", __fixPath__(artist))
    filename_format = filename_format.replace(R"{ArtistsName}", __fixPath__(artists))
    filename_format = filename_format.replace(R"{TrackTitle}", title)
    filename_format = filename_format.replace(R"{ExplicitFlag}", explicit)
    filename_format = filename_format.replace(R"{AlbumYear}", year)
    filename_format = filename_format.replace(R"{AlbumTitle}", albumName)
    audio_quality_name = getattr(getattr(track, 'audioQuality', None), 'name', '')
    filename_format = filename_format.replace(R"{AudioQuality}", audio_quality_name)
    filename_format = filename_format.replace(R"{DurationSeconds}", str(getattr(track, 'duration', 0)))
    filename_format = filename_format.replace(R"{Duration}", __getDurationStr__(getattr(track, 'duration', 0)))
    filename_format = filename_format.replace(R"{TrackID}", str(getattr(track, 'id', '')))
    filename_format = filename_format.replace(R"{None}", "")

    # Clean up filename string
    filename_format = ' '.join(filename_format.split())
    filename_format = re.sub(r"^\s*[-._]\s*", "", filename_format)
    filename_format = re.sub(r"\s*[-._]\s*$", "", filename_format)
    filename_format = re.sub(r"\s*([-._])\s*(\1\s*)+", r" \1 ", filename_format)
    filename_format = re.sub(r"\s*([-._])\s*", r" \1 ", filename_format)

    # Get the resolved, absolute base download path and group downloads by audio type.
    download_root = get_user_download_path(SETTINGS.downloadPath)
    audio_folder = _normalize_audio_type_folder(audio_type_folder or getAudioTypeFolder(stream))
    base_path = os.path.join(download_root, audio_folder)

    # Determine the subdirectory structure
    sub_folder = ""
    if album:
        album_artist_name = getattr(getattr(album, "artist", None), "name", "") or artists or artist
        album_artists_name = _get_artists_name(getattr(album, "artists", None))
        artist_name_for_album = album_artists_name or artists or artist
        sub_folder = _format_album_relative_path(
            album,
            artist_name_for_album,
            album_artist_name,
            _get_content_flag(album, Type.Album, True, ""),
        )
    elif playlist_context:
        resolved_playlist_path = _resolve_playlist_path_from_context(
            playlist_context, base_path
        )
        if resolved_playlist_path:
            normalized_playlist_path = os.path.normpath(resolved_playlist_path)
            normalized_base_path = os.path.normpath(base_path)
            try:
                relative_sub_folder = os.path.relpath(
                    normalized_playlist_path, normalized_base_path
                )
                if relative_sub_folder.startswith(".."):
                    sub_folder = os.path.basename(normalized_playlist_path)
                else:
                    sub_folder = relative_sub_folder
            except ValueError:
                # Different drives on Windows: fall back to last folder segment.
                sub_folder = os.path.basename(normalized_playlist_path)
        else:
            sub_folder = os.path.join("Artists", __fixPath__(artist))
    else:
        sub_folder = os.path.join("Artists", __fixPath__(artist))

    # Construct the full, absolute path
    filename_with_ext = f"{filename_format.strip()}{extension}"
    final_path = os.path.join(base_path, sub_folder, filename_with_ext)

    logger.debug(f"getTrackPath final path: '{final_path}'")
    return final_path
