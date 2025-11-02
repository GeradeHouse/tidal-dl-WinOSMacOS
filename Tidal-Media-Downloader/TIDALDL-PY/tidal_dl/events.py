#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :  events.py
@Date    :  2022/06/10
@Author  :  Yaronzz
@Modified by: GeradeHouse
@Version :  1.0
@Contact :  yaronhuang@foxmail.com
@Modified by: GeradeHouse
@Desc    :
"""
import threading
import time
import os
from typing import Dict, cast, Any, Union, List
import logging

import aigpy
import tidal_dl.settings as settings
from .settings import SETTINGS, TOKEN
from .download import downloadAlbumInfo, downloadCover, downloadTrack, downloadTracks
from .tidal import TIDAL_API
from . import apiKey
from .printf import Printf
from .enums import Type, AudioQuality
from .model import Album, Track, Artist, Playlist, Mix

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (event operations)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class DummyMainView:
    """
    Dummy class to satisfy the main_view_instance parameter in download
    functions for CLI usage. This prevents the need to pull in the entire
    GUI framework for command-line operations.
    """

    def __init__(self):
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()  # Start in non-paused state
        self.download_paused = False
        self.stop_requested = False
        self.cancel_requested = False
        # A dummy signal object with an emit method that does nothing
        self.signal_actually_paused = type(
            "DummySignal", (object,), {"emit": lambda: None}
        )()


dummy_view = cast(Any, DummyMainView())

"""
=================================
START DOWNLOAD
=================================
"""


def start_album(obj: Album):
    logger.info(f"Album: {obj.title} (ID: {obj.id}, Tracks: {obj.numberOfTracks}, Videos: {obj.numberOfVideos})")
    if obj.id is None:
        logger.error("Album has no ID.")
        return
    tracks, _ = TIDAL_API.getItems(str(obj.id), Type.Album)
    if SETTINGS.saveAlbumInfo:
        downloadAlbumInfo(obj, tracks)
    if SETTINGS.saveCovers and obj.cover is not None:
        downloadCover(obj)
    downloadTracks(
        tracks, dummy_view, album=obj, downloadQuality=SETTINGS.audioQuality.name
    )


def start_track(obj: Track):
    if obj.album is None or obj.album.id is None:
        logger.error("Track's album has no ID.")
        return
    album = TIDAL_API.getAlbum(str(obj.album.id))
    if SETTINGS.saveCovers:
        downloadCover(album)
    downloadTrack(
        obj, dummy_view, album=album, downloadQuality=SETTINGS.audioQuality.name
    )


def start_artist(obj: Artist):
    if obj.id is None:
        logger.error("Artist has no ID.")
        return
    albums = TIDAL_API.getArtistAlbums(str(obj.id), SETTINGS.includeEP)
    logger.info(f"Artist: {obj.name} (ID: {obj.id}, Album count: {len(albums)})")
    for item in albums:
        start_album(item)


def start_playlist(obj: Playlist):
    logger.info(f"Playlist: {obj.title} (UUID: {obj.uuid}, Tracks: {obj.numberOfTracks}, Videos: {obj.numberOfVideos})")
    if obj.uuid is None:
        logger.error("Playlist has no UUID.")
        return
    tracks, _ = TIDAL_API.getItems(str(obj.uuid), Type.Playlist)
    downloadTracks(
        tracks,
        dummy_view,
        playlist_context=obj,
        downloadQuality=SETTINGS.audioQuality.name,
    )


def start_mix(obj: Mix):
    logger.info(f"Mix: ID={obj.id}, Track count={len(obj.tracks) if hasattr(obj, 'tracks') and isinstance(obj.tracks, list) else 0}")
    downloadTracks(
        cast(List[Track], obj.tracks),
        dummy_view,
        downloadQuality=SETTINGS.audioQuality.name,
    )


def start_file(string: str):
    txt = aigpy.file.getContent(string)
    if aigpy.string.isNull(txt):
        logger.error("Nothing can read!")
        return
    array = txt.split("\n")
    for item in array:
        if aigpy.string.isNull(item):
            continue
        if item[0] == "#":
            continue
        if item[0] == "[":
            continue
        start(item)


def _dispatch_start(etype: Type, obj: Any):
    if etype == Type.Album:
        start_album(obj)
    elif etype == Type.Track:
        start_track(obj)
    elif etype == Type.Artist:
        start_artist(obj)
    elif etype == Type.Playlist:
        start_playlist(obj)
    elif etype == Type.Mix:
        start_mix(obj)


def start(string: str):
    if aigpy.string.isNull(string):
        logger.error("Please enter something.")
        return

    strings = string.split(" ")
    for item in strings:
        if aigpy.string.isNull(item):
            continue
        if os.path.exists(item):
            start_file(item)
            return

        try:
            etype, obj = TIDAL_API.getByString(item)
        except Exception as e:
            logger.error(str(e) + " [" + item + "]")
            return

        try:
            _dispatch_start(etype, obj)
        except Exception as e:
            logger.error(str(e))


"""
=================================
CHANGE SETTINGS
=================================
"""


def changePathSettings():
    """
    Removed: CLI-only path settings function.
    GUI handles path settings through the settings interface.
    """
    logger.info("Path settings configuration is handled by the GUI settings interface.")


def changeQualitySettings():
    """
    Removed: CLI-only quality settings function.
    GUI handles audio quality selection through the settings interface.
    """
    logger.info("Audio quality settings are handled by the GUI settings interface.")


def changeSettings():
    """
    Removed: CLI-only settings function.
    GUI handles all settings through the settings interface.
    """
    logger.info("General settings configuration is handled by the GUI settings interface.")


def changeApiKey():
    # Use `cast` to resolve Pylance's type inference issue with apiKey.getItem
    item = cast(Dict[str, str], apiKey.getItem(SETTINGS.apiKeyIndex))
    ver = apiKey.getVersion()

    logger.info(
        f'Current APIKeys: {str(SETTINGS.apiKeyIndex)} {item["platform"]}-{item["formats"]}'
    )
    logger.info(f"Current Version: {str(ver)}")
    logger.info(f"Available API Keys: {len(apiKey.getItems())} keys")
    logger.info("API key selection is handled by the GUI settings interface.")
    return False


"""
=================================
LOGIN
=================================
"""


def __displayTime__(seconds: Union[int, float, str], granularity: int = 2) -> str:
    if not isinstance(seconds, (int, float)):
        try:
            seconds = int(seconds)
        except (ValueError, TypeError):
            return "unknown"

    if seconds <= 0:
        return "unknown"

    result = []
    intervals = (
        ("weeks", 604800),
        ("days", 86400),
        ("hours", 3600),
        ("minutes", 60),
        ("seconds", 1),
    )

    for name, count in intervals:
        value = seconds // count
        if value:
            seconds -= value * count
            if value == 1:
                name = name.rstrip("s")
            result.append(f"{int(value)} {name}")
    return ", ".join(result[:granularity])


def getLoginUrl():
    """
    Gets the device authorization URL from Tidal.
    Does not perform polling.
    """
    try:
        url = TIDAL_API.getDeviceCode()
        return url
    except Exception as e:
        logger.error(f"Could not retrieve login URL: {str(e)}")
        return None


def pollForToken():
    """
    Polls Tidal to check for successful device authorization.
    This is a blocking operation.
    """
    try:
        start_time = time.time()
        timeout = TIDAL_API.key.authCheckTimeout

        while time.time() - start_time < timeout:
            status = TIDAL_API.checkAuthStatus()
            if status == "SUCCESS":
                Printf.success(
                    settings.LANG.MSG_VALID_ACCESSTOKEN.format(  # type: ignore
                        __displayTime__(int(TIDAL_API.key.expiresIn))
                    )
                )

                # Save the new token details
                TOKEN.userid = TIDAL_API.key.userId
                TOKEN.countryCode = TIDAL_API.key.countryCode
                TOKEN.accessToken = TIDAL_API.key.accessToken
                TOKEN.refreshToken = TIDAL_API.key.refreshToken
                TOKEN.expiresAfter = int(time.time()) + int(TIDAL_API.key.expiresIn)
                TOKEN.save()
                return True
            elif status in ("PENDING", "SLOW_DOWN"):
                time.sleep(TIDAL_API.key.authCheckInterval)
            else:
                # This case shouldn't be reached if checkAuthStatus raises exceptions for other errors
                time.sleep(TIDAL_API.key.authCheckInterval)

        raise Exception(settings.LANG.AUTH_TIMEOUT)  # type: ignore
    except Exception as e:
        logger.error(f"Authentication polling failed: {str(e)}")
        return False


def loginByWeb():
    """
    Orchestrates the web login flow for command-line usage.
    """
    try:
        print(settings.LANG.AUTH_START_LOGIN)  # type: ignore
        url = getLoginUrl()
        if not url:
            return False

        print(
            settings.LANG.AUTH_NEXT_STEP.format(  # type: ignore
                aigpy.cmd.green(url),
                aigpy.cmd.yellow(__displayTime__(TIDAL_API.key.authCheckTimeout)),
            )
        )
        print(settings.LANG.AUTH_WAITING)  # type: ignore

        return pollForToken()

    except Exception as e:
        logger.error(f"Login failed: {str(e)}")
        return False


def loginByConfig():
    try:
        if not TOKEN.accessToken:
            return False

        if TIDAL_API.verifyAccessToken(TOKEN.accessToken):
            logger.info(
                settings.LANG.MSG_VALID_ACCESSTOKEN.format(  # type: ignore
                    __displayTime__(int(TOKEN.expiresAfter - time.time()))
                )
            )

            TIDAL_API.key.countryCode = TOKEN.countryCode
            TIDAL_API.key.userId = TOKEN.userid
            TIDAL_API.key.accessToken = TOKEN.accessToken
            return True

        logger.info(settings.LANG.MSG_INVALID_ACCESSTOKEN)  # type: ignore
        if TOKEN.refreshToken and TIDAL_API.refreshAccessToken(TOKEN.refreshToken):
            Printf.success(
                settings.LANG.MSG_VALID_ACCESSTOKEN.format(  # type: ignore
                    __displayTime__(int(TIDAL_API.key.expiresIn))
                )
            )

            TOKEN.userid = TIDAL_API.key.userId
            TOKEN.countryCode = TIDAL_API.key.countryCode
            # FIX: Suppress Pylance error due to incorrect type inference on TOKEN
            TOKEN.accessToken = TIDAL_API.key.accessToken  # type: ignore
            # FIX: Add missing refresh token update & suppress Pylance error
            TOKEN.refreshToken = TIDAL_API.key.refreshToken  # type: ignore
            TOKEN.expiresAfter = int(time.time() + int(TIDAL_API.key.expiresIn))
            TOKEN.save()
            return True
        else:
            TOKEN.save()
            return False
    except Exception:
        return False


def loginByAccessToken():
    """
    Removed: CLI-only manual token entry function.
    GUI handles authentication via web-based flow and GUI dialogs.
    """
    logger.warning("CLI manual token entry is not available in GUI mode.")
    return False
