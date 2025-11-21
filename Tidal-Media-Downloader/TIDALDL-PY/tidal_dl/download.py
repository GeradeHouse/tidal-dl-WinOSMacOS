# tidal_dl/download.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   download.py
@Time    :   2020/11/08
@Author  :   Yaronzz
@Modified by: GeradeHouse
@Version :   1.0
@Contact :   yaronhuang@foxmail.com
@Desc    :   Core download module for Tidal Media Downloader.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union, cast

import aigpy
from moviepy.audio.io.AudioFileClip import AudioFileClip
from mutagen import File as MutagenFile

from .decryption import *
from .format import getAlbumPath, getTrackPath
from .metadata.album import AlbumMetadata
from .metadata.track import TrackMetadata
from .metadata.tagger import tag_file
from .model import Album, Artist, Playlist, StreamUrl, Track
from .printf import *
from .tidal import TIDAL_API, SETTINGS, AudioQuality, Type

if TYPE_CHECKING:
    from tidal_dl.gui.gui_main import MainView  # type: ignore

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from .gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()


def log_ffprobe_info(filepath: str, stage_name: str):
    """Runs ffprobe on the file and logs format/stream info."""
    try:
        command: List[str] = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            filepath,
        ]
        logger.debug(f"[{stage_name}] Running ffprobe: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        probe_data = json.loads(result.stdout)
        logger.debug(
            f"[{stage_name}] ffprobe result for '{filepath}': {json.dumps(probe_data, indent=2)}"
        )
        # Log specific useful info
        if "format" in probe_data:
            logger.debug(
                f"[{stage_name}] Format: {probe_data['format'].get('format_name', 'N/A')}, Duration: {probe_data['format'].get('duration', 'N/A')}"
            )
        if "streams" in probe_data:
            for i, stream in enumerate(probe_data["streams"]):
                logger.debug(
                    f"[{stage_name}] Stream #{i}: Codec: {stream.get('codec_name', 'N/A')}, Type: {stream.get('codec_type', 'N/A')}, Profile: {stream.get('profile', 'N/A')}, Bitrate: {stream.get('bit_rate', 'N/A')}, SampleFmt: {stream.get('sample_fmt', 'N/A')}, SampleRate: {stream.get('sample_rate', 'N/A')}"
                )
    # except FileNotFoundError: # Removed ffprobe dependency
    #     logger.warning(f"[{stage_name}] ffprobe command not found. Skipping ffprobe analysis for '{filepath}'. Make sure ffmpeg (which includes ffprobe) is installed and in your system's PATH.")
    # except subprocess.CalledProcessError as e: # Removed ffprobe dependency
    #     logger.error(f"[{stage_name}] ffprobe failed for '{filepath}': {e}")
    #     logger.error(f"[{stage_name}] ffprobe stderr: {e.stderr}")
    # except json.JSONDecodeError as e: # Removed ffprobe dependency
    #     logger.error(f"[{stage_name}] Failed to parse ffprobe JSON output for '{filepath}': {e}")
    except (
        Exception
    ) as e:  # Catch general exceptions during media analysis (ffprobe replacement/removal)
        logger.error(
            f"[{stage_name}] Error during media analysis (ffprobe replacement) on '{filepath}': {e}"
        )


def __isSkip__(finalpath: str, url: str) -> bool:
    if not SETTINGS.checkExist:
        return False
    curSize = aigpy.file.getSize(finalpath)
    if curSize <= 0:
        return False
    netSize = aigpy.net.getSize(url)
    return curSize >= netSize


def __encrypted__(stream: StreamUrl, srcPath: str, descPath: str):
    if aigpy.string.isNull(stream.encryptionKey):
        os.replace(srcPath, descPath)
    else:
        assert stream.encryptionKey is not None
        key, nonce = decrypt_security_token(stream.encryptionKey)
        decrypt_file(srcPath, descPath, key, nonce)
        os.remove(srcPath)


def __parseContributors__(
    roleType: str, Contributors: Optional[Dict[str, Any]]
) -> Optional[List[str]]:
    if Contributors is None:
        return None
    try:
        ret: List[str] = []
        for item in Contributors["items"]:
            if item["role"] == roleType:
                ret.append(item["name"])
        return ret
    except:
        return None


def create_streamrip_metadata(track: Track, album: Album) -> TrackMetadata:
    """
    Converts tidal-dl's Track and Album models to streamrip's Metadata models.
    This acts as a bridge between the two systems.
    """
    # ADDED: Debug log to inspect the album object being processed
    logger.debug(f"[create_streamrip_metadata] Processing Album object: {album}")

    # Step 1: Create the streamrip AlbumMetadata object from the tidal-dl Album
    # We adapt the logic from streamrip's `from_tidal` classmethod
    album_meta = AlbumMetadata.from_tidal(album)

    # Step 2: Create the streamrip TrackMetadata object
    # We adapt the logic from streamrip's `from_tidal` classmethod for tracks
    track_meta = TrackMetadata.from_tidal(album_meta, track)

    # Step 3: Manually add any extra information if needed
    # For example, streamrip's model can hold composer, which we get from contributors
    try:
        contributors = TIDAL_API.getTrackContributors(str(track.id))
        composers = __parseContributors__("Composer", contributors)
        if composers:
            track_meta.composer = ", ".join(composers)
    except Exception as e:
        logger.warning(f"Could not fetch contributors for track {track.id}: {e}")

    return track_meta


def __setMetaData__(
    track: "Track",
    album: Optional["Album"],
    filepath: str,
    contributors: Optional[Dict[str, Any]], # Kept for potential future use, but logic is now in bridge
    lyrics: Optional[str], # Kept for potential future use
):
    """
    This function now uses the advanced streamrip tagging engine.
    """
    logger.info(f"Starting metadata tagging for '{os.path.basename(filepath)}' using streamrip engine.")
    
    # Determine the correct album object
    album_obj = album if album is not None else track.album
    if not isinstance(album_obj, Album):
        logger.error("Could not determine a valid album object for metadata. Aborting tagging.")
        return

    cover_path = None  # Initialize cover_path to None
    try:
        # Step 1: Create the rich streamrip metadata object
        streamrip_meta = create_streamrip_metadata(track, album_obj)
        
        # Add lyrics if available
        if lyrics:
            streamrip_meta.lyrics = lyrics

        # --- ADVANCED: RYM Enrichment (Optional but Recommended) ---
        # To enable this, you need to initialize the service.
        # This is a simplified example.
        try:
            # These imports are here to prevent crashing if the files are not yet integrated
            from .metadata.rym_service import RymMetadataService
            # from streamrip.scraper import RYMMetadataScraper # You would need to integrate this file too
            # from streamrip.config import RymConfig # You would need to integrate this file too
            
            # This setup should ideally be done once, not per track
            # rym_config = RymConfig(enabled=True, genre_mode="replace") 
            # scraper = RYMMetadataScraper()
            # rym_service = RymMetadataService(scraper, rym_config)
            # asyncio.run(streamrip_meta.album.enrich_with_rym(rym_service))
            # logger.info("Enriched metadata with RYM data.")
            logger.debug("RYM enrichment is a placeholder. Full integration of scraper/config needed.")
        except ImportError:
            logger.warning("Could not import RYM service components. Skipping metadata enrichment.")
        # ---------------------------------------------------------

        # Step 2: Download cover art to a temporary file
        cover_data = TIDAL_API.getCoverData(album_obj.cover, "1280", "1280")
        if cover_data:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_f:
                temp_f.write(cover_data)
                cover_path = temp_f.name
        
        # Step 3: Run the asynchronous tagging function
        # We use asyncio.run() to call the async tag_file from our sync code
        asyncio.run(tag_file(filepath, streamrip_meta, cover_path))

        logger.info(f"Successfully tagged '{os.path.basename(filepath)}' with extensive metadata.")

    except Exception as e:
        logger.error(f"Failed to tag file '{os.path.basename(filepath)}' using streamrip engine: {e}", exc_info=True)
        logger.error(f"Failed to write metadata for '{track.title}': {e}")
    finally:
        # Clean up the temporary cover file
        if cover_path and os.path.exists(cover_path):
            os.remove(cover_path)


def downloadCover(album: Album):
    if album is None:
        return

    artistName = TIDAL_API.getArtistsName(cast(List[Artist], album.artists))
    albumArtistName = getattr(getattr(album, "artist", None), "name", "")
    flag = TIDAL_API.getFlag(album, Type.Album, True, "")

    path = getAlbumPath(album, artistName, albumArtistName, flag)
    if path is None:
        return
    path += "/cover.jpg"
    
    # --- START OF MODIFICATION ---
    # Use the authenticated getCoverData method instead of a direct, unauthenticated download.
    cover_data = TIDAL_API.getCoverData(album.cover, "1280", "1280")
    if cover_data:
        with open(path, "wb") as f:
            f.write(cover_data)
    # --- END OF MODIFICATION ---


def downloadAlbumInfo(album: Album, tracks: List[Track]):
    if album is None:
        return

    artistName = TIDAL_API.getArtistsName(cast(List[Artist], album.artists))
    albumArtistName = getattr(getattr(album, "artist", None), "name", "")
    flag = TIDAL_API.getFlag(album, Type.Album, True, "")

    path = getAlbumPath(album, artistName, albumArtistName, flag)
    if path is None:
        return
    aigpy.path.mkdirs(path)

    # Use f-string formatting to avoid concatenation issues if any value is None.
    infos = f"[ID]          {album.id}\n"
    infos += f"[Title]       {album.title}\n"
    infos += (
        f"[Artists]     {TIDAL_API.getArtistsName(cast(List[Artist], album.artists))}\n"
    )
    infos += f"[ReleaseDate] {album.releaseDate}\n"
    infos += f"[SongNum]     {album.numberOfTracks}\n"
    infos += f"[Duration]    {album.duration}\n\n"

    if album.numberOfVolumes is None:
        return
    for index in range(0, album.numberOfVolumes):
        volumeNumber = index + 1
        infos += f"===========CD {volumeNumber}=============\n"
        for item in tracks:
            if item.volumeNumber != volumeNumber:
                continue
            infos += "{:<8}".format(f"[{item.trackNumber}]")
            infos += f"{item.title}\n"
    aigpy.file.write(path + "/AlbumInfo.txt", infos, "w+")  # Added filename


def __validateTrackObject__(track: Any) -> Track:
    """Ensure track is a proper Track object with required attributes"""
    import logging

    # Re-import Track locally for Pylance
    from .model import Track

    # If track is a string, attempt to convert using TIDAL_API.getTypeData
    if isinstance(track, str):
        logger.debug(f"Converting track from string: {track}")
        from .tidal import TIDAL_API, Type

        track_obj = TIDAL_API.getTypeData(track, Type.Track)
        if track_obj is None:
            raise ValueError(f"Failed conversion from string '{track}' to Track object")
        track = track_obj
    # If track is a dict, convert to Track object using aigpy
    if isinstance(track, dict):
        logger.debug(f"Converting track from dict: {track}")
        # Use aigpy.model.dictToModel for conversion
        track_obj = aigpy.model.dictToModel(track, Track())
        if track_obj is None:
            raise ValueError(f"Failed conversion from dict to Track object: {track}")
        track = track_obj  # Assign the converted object back
    # Extra check: if track.title is callable, this indicates invalid data
    if hasattr(track, "title") and callable(track.title):
        logger.error(f"Track title is callable, invalid track data: {track}")
        if "title" in track.__dict__ and not callable(track.__dict__["title"]):
            track.title = track.__dict__["title"]
            logger.debug(f"Fixed track title from __dict__: {track.title}")
        else:
            raise ValueError(
                "Track title remains callable after conversion: " + str(track)
            )
    if not hasattr(track, "audioQuality"):
        # Attempt to fetch full track info if audioQuality is missing
        track_id = getattr(track, "id", None)  # Get ID safely
        if track_id is None:
            raise ValueError(
                "Invalid track object - missing ID and audioQuality attribute."
            )
        logger.warning(
            f"Track object missing audioQuality, attempting to fetch full info for ID: {track_id}"
        )
        try:
            from .tidal import TIDAL_API, Type

            full_track = TIDAL_API.getTrack(str(track_id))  # Ensure ID is string
            if full_track and hasattr(full_track, "audioQuality"):
                logger.info(
                    f"Successfully fetched full track info with audioQuality for ID: {track_id}"
                )
                return full_track  # Return the complete object
            else:
                raise ValueError(
                    "Failed to fetch full track info or audioQuality still missing."
                )
        except Exception as e:
            logger.error(f"Error fetching full track info for ID {track_id}: {e}")
            raise ValueError(
                "Invalid track object - missing audioQuality attribute and failed to fetch full info; got type "
                + str(type(track))
            )

    # Additional check: if track is still a string, attempt conversion using getTrack
    if isinstance(track, str):
        logger.error(
            "Track remains a string after conversion; attempting getTrack conversion again"
        )
        from .tidal import TIDAL_API, Type

        try:
            new_track = TIDAL_API.getTrack(track)
            if not isinstance(new_track, Track):
                raise TypeError(f"Expected Track object but got {type(new_track)}")
            track = new_track
            logger.debug(
                f"Successfully converted track string to Track object: {track.id}"
            )
        except Exception as e:
            logger.error(f"Critical error converting track {track}: {str(e)}")
            raise ValueError(
                f"Failed to convert track ID to valid object: {track}"
            ) from e
    # Final check to ensure we have a Track object
    if not isinstance(track, Track):
        raise TypeError(
            f"Validation failed: Expected Track object, but got {type(track)}"
        )
    return track


def __getTrackQuality__(track: Track) -> AudioQuality:
    """Safely get audio quality from track object with extra debug logging"""
    import logging

    logger.debug(
        f"Entering __getTrackQuality__ with track: {track} (type: {type(track)}) and mediaMetadata: {getattr(track, 'mediaMetadata', None)}"
    )
    try:
        if hasattr(track, "audioQuality") and track.audioQuality is not None:
            quality_str = str(track.audioQuality)
            logger.debug(f"Track has audioQuality attribute: {quality_str}")
            return AudioQuality(quality_str)
        if hasattr(track, "mediaMetadata"):
            media_meta = track.mediaMetadata
            if isinstance(media_meta, dict):
                quality_str = media_meta.get("audioQuality")
                if quality_str and isinstance(quality_str, str):
                    logger.debug(f"Derived quality from mediaMetadata: {quality_str}")
                    return AudioQuality(quality_str)
        # Fallback if neither attribute exists
        logger.warning(
            f"Track object missing both audioQuality and mediaMetadata. Defaulting to LOSSLESS."
        )
        return AudioQuality.LOSSLESS
    except Exception as e:
        logger.error(f"Exception in __getTrackQuality__: {e}. Defaulting to LOSSLESS.")
        return AudioQuality.LOSSLESS


# Modify signature to accept playlist_context (including Album)
def downloadTrack(
    track: "Track",
    main_view_instance: "MainView",
    album: Optional["Album"] = None,
    playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    userProgress: Optional[Any] = None,
    partSize: int = 1048576,
    downloadQuality: Optional[str] = None,
):
    check = False
    actual_download_part_path = None
    try:
        track = __validateTrackObject__(track)
        assert isinstance(
            track, Track
        ), f"Validation failed, expected Track, got {type(track)}"

        # --- Fix missing album artist ---
        # The track.album object from search/track info might lack artist data.
        # We check and fetch full album info if needed to ensure correct paths and tagging.
        target_album = album if album else track.album
        if target_album and getattr(target_album, 'id', None):
            # Check if artist info is missing
            # Note: model.py initializes artist=Artist(), so we check for name/id or if it's None
            has_artist = False
            
            # Check 'artists' list
            artists_list = getattr(target_album, 'artists', None)
            if artists_list:
                has_artist = True
                
            # Check 'artist' object
            if not has_artist:
                artist_obj = getattr(target_album, 'artist', None)
                if artist_obj and getattr(artist_obj, 'name', None):
                    has_artist = True
            
            if not has_artist:
                logger.debug(f"Album metadata for '{getattr(target_album, 'title', 'Unknown')}' missing artist. Fetching full details...")
                try:
                    full_album = TIDAL_API.getAlbum(str(target_album.id))
                    if full_album:
                        if album:
                            # We can't reassign the local 'album' variable to affect the caller, 
                            # but we can use 'full_album' for local operations.
                            album = full_album
                        else:
                            track.album = full_album
                except Exception as e:
                    logger.warning(f"Failed to fetch full album info: {e}")
        # --- End Fix ---

        logger.debug(f"Starting downloadTrack for '{track.title}'")

        requested_mp3 = False
        intended_quality = SETTINGS.audioQuality

        if downloadQuality:
            quality_map = {
                "low": AudioQuality.LOW, "high": AudioQuality.HIGH, "mp3": AudioQuality.MP3,
                "lossless": AudioQuality.LOSSLESS, "hi_res_lossless": AudioQuality.HI_RES_LOSSLESS,
                "highest": AudioQuality.HIGHEST
            }
            # Normalize the input string for broader matching
            normalized_quality = downloadQuality.lower().replace(" ", "_").replace("-", "_")
            for key, value in quality_map.items():
                if key in normalized_quality:
                    intended_quality = value
                    break
        
        if intended_quality == AudioQuality.MP3:
            requested_mp3 = True
            intended_quality = AudioQuality.HIGH
            logger.debug("MP3 requested. Setting download quality to HIGH for conversion.")

        q = intended_quality
        if intended_quality == AudioQuality.HIGHEST:
            is_hires = False
            media_metadata = getattr(track, "mediaMetadata", {})
            if isinstance(media_metadata, dict):
                tags = media_metadata.get("tags", [])
                if "HIRES_LOSSLESS" in [str(t).upper() for t in tags]:
                    is_hires = True
            q = AudioQuality.HI_RES_LOSSLESS if is_hires else AudioQuality.LOSSLESS

        if track.id is None:
            raise ValueError(f"Track '{track.title}' has no ID.")
        stream = TIDAL_API.getStreamUrl(str(track.id), q)

        artists = TIDAL_API.getArtistsName(cast(List[Artist], getattr(track, "artists", [])))
        artist = getattr(getattr(track, "artist", None), "name", "") or artists
        path = getTrackPath(track, stream, artist, artists, album, playlist_context)
        path = os.path.join(SETTINGS.downloadPath, path)
        path = os.path.normpath(path)
        aigpy.path.mkdirs(os.path.dirname(path))

        if SETTINGS.showTrackInfo and not SETTINGS.multiThread:
            # Convert to logger.info() with structured track information
            logger.info(f"Track: {track.title} (ID: {track.id}, Quality: {Printf.map_track_quality(track)})")
        if userProgress:
            userProgress.updateStream(stream)

        ### START DASH INTEGRATION ###
        # The key change is here. We now use `stream.urls` which can be a list of segments.
        url_list = stream.urls if stream.urls and len(stream.urls) > 1 else [stream.url]
        if not url_list or url_list[0] is None:
            raise Exception("No valid URL or URL list available for download.")
        
        # The aigpy DownloadTool natively supports a list of URLs for concatenation.
        logger.info(f"[DL Track] name='{os.path.basename(path)}'. Preparing to download {len(url_list)} segment(s).")
        ### END DASH INTEGRATION ###

        if __isSkip__(path, url_list[0]):
            logger.info(f"{os.path.basename(path)} (skip:already exists!)")
            return True, ""

        actual_download_part_path = path + ".part"
        if main_view_instance.cancel_requested:
            return False, "Download cancelled by user before start."

        tool = aigpy.download.DownloadTool(actual_download_part_path, url_list)
        tool.setUserProgress(userProgress)
        tool.setPartSize(partSize)
        check, err = tool.start(False)

        if not check:
            logger.error(f"DL Track '{track.title}' failed: {err or ''}")
            return False, str(err or "")

        __encrypted__(stream, actual_download_part_path, path)

        if requested_mp3 and path.lower().endswith((".m4a", ".mp4", ".mov", ".flac")):
            logger.info(f"Converting '{track.title}' to MP3 (320kbps)...")
            logger.info(f"Converting '{track.title}' to MP3...")
            mp3_path = path.rsplit('.', 1)[0] + '.mp3'
            try:
                with AudioFileClip(path) as audio_clip:
                    audio_clip.write_audiofile(mp3_path, codec='mp3', bitrate='320k', logger=None)
                os.remove(path)
                path = mp3_path
            except Exception as e:
                logger.error(f"Failed to convert '{track.title}' to MP3: {e}")

        if path.lower().endswith((".mp4", ".mov")) and stream.codec and 'flac' in stream.codec.lower():
            logger.info(f"Detected FLAC in MP4 container for '{track.title}'. Extracting...")
            demuxed_path = path.rsplit('.', 1)[0] + '.flac'
            try:
                with AudioFileClip(path) as audio_clip:
                    audio_clip.write_audiofile(demuxed_path, codec="flac", logger="bar")
                os.remove(path)
                path = demuxed_path
            except Exception as e:
                logger.error(f"Demuxing of FLAC stream failed: {e}")
                return False, str(e)

        contributors = None
        if track.id:
            try:
                contributors = TIDAL_API.getTrackContributors(str(track.id))
            except Exception as ex:
                logger.debug(f"Failed to get contributors: {ex}")

        lyrics = ""
        if track.id:
            try:
                lyrics = TIDAL_API.getLyrics(str(track.id)).subtitles
                if SETTINGS.lyricFile:
                    lrcPath = path.rsplit(".", 1)[0] + ".lrc"
                    aigpy.file.write(lrcPath, lyrics, "w")
            except Exception as ex:
                logger.debug(f"No lyrics available: {ex}")

        __setMetaData__(cast(Track, track), album, path, contributors, lyrics)
        logger.info(track.title or f"Track {track.id}")
        return True, ""

    except Exception as e:
        logger.error(f"DL Track '{getattr(track, 'title', 'Unknown')}' failed: {e}")
        logger.error(f"Exception in downloadTrack for '{getattr(track, 'title', 'Unknown')}': {e}", exc_info=True)
        return False, str(e)
    finally:
        interrupted = main_view_instance.stop_requested or main_view_instance.cancel_requested
        if (not check or interrupted) and actual_download_part_path and os.path.exists(actual_download_part_path):
            os.remove(actual_download_part_path)


# Modify signature to accept playlist_context (including Album)
def downloadTracks(
    tracks: List[Track],
    main_view_instance: "MainView",
    album: Optional["Album"] = None,
    playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    downloadQuality: Optional[str] = None,
):
    # When downloading a playlist with a selected quality (other than "highest"),
    # only download tracks whose maximum available quality (normalized) exactly matches the requested quality.
    if downloadQuality is not None and downloadQuality.lower() != "highest":
        filtered_tracks = []
        # Determine a quality filter using the same mapping.
        quality_map_enum = {
            "m4a - aac – high efficiency (96 kbps, 44.1 khz)": AudioQuality.LOW,
            "m4a - aac – full bandwidth (320 kbps, 44.1 khz)": AudioQuality.HIGH,
            "flac – cd standard (16-bit, 44.1 khz)": AudioQuality.LOSSLESS,
            "flac – high resolution (24-bit, 96 khz)": AudioQuality.HI_RES_LOSSLESS,  # Corrected kHz typo
            "highest available": AudioQuality.HIGHEST,
            "low": AudioQuality.LOW,
            "high": AudioQuality.HIGH,
            "lossless": AudioQuality.LOSSLESS,
            "hi_res_lossless": AudioQuality.HI_RES_LOSSLESS,
            "highest": AudioQuality.HIGHEST,
        }
        filter_quality = quality_map_enum.get(downloadQuality.lower(), None)
        for track in tracks:
            try:
                # Determine maximum available quality from track metadata
                track = __validateTrackObject__(track)  # Ensure track object is valid
                max_available_enum = AudioQuality.LOW  # Default lowest
                is_hires = False
                media_metadata = getattr(track, "mediaMetadata", {})
                if isinstance(media_metadata, dict):
                    tags = media_metadata.get("tags", [])
                    if isinstance(tags, list):
                        for tag in tags:
                            if isinstance(tag, str) and tag.upper() == "HIRES_LOSSLESS":
                                is_hires = True
                                break
                if is_hires:
                    max_available_enum = AudioQuality.HI_RES_LOSSLESS
                elif hasattr(track, "audioQuality") and track.audioQuality is not None:
                    # Compare with enum *value* as track.audioQuality is likely a string from API
                    if track.audioQuality == AudioQuality.LOSSLESS.value:
                        max_available_enum = AudioQuality.LOSSLESS
                    elif track.audioQuality == AudioQuality.HIGH.value:
                        max_available_enum = AudioQuality.HIGH
                    # LOW is default if neither matches

                # Define quality ranking (higher value is better)
                quality_ranking = {
                    AudioQuality.LOW: 1,
                    AudioQuality.HIGH: 2,
                    AudioQuality.LOSSLESS: 3,
                    AudioQuality.HI_RES_LOSSLESS: 4,
                    # HIGHEST is handled separately, but assign a rank for completeness
                    AudioQuality.HIGHEST: 5,
                }

                # Compare the track's max available quality with the filter quality using ranking
                # Handle None case for filter_quality before calling .get()
                filter_rank = (
                    quality_ranking.get(filter_quality, 0)
                    if filter_quality is not None
                    else 0
                )
                available_rank = quality_ranking.get(max_available_enum, 0)
                logger.debug(
                    f"Filtering track {track.id}: Max Available Quality Enum={max_available_enum.name} (Rank {available_rank}), Filter Quality Enum={filter_quality.name if filter_quality else 'None'} (Rank {filter_rank})"
                )

                # Allow download if available quality rank is >= filter quality rank
                if filter_quality is not None and available_rank >= filter_rank:
                    logger.debug(
                        f"Track {track.id} passed quality filter (Available >= Requested)."
                    )
                    filtered_tracks.append(track)
                else:
                    # Provide more context in the log message when a track fails the filter
                    logger.debug(
                        f"Track {track.id} did NOT pass quality filter (Available Rank {available_rank} < Requested Rank {filter_rank})."
                    )
            except Exception as e:
                logger.warning(
                    f"Error during quality filtering for track {track.id}: {e}"
                )
                logger.error(
                    traceback.format_exc()
                )  # Add traceback for better debugging
                continue
        tracks = filtered_tracks
        logger.debug(
            f"Finished filtering tracks. Number of tracks remaining: {len(tracks)}"
        )
        if not tracks:
            logger.warning(
                "No tracks matched the specified quality filter. Nothing to download."
            )
            return  # Exit if no tracks are left

    # If downloadQuality was HIGHEST, the filtering above might be too strict or unnecessary
    # as downloadTrack already handles selecting the best available.
    # However, keeping the filter as corrected ensures consistency if user specifically requests e.g., LOSSLESS
    # and wants ONLY lossless tracks, even if HIRES is available.

    # Now, for each track that passes the quality filter, call downloadTrack with album forced as None.
    logger.debug(
        f"Proceeding to download {len(tracks)} tracks. MultiThread: {SETTINGS.multiThread}"
    )
    if not SETTINGS.multiThread:
        logger.debug("Using single-thread download.")
        for index, item in enumerate(tracks):
            # === Pause/Stop Check (Inside Loop) ===
            logger.debug(
                f"[Thread] Loop {index+1}/{len(tracks)}: Checking stop_event (is_set={main_view_instance.stop_event.is_set()})"
            )
            if main_view_instance.stop_event.is_set():
                logger.info("Stop request detected. Aborting download queue.")
                break  # Exit the loop

            logger.debug(
                f"[Thread] Loop {index+1}/{len(tracks)}: Checking download_paused (is {main_view_instance.download_paused})"
            )
            if main_view_instance.download_paused:
                logger.info(
                    "Download queue paused. Waiting for resume signal..."
                )  # Updated log
                logger.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Emitting pause confirmation signal."
                )
                main_view_instance.signal_actually_paused.emit()
                logger.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Calling pause_event.wait() (event is_set={main_view_instance.pause_event.is_set()})"
                )
                main_view_instance.pause_event.wait()  # Wait for GUI to set() the event
                logger.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Returned from pause_event.wait() (event is_set={main_view_instance.pause_event.is_set()})"
                )
                logger.info("Download queue resumed.")  # Updated log
                # Re-check stop after pause
                logger.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Re-checking stop_event after pause (is_set={main_view_instance.stop_event.is_set()})"
                )
                if main_view_instance.stop_event.is_set():
                    logger.info(
                        "Stop request detected after pause. Aborting download queue."
                    )
                    break
            # === End Pause/Stop Check ===

            logger.debug(
                f"Initiating single-thread download for track {index+1}/{len(tracks)}: {item.title}"
            )
            # Pass main_view_instance and playlist_context to downloadTrack
            downloadTrack(
                item,
                main_view_instance,
                album=None,
                playlist_context=playlist_context,
                downloadQuality=downloadQuality,
            )
    else:
        # Note: Pause/Stop logic is NOT implemented for multi-thread mode here.
        thread_pool = ThreadPoolExecutor(max_workers=5)
        for index, item in enumerate(tracks):
            # Need to pass main_view_instance if multi-thread pause/stop were implemented
            # Pass playlist_context here as well
            thread_pool.submit(
                downloadTrack,
                item,
                main_view_instance,
                None,
                playlist_context,
                None,
                1048576,
                downloadQuality,
            )  # Pass main_view_instance and playlist_context
        thread_pool.shutdown(wait=True)