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
from .enums import Type
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


def getAlbumPath(album: Album, artistName: str, albumArtistName: str, flag: str) -> Optional[str]:
    """Generates the directory path for an album based on settings."""
    if not album or not hasattr(album, 'title'):
        logger.error("Invalid album object passed to getAlbumPath.")
        return None

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

    # Post-process to avoid leading '/' if artist empty and trailing '[]' if year empty
    if not artistName.strip():
        relative_path = relative_path.lstrip('/')
    if year == '' and '[{AlbumYear}]' in relative_path:
        relative_path = relative_path.replace(' [{AlbumYear}]', '')

    base_path = get_user_download_path(SETTINGS.downloadPath)
    full_path = os.path.join(base_path, relative_path.strip())

    logger.debug(f"getAlbumPath result: artist='{artistName}', year='{year}', path='{full_path}'")

    return full_path


def getPlaylistPath(playlist: Union[Playlist, Dict[str, Any]]) -> Optional[str]:
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

    base_path = get_user_download_path(SETTINGS.downloadPath)
    full_path = os.path.join(base_path, relative_path.strip())

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
            return getPlaylistPath(playlist_context)

        if isinstance(context_data, Playlist):
            return getPlaylistPath(context_data)

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
        return getPlaylistPath(playlist_context)

    return None


def getTrackPath(track: Track, stream: Optional[StreamUrl], artist: str, artists: str, album: Optional[Album] = None, playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None) -> str:
    """Generates the full file path for a track based on context and settings."""
    logger.debug(f"getTrackPath: artist='{artist}', artists='{artists}', track.title='{track.title if track else 'None'}'")
    if not track or not hasattr(track, 'title') or not stream:
        logger.error("Invalid track or stream object passed to getTrackPath.")
        return "Invalid_Track.m4a"

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

    # Get the resolved, absolute base download path
    base_path = get_user_download_path(SETTINGS.downloadPath)

    # Determine the subdirectory structure
    sub_folder = ""
    if album:
        sub_folder = os.path.join("Albums", __fixPath__(album.title))
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
