#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   settings.py
@Time    :   2020/11/08
@Author  :   Yaronzz
@Modified by: GeradeHouse
@Version :   3.0
@Contact :   yaronhuang@foxmail.com
@Desc    :
"""
import base64
import json
import sys
from typing import Optional, Union

import aigpy

from . import enums
from .lang.language import getLang
from .paths import get_default_download_path


def _get_initial_download_path() -> str:
    if sys.platform == "win32":
        return get_default_download_path()
    return "./Downloads/"


def _get_default_path_format(type: enums.Type) -> str:
    if sys.platform == "win32":
        if type == enums.Type.Album:
            return R"{ArtistName}\{AlbumTitle} [{AlbumID}] [{AlbumYear}]"
        if type == enums.Type.Playlist:
            return R"Playlists/{PlaylistName}"
        if type == enums.Type.Track:
            return R"{ArtistName} - {TrackTitle}"

    if type == enums.Type.Album:
        return R"{ArtistName}/{Flag} {AlbumTitle} [{AlbumID}] [{AlbumYear}]"
    if type == enums.Type.Playlist:
        return R"Playlists/{PlaylistName} [{PlaylistUUID}]"
    if type == enums.Type.Track:
        return R"{TrackNumber} - {ArtistName} - {TrackTitle}{ExplicitFlag}"
    return ""


class Settings(aigpy.model.ModelBase):
    checkExist = True
    includeEP = True
    saveCovers = True
    language = "english"
    lyricFile = True
    # Default API key to use on *first run only* (when settings.json doesn't exist yet).
    # Use the first bundled profile unless settings.json contains a saved user choice.
    # NOTE: If a settings.json is present, its value takes precedence and this
    #       does NOT overwrite the user's saved choice.
    apiKeyIndex = 0
    showProgress = True
    showTrackInfo = True
    saveAlbumInfo = False
    fontSize = 9
    multiThread = False
    downloadDelay = True
    autoSpotifyLogin = False
    spotifyUsePlaylistFolders = True
    tidalStartCollapsed = True
    showPlaylistIcons = True
    playlistIconSize = 25
    playbackOutputDeviceId = ""
    playbackOutputDeviceName = "Default Playback Device"
    useCamelotKeyNotation = True
    tableColumnVisibility = {}
    debugOpenApiProviderLabel = False
    enableOpenApiBatchQualityFetching = True
    openApiQualityBatchSize = 20
    downloadStructureMigrationDone = False
    downloadStructureMigrationDoNotRemind = False
    downloadStructureMigrationVersion = 1

    downloadPath = _get_initial_download_path()
    audioQuality = enums.AudioQuality.LOSSLESS
    usePlaylistFolder = True
    albumFolderFormat = _get_default_path_format(enums.Type.Album)
    playlistFolderFormat = _get_default_path_format(enums.Type.Playlist)
    trackFileFormat = _get_default_path_format(enums.Type.Track)

    # Spotify Integration Settings
    spotifyClientId = ""
    spotifyClientSecret = ""
    spotifyRedirectUri = "http://127.0.0.1:8888/callback"  # Use loopback IP literal

    # Playlist Cover Cache Settings
    playlistCoverCachePath: Optional[str] = None  # Type hint allows str or None
    playlistCoverCacheTTL: int = 7  # Type hint for clarity

    def getDefaultPathFormat(self, type: enums.Type):
        return _get_default_path_format(type)

    def getAudioQuality(self, value: Union[str, enums.AudioQuality]):
        if isinstance(value, enums.AudioQuality):
            return value
        if isinstance(value, str) and value.lower() == "master":
            return enums.AudioQuality.HI_RES_LOSSLESS
        for item in enums.AudioQuality:
            if isinstance(value, str) and item.name.lower() == value.lower():
                return item
        return enums.AudioQuality.HIGH

    def read(self, path: str):
        self._path_ = path
        txt = aigpy.file.getContent(self._path_)
        file_existed_and_had_content = len(txt) > 0
        if file_existed_and_had_content:
            try:
                data = json.loads(txt)
                if aigpy.model.dictToModel(data, self) is None:
                    print(f"Warning: Could not fully parse settings from {self._path_}")
            except json.JSONDecodeError as e:
                print(
                    f"Error reading settings file {self._path_}: {e}. Using defaults."
                )
                file_existed_and_had_content = False

        self.audioQuality = self.getAudioQuality(self.audioQuality)

        if self.albumFolderFormat is None:
            self.albumFolderFormat = self.getDefaultPathFormat(enums.Type.Album)
        if self.trackFileFormat is None:
            self.trackFileFormat = self.getDefaultPathFormat(enums.Type.Track)
        if self.playlistFolderFormat is None:
            self.playlistFolderFormat = self.getDefaultPathFormat(enums.Type.Playlist)
        if sys.platform == "win32" and self.downloadPath == "./Downloads/":
            self.downloadPath = get_default_download_path()
        if sys.platform == "win32" and self.trackFileFormat == R"{TrackNumber} - {ArtistName} - {TrackTitle}":
            self.trackFileFormat = self.getDefaultPathFormat(enums.Type.Track)
        if self.apiKeyIndex is None:
            self.apiKeyIndex = 0
        if not hasattr(self, "spotifyClientId"):
            self.spotifyClientId = ""
        if not hasattr(self, "spotifyClientSecret"):
            self.spotifyClientSecret = ""
        if not hasattr(self, "spotifyRedirectUri") or not self.spotifyRedirectUri:
            self.spotifyRedirectUri = "http://127.0.0.1:8888/callback"
        if not hasattr(self, "autoSpotifyLogin"):
            self.autoSpotifyLogin = False
        if not hasattr(self, "spotifyUsePlaylistFolders"):
            self.spotifyUsePlaylistFolders = True
        if not hasattr(self, "tidalStartCollapsed"):
            self.tidalStartCollapsed = True
        if not hasattr(self, "showPlaylistIcons"):
            self.showPlaylistIcons = True
        if not hasattr(self, "playlistIconSize"):
            self.playlistIconSize = 25
        if not hasattr(self, "playbackOutputDeviceId"):
            self.playbackOutputDeviceId = ""
        if not hasattr(self, "playbackOutputDeviceName") or not self.playbackOutputDeviceName:
            self.playbackOutputDeviceName = "Default Playback Device"
        if not hasattr(self, "useCamelotKeyNotation"):
            self.useCamelotKeyNotation = True
        if not hasattr(self, "tableColumnVisibility") or not isinstance(self.tableColumnVisibility, dict):
            self.tableColumnVisibility = {}
        if not hasattr(self, "debugOpenApiProviderLabel"):
            self.debugOpenApiProviderLabel = False
        if not hasattr(self, "enableOpenApiBatchQualityFetching"):
            self.enableOpenApiBatchQualityFetching = True
        if (
            not hasattr(self, "openApiQualityBatchSize")
            or not isinstance(self.openApiQualityBatchSize, int)
            or self.openApiQualityBatchSize < 1
            or self.openApiQualityBatchSize > 20
        ):
            self.openApiQualityBatchSize = 20
        if not hasattr(self, "downloadStructureMigrationDone"):
            self.downloadStructureMigrationDone = False
        if not hasattr(self, "downloadStructureMigrationDoNotRemind"):
            self.downloadStructureMigrationDoNotRemind = False
        if not hasattr(self, "downloadStructureMigrationVersion"):
            self.downloadStructureMigrationVersion = 1
        if not hasattr(self, "playlistCoverCachePath"):
            self.playlistCoverCachePath = None
        if (
            not hasattr(self, "playlistCoverCacheTTL")
            or not isinstance(self.playlistCoverCacheTTL, int)
            or self.playlistCoverCacheTTL <= 0
        ):
            self.playlistCoverCacheTTL = 7
        if not hasattr(self, "fontSize") or not isinstance(self.fontSize, int) or not (8 <= self.fontSize <= 16):
            self.fontSize = 11

        # --- FIX: Ensure downloadPath is absolute ---
        # This prevents the app from trying to write to Program Files if the default "./Downloads/" is used.
        from .paths import get_user_download_path
        self.downloadPath = get_user_download_path(self.downloadPath)
        
        #log download path
        print(f"Download path set to: {self.downloadPath}")
        # --------------------------------------------

        from .lang.language import getLang

        if not isinstance(self.language, str) or self.language.lower() not in [
            "english",
            "dutch",
        ]:
            self.language = "English"
        
        global LANG
        LANG = getLang(self.language.lower())

        # If the file was missing or corrupt, save the now-initialized and path-corrected settings
        if not file_existed_and_had_content:
            print(
                f"Settings file '{self._path_}' not found or invalid. Creating with defaults."
            )
            try:
                self.save()
            except Exception as e:
                print(f"Error: Could not write default settings to {self._path_}: {e}")

    def save(self, path: Optional[str] = None):
        if path is not None:
            self._path_ = path
        data = aigpy.model.modelToDict(self)
        if data is None:  # Ensure data is a dict to satisfy Pylance
            data = {}
        data["audioQuality"] = self.audioQuality.name
        # Ensure Spotify settings are included, even if empty
        data["spotifyClientId"] = (
            self.spotifyClientId if hasattr(self, "spotifyClientId") else ""
        )
        data["spotifyClientSecret"] = (
            self.spotifyClientSecret if hasattr(self, "spotifyClientSecret") else ""
        )
        data["spotifyRedirectUri"] = (
            self.spotifyRedirectUri
            if hasattr(self, "spotifyRedirectUri")
            else "http://127.0.0.1:8888/callback"
        )
        data["autoSpotifyLogin"] = self.autoSpotifyLogin
        data["spotifyUsePlaylistFolders"] = self.spotifyUsePlaylistFolders
        data["tidalStartCollapsed"] = self.tidalStartCollapsed
        data["showPlaylistIcons"] = self.showPlaylistIcons
        data["playlistIconSize"] = self.playlistIconSize
        data["playbackOutputDeviceId"] = self.playbackOutputDeviceId
        data["playbackOutputDeviceName"] = self.playbackOutputDeviceName
        data["useCamelotKeyNotation"] = self.useCamelotKeyNotation
        data["tableColumnVisibility"] = self.tableColumnVisibility
        data["debugOpenApiProviderLabel"] = self.debugOpenApiProviderLabel
        data["enableOpenApiBatchQualityFetching"] = self.enableOpenApiBatchQualityFetching
        data["openApiQualityBatchSize"] = self.openApiQualityBatchSize
        data["downloadStructureMigrationDone"] = self.downloadStructureMigrationDone
        data["downloadStructureMigrationDoNotRemind"] = self.downloadStructureMigrationDoNotRemind
        data["downloadStructureMigrationVersion"] = self.downloadStructureMigrationVersion
        data["fontSize"] = self.fontSize
        txt = json.dumps(data, indent=4)  # Add indent for readability
        aigpy.file.write(self._path_, txt, "w+")


class TokenSettings(aigpy.model.ModelBase):
    userid = None
    countryCode = None
    accessToken = None
    refreshToken = None
    expiresAfter = 0
    apiKeyIndex: Optional[int] = None

    def __encode__(self, string):
        sw = bytes(string, "utf-8")
        st = base64.b64encode(sw)
        return st

    def __decode__(self, string):
        try:
            sr = base64.b64decode(string)
            st = sr.decode()
            return st
        except:
            return string

    def read(self, path: str):
        self._path_ = path
        txt = aigpy.file.getContent(self._path_)
        if len(txt) > 0:
            try:
                data = json.loads(self.__decode__(txt))
                aigpy.model.dictToModel(data, self)
                # --- MODIFICATION START: Ensure expiresAfter is numeric ---
                if self.expiresAfter is not None:
                    try:
                        self.expiresAfter = int(self.expiresAfter)
                    except (ValueError, TypeError):
                        self.expiresAfter = 0
                else:
                    self.expiresAfter = 0
        
            except (json.JSONDecodeError, TypeError):
                # If decoding or parsing fails, treat as empty
                self.userid = None
                self.accessToken = None
                self.expiresAfter = 0


    def save(self):
        data = aigpy.model.modelToDict(self)
        txt = json.dumps(data)
        aigpy.file.write(self._path_, self.__encode__(txt), "wb")


# Singleton
SETTINGS = Settings()
TOKEN = TokenSettings()
