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

import aigpy
import tidal_dl.settings as settings
from .settings import SETTINGS, TOKEN
from .download import downloadAlbumInfo, downloadCover, downloadTrack, downloadTracks
from .tidal import TIDAL_API
from . import apiKey
from .printf import Printf
from .enums import Type, AudioQuality
from .model import Album, Track, Artist, Playlist, Mix


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
    Printf.album(obj)
    if obj.id is None:
        Printf.err("Album has no ID.")
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
        Printf.err("Track's album has no ID.")
        return
    album = TIDAL_API.getAlbum(str(obj.album.id))
    if SETTINGS.saveCovers:
        downloadCover(album)
    downloadTrack(
        obj, dummy_view, album=album, downloadQuality=SETTINGS.audioQuality.name
    )


def start_artist(obj: Artist):
    if obj.id is None:
        Printf.err("Artist has no ID.")
        return
    albums = TIDAL_API.getArtistAlbums(str(obj.id), SETTINGS.includeEP)
    Printf.artist(obj, len(albums))
    for item in albums:
        start_album(item)


def start_playlist(obj: Playlist):
    Printf.playlist(obj)
    if obj.uuid is None:
        Printf.err("Playlist has no UUID.")
        return
    tracks, _ = TIDAL_API.getItems(str(obj.uuid), Type.Playlist)
    downloadTracks(
        tracks,
        dummy_view,
        playlist_context=obj,
        downloadQuality=SETTINGS.audioQuality.name,
    )


def start_mix(obj: Mix):
    Printf.mix(obj)
    downloadTracks(
        cast(List[Track], obj.tracks),
        dummy_view,
        downloadQuality=SETTINGS.audioQuality.name,
    )


def start_file(string: str):
    txt = aigpy.file.getContent(string)
    if aigpy.string.isNull(txt):
        Printf.err("Nothing can read!")
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
        Printf.err("Please enter something.")
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
            Printf.err(str(e) + " [" + item + "]")
            return

        try:
            _dispatch_start(etype, obj)
        except Exception as e:
            Printf.err(str(e))


"""
=================================
CHANGE SETTINGS
=================================
"""


def changePathSettings():
    Printf.settings()
    SETTINGS.downloadPath = Printf.enterPath(
        settings.LANG.CHANGE_DOWNLOAD_PATH,  # type: ignore
        settings.LANG.MSG_PATH_ERR,  # type: ignore
        "0",
        SETTINGS.downloadPath,
    )
    SETTINGS.albumFolderFormat = Printf.enterFormat(
        settings.LANG.CHANGE_ALBUM_FOLDER_FORMAT,  # type: ignore
        SETTINGS.albumFolderFormat,
        SETTINGS.getDefaultPathFormat(Type.Album),
    )
    SETTINGS.playlistFolderFormat = Printf.enterFormat(
        settings.LANG.CHANGE_PLAYLIST_FOLDER_FORMAT,  # type: ignore
        SETTINGS.playlistFolderFormat,
        SETTINGS.getDefaultPathFormat(Type.Playlist),
    )
    SETTINGS.trackFileFormat = Printf.enterFormat(
        settings.LANG.CHANGE_TRACK_FILE_FORMAT,  # type: ignore
        SETTINGS.trackFileFormat,
        SETTINGS.getDefaultPathFormat(Type.Track),
    )
    SETTINGS.save()


def changeQualitySettings():
    Printf.settings()
    SETTINGS.audioQuality = AudioQuality(
        int(
            Printf.enterLimit(
                settings.LANG.CHANGE_AUDIO_QUALITY,  # type: ignore
                settings.LANG.MSG_INPUT_ERR,  # type: ignore
                ["0", "1", "2", "3", "4"],
            )
        )
    )
    SETTINGS.save()


def changeSettings():
    Printf.settings()
    SETTINGS.showProgress = Printf.enterBool(settings.LANG.CHANGE_SHOW_PROGRESS)  # type: ignore
    SETTINGS.showTrackInfo = Printf.enterBool(settings.LANG.CHANGE_SHOW_TRACKINFO)  # type: ignore
    SETTINGS.checkExist = Printf.enterBool(settings.LANG.CHANGE_CHECK_EXIST)  # type: ignore
    SETTINGS.includeEP = Printf.enterBool(settings.LANG.CHANGE_INCLUDE_EP)  # type: ignore
    SETTINGS.saveCovers = Printf.enterBool(settings.LANG.CHANGE_SAVE_COVERS)  # type: ignore
    SETTINGS.saveAlbumInfo = Printf.enterBool(settings.LANG.CHANGE_SAVE_ALBUM_INFO)  # type: ignore
    SETTINGS.lyricFile = Printf.enterBool(settings.LANG.CHANGE_ADD_LRC_FILE)  # type: ignore
    SETTINGS.multiThread = Printf.enterBool(settings.LANG.CHANGE_MULITHREAD_DOWNLOAD)  # type: ignore
    SETTINGS.usePlaylistFolder = Printf.enterBool(settings.LANG.SETTING_USE_PLAYLIST_FOLDER + "('0'-No,'1'-Yes):")  # type: ignore
    SETTINGS.downloadDelay = Printf.enterBool(settings.LANG.CHANGE_USE_DOWNLOAD_DELAY)  # type: ignore
    SETTINGS.language = Printf.enter(
        settings.LANG.CHANGE_LANGUAGE
        + "("
        + settings.LANG.getLangChoicePrint()
        + "):"
    )  # type: ignore
    settings.LANG.setLang(SETTINGS.language)  # type: ignore
    SETTINGS.save()


def changeApiKey():
    # Use `cast` to resolve Pylance's type inference issue with apiKey.getItem
    item = cast(Dict[str, str], apiKey.getItem(SETTINGS.apiKeyIndex))
    ver = apiKey.getVersion()

    Printf.info(
        f'Current APIKeys: {str(SETTINGS.apiKeyIndex)} {item["platform"]}-{item["formats"]}'
    )
    Printf.info(f"Current Version: {str(ver)}")
    Printf.apikeys(apiKey.getItems())
    index = int(Printf.enterLimit("APIKEY index:", settings.LANG.MSG_INPUT_ERR, apiKey.getLimitIndexs()))  # type: ignore

    if index != SETTINGS.apiKeyIndex:
        SETTINGS.apiKeyIndex = index
        SETTINGS.save()
        TIDAL_API.apiKey = cast(Dict[str, str], apiKey.getItem(index))
        return True
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
        Printf.err(f"Could not retrieve login URL: {str(e)}")
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
        Printf.err(f"Authentication polling failed: {str(e)}")
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
        Printf.err(f"Login failed: {str(e)}")
        return False


def loginByConfig():
    try:
        if not TOKEN.accessToken:
            return False

        if TIDAL_API.verifyAccessToken(TOKEN.accessToken):
            Printf.info(
                settings.LANG.MSG_VALID_ACCESSTOKEN.format(  # type: ignore
                    __displayTime__(int(TOKEN.expiresAfter - time.time()))
                )
            )

            TIDAL_API.key.countryCode = TOKEN.countryCode
            TIDAL_API.key.userId = TOKEN.userid
            TIDAL_API.key.accessToken = TOKEN.accessToken
            return True

        Printf.info(settings.LANG.MSG_INVALID_ACCESSTOKEN)  # type: ignore
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
    try:
        print("-------------AccessToken---------------")
        token = Printf.enter("accessToken('0' go back):")
        if token == "0":
            return
        TIDAL_API.loginByAccessToken(token, TOKEN.userid)
    except Exception as e:
        Printf.err(str(e))
        return

    print("-------------RefreshToken---------------")
    refreshToken = Printf.enter("refreshToken('0' to skip):")
    if refreshToken == "0":
        refreshToken = TOKEN.refreshToken

    # FIX: Suppress Pylance error due to incorrect type inference on TOKEN
    TOKEN.accessToken = token  # type: ignore
    TOKEN.refreshToken = refreshToken  # type: ignore
    TOKEN.expiresAfter = 0
    TOKEN.countryCode = TIDAL_API.key.countryCode
    TOKEN.save()
