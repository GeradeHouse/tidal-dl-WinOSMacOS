# --- START OF FILE download.py ---

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
This module handles downloading and processing of Tidal media content including tracks,
album covers, and album information. It supports various audio quality levels
from standard to hi-res, handles proper metadata tagging, and manages decryption of
protected content. Features include multi-threading capability, download progress tracking,
pause/resume functionality, and format conversion (particularly for hi-res content delivered
in MP4 containers). The module integrates with ffmpeg for media processing and uses
mutagen for metadata validation. Error handling includes extensive logging for debugging
and troubleshooting download issues.
"""

from moviepy import VideoFileClip  # Explicit import for clarity

from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Union, List, Dict, Any, cast
import os
import logging

# Removed sys import as it's no longer needed here
# Removed ColorFormatter and logging setup - now handled in logging_config.py

import traceback
import subprocess
import json
from .decryption import *
from .printf import *

# Moved Track import to top level
from .model import Track, Album, Playlist, Artist, StreamUrl
from .tidal import *  # Imports TIDAL_API, SETTINGS, AudioQuality, VideoQuality, Type
from .format import getTrackPath, getAlbumPath
import aigpy  # Imports aigpy.path, aigpy.file, aigpy.net, aigpy.string, aigpy.tag, aigpy.m3u8, aigpy.download

# Import mutagen for detailed file inspection.
from mutagen import File as MutagenFile

# logging.basicConfig(level=logging.DEBUG) # Removed as setup is now handled in logging_config.py

# Forward declaration for type hinting MainView without circular import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Ensure MainView import is ONLY here
    from tidal_dl.gui.gui import MainView  # type: ignore # Suppress Pylance warning about unknown import symbol


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
        logging.debug(f"[{stage_name}] Running ffprobe: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        probe_data = json.loads(result.stdout)
        logging.debug(
            f"[{stage_name}] ffprobe result for '{filepath}': {json.dumps(probe_data, indent=2)}"
        )
        # Log specific useful info
        if "format" in probe_data:
            logging.debug(
                f"[{stage_name}] Format: {probe_data['format'].get('format_name', 'N/A')}, Duration: {probe_data['format'].get('duration', 'N/A')}"
            )
        if "streams" in probe_data:
            for i, stream in enumerate(probe_data["streams"]):
                logging.debug(
                    f"[{stage_name}] Stream #{i}: Codec: {stream.get('codec_name', 'N/A')}, Type: {stream.get('codec_type', 'N/A')}, Profile: {stream.get('profile', 'N/A')}, Bitrate: {stream.get('bit_rate', 'N/A')}, SampleFmt: {stream.get('sample_fmt', 'N/A')}, SampleRate: {stream.get('sample_rate', 'N/A')}"
                )
    # except FileNotFoundError: # Removed ffprobe dependency
    #     logging.warning(f"[{stage_name}] ffprobe command not found. Skipping ffprobe analysis for '{filepath}'. Make sure ffmpeg (which includes ffprobe) is installed and in your system's PATH.")
    # except subprocess.CalledProcessError as e: # Removed ffprobe dependency
    #     logging.error(f"[{stage_name}] ffprobe failed for '{filepath}': {e}")
    #     logging.error(f"[{stage_name}] ffprobe stderr: {e.stderr}")
    # except json.JSONDecodeError as e: # Removed ffprobe dependency
    #     logging.error(f"[{stage_name}] Failed to parse ffprobe JSON output for '{filepath}': {e}")
    except (
        Exception
    ) as e:  # Catch general exceptions during media analysis (ffprobe replacement/removal)
        logging.error(
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


def __setMetaData__(
    track: "Track",
    album: Optional["Album"],
    filepath: str,
    contributors: Optional[Dict[str, Any]],
    lyrics: Optional[str],
):
    obj = aigpy.tag.TagTool(filepath)
    # Use album parameter if available and not a string; otherwise, fallback to track.album if it is an object.
    album_obj: Union[Album, Any]
    if album is not None:
        album_obj = album
    elif (
        hasattr(track, "album")
        and track.album is not None
        and not isinstance(track.album, str)
    ):
        album_obj = track.album
    else:
        # Create a dummy album object with minimal attributes to avoid attribute errors.
        class DummyAlbum:
            title = ""
            artists: Union[str, List[Artist]] = ""  # Adjusted type hint
            releaseDate = ""
            numberOfVolumes = 1
            numberOfTracks = 0
            cover = ""

        album_obj = DummyAlbum()
    obj.album = album_obj.title
    obj.title = track.title
    if not aigpy.string.isNull(track.version):
        if obj.title is None:
            obj.title = ""
        obj.title += f" ({str(track.version)})"
    # Ensure track.artists is a list of strings.
    if not track.artists:
        obj.artist = [""]
    elif isinstance(track.artists, list):
        obj.artist = [
            artist.name if hasattr(artist, "name") else str(artist)
            for artist in cast(List[Artist], track.artists)
        ]
    elif isinstance(track.artists, str):
        obj.artist = [track.artists]
    elif hasattr(track.artists, "name"):
        obj.artist = [track.artists.name]
    else:
        obj.artist = [str(track.artists)]
    obj.copyright = track.copyRight
    obj.tracknumber = track.trackNumber
    obj.discnumber = track.volumeNumber
    obj.composer = __parseContributors__("Composer", contributors)
    obj.isrc = track.isrc

    # Ensure album_obj.artists is a list of strings.
    if not album_obj.artists:
        obj.albumartist = [""]
    elif isinstance(album_obj.artists, list):
        obj.albumartist = [
            artist.name if hasattr(artist, "name") else str(artist)
            for artist in cast(List[Artist], album_obj.artists)
        ]
    elif isinstance(album_obj.artists, str):
        obj.albumartist = [album_obj.artists]  # Handle string case
    elif hasattr(album_obj.artists, "name"):  # Check if it's an object with 'name'
        obj.albumartist = [album_obj.artists.name]
    else:
        obj.albumartist = [str(album_obj.artists)]  # Fallback to string conversion
    obj.date = album_obj.releaseDate
    # Ensure numberOfVolumes is an integer (default to 1 if missing)
    totaldisc = (
        album_obj.numberOfVolumes if album_obj.numberOfVolumes is not None else 1
    )
    obj.totaldisc = totaldisc
    obj.lyrics = lyrics
    # Similarly, ensure numberOfTracks is an integer (default to 0 if missing)
    if totaldisc <= 1:
        obj.totaltrack = (
            album_obj.numberOfTracks if album_obj.numberOfTracks is not None else 0
        )
    coverpath = TIDAL_API.getCoverUrl(album_obj.cover, "1280", "1280")
    obj.save(coverpath)


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
    url = TIDAL_API.getCoverUrl(album.cover, "1280", "1280")
    aigpy.net.downloadFile(url, path)


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
        logging.debug(f"Converting track from string: {track}")
        from .tidal import TIDAL_API, Type

        track_obj = TIDAL_API.getTypeData(track, Type.Track)
        if track_obj is None:
            raise ValueError(f"Failed conversion from string '{track}' to Track object")
        track = track_obj
    # If track is a dict, convert to Track object using aigpy
    if isinstance(track, dict):
        logging.debug(f"Converting track from dict: {track}")
        # Use aigpy.model.dictToModel for conversion
        track_obj = aigpy.model.dictToModel(track, Track)
        if track_obj is None:
            raise ValueError(f"Failed conversion from dict to Track object: {track}")
        track = track_obj  # Assign the converted object back
    # Extra check: if track.title is callable, this indicates invalid data
    if hasattr(track, "title") and callable(track.title):
        logging.error(f"Track title is callable, invalid track data: {track}")
        if "title" in track.__dict__ and not callable(track.__dict__["title"]):
            track.title = track.__dict__["title"]
            logging.debug(f"Fixed track title from __dict__: {track.title}")
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
        logging.warning(
            f"Track object missing audioQuality, attempting to fetch full info for ID: {track_id}"
        )
        try:
            from .tidal import TIDAL_API, Type

            full_track = TIDAL_API.getTrack(str(track_id))  # Ensure ID is string
            if full_track and hasattr(full_track, "audioQuality"):
                logging.info(
                    f"Successfully fetched full track info with audioQuality for ID: {track_id}"
                )
                return full_track  # Return the complete object
            else:
                raise ValueError(
                    "Failed to fetch full track info or audioQuality still missing."
                )
        except Exception as e:
            logging.error(f"Error fetching full track info for ID {track_id}: {e}")
            raise ValueError(
                "Invalid track object - missing audioQuality attribute and failed to fetch full info; got type "
                + str(type(track))
            )

    # Additional check: if track is still a string, attempt conversion using getTrack
    if isinstance(track, str):
        logging.error(
            "Track remains a string after conversion; attempting getTrack conversion again"
        )
        from .tidal import TIDAL_API, Type

        try:
            new_track = TIDAL_API.getTrack(track)
            if not isinstance(new_track, Track):
                raise TypeError(f"Expected Track object but got {type(new_track)}")
            track = new_track
            logging.debug(
                f"Successfully converted track string to Track object: {track.id}"
            )
        except Exception as e:
            logging.error(f"Critical error converting track {track}: {str(e)}")
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

    logging.debug(
        f"Entering __getTrackQuality__ with track: {track} (type: {type(track)}) and mediaMetadata: {getattr(track, 'mediaMetadata', None)}"
    )
    try:
        if hasattr(track, "audioQuality") and track.audioQuality is not None:
            quality_str = str(track.audioQuality)
            logging.debug(f"Track has audioQuality attribute: {quality_str}")
            return AudioQuality(quality_str)
        if hasattr(track, "mediaMetadata"):
            media_meta = track.mediaMetadata
            if isinstance(media_meta, dict):
                quality_str = media_meta.get("audioQuality")
                if quality_str and isinstance(quality_str, str):
                    logging.debug(f"Derived quality from mediaMetadata: {quality_str}")
                    return AudioQuality(quality_str)
        # Fallback if neither attribute exists
        logging.warning(
            f"Track object missing both audioQuality and mediaMetadata. Defaulting to LOSSLESS."
        )
        return AudioQuality.LOSSLESS
    except Exception as e:
        logging.error(f"Exception in __getTrackQuality__: {e}. Defaulting to LOSSLESS.")
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
    # Define check and actual_download_part_path with default values
    # to ensure they exist in the finally block scope
    check = False
    actual_download_part_path = None
    try:
        track = __validateTrackObject__(track)  # Ensure track is a valid Track object
        # Add assertion for Pylance after validation
        assert isinstance(
            track, Track
        ), f"Validation failed, expected Track, got {type(track)}"

        logging.debug(f"downloadTrack: track type after validation: {type(track)}")
        # Removed redundant string check as validation handles it
        logging.debug(f"Starting downloadTrack for '{track.title}'")

        # Determine the intended quality based on parameters and settings
        intended_quality = None
        if downloadQuality is None:
            # If None is passed, it means "Highest Available" was explicitly chosen in GUI
            intended_quality = AudioQuality.HIGHEST
            logging.debug(
                f"downloadQuality is None, setting intended_quality to HIGHEST."
            )
        else:
            # A specific quality string was passed
            logging.debug(f"Specific downloadQuality requested: '{downloadQuality}'")
            quality_map_enum = {
                "m4a - aac – high efficiency (96 kbps, 44.1 khz)": AudioQuality.LOW,
                "m4a - aac – full bandwidth (320 kbps, 44.1 khz)": AudioQuality.HIGH,
                "flac – cd standard (16-bit, 44.1 khz)": AudioQuality.LOSSLESS,
                "flac – high resolution (24-bit, 96 khz)": AudioQuality.HI_RES_LOSSLESS,
                "highest available": AudioQuality.HIGHEST,  # Mapping for completeness
                "low": AudioQuality.LOW,
                "high": AudioQuality.HIGH,
                "lossless": AudioQuality.LOSSLESS,
                "hi_res_lossless": AudioQuality.HI_RES_LOSSLESS,
                "highest": AudioQuality.HIGHEST,
            }
            intended_quality = quality_map_enum.get(downloadQuality.lower())
            if intended_quality is None:
                logging.warning(
                    f"Could not map specific downloadQuality '{downloadQuality}', falling back to global settings."
                )
                intended_quality = SETTINGS.audioQuality  # Fallback if mapping fails
            else:
                logging.debug(
                    f"Intended quality set from specific request: {intended_quality.name}"
                )

        # Initialize final request quality 'q' with the determined intent
        q = intended_quality

        # If the *intended* quality is HIGHEST, determine the actual best available
        if intended_quality == AudioQuality.HIGHEST:
            logging.debug(
                f"Intended quality is HIGHEST, performing metadata analysis for track '{track.title}'..."
            )
            is_hires = False
            media_metadata = getattr(track, "mediaMetadata", {})
            if isinstance(media_metadata, dict):
                tags = media_metadata.get("tags", [])
                if isinstance(tags, list):
                    for tag in tags:
                        if isinstance(tag, str) and tag.upper() == "HIRES_LOSSLESS":
                            is_hires = True
                            logging.debug(
                                f"Track '{track.title}' identified as HIRES based on metadata."
                            )
                            break

            if is_hires:
                q = AudioQuality.HI_RES_LOSSLESS  # Upgrade q
                logging.debug(
                    f"Setting final request quality 'q' to HI_RES_LOSSLESS for '{track.title}'."
                )
            else:
                q = AudioQuality.LOSSLESS  # Downgrade q to LOSSLESS if HIRES not found
                logging.debug(
                    f"Setting final request quality 'q' to LOSSLESS for '{track.title}' (HIRES not available)."
                )
        else:
            # If intended quality was specific (Low, High, Lossless), q remains as intended_quality
            logging.debug(
                f"Final request quality 'q' is '{q.name}' (specific quality requested)."
            )

        # Now, 'q' holds the correct AudioQuality enum value for the API call
        # Add check for track.id before calling API
        if track.id is None:
            raise ValueError(f"Track '{track.title}' has no ID. Cannot fetch stream.")
        stream = TIDAL_API.getStreamUrl(str(track.id), q)  # Ensure ID is string
        logging.debug(f"Requesting stream for '{track.title}' with quality: {q.name}")
        logging.debug(f"Retrieved stream for '{track.title}': {stream}")
        logging.debug(f"stream.url: {stream.url}")
        # Do not log the entire list of URLs to avoid excessive output.
        # logging.debug(f"stream.urls: {stream.urls}")

        # Check if the received stream quality matches the requested quality
        requested_quality_str = q.value
        received_quality_str = stream.soundQuality
        if (
            requested_quality_str == AudioQuality.HI_RES_LOSSLESS.value
            and received_quality_str != AudioQuality.HI_RES_LOSSLESS.value
        ):
            logging.warning(
                f"Requested HI_RES_LOSSLESS for track '{track.title}' (ID: {track.id}), but API returned stream quality: {received_quality_str}. Proceeding with download at {received_quality_str}."
            )
        elif requested_quality_str != received_quality_str:
            logging.debug(
                f"Requested quality '{requested_quality_str}' but received '{received_quality_str}' for track '{track.title}'."
            )

        # Get artist and flag info to pass to getTrackPath
        artists = TIDAL_API.getArtistsName(
            cast(List[Artist], getattr(track, "artists", []))
        )
        artist = getattr(getattr(track, "artist", None), "name", "") or artists

        # Pass playlist_context to getTrackPath
        path = getTrackPath(track, stream, artist, artists, album, playlist_context)
        logging.debug(f"Computed download path: {path}")

        if SETTINGS.showTrackInfo and not SETTINGS.multiThread:
            Printf.track(cast(Track, track), stream)

        if userProgress is not None:
            logging.debug(
                f"[DL Track] Calling userProgress.updateStream for '{track.title}'"
            )
            userProgress.updateStream(stream)

        # check exist
        if stream.url and __isSkip__(path, stream.url):
            Printf.success(aigpy.path.getFileName(path) + " (skip:already exists!)")
            logging.debug(f"File exists, skipping download for '{track.title}'")
            return True, ""

        logging.info(
            f"[DL Track] name={aigpy.path.getFileName(path)}\nurl={stream.url or ''}"
        )
        logging.debug(f"Preparing to download '{track.title}'")
        # --- Correction for MP4 streams saved with .flac extension ---
        # Check if the stream is actually MP4/DASH but the path was set to .flac
        logging.debug(
            f"[Path Correction] Checking stream.manifestMimeType='{getattr(stream, 'manifestMimeType', 'N/A')}', stream.codec='{getattr(stream, 'codec', 'N/A')}', current path='{path}'"
        )
        is_mp4_stream = False
        if stream.manifestMimeType and "dash+xml" in stream.manifestMimeType:
            is_mp4_stream = True
            logging.debug("Stream identified as DASH/MP4 based on manifestMimeType.")
        elif stream.codec and (
            "mp4a" in stream.codec.lower() or "avc" in stream.codec.lower()
        ):  # Check codec if manifest type is missing
            is_mp4_stream = True
            logging.debug(f"Stream identified as MP4 based on codec: {stream.codec}.")

        if is_mp4_stream and path.lower().endswith(".flac"):
            original_path_flac = path  # Store the original intended .flac path
            # Explicit log for detecting Hi-Res FLAC in MP4 container scenario
            if q == AudioQuality.HI_RES_LOSSLESS:
                logging.info(
                    f"Detected HI-RES FLAC stream delivered in MP4 container for track '{track.title}'. Path correction applied."
                )
            path = path[:-5] + ".mp4"  # Correct path to .mp4 for download
            logging.warning(
                f"Corrected path extension to .mp4 for download based on stream type. Original: '{original_path_flac}', Corrected: '{path}'"
            )
        else:
            original_path_flac = path  # Store the original path if no correction needed
        # --- End Correction ---
        if SETTINGS.showTrackInfo and not SETTINGS.multiThread:
            Printf.track(cast(Track, track), stream)

        if userProgress is not None:
            logging.debug(
                f"[DL Track] Calling userProgress.updateStream after path correction for '{track.title}'"
            )
            userProgress.updateStream(stream)

        # check exist (again after potential path correction)
        if stream.url and __isSkip__(path, stream.url):
            Printf.success(aigpy.path.getFileName(path) + " (skip:already exists!)")
            logging.debug(
                f"File exists after path correction, skipping download for '{track.title}'"
            )
            return True, ""

        # Log the final path used for the download tool (including .part)
        actual_download_part_path = path + ".part"
        logging.info(
            f"[DL Track] Starting download for '{track.title}' to temporary file: '{actual_download_part_path}'"
        )
        logging.info(
            f"[DL Track] Source URL: {stream.url or ''}"
        )  # Log URL for reference
        logging.debug(f"Preparing to download '{track.title}'")

        # === Immediate Cancellation Check ===
        if main_view_instance.cancel_requested:
            Printf.info(
                f"Cancellation requested before starting download for '{track.title}'."
            )
            # The finally block will handle cleanup
            return False, "Download cancelled by user before start."
        # === End Cancellation Check ===

        # Use stream.urls if available; otherwise, fallback to [stream.url] (and ensure nonempty list)
        url_list = (
            stream.urls
            if stream.urls
            else ([stream.url] if stream.url is not None else [])
        )
        logging.debug(
            f"Created url_list for '{track.title}' with {len(url_list)} URL(s)"
        )
        if not url_list:
            raise Exception("No URL available for download")

        # *** TODO: Cancellation Check Point ***
        # Need to investigate if aigpy.download.DownloadTool can be cancelled.
        # If yes, pass main_view_instance.cancel_requested or an event to tool.start()
        # If no, immediate cancellation is not possible mid-download.
        tool = aigpy.download.DownloadTool(actual_download_part_path, url_list)
        tool.setUserProgress(userProgress)
        tool.setPartSize(partSize)
        logging.debug(
            f"[DL Track] Starting download tool for '{track.title}' with partSize {partSize}"
        )
        # Re-enable the tool's internal progress printing for GUI progress bar
        # Keep the tool's internal progress printing enabled (True) for console output
        # --- CHANGE THIS LINE ---
        check, err = tool.start(False)  # Set showProgress to False
        # --- END CHANGE ---
        logging.debug(
            f"[DL Track] Download tool returned for '{track.title}' with check={check}, err={err}"
        )
        if not check:
            # Error occurred during download, return False
            # The finally block will handle cleanup
            Printf.err(f"DL Track '{track.title}' failed: {str(err) if err else ''}")
            return False, str(err) if err else ""

        # --- Debugging before decryption ---
        part_path = actual_download_part_path  # Use the defined variable
        logging.debug(f"--- Debug Info: Before Decryption for '{track.title}' ---")
        try:
            if os.path.exists(part_path):
                with open(part_path, "rb") as f_part:
                    part_header = f_part.read(64)
                logging.debug(
                    f"File header of '{part_path}' (first 64 bytes, hex): {part_header.hex()}"
                )
                # Run ffprobe on the .part file
                log_ffprobe_info(part_path, "Before Decryption")
            else:
                logging.warning(f"Part file '{part_path}' not found before decryption.")
        except Exception as e:
            logging.error(
                f"Error getting debug info before decryption for '{part_path}': {e}"
            )
        # --- End Debugging before decryption ---

        # encrypted -> decrypt and remove encrypted file
        logging.debug(
            f"Starting decryption step for '{track.title}' (Input: '{part_path}', Output: '{path}')"
        )
        __encrypted__(stream, part_path, path)
        logging.debug(f"Decryption complete for '{track.title}'")

        # --- Debugging after decryption ---
        logging.debug(f"--- Debug Info: After Decryption for '{track.title}' ---")
        try:
            if os.path.exists(path):
                # Run ffprobe on the potentially decrypted file
                log_ffprobe_info(path, "After Decryption")
            else:
                logging.warning(
                    f"Output file '{path}' not found after decryption step."
                )
        except Exception as e:
            logging.error(f"Error running ffprobe after decryption for '{path}': {e}")
        # --- End Debugging after decryption ---

        # Add additional debug information: file size and file header (in hex) - This logs the header *again* after decryption, which is fine.
        try:
            file_size = os.path.getsize(path)
            with open(path, "rb") as f:
                header = f.read(64)
            logging.debug(f"Downloaded file '{path}' size: {file_size} bytes")
            logging.debug(f"File header (first 64 bytes, hex): {header.hex()}")
        except Exception as e:
            logging.debug(
                f"Failed to retrieve file header info for '{track.title}': {e}"
            )

        # Process quality conversion (Demuxing for HI_RES_LOSSLESS in MP4):
        # This needs to happen BEFORE Mutagen check if the original download was MP4
        # Check if the *resolved* quality for this track is HI_RES_LOSSLESS, not the global setting
        if q == AudioQuality.HI_RES_LOSSLESS:
            logging.debug(
                f"Resolved quality 'q' is HI_RES_LOSSLESS, proceeding with demux check for track '{track.title}'."
            )
            try:
                # Check the stream codec or mime type if available, or rely on file extension as fallback
                # For now, relying on the initial path extension determined by getTrackPath based on stream info
                # Use the potentially corrected 'path' variable here!
                if path.lower().endswith((".mp4", ".mov")):  # Check corrected path
                    # Use the stored original .flac path as the target for ffmpeg output
                    demuxed_path = original_path_flac

                    # --- Replacing ffmpeg with moviepy ---
                    logging.debug(
                        f"--- Using moviepy to extract FLAC from '{path}' to '{demuxed_path}' for '{track.title}' ---"
                    )
                    try:
                        logging.info(
                            f"Attempting moviepy audio extraction from '{path}' to '{demuxed_path}'..."
                        )
                        # Use VideoFileClip directly
                        with Clip(path) as video_clip:
                            audio_clip = video_clip.audio
                            if audio_clip:
                                # Write audio with FLAC codec
                                # Use logger=None to avoid moviepy's verbose console output if desired, or 'bar' for progress
                                audio_clip.write_audiofile(
                                    demuxed_path, codec="flac", logger="bar"
                                )
                                audio_clip.close()  # Explicitly close audio clip
                            else:
                                logging.error(
                                    f"Moviepy could not extract audio stream from '{path}'."
                                )
                                raise ValueError(
                                    f"No audio stream found in '{path}' by moviepy."
                                )

                        logging.info(
                            f"Moviepy audio extraction completed successfully for '{track.title}'."
                        )
                        os.remove(path)  # Remove original MP4
                        path = demuxed_path  # Update path to the new FLAC file
                        logging.debug(
                            f"Moviepy extraction complete. Path updated to '{path}'"
                        )

                        # --- Debugging after moviepy (ffprobe removed) ---
                        logging.debug(
                            f"--- Debug Info: After moviepy extraction for '{track.title}' ---"
                        )
                        # log_ffprobe_info(path, "After ffmpeg") # Removed ffprobe call
                        # --- End Debugging ---
                        # *** Use Mutagen to inspect the final audio file ***
                        # This runs AFTER successful moviepy extraction
                        try:
                            # Use the 'path' variable which points to the final file (demuxed .flac)
                            audio_info = MutagenFile(path)
                            if audio_info is not None:
                                logging.debug(
                                    f"Mutagen validation successful for '{track.title}' on file '{path}'. Info: {audio_info}"
                                )
                            else:
                                # If Mutagen returns None, the file is likely corrupt or not the expected format
                                logging.error(
                                    f"Mutagen could not parse file '{path}' after ffmpeg. It might be corrupt or an unexpected format."
                                )
                                raise ValueError(
                                    f"Mutagen could not parse file '{path}' after ffmpeg."
                                )
                        except Exception as e:
                            # Catch Mutagen-specific errors and other potential issues during validation
                            logging.error(
                                f"Mutagen inspection failed for '{track.title}' on file '{path}' after ffmpeg: {e}"
                            )
                            # Log the full traceback for detailed debugging
                            logging.error(traceback.format_exc())
                            # Re-raise the exception to halt the process if validation fails
                            raise ValueError(
                                f"Mutagen validation failed for '{path}' after ffmpeg"
                            ) from e

                    except Exception as moviepy_err:  # Catch moviepy/general errors
                        logging.error(
                            f"Moviepy audio extraction failed for '{track.title}': {moviepy_err}",
                            exc_info=True,
                        )
                        # Attempt to clean up potentially incomplete output file
                        if os.path.exists(demuxed_path):
                            try:
                                os.remove(demuxed_path)
                                logging.info(
                                    f"Cleaned up potentially incomplete output file: '{demuxed_path}'"
                                )
                            except OSError as remove_err:
                                logging.error(
                                    f"Failed to remove incomplete output file '{demuxed_path}': {remove_err}"
                                )
                        raise moviepy_err  # Re-raise to stop the process

                else:
                    logging.debug(
                        f"No moviepy processing needed for '{track.title}' (file was not .mp4/.mov)"
                    )
            except Exception as e:
                Printf.err(f"Demuxing of FLAC stream failed: {str(e)}")
                return False, str(e)

        # contributors
        try:
            # Add check for track.id before calling API
            if track.id is None:
                raise ValueError("Track ID is missing, cannot fetch contributors.")
            contributors = TIDAL_API.getTrackContributors(
                str(track.id)
            )  # Ensure ID is string
            logging.debug(f"Retrieved contributors for '{track.title}': {contributors}")
        except Exception as ex:
            logging.debug(f"Failed to retrieve contributors for '{track.title}': {ex}")
            contributors = None

        # lyrics
        try:
            # Add check for track.id before calling API
            if track.id is None:
                raise ValueError("Track ID is missing, cannot fetch lyrics.")
            lyrics = TIDAL_API.getLyrics(str(track.id)).subtitles  # Ensure ID is string
            logging.debug(f"Retrieved lyrics for '{track.title}'")
            if SETTINGS.lyricFile:
                lrcPath = path.rsplit(".", 1)[0] + ".lrc"
                aigpy.file.write(lrcPath, lyrics, "w")
                logging.debug(f"Written lyric file for '{track.title}' to {lrcPath}")
        except Exception as ex:
            # logging.debug(f"No lyrics available for '{track.title}': {ex}")
            lyrics = ""

        __setMetaData__(cast(Track, track), album, path, contributors, lyrics)
        Printf.success(track.title or f"Track {track.id}")
        logging.debug(
            f"[DL Track] Finished downloadTrack for '{track.title}' successfully"
        )

        return True, ""  # Return success

    except Exception as e:
        Printf.err(f"DL Track '{track.title}' failed: {str(e)}")
        logging.debug(f"Exception in downloadTrack for '{track.title}': {e}")
        return False, str(e)
    finally:
        # Cleanup: Remove .part file if download failed or was stopped/cancelled
        # Check if the download tool completed successfully AND no stop/cancel was requested
        check_defined_and_true = "check" in locals() and check
        interrupted = (
            main_view_instance.stop_requested or main_view_instance.cancel_requested
        )

        # Need the path to the .part file, ensure 'actual_download_part_path' is accessible
        if (
            "actual_download_part_path" in locals()
            and actual_download_part_path is not None
        ):
            part_file_to_remove = actual_download_part_path
            if not check_defined_and_true or interrupted:
                if os.path.exists(part_file_to_remove):
                    logging.warning(
                        f"Download incomplete or interrupted for '{track.title}'. Removing temporary file: {part_file_to_remove}"
                    )
                    try:
                        os.remove(part_file_to_remove)
                    except OSError as remove_err:
                        logging.error(
                            f"Failed to remove temporary file '{part_file_to_remove}': {remove_err}"
                        )


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
            "m4a - aac – high efficiency (96 kbps, 44.1 khz)": AudioQuality.LOW,
            "m4a - aac – full bandwidth (320 kbps, 44.1 khz)": AudioQuality.HIGH,
            "flac – cd standard (16-bit, 44.1 khz)": AudioQuality.LOSSLESS,
            "flac – high resolution (24-bit, 96 kbps)": AudioQuality.HI_RES_LOSSLESS,  # Corrected kHz typo
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
                logging.debug(
                    f"Filtering track {track.id}: Max Available Quality Enum={max_available_enum.name} (Rank {available_rank}), Filter Quality Enum={filter_quality.name if filter_quality else 'None'} (Rank {filter_rank})"
                )

                # Allow download if available quality rank is >= filter quality rank
                if filter_quality is not None and available_rank >= filter_rank:
                    logging.debug(
                        f"Track {track.id} passed quality filter (Available >= Requested)."
                    )
                    filtered_tracks.append(track)
                else:
                    # Provide more context in the log message when a track fails the filter
                    logging.debug(
                        f"Track {track.id} did NOT pass quality filter (Available Rank {available_rank} < Requested Rank {filter_rank})."
                    )
            except Exception as e:
                logging.warning(
                    f"Error during quality filtering for track {track.id}: {e}"
                )
                logging.error(
                    traceback.format_exc()
                )  # Add traceback for better debugging
                continue
        tracks = filtered_tracks
        logging.debug(
            f"Finished filtering tracks. Number of tracks remaining: {len(tracks)}"
        )
        if not tracks:
            logging.warning(
                "No tracks matched the specified quality filter. Nothing to download."
            )
            return  # Exit if no tracks are left

    # If downloadQuality was HIGHEST, the filtering above might be too strict or unnecessary
    # as downloadTrack already handles selecting the best available.
    # However, keeping the filter as corrected ensures consistency if user specifically requests e.g., LOSSLESS
    # and wants ONLY lossless tracks, even if HIRES is available.

    # Now, for each track that passes the quality filter, call downloadTrack with album forced as None.
    logging.debug(
        f"Proceeding to download {len(tracks)} tracks. MultiThread: {SETTINGS.multiThread}"
    )
    if not SETTINGS.multiThread:
        logging.debug("Using single-thread download.")
        for index, item in enumerate(tracks):
            # === Pause/Stop Check (Inside Loop) ===
            logging.debug(
                f"[Thread] Loop {index+1}/{len(tracks)}: Checking stop_event (is_set={main_view_instance.stop_event.is_set()})"
            )
            if main_view_instance.stop_event.is_set():
                Printf.info("Stop request detected. Aborting download queue.")
                break  # Exit the loop

            logging.debug(
                f"[Thread] Loop {index+1}/{len(tracks)}: Checking download_paused (is {main_view_instance.download_paused})"
            )
            if main_view_instance.download_paused:
                Printf.info(
                    "Download queue paused. Waiting for resume signal..."
                )  # Updated log
                logging.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Emitting pause confirmation signal."
                )
                main_view_instance.signal_actually_paused.emit()
                logging.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Calling pause_event.wait() (event is_set={main_view_instance.pause_event.is_set()})"
                )
                main_view_instance.pause_event.wait()  # Wait for GUI to set() the event
                logging.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Returned from pause_event.wait() (event is_set={main_view_instance.pause_event.is_set()})"
                )
                Printf.info("Download queue resumed.")  # Updated log
                # Re-check stop after pause
                logging.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Re-checking stop_event after pause (is_set={main_view_instance.stop_event.is_set()})"
                )
                if main_view_instance.stop_event.is_set():
                    Printf.info(
                        "Stop request detected after pause. Aborting download queue."
                    )
                    break
            # === End Pause/Stop Check ===

            logging.debug(
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
