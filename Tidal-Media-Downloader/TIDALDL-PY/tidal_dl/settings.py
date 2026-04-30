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
from typing import Optional, Union

import aigpy

from . import enums
from .lang.language import getLang
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
    downloadStructureMigrationDone = False
    downloadStructureMigrationDoNotRemind = False
    downloadStructureMigrationVersion = 1

    downloadPath = "./Downloads/"
    audioQuality = enums.AudioQuality.LOSSLESS
    usePlaylistFolder = True
    albumFolderFormat = R"{ArtistName}/{Flag} {AlbumTitle} [{AlbumID}] [{AlbumYear}]"
    playlistFolderFormat = R"Playlists/{PlaylistName} [{PlaylistUUID}]"  # Changed "Playlist" to "Playlists"
    trackFileFormat = R"{TrackNumber} - {ArtistName} - {TrackTitle}{ExplicitFlag}"

    # Spotify Integration Settings
    spotifyClientId = ""
    spotifyClientSecret = ""
    spotifyRedirectUri = "http://127.0.0.1:8888/callback"  # Use loopback IP literal

    # Playlist Cover Cache Settings
    playlistCoverCachePath: Optional[str] = None  # Type hint allows str or None
    playlistCoverCacheTTL: int = 7  # Type hint for clarity

    def getDefaultPathFormat(self, type: enums.Type):
        if type == enums.Type.Album:
            return R"{ArtistName}/{Flag} {AlbumTitle} [{AlbumID}] [{AlbumYear}]"
        elif type == enums.Type.Playlist:
            return R"Playlists/{PlaylistName} [{PlaylistUUID}]"  # Changed "Playlist" to "Playlists"
        elif type == enums.Type.Track:
            return R"{TrackNumber} - {ArtistName} - {TrackTitle}{ExplicitFlag}"
        return ""

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
