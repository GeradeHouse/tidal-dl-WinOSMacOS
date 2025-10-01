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
from typing import Optional, Union, Dict, Any

import aigpy

# Import singleton and enums needed for path formatting
from .settings import SETTINGS
from .enums import Type
from .model import Album, Playlist, Track, StreamUrl


logger = logging.getLogger(__name__)


def __fixPath__(name: Any) -> str:
    """Replaces invalid path characters and strips whitespace."""
    if name is None:
        return ""
    if not isinstance(name, str):
        name = str(name)
    if name == "None":
        return ""
    return aigpy.path.replaceLimitChar(name, '-').strip()


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
    if stream and stream.url:
        url_lower = stream.url.lower()
        if '.flac' in url_lower:
            return '.flac'
        if '.mp4' in url_lower:
            codec_lower = getattr(stream, 'codec', '').lower()
            if 'ac4' in codec_lower or 'mha1' in codec_lower:
                return '.mp4'
            elif 'flac' in codec_lower:
                return '.mp4'
            return '.m4a'
    return '.m4a'


def getAlbumPath(album: Album, artistName: str, albumArtistName: str, flag: str) -> Optional[str]:
    """Generates the directory path for an album based on settings."""
    if not album or not hasattr(album, 'title'):
        logging.error("Invalid album object passed to getAlbumPath.")
        return None

    if flag:
        flag = f"[{flag}] "
    else:
        flag = ""

    albumName = __fixPath__(album.title)
    year = __getYear__(getattr(album, 'releaseDate', ''))
    
    retpath = SETTINGS.albumFolderFormat or SETTINGS.getDefaultPathFormat(Type.Album)

    retpath = retpath.replace(R"{ArtistName}", artistName)
    retpath = retpath.replace(R"{AlbumArtistName}", albumArtistName)
    retpath = retpath.replace(R"{Flag}", flag)
    retpath = retpath.replace(R"{AlbumID}", str(getattr(album, 'id', '')))
    retpath = retpath.replace(R"{AlbumYear}", year)
    retpath = retpath.replace(R"{AlbumTitle}", albumName)
    audio_quality_name = getattr(getattr(album, 'audioQuality', None), 'name', '')
    retpath = retpath.replace(R"{AudioQuality}", audio_quality_name)
    retpath = retpath.replace(R"{DurationSeconds}", str(getattr(album, 'duration', 0)))
    retpath = retpath.replace(R"{Duration}", __getDurationStr__(getattr(album, 'duration', 0)))
    retpath = retpath.replace(R"{NumberOfTracks}", str(getattr(album, 'numberOfTracks', 0)))
    retpath = retpath.replace(R"{NumberOfVideos}", str(getattr(album, 'numberOfVideos', 0)))
    retpath = retpath.replace(R"{NumberOfVolumes}", str(getattr(album, 'numberOfVolumes', 0)))
    retpath = retpath.replace(R"{ReleaseDate}", str(getattr(album, 'releaseDate', '')))
    record_type_name = getattr(getattr(album, 'type', None), 'name', '')
    retpath = retpath.replace(R"{RecordType}", record_type_name)
    retpath = retpath.replace(R"{None}", "")
    
    return retpath.strip()


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
        logging.warning(f"getPlaylistPath: Received unexpected playlist type: {type(playlist)}")
        return None

    retpath = SETTINGS.playlistFolderFormat or SETTINGS.getDefaultPathFormat(Type.Playlist)
    retpath = retpath.replace(R"{PlaylistUUID}", playlistUUID)
    retpath = retpath.replace(R"{PlaylistName}", playlistName)
    
    return retpath.strip()


def getTrackPath(track: Track, stream: Optional[StreamUrl], artist: str, artists: str, album: Optional[Album] = None, playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None) -> str:
    """Generates the full file path for a track based on context and settings."""
    if not track or not hasattr(track, 'title') or not stream:
        logging.error("Invalid track or stream object passed to getTrackPath.")
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
    
    retpath = SETTINGS.trackFileFormat or SETTINGS.getDefaultPathFormat(Type.Track)

    # Replace all placeholders
    retpath = retpath.replace(R"{TrackNumber}", number_for_format)
    retpath = retpath.replace(R"{ArtistName}", artist)
    retpath = retpath.replace(R"{ArtistsName}", artists)
    retpath = retpath.replace(R"{TrackTitle}", title)
    retpath = retpath.replace(R"{ExplicitFlag}", explicit)
    retpath = retpath.replace(R"{AlbumYear}", year)
    retpath = retpath.replace(R"{AlbumTitle}", albumName)
    audio_quality_name = getattr(getattr(track, 'audioQuality', None), 'name', '')
    retpath = retpath.replace(R"{AudioQuality}", audio_quality_name)
    retpath = retpath.replace(R"{DurationSeconds}", str(getattr(track, 'duration', 0)))
    retpath = retpath.replace(R"{Duration}", __getDurationStr__(getattr(track, 'duration', 0)))
    retpath = retpath.replace(R"{TrackID}", str(getattr(track, 'id', '')))
    retpath = retpath.replace(R"{None}", "")

    # Clean up path string
    retpath = ' '.join(retpath.split())
    retpath = re.sub(r"^\s*[-._]\s*", "", retpath)
    retpath = re.sub(r"\s*[-._]\s*$", "", retpath)
    retpath = re.sub(r"\s*([-._])\s*(\1\s*)+", r" \1 ", retpath)
    retpath = re.sub(r"\s*([-._])\s*", r" \1 ", retpath)
    
    return f"{retpath.strip()}{extension}"