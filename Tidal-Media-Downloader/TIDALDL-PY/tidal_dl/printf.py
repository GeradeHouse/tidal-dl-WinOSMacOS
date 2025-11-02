#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   printf.py
@Time    :   2020/08/16
@Author  :   Yaronzz
@Modified by: GeradeHouse
@Version :   3.0
@Contact :   yaronhuang@foxmail.com
@Desc    :   Core utility module providing GUI formatting functions for the Tidal Media Downloader.
This module contains essential utility functions for:
- Quality mapping and track quality analysis
- Duration formatting for display
- Table creation utilities for GUI components
The module has been cleaned up to remove legacy CLI-only code and now focuses solely on GUI utilities.
"""

from . import settings

from pickle import GLOBAL
import threading
import aigpy

aigpy.cmd.init(autoreset=True)  # Attempt to explicitly enable/initialize color output
import logging
import prettytable
from .model import Track
from typing import Optional, List, Dict, Any
import logging

from . import apiKey

from .model import *
from .paths import getProfilePath
from .lang.language import *
from .enums import AudioQuality




print_mutex = threading.Lock()

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (more verbose GUI output for downloads)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class Printf(object):
    @staticmethod
    def formatDuration(seconds) -> str:
        """
        Formats a duration given in seconds into a mm:ss string.

        Args:
            seconds: The duration in seconds (can be string or number).

        Returns:
            str: The formatted duration string (e.g., "3:45") or an empty
                 string if formatting fails.
        """
        try:
            seconds_int = int(seconds)
            m, s = divmod(seconds_int, 60)
            return f"{m}:{s:02d}"
        except (ValueError, TypeError):
            # Handle cases where conversion to int fails or input is invalid
            return ""

    @staticmethod
    def map_quality(item):
        """Handle both AudioQuality enum and Track object inputs"""
        if isinstance(item, AudioQuality):
            return Printf.map_quality_enum(item)
        return Printf.map_track_quality(item)

    @staticmethod
    def map_track_quality(track):
        """Handle track objects with metadata check, ensuring proper attribute values."""
        # Validate track type first
        if not isinstance(track, Track):
            logger.error(
                f"Invalid track type passed to map_track_quality: {type(track)}"
            )
            return "Unknown Quality"

        # Safer title access with multiple fallbacks
        track_id = getattr(track, "id", "unknown")
        track_title = "unknown title"

        try:
            # First try direct attribute access
            title_accessor = track.title
            if callable(title_accessor):
                track_title = title_accessor()
            else:
                track_title = title_accessor
        except AttributeError:
            pass

        if not isinstance(track_title, str):
            # Final fallback to __dict__ with safety check
            track_title = (
                track.__dict__.get("title", "unknown title")
                if hasattr(track, "__dict__")
                else "unknown title"
            )
        # logger.debug(f"\n=== Quality analysis for track {track_id} '{track_title}' ===")

        # Get mediaMetadata with type checking
        media_metadata = getattr(track, "mediaMetadata", {})
        # logger.debug(f"MediaMetadata type: {type(media_metadata)}")
        # logger.debug(f"Raw mediaMetadata: {repr(media_metadata)}")

        # Extract tags with detailed type checking
        if isinstance(media_metadata, dict):
            tags = media_metadata.get("tags", [])
            # logger.debug("Extracted tags from dictionary")
        else:
            tags = getattr(media_metadata, "tags", [])
            # logger.debug("Extracted tags from object attribute")

        # logger.debug(f"Tags type: {type(tags)}")
        # logger.debug("Individual tag checks:")
        for i, tag in enumerate(tags):
            tag_type = type(tag).__name__
            normalized_tag = tag.upper() if isinstance(tag, str) else str(tag)
            # logger.debug(f"  Tag {i}: {tag} (type: {tag_type}) -> Normalized: {normalized_tag}")

        # Check for HIRES_LOSSLESS with detailed logging
        hires_detected = False
        for tag in tags:
            if isinstance(tag, str) and tag.upper() == "HIRES_LOSSLESS":
                hires_detected = True
                # logger.debug("MATCHED HIRES_LOSSLESS tag!")
                break

        # Handle audioQuality attribute properly
        audio_quality_attr = getattr(track, "audioQuality", None)
        if callable(audio_quality_attr):
            # logger.debug("track.audioQuality is callable; retrieving from __dict__")
            audio_quality_attr = track.__dict__.get("audioQuality", None)
        final_quality = (
            Printf.map_quality_enum(audio_quality_attr)
            if not hires_detected
            else "FLAC – High Resolution (24-bit, 96 kHz)"
        )

        # logger.debug(f"Final quality decision based on metadata: {'HIRES' if hires_detected else 'Standard'} -> {final_quality}")
        # logger.debug(f"Track's base 'audioQuality' attribute from API: {audio_quality_attr}") # Clarified log
        # logger.debug("=== End of quality analysis ===\n")

        return final_quality

    @staticmethod
    def map_quality_enum(q):
        """Map AudioQuality enum to string"""
        quality_map = {
            AudioQuality.LOW: "M4a - AAC – High Efficiency (96 kbps, 44.1 kHz)",
            AudioQuality.HIGH: "M4a - AAC – Full Bandwidth (320 kbps, 44.1 kHz)",
            AudioQuality.LOSSLESS: "FLAC – CD Standard (16-bit, 44.1 kHz)",
            AudioQuality.HI_RES_LOSSLESS: "FLAC – High Resolution (24-bit, 96 kHz)",
            AudioQuality.HIGHEST: "Highest available",
            AudioQuality.MP3: "MP3 - Constant Bitrate (320 kbps, 44.1 kHz)",
        }
        if isinstance(q, str):
            try:
                # Attempt to convert string to enum member by name
                q_enum = AudioQuality[q.upper()]
                return quality_map.get(q_enum, q)
            except KeyError:
                return q  # Return original string if it's not a valid enum name
        return quality_map.get(q, "Unknown")

    

    @staticmethod
    def __gettable__(columns, rows):
        tb = prettytable.PrettyTable()
        tb.field_names = list(aigpy.cmd.green(item) for item in columns)
        tb.align = "l"
        for item in rows:
            tb.add_row(item)
        return tb

    

    

    

    

    @staticmethod
    def enter(string: str) -> str:
        print(aigpy.cmd.yellow(string))
        ret = input("")
        return ret

    @staticmethod
    def enterBool(string: str) -> bool:
        print(aigpy.cmd.yellow(string))
        ret = input("")
        return ret == "1"

    @staticmethod
    def enterPath(
        string: str, errmsg: str, retWord: str = "0", default: str = ""
    ) -> str:
        while True:
            ret = aigpy.cmd.inputPath(aigpy.cmd.yellow(string), retWord)
            if ret == retWord:
                return default
            elif ret == "":
                print(aigpy.cmd.red(f"{settings.LANG.PRINT_ERR} ") + errmsg)
            else:
                break
        return ret

    @staticmethod
    def enterLimit(string: str, errmsg: str, limit: list) -> str:
        while True:
            ret = aigpy.cmd.inputLimit(aigpy.cmd.yellow(string), limit)
            if ret is None:
                print(aigpy.cmd.red(f"{settings.LANG.PRINT_ERR} ") + errmsg)
            else:
                break
        return ret

    @staticmethod
    def enterFormat(string: str, current: str, default: str) -> str:
        ret = Printf.enter(string)
        if ret == "0" or aigpy.string.isNull(ret):
            return current
        if ret.lower() == "default":
            return default
        return ret

    @staticmethod
    def err(string: str):
        # ERROR messages should always be shown (they are critical)
        if logging.getLogger().isEnabledFor(logging.ERROR):
            from .settings import LANG

            # ANSI escape codes (redefined here for clarity, could also import)
            COLOR_RED = "\033[91m"
            COLOR_RESET = "\033[0m"

            global print_mutex
            print_mutex.acquire()
            # Apply ANSI color codes directly, bypassing aigpy.cmd.red()
            print(f"{COLOR_RED}{settings.LANG.PRINT_ERR} {string}{COLOR_RESET}")
            # logger.error(string) # Keep this commented out to avoid duplicate logs
            print_mutex.release()

    @staticmethod
    def warning(string: str):
        # Check if WARNING level logging is enabled before printing
        if logging.getLogger().isEnabledFor(logging.WARNING):
            from . import settings  # Import settings locally

            global print_mutex
            print_mutex.acquire()
            # Use yellow color for warnings, referencing the new LANG variable
            print(aigpy.cmd.yellow(f"{settings.LANG.PRINT_WARNING} ") + string)
            # Optional: Add logger.warning(string) here if needed for log files
            print_mutex.release()

    @staticmethod
    def info(string: str):
        # Check if INFO level logging is enabled before printing
        if logging.getLogger().isEnabledFor(logging.INFO):
            global print_mutex
            print_mutex.acquire()
            print(aigpy.cmd.blue(f"{settings.LANG.PRINT_INFO} ") + string)
            print_mutex.release()

    @staticmethod
    def success(string: str):
        # SUCCESS messages should be more visible - show them if either:
        # 1. Root logger is at INFO level or lower (usual case for user-visible messages)
        # 2. The SUCCESS message is critical (login, authentication, etc.)
        is_critical_success = any(keyword in string.lower() for keyword in [
            'login successful', 'authentication', 'token', 'credential', 
            'connected', 'authenticated', 'logged in'
        ])
        
        # Show if it's a critical success message OR if INFO logging is generally enabled
        if is_critical_success or logging.getLogger().isEnabledFor(logging.INFO):
            global print_mutex
            print_mutex.acquire()
            print(aigpy.cmd.green(f"{settings.LANG.PRINT_SUCCESS} ") + string)
            print_mutex.release()

    @staticmethod
    def album(data: Album):
        tb = Printf.__gettable__(
            [settings.LANG.MODEL_ALBUM_PROPERTY, settings.LANG.VALUE],
            [
                [settings.LANG.MODEL_TITLE, data.title],
                ["ID", data.id],
                [settings.LANG.MODEL_TRACK_NUMBER, data.numberOfTracks],
                [settings.LANG.MODEL_VIDEO_NUMBER, data.numberOfVideos],
                [settings.LANG.MODEL_RELEASE_DATE, data.releaseDate],
                [settings.LANG.MODEL_VERSION, data.version],
                [settings.LANG.MODEL_EXPLICIT, data.explicit],
            ],
        )
        print(tb)
        logger.info(
            "====album "
            + str(data.id)
            + "====\n"
            + f"title:{data.title}\n"
            + f"track num:{data.numberOfTracks}\n"
            + "=================================="
        )

    @staticmethod
    def track(data: Track, stream: Optional[StreamUrl] = None):
        tb = Printf.__gettable__(
            [settings.LANG.MODEL_TRACK_PROPERTY, settings.LANG.VALUE],
            [
                [settings.LANG.MODEL_TITLE, data.title],
                ["ID", data.id],
                [settings.LANG.MODEL_ALBUM, data.album.title],
                [settings.LANG.MODEL_VERSION, data.version],
                [settings.LANG.MODEL_EXPLICIT, data.explicit],
                # Use map_quality_enum for the attribute, map_track_quality for the object analysis
                ["Max-Q", Printf.map_track_quality(data)],
            ],
        )
        if stream is not None and stream.soundQuality is not None:
            # Use map_quality_enum for the stream quality attribute
            retrieved_quality_str = Printf.map_quality_enum(stream.soundQuality)
            tb.add_row(["Get-Q", retrieved_quality_str])
            # Compare the *retrieved* quality string to determine codec display
            if retrieved_quality_str == "FLAC – High Resolution (24-bit, 96 kHz)":
                tb.add_row(["Get-Codec", "flac"])  # Assuming HI_RES_LOSELESS is always FLAC
            else:
                tb.add_row(["Get-Codec", str(stream.codec)])
        print(tb)
        logger.info(
            "====track "
            + str(data.id)
            + "====\n"
            + f"title:{data.title}\n"
            + f"version:{str(data.version)}\n"
            + "=================================="
        )

    @staticmethod
    def artist(data: Artist, num: int):
        tb = Printf.__gettable__(
            [settings.LANG.MODEL_ARTIST_PROPERTY, settings.LANG.VALUE],
            [
                [settings.LANG.MODEL_ID, data.id],
                [settings.LANG.MODEL_NAME, data.name],
                ["Number of albums", num],
                [settings.LANG.MODEL_TYPE, str(data.type)],
            ],
        )
        print(tb)
        logger.info(
            "====artist "
            + str(data.id)
            + "====\n"
            + f"name:{data.name}\n"
            + f"album num:{num}\n"
            + "=================================="
        )

    @staticmethod
    def playlist(data: Playlist):
        tb = Printf.__gettable__(
            [settings.LANG.MODEL_PLAYLIST_PROPERTY, settings.LANG.VALUE],
            [
                [settings.LANG.MODEL_TITLE, data.title],
                [settings.LANG.MODEL_TRACK_NUMBER, data.numberOfTracks],
                [settings.LANG.MODEL_VIDEO_NUMBER, data.numberOfVideos],
            ],
        )
        print(tb)
        logger.info(
            "====playlist "
            + str(data.uuid)
            + "====\n"
            + f"title:{data.title}\n"
            + f"track num:{data.numberOfTracks}\n"
            + "=================================="
        )

    @staticmethod
    def mix(data: Mix):
        tb = Printf.__gettable__(
            [settings.LANG.MODEL_PLAYLIST_PROPERTY, settings.LANG.VALUE],
            [
                [settings.LANG.MODEL_ID, data.id],
                [
                    settings.LANG.MODEL_TRACK_NUMBER,
                    (
                        len(data.tracks)
                        if hasattr(data, "tracks") and isinstance(data.tracks, list)
                        else 0
                    ),
                ],
            ],
        )
        print(tb)
        logger.info(
            "====Mix "
            + str(data.id)
            + "====\n"
            + f"track num:{len(data.tracks) if hasattr(data, 'tracks') and isinstance(data.tracks, list) else 0}\n"
            + "=================================="
        )

    @staticmethod
    def apikeys(items: List[Dict[str, str]]):
        print("-------------API-KEYS---------------")
        tb = prettytable.PrettyTable()
        tb.field_names = [
            aigpy.cmd.green("Index"),
            aigpy.cmd.green("Valid"),
            aigpy.cmd.green("Platform"),
            aigpy.cmd.green("Formats"),
        ]
        tb.align = "l"

        for index, item in enumerate(items):
            tb.add_row(
                [
                    str(index),
                    (
                        aigpy.cmd.green("True")
                        if item.get("valid") == "True"
                        else aigpy.cmd.red("False")
                    ),
                    item.get("platform", ""),
                    item.get("formats", ""),
                ]
            )
        print(tb)