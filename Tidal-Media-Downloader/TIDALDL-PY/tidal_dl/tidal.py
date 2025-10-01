#!/usr/bin/env python
# -*- encoding: utf-8 -*-
print("[DEBUG] Importing in tidal_dl/tidal.py")
"""
@File    :   tidal.py
@Time    :   2019/02/27
@Author  :   Yaronzz
@Modified by: GeradeHouse
@VERSION :   3.0
@Contact :   yaronhuang@foxmail.com
@Desc    :   tidal api
"""
# @Modified by: GeradeHouse
import random
import re
import time
import json
import base64
import binascii  # Added to handle binascii.Error
from typing import (
    List,
    Any,
    Union,
    cast,
    Tuple,
    Dict,
    Optional,
    overload,
    Literal,
)
from xml.etree import ElementTree

# import os # Removed unused import
import logging

# Create a logger instance for this module
logger = logging.getLogger(__name__)
logger.setLevel(
    logging.WARNING
)  # Set specific level for this module to only receive warnings

from PIL import Image

# import io # Removed unused import
from io import BytesIO  # Explicit import for BytesIO

import requests
import urllib3  # Import urllib3 directly

# Attempt to import aigpy directly, assuming it's installed or available
try:
    import aigpy
except ImportError:
    logger.error(
        "Could not import the 'aigpy' library. Please ensure it is installed (`pip install aigpy`)."
    )
    raise

from .model import (
    Lyrics,
    Mix,
    SearchResult,
    StreamUrl,
    StreamRespond,
    LoginKey,
    Artist,
    Album,
    Track,
    Playlist,
)

print("[DEBUG] tidal.py: about to import SETTINGS from .settings")
from .settings import SETTINGS

print("[DEBUG] tidal.py: successfully imported SETTINGS from .settings")
from .enums import AudioQuality, Type
from .format import getAlbumPath, getTrackPath


# SSL Warnings | retry number
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class TidalAPI(object):
    def __init__(self):
        # Treat LoginKey dynamically to avoid strict attribute-assignment complaints
        self.key: Any = LoginKey()  # type: ignore[assignment]
        self.apiKey = {
            # Android Auto
            "clientId": "zU4XHVVkc2tDPo4t",
            "clientSecret": "VJKhDFqJPqvsPVNBV6ukXTJmwlvbttP7wlMlrc72se4=",
        }

    # --------------------------------------------------------------------- #
    #                           Internal helpers                            #
    # --------------------------------------------------------------------- #

    @overload
    def __get__(
        self,
        path: str,
        params: Dict[str, Any] = ...,
        urlpre: str = ...,
        *,
        return_raw: Literal[True],
    ) -> Tuple[Dict[str, Any], str]: ...

    @overload
    def __get__(
        self,
        path: str,
        params: Dict[str, Any] = ...,
        urlpre: str = ...,
        *,
        return_raw: Literal[False] = ...,
    ) -> Dict[str, Any]: ...

    def __get__(
        self,
        path: str,
        params: Dict[str, Any] = {},
        urlpre: str = "https://api.tidalhifi.com/v1/",
        *,
        return_raw: bool = False,
    ) -> Union[Dict[str, Any], Tuple[Dict[str, Any], str]]:
        """Internal method to perform GET requests. Optionally returns raw response text."""
        header = {"authorization": f"Bearer {self.key.accessToken}"}
        params["countryCode"] = self.key.countryCode
        errmsg: str = "Get operation err!"
        raw: str = ""
        result: Dict[str, Any] = {}
        respond: Optional[requests.Response] = None

        for index in range(0, 3):
            try:
                respond = requests.get(urlpre + path, headers=header, params=params)
                raw = respond.text

                # Only log status and headers if status is NOT 404
                if respond.status_code != 404:
                    pass  # Added pass to prevent indentation error

                # Uncomment to log raw responnse
                # logging.debug(f"__get__ raw response: {raw[:500]}")

                if not raw.strip():
                    continue  # Retry if empty

                result = json.loads(raw)

                if not isinstance(result, dict):
                    logger.warning(f"__get__ received non-dict JSON response: {result}")
                    continue  # Retry if not a dictionary

                # If 'status' key exists, check for API errors
                if "status" in result:
                    if "userMessage" in result and result["userMessage"] is not None:
                        errmsg += str(result["userMessage"])
                        break

                if return_raw:
                    return result, raw
                else:
                    return result

            except json.JSONDecodeError:
                logger.error(f"__get__ failed to decode JSON response: {raw[:500]}...")
                errmsg += f" | Invalid JSON received: {raw[:100]}..."
                break
            except requests.exceptions.RequestException as e:
                logger.error(f"__get__ request failed: {e}")
                errmsg += f" | Request failed: {e}"
                if index >= 2:
                    raise Exception(errmsg)
                time.sleep(1)
                continue
            except Exception as e:
                logger.error(f"__get__ unexpected error: {e}", exc_info=True)
                errmsg += f" | Unexpected error: {e}"
                if index >= 2:
                    if respond is not None:
                        errmsg += f" | Last raw response: {respond.text[:100]}..."
                    raise Exception(errmsg)
                time.sleep(1)
                continue

        final_errmsg = f"Get operation failed after multiple retries: {errmsg}"
        if raw:
            final_errmsg += f" | Last raw response snippet: {raw[:100]}..."
        raise Exception(final_errmsg)

    def __getQuality__(
        self,
        path: str,
        params: Dict[str, Any] = {},
        urlpre: str = "https://api.tidalhifi.com/v1/",
    ) -> Dict[str, Any]:
        header = {"authorization": f"Bearer {self.key.accessToken}"}
        params["countryCode"] = self.key.countryCode
        errmsg: str = "GetQuality operation err!"
        respond: Optional[requests.Response] = None
        result: Dict[str, Any] = {}

        for index in range(0, 3):
            try:
                respond = requests.get(urlpre + path, headers=header, params=params)
                if (
                    respond.url.find("playbackinfopostpaywall") != -1
                    and SETTINGS.downloadDelay is not False
                ):
                    sleep_time = random.randint(200, 2000) / 1000
                    print(f"Quality retrieval: sleeping for {sleep_time} seconds")
                    time.sleep(sleep_time)
                if respond.status_code == 429:
                    print(
                        "Quality retrieval: Too many requests, waiting for 20 seconds..."
                    )
                    for i in range(20, 0, -1):
                        time.sleep(1)
                        print(i, end=" ")
                    print("")
                    continue
                result = json.loads(respond.text)
                if not isinstance(result, dict):
                    continue
                if "status" not in result:
                    return result
                if "userMessage" in result and result["userMessage"] is not None:
                    errmsg += str(result["userMessage"])
                break
            except Exception:
                if index >= 2:
                    if respond is not None:
                        errmsg += respond.text
                    raise Exception(errmsg)
                time.sleep(1)
                continue

        if (
            "status" in result
            and "userMessage" in result
            and result["userMessage"] is not None
        ):
            raise Exception(errmsg)

        if result and "status" not in result:
            return result
        elif result:
            return result
        else:
            raise Exception(errmsg)

    def __getItems__(self, path: str, params: Dict[str, Any] = {}) -> List[Any]:
        params["limit"] = 50
        params["offset"] = 0
        total: int = 0
        ret: List[Any] = []
        while True:
            data: Dict[str, Any] = self.__get__(
                path, params
            )  # precise type now inferred
            if not data:
                print("[WARN] __getItems__: No data received from API.")
                return []
            if "totalNumberOfItems" in data:
                total = data["totalNumberOfItems"]
            else:
                print(
                    "[WARN] __getItems__: 'totalNumberOfItems' key missing in response."
                )
                return []
            items = data.get("items")
            if not items or not isinstance(items, list):
                print(
                    f"[WARN] __getItems__: 'items' key missing or empty/invalid in response for path: {path}"
                )
                return ret
            if total > 0 and len(ret) >= total:
                return ret

            ret.extend(items)
            num = len(items)

            if total > 0 and len(ret) >= total:
                return ret[:total]

            if num < 50:
                break
            params["offset"] += num
        return ret

    def __getResolutionList__(self, url: str) -> List[StreamUrl]:
        ret: List[StreamUrl] = []
        try:
            txt = requests.get(url).content.decode("utf-8")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch M3U8 list from {url}: {e}")
            return ret

        array = txt.split("#")
        for item in array:
            if "RESOLUTION=" not in item or "EXT-X-STREAM-INF:" not in item:
                continue

            stream = cast(Any, StreamUrl())  # dynamic assignment
            codec_match = aigpy.string.getSub(item, 'CODECS="', '"')
            stream.codec = codec_match if codec_match else None  # type: ignore[assignment]

            m3u8_match = aigpy.string.getSubOnlyStart(item, "http")
            stream.m3u8Url = (
                "http" + m3u8_match.strip() if m3u8_match else None
            )  # type: ignore[assignment]

            resolution_match = aigpy.string.getSub(item, "RESOLUTION=", "http")
            if resolution_match:
                resolution_str = resolution_match.strip().split(",")[0]
                stream.resolution = resolution_str  # type: ignore[assignment]
                stream.resolutions = resolution_str.split("x")  # type: ignore[assignment]
            else:
                stream.resolution = None  # type: ignore[assignment]
                stream.resolutions = None  # type: ignore[assignment]

            ret.append(stream)
        return ret

    def __post__(
        self,
        path: str,
        data: Dict[str, Any],
        auth: Optional[Tuple[str, str]] = None,
        urlpre: str = "https://auth.tidal.com/v1/oauth2",
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for index in range(3):
            try:
                # The auth parameter handles Basic Auth automatically if it's a tuple.
                response = requests.post(
                    urlpre + path, data=data, auth=auth, verify=False
                )
                response.raise_for_status()  # Raises HTTPError for 4xx/5xx responses
                result = response.json()
                return result
            except requests.exceptions.HTTPError as e:
                logger.error(f"__post__ HTTP error for URL: {e.request.url}")
                logger.error(f"Status Code: {e.response.status_code}")
                # Log the server's response body, which often contains specific error details.
                logger.error(f"Response Body: {e.response.text}")
                if index == 2:
                    raise e
                time.sleep(1)
            except requests.exceptions.RequestException as e:
                # This will catch other request errors like connection issues.
                logger.error(f"__post__ request failed: {e}")
                if index == 2:
                    raise e
                time.sleep(1)
            except json.JSONDecodeError as e:
                logger.error(f"__post__ failed to decode JSON response: {e}")
                raise e
            except Exception as e:
                logger.error(f"__post__ unexpected error: {e}", exc_info=True)
                if index == 2:
                    raise e
                time.sleep(1)
        raise Exception(
            "__post__ failed after multiple retries without specific error capture."
        )

    # --------------------------------------------------------------------- #
    #                           Public  methods                             #
    # --------------------------------------------------------------------- #

    def getDeviceCode(self) -> str:
        data: Dict[str, str] = {
            "client_id": self.apiKey["clientId"],
            "scope": "r_usr+w_usr+w_sub",
        }
        result = self.__post__("/device_authorization", data)
        if result.get("status") and result["status"] != 200:
            raise Exception(
                f"Device authorization failed. Status: {result.get('status')}, Message: {result.get('userMessage', 'N/A')}. Please choose another apikey."
            )

        device_code = result.get("deviceCode")
        user_code = result.get("userCode")
        verification_uri = result.get("verificationUri")
        expires_in = result.get("expiresIn")
        interval = result.get("interval")

        if not all([device_code, user_code, verification_uri, expires_in, interval]):
            raise Exception("Device authorization response missing required fields.")

        self.key.deviceCode = device_code
        self.key.userCode = user_code
        self.key.verificationUrl = verification_uri
        self.key.authCheckTimeout = expires_in
        self.key.authCheckInterval = interval
        return "http://" + str(self.key.verificationUrl) + "/" + str(self.key.userCode)

    def checkAuthStatus(self) -> str:
        """
        Checks the device authorization status.
        Returns:
            str: "SUCCESS", "PENDING", "SLOW_DOWN", or raises an exception on hard error.
        """
        data: Dict[str, Any] = {
            "client_id": self.apiKey["clientId"],
            "device_code": self.key.deviceCode,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "scope": "r_usr+w_usr+w_sub",
        }
        auth = (self.apiKey["clientId"], self.apiKey["clientSecret"])

        try:
            result = self.__post__("/token", data, auth=auth)
        except requests.exceptions.HTTPError as e:
            try:
                # Attempt to parse the JSON error body from the server
                error_data = e.response.json()
                status = error_data.get("status")
                error_type = error_data.get("error")

                # Handle specific, expected polling errors
                if status == 400 and error_type == "authorization_pending":
                    return "PENDING"
                if status == 400 and error_type == "slow_down":
                    return "SLOW_DOWN"
            except (json.JSONDecodeError, AttributeError):
                # If the error response isn't JSON or something is wrong, re-raise the original exception
                raise e
            # If it's another type of HTTP error, let it be raised
            raise e

        # If __post__ was successful (status 200)
        user_info = result.get("user")
        access_token = result.get("access_token")
        refresh_token = result.get("refresh_token")
        expires_in = result.get("expires_in")

        if not user_info or not access_token:
            raise Exception(
                "Authorization response missing required user info or tokens."
            )

        user_id = user_info.get("userId")
        country_code = user_info.get("countryCode")

        if user_id is None or country_code is None:
            raise Exception("Authorization response missing userId or countryCode.")

        self.key.userId = user_id
        self.key.countryCode = country_code
        self.key.accessToken = access_token
        self.key.refreshToken = refresh_token
        self.key.expiresIn = expires_in if expires_in is not None else 0
        return "SUCCESS"

    def verifyAccessToken(self, accessToken: str) -> bool:
        header = {"authorization": f"Bearer {accessToken}"}
        try:
            response = requests.get("https://api.tidal.com/v1/sessions", headers=header)
            response.raise_for_status()
            result = response.json()
            if "status" in result and result["status"] != 200:
                logger.warning(
                    f"verifyAccessToken failed with API status: {result.get('status')}, message: {result.get('userMessage')}"
                )
                return False
            if "userId" not in result:
                logger.warning("verifyAccessToken response missing userId.")
                return False
            return True
        except requests.exceptions.RequestException as e:
            logger.debug(f"verifyAccessToken request failed: {e}")
            return False
        except json.JSONDecodeError as e:
            logger.error(f"verifyAccessToken failed to decode JSON: {e}")
            return False
        except Exception as e:
            logger.error(f"verifyAccessToken unexpected error: {e}", exc_info=True)
            return False

    def refreshAccessToken(self, refreshToken: str) -> bool:
        data: Dict[str, str] = {
            "client_id": self.apiKey["clientId"],
            "refresh_token": refreshToken,
            "grant_type": "refresh_token",
            "scope": "r_usr+w_usr+w_sub",
        }
        auth = (self.apiKey["clientId"], self.apiKey["clientSecret"])
        try:
            result = self.__post__("/token", data, auth=auth)

            status = result.get("status")
            if status and status != 200:
                logger.error(
                    f"refreshAccessToken failed. Status: {status}, Message: {result.get('userMessage', 'N/A')}"
                )
                return False

            user_info = result.get("user")
            access_token = result.get("access_token")
            expires_in = result.get("expires_in")

            if not user_info or not access_token:
                logger.error(
                    "refreshAccessToken response missing required user info or access token."
                )
                return False

            user_id = user_info.get("userId")
            country_code = user_info.get("countryCode")

            if user_id is None or country_code is None:
                logger.error(
                    "refreshAccessToken response missing userId or countryCode."
                )
                return False

            self.key.userId = user_id
            self.key.countryCode = country_code
            self.key.accessToken = access_token
            self.key.expiresIn = expires_in if expires_in is not None else 0
            return True

        except Exception as e:
            logger.error(
                f"refreshAccessToken encountered an exception: {e}", exc_info=True
            )
            return False

    def loginByAccessToken(
        self, accessToken: str, userid: Union[str, int, None] = None
    ) -> None:
        header = {"authorization": f"Bearer {accessToken}"}
        try:
            response = requests.get("https://api.tidal.com/v1/sessions", headers=header)
            response.raise_for_status()
            result = response.json()

            status = result.get("status")
            if status and status != 200:
                raise Exception(
                    f"Login failed! Status: {status}, Message: {result.get('userMessage', 'N/A')}"
                )

            session_user_id = result.get("userId")
            country_code = result.get("countryCode")

            if session_user_id is None or country_code is None:
                raise Exception(
                    "Login failed: Session response missing userId or countryCode."
                )

            if userid is not None and str(session_user_id) != str(userid):
                raise Exception(
                    f"User mismatch! Token belongs to user {session_user_id}, expected {userid}. Please use your own access token."
                )

            self.key.userId = session_user_id
            self.key.countryCode = country_code
            self.key.accessToken = accessToken
            self.key.refreshToken = None
            self.key.expiresIn = 0

        except requests.exceptions.RequestException as e:
            raise Exception(f"Login failed due to network error: {e}") from e
        except json.JSONDecodeError as e:
            raise Exception(f"Login failed: Could not decode API response: {e}") from e
        except Exception as e:
            raise Exception(f"Login failed due to an unexpected error: {e}") from e

    def getAlbum(self, id: str) -> Album:
        data: Dict[str, Any] = self.__get__("albums/" + str(id))
        model = aigpy.model.dictToModel(data, Album())
        if model is None:
            raise Exception(
                f"Failed to convert API response to Album model for ID {id}"
            )
        return cast(Album, model)

    def getPlaylist(self, id: str) -> Playlist:
        data: Dict[str, Any] = self.__get__("playlists/" + str(id))
        model = aigpy.model.dictToModel(data, Playlist())
        if model is None:
            raise Exception(
                f"Failed to convert API response to Playlist model for ID {id}"
            )
        return cast(Playlist, model)

    def getPlaylistSelf(self) -> List[Playlist]:
        if not self.key.userId:
            raise Exception("User ID not set. Please login first.")
        ret_data: Dict[str, Any] = self.__get__(f"users/{self.key.userId}/playlists")
        playlists: List[Playlist] = []
        items = ret_data.get("items", [])
        if not isinstance(items, list):
            logger.warning(
                f"getPlaylistSelf: Expected 'items' to be a list, got {type(items)}"
            )
            return []

        for item_dict in items:
            if isinstance(item_dict, dict):
                model = aigpy.model.dictToModel(item_dict, Playlist())
                if model:
                    playlists.append(cast(Playlist, model))
        return playlists

    def getArtist(self, id: str) -> Artist:
        data: Dict[str, Any] = self.__get__("artists/" + str(id))
        model = aigpy.model.dictToModel(data, Artist())
        if model is None:
            raise Exception(
                f"Failed to convert API response to Artist model for ID {id}"
            )
        return cast(Artist, model)

    def getTrack(self, id: str, suppress_debug_prints: bool = False) -> Track:
        data: Dict[str, Any] = self.__get__("tracks/" + str(id))
        if not suppress_debug_prints:
            logger.debug(
                f"[DEBUG] getTrack: raw data for track {id}: {str(data)[:500]}..."
            )
        track_obj = aigpy.model.dictToModel(data, Track())
        if track_obj is None:
            raise Exception(
                f"Failed to convert API response to Track model for ID {id}"
            )
        if not suppress_debug_prints:
            logger.debug(
                f"[DEBUG] getTrack: converted Track object for id {id}: {track_obj} (type: {type(track_obj)})"
            )
        return cast(Track, track_obj)

    def getMix(self, id: str) -> Mix:
        mix = Mix()
        setattr(mix, "id", id)  # type: ignore[assignment]
        tracks, _ = self.getItems(id, Type.Mix)
        setattr(mix, "tracks", tracks)  # type: ignore[assignment]
        return mix

    def getTypeData(self, id: str, type: Type) -> Any:
        if type == Type.Album:
            return self.getAlbum(id)
        if type == Type.Artist:
            return self.getArtist(id)
        if type == Type.Track:
            return self.getTrack(id)
        if type == Type.Playlist:
            return self.getPlaylist(id)
        if type == Type.Mix:
            return self.getMix(id)
        logger.warning(f"getTypeData called with unhandled type: {type}")
        return None

    def search(
        self,
        text: str,
        type: Type,
        offset: int = 0,
        limit: int = 10,
        return_raw: bool = False,
    ) -> Union[SearchResult, Tuple[SearchResult, str]]:
        """Searches Tidal for content. Optionally returns raw API response."""
        typeStr = type.name.upper() + "S"
        if type == Type.Null:
            typeStr = "ARTISTS,ALBUMS,TRACKS,PLAYLISTS"  # Removed VIDEOS

        params: Dict[str, Any] = {
            "query": text,
            "offset": offset,
            "limit": limit,
            "types": typeStr,
        }
        get_result = self.__get__("search", params=params, return_raw=return_raw)

        if return_raw:
            if not isinstance(get_result, tuple):
                raise TypeError(f"Expected a tuple from __get__ with return_raw=True, got {type(get_result)}")  # type: ignore
            result_dict, raw_text = cast(Tuple[Dict[str, Any], str], get_result)
            search_result_model = aigpy.model.dictToModel(
                result_dict, SearchResult()
            )
            if search_result_model is None:
                raise Exception(
                    "Failed to convert search API response to SearchResult model."
                )
            return search_result_model, raw_text
        else:
            if not isinstance(get_result, dict):
                raise TypeError(f"Expected a dict from __get__ with return_raw=False, got {type(get_result)}")  # type: ignore
            result_dict = cast(Dict[str, Any], get_result)
            search_result_model = aigpy.model.dictToModel(
                result_dict, SearchResult()
            )
            if search_result_model is None:
                raise Exception(
                    "Failed to convert search API response to SearchResult model."
                )
            return search_result_model

    # search results
    def getSearchResultItems(self, result: SearchResult, type: Type) -> List[Any]:
        if type == Type.Track:
            return getattr(result.tracks, "items", []) if result.tracks else []
        if type == Type.Album:
            return getattr(result.albums, "items", []) if result.albums else []
        if type == Type.Artist:
            return getattr(result.artists, "items", []) if result.artists else []
        if type == Type.Playlist:
            return getattr(result.playlists, "items", []) if result.playlists else []
        return []

    def getLyrics(self, id: str) -> Lyrics:
        data: Dict[str, Any] = self.__get__(
            f"tracks/{str(id)}/lyrics", urlpre="https://listen.tidal.com/v1/"
        )
        model = aigpy.model.dictToModel(data, Lyrics())
        if model is None:
            raise Exception(
                f"Failed to convert API response to Lyrics model for track ID {id}"
            )
        return cast(Lyrics, model)

    def getItems(self, id: str, type: Type) -> Tuple[List[Track], List[Any]]:
        if type == Type.Playlist:
            raw_data = self.__getItems__("playlists/" + str(id) + "/items")
        elif type == Type.Album:
            raw_data = self.__getItems__("albums/" + str(id) + "/items")
        elif type == Type.Mix:
            raw_data = self.__getItems__("mixes/" + str(id) + "/items")
        else:
            raise ValueError(f"invalid Type '{type}' for getItems!")

        data = [item for item in raw_data if isinstance(item, dict)]

        tracks: List[Track] = []
        videos: List[Any] = []
        for item_dict in data:
            item_type = item_dict.get("type")
            item_content = item_dict.get("item")

            if not item_content or not isinstance(item_content, dict):
                logger.warning(
                    f"Skipping item with missing or invalid 'item' field: {item_dict}"
                )
                continue

            if item_type == "track":
                if item_content.get("streamReady"):
                    track_model = aigpy.model.dictToModel(item_content, Track())
                    if track_model:
                        track = cast(Track, track_model)
                        track.mediaMetadata = item_content.get("mediaMetadata", {})
                        tracks.append(track)
                    else:
                        logger.warning(
                            f"Failed to convert track item to model: {item_content}"
                        )
                else:
                    logger.debug(
                        f"Skipping track item not streamReady: {item_content.get('id')}"
                    )
            else:
                logger.debug(
                    f"Skipping item with unknown type '{item_type}': {item_content.get('id')}"
                )

        return tracks, videos

    def getArtistAlbums(self, id: str, includeEP: bool = False) -> List[Album]:
        albums_data: List[Dict[str, Any]] = [
            item
            for item in self.__getItems__(f"artists/{str(id)}/albums")
            if isinstance(item, dict)
        ]
        albums: List[Album] = []
        for item_dict in albums_data:
            model = aigpy.model.dictToModel(item_dict, Album())
            if model:
                albums.append(cast(Album, model))

        if not includeEP:
            return albums

        eps_data: List[Dict[str, Any]] = [
            item
            for item in self.__getItems__(
                f"artists/{str(id)}/albums", {"filter": "EPSANDSINGLES"}
            )
            if isinstance(item, dict)
        ]
        for item_dict in eps_data:
            model = aigpy.model.dictToModel(item_dict, Album())
            if model and model.id not in {a.id for a in albums}:
                albums.append(cast(Album, model))
        return albums

    def getArtistTopTracks(self, id: str, limit: int = 10) -> List[Track]:
        """Gets the top tracks for a given artist."""
        path = f"artists/{id}/toptracks"
        params = {"limit": limit}
        raw_data = self.__getItems__(path, params)

        tracks: List[Track] = []
        for item_dict in raw_data:
            if isinstance(item_dict, dict):
                track_model = aigpy.model.dictToModel(item_dict, Track())
                if track_model:
                    tracks.append(cast(Track, track_model))
        return tracks

    def parse_mpd(self, xml: Union[str, bytes]) -> List[List[str]]:
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8")
        xml = cast(str, xml)
        xml = re.sub(r'xmlns="[^"]+"', "", xml, count=1)
        try:
            root: ElementTree.Element = ElementTree.fromstring(xml)
        except ElementTree.ParseError as e:
            logger.error(f"Failed to parse MPD XML: {e}")
            return []

        tracks: List[List[str]] = []
        for period in root.findall("Period"):
            for adaptation_set in period.findall("AdaptationSet"):
                content_type = adaptation_set.get("contentType")
                if content_type != "audio":
                    logger.warning(
                        f"Skipping non-audio AdaptationSet (contentType='{content_type}')"
                    )
                    continue

                for rep in adaptation_set.findall("Representation"):
                    codec_attr = rep.get("codecs")
                    codec = codec_attr.upper() if codec_attr else "UNKNOWN"
                    if codec.startswith("MP4A"):
                        codec = "AAC"

                    seg_template: Optional[ElementTree.Element] = rep.find(
                        "SegmentTemplate"
                    )
                    if seg_template is None:
                        logger.warning(
                            "Representation missing SegmentTemplate, skipping."
                        )
                        continue

                    initialization_url = seg_template.get("initialization")
                    if not initialization_url:
                        logger.warning(
                            "SegmentTemplate missing initialization URL, skipping representation."
                        )
                        continue
                    track_urls: List[str] = [initialization_url]

                    start_number_str = seg_template.get("startNumber", "1")
                    try:
                        start_number = int(start_number_str)
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Invalid startNumber '{start_number_str}', defaulting to 1."
                        )
                        start_number = 1

                    media_pattern = seg_template.get("media")
                    if not media_pattern:
                        logger.warning(
                            "SegmentTemplate missing media URL pattern, skipping representation."
                        )
                        continue

                    seg_timeline: Optional[ElementTree.Element] = seg_template.find(
                        "SegmentTimeline"
                    )
                    if seg_timeline is not None:
                        seg_time_list: List[int] = []
                        cur_time = 0
                        segments = seg_timeline.findall("S")
                        if not segments:
                            logger.warning(
                                "SegmentTimeline found but contains no S elements."
                            )
                            continue

                        for s in segments:
                            t_attr = s.get("t")
                            d_attr = s.get("d")
                            r_attr = s.get("r", "0")

                            try:
                                if t_attr is not None:
                                    cur_time = int(t_attr)
                                seg_duration = int(d_attr) if d_attr is not None else 0
                                repeat_count = int(r_attr)
                            except (ValueError, TypeError):
                                logger.warning(
                                    f"Invalid attributes in SegmentTimeline S element: t='{t_attr}', d='{d_attr}', r='{r_attr}'. Skipping segment."
                                )
                                continue

                            for _ in range(repeat_count + 1):
                                seg_time_list.append(cur_time)
                                cur_time += seg_duration

                        seg_num_list: List[int] = list(
                            range(start_number, len(seg_time_list) + start_number)
                        )
                        track_urls.extend(
                            [
                                media_pattern.replace("$Number$", str(n))
                                for n in seg_num_list
                            ]
                        )
                        tracks.append(track_urls)
                    else:
                        logger.warning(
                            "SegmentTemplate missing SegmentTimeline, cannot determine segment URLs."
                        )
                        continue
        return tracks

    def getStreamUrl(self, id: str, quality: AudioQuality) -> StreamUrl:
        paras: Dict[str, str] = {
            "audioquality": quality.value,
            "playbackmode": "STREAM",
            "assetpresentation": "FULL",
        }
        data: Dict[str, Any] = self.__get__(
            f"tracks/{str(id)}/playbackinfopostpaywall", paras
        )

        resp_model = aigpy.model.dictToModel(data, StreamRespond())
        if not resp_model:
            raise Exception(
                "Failed to convert playback info response to StreamRespond model."
            )
        resp = cast(StreamRespond, resp_model)

        if resp.manifestMimeType and "vnd.tidal.bt" in resp.manifestMimeType:
            if resp.manifest is None:
                raise Exception("Manifest is None for vnd.tidal.bt type.")
            try:
                manifest_bytes = base64.b64decode(resp.manifest)
                manifest_str = manifest_bytes.decode("utf-8")
                manifest: Dict[str, Any] = json.loads(manifest_str)
            except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
                raise Exception(f"Failed to decode or parse manifest: {e}") from e

            ret = cast(Any, StreamUrl())
            ret.trackid = resp.trackid  # type: ignore[assignment]
            ret.soundQuality = resp.audioQuality  # type: ignore[assignment]
            ret.codec = manifest.get("codecs")  # type: ignore[assignment]
            ret.encryptionKey = manifest.get("keyId", "")  # type: ignore[assignment]
            urls = manifest.get("urls")
            if not urls or not isinstance(urls, list) or not urls[0]:
                raise Exception("Manifest missing or invalid 'urls' field.")
            ret.url = urls[0]  # type: ignore[assignment]
            ret.urls = urls  # type: ignore[assignment]
            ret.manifestMimeType = resp.manifestMimeType  # type: ignore[assignment]
            logger.debug(
                f"[getStreamUrl] Propagating manifestMimeType: API='{resp.manifestMimeType}', Object='{ret.manifestMimeType}'"
            )
            return ret

        elif resp.manifestMimeType and "dash+xml" in resp.manifestMimeType:
            if resp.manifest is None:
                raise Exception("Manifest is None for dash+xml type.")
            try:
                manifest_bytes = base64.b64decode(resp.manifest)
                xmldata: str = manifest_bytes.decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as e:
                raise Exception(f"Failed to decode manifest: {e}") from e

            ret = cast(Any, StreamUrl())
            ret.trackid = resp.trackid  # type: ignore[assignment]
            ret.soundQuality = resp.audioQuality  # type: ignore[assignment]
            codec_match = aigpy.string.getSub(xmldata, 'codecs="', '"')
            ret.codec = codec_match if codec_match else None  # type: ignore[assignment]
            ret.encryptionKey = ""  # type: ignore[assignment]

            parsed_tracks = self.parse_mpd(xmldata)
            if not parsed_tracks:
                raise Exception(
                    "Failed to parse MPD or no suitable audio tracks found."
                )
            ret.urls = parsed_tracks[0]  # type: ignore[assignment]
            if not ret.urls:
                raise Exception("Parsed MPD track list is empty.")
            ret.url = ret.urls[0]  # type: ignore[assignment]
            ret.manifestMimeType = resp.manifestMimeType  # type: ignore[assignment]
            logger.debug(
                f"[getStreamUrl] Propagating manifestMimeType: API='{resp.manifestMimeType}', Object='{ret.manifestMimeType}'"
            )
            return ret

        mime_type_str = resp.manifestMimeType if resp.manifestMimeType else "None"
        raise Exception(
            f"Can't get the streamUrl, unsupported or missing manifest type: {mime_type_str}"
        )

    def getQualityStreamUrl(self, id: str, quality: AudioQuality) -> StreamUrl:
        squality = AudioQuality.HI_RES_LOSSLESS.value
        paras: Dict[str, str] = {
            "audioquality": squality,
            "playbackmode": "STREAM",
            "assetpresentation": "FULL",
        }
        data: Dict[str, Any] = self.__getQuality__(
            f"tracks/{str(id)}/playbackinfopostpaywall", paras
        )

        resp_model = aigpy.model.dictToModel(data, StreamRespond())
        if not resp_model:
            raise Exception(
                "Failed to convert quality check response to StreamRespond model."
            )
        resp = cast(StreamRespond, resp_model)

        ret = cast(Any, StreamUrl())
        ret.trackid = resp.trackid  # type: ignore[assignment]
        ret.soundQuality = resp.audioQuality  # type: ignore[assignment]

        if resp.manifestMimeType and "vnd.tidal.bt" in resp.manifestMimeType:
            if resp.manifest is None:
                raise Exception(
                    "Manifest is None for vnd.tidal.bt type during quality check."
                )
            try:
                manifest_bytes = base64.b64decode(resp.manifest)
                manifest_str = manifest_bytes.decode("utf-8")
                manifest: Dict[str, Any] = json.loads(manifest_str)
                ret.codec = manifest.get("codecs")  # type: ignore[assignment]
            except Exception as e:
                logger.warning(
                    f"Failed to decode/parse manifest during quality check (vnd.tidal.bt): {e}"
                )
                ret.codec = None  # type: ignore[assignment]
        elif resp.manifestMimeType and "dash+xml" in resp.manifestMimeType:
            if resp.manifest is None:
                raise Exception(
                    "Manifest is None for dash+xml type during quality check."
                )
            try:
                manifest_bytes = base64.b64decode(resp.manifest)
                xmldata: str = manifest_bytes.decode("utf-8")
                codec_match = aigpy.string.getSub(xmldata, 'codecs="', '"')
                ret.codec = codec_match if codec_match else None  # type: ignore[assignment]
            except Exception as e:
                logger.warning(
                    f"Failed to decode manifest or extract codec during quality check (dash+xml): {e}"
                )
                ret.codec = None  # type: ignore[assignment]
        else:
            mime_type_str = resp.manifestMimeType if resp.manifestMimeType else "None"
            logger.warning(
                f"Cannot determine codec during quality check, unsupported or missing manifest type: {mime_type_str}"
            )
            ret.codec = None  # type: ignore[assignment]

        return ret  # type: ignore[return-value]

    #     resp_model = aigpy.model.dictToModel(data, StreamRespond())
    #
    #     if resp.manifestMimeType and "vnd.tidal.emu" in resp.manifestMimeType:
    #             manifest_bytes = base64.b64decode(resp.manifest)
    #             manifest_str = manifest_bytes.decode("utf-8")
    #             manifest: Dict[str, Any] = json.loads(manifest_str)
    #         urls = manifest.get("urls")
    #         if not urls or not isinstance(urls, list) or not urls[0]:
    #             raise Exception("Video manifest missing or invalid 'urls' field.")
    #
    #         m3u8_master_url = urls[0]
    #         resolution_list: List[VideoStreamUrl] = self.__getResolutionList__(m3u8_master_url)
    #
    #         try:
    #             target_height = int(quality.value)
    #         except ValueError:
    #             raise ValueError(f"Invalid VideoQuality value: {quality.value}")
    #
    #         best_match: Optional[VideoStreamUrl] = None
    #         resolution_list.sort(
    #             key=lambda s: int(s.resolutions[1]) if s.resolutions and len(s.resolutions) > 1 else 0,
    #             reverse=True,
    #         )
    #
    #         for stream in resolution_list:
    #             try:
    #                 stream_height = int(stream.resolutions[1]) if stream.resolutions and len(stream.resolutions) > 1 else 0
    #                 if stream_height <= target_height:
    #                     best_match = stream
    #                     break
    #
    #         if not best_match and resolution_list:
    #             best_match = resolution_list[-1]
    #
    #         if best_match:
    #             return best_match

    def getTrackContributors(self, id: str) -> Dict[str, Any]:
        data: Dict[str, Any] = self.__get__(f"tracks/{str(id)}/contributors")
        return data

    def getCoverUrl(
        self, sid: Optional[str], width: str = "320", height: str = "320"
    ) -> str:
        if sid is None:
            return ""
        # If 'sid' is already a full URL (like from Spotify), return it directly.
        if sid.startswith("http"):
            return sid
        # Check if 'sid' is a valid Tidal UUID.
        if not re.match(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            sid,
        ):
            logger.warning(
                f"Invalid cover SID format: '{sid}'. Not a valid UUID or a full URL."
            )
            return ""
        if not width.isdigit() or not height.isdigit():
            logger.warning(f"Invalid width/height for cover URL: {width}x{height}")
            width, height = "320", "320"
        return f"https://resources.tidal.com/images/{sid.replace('-', '/')}/{width}x{height}.jpg"

    def getCoverData(
        self, sid: Optional[str], width: str = "320", height: str = "320"
    ) -> bytes:
        url = self.getCoverUrl(sid, width, height)
        if not url:
            return b""
        try:
            # More comprehensive headers to mimic a browser request
            headers = {
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "en-US,en;q=0.9",
                "Authorization": f"Bearer {self.key.accessToken}",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://listen.tidal.com/",
                "Sec-Ch-Ua": '"Not/A)Brand";v="99", "Google Chrome";v="91", "Chromium";v="91"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",  # Changed from same-origin as resources.tidal.com is different
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            }
            response = requests.get(url, headers=headers, timeout=15, stream=True)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "image" not in content_type:
                logger.warning(
                    f"Expected image content type, got '{content_type}' for URL: {url}"
                )
                return b""
            content = response.content
            if not content:
                logger.warning(f"Empty response content for cover URL: {url}")
                return b""
            return content
        except requests.exceptions.Timeout:
            logger.error(f"Timeout fetching cover data for {sid} from {url}")
            return b""
        except requests.exceptions.HTTPError as e:
            logger.error(
                f"HTTP error {e.response.status_code} fetching cover data for {sid} from {url}: {e}"
            )
            return b""
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching cover data for {sid} from {url}: {e}")
            return b""
        except Exception as e:
            logger.error(
                f"Unexpected error in getCoverData for {sid}: {e}", exc_info=True
            )
            return b""

    def getPlaylistCoverData(
        self, playlist_id: str, width: int = 320, height: int = 320
    ) -> bytes:
        try:
            tracks, _ = self.getItems(playlist_id, Type.Playlist)
        except Exception as e:
            logger.error(
                f"Failed to get items for playlist {playlist_id} to generate cover: {e}"
            )
            placeholder = Image.new("RGB", (width, height), color=(30, 30, 30))
            buf = BytesIO()
            placeholder.save(buf, format="JPEG")
            return buf.getvalue()

        count = len(tracks)

        w, h = width, height
        tile_sizes: List[Tuple[int, int]] = [(w // 2, h // 2)] * 4
        paste_coords: List[Tuple[int, int]] = [
            (0, 0),
            (w // 2, 0),
            (0, h // 2),
            (w // 2, h // 2),
        ]

        if count == 0:
            placeholder = Image.new("RGB", (w, h), color=(30, 30, 30))
            buf = BytesIO()
            placeholder.save(buf, format="JPEG")
            return buf.getvalue()
        elif count == 1:
            tile_sizes = [(w, h)]
            paste_coords = [(0, 0)]
        elif count == 2:
            tile_sizes = [(w // 2, h), (w // 2, h)]
            paste_coords = [(0, 0), (w // 2, 0)]
        elif count == 3:
            tile_sizes = [(w // 2, h // 2)] * 3
            paste_coords = [(0, 0), (w // 2, 0), (0, h // 2)]

        tracks_to_process = tracks[: len(tile_sizes)]

        imgs: List[Image.Image] = []
        for idx, t in enumerate(tracks_to_process):
            cover_id: Optional[str] = getattr(getattr(t, "album", None), "cover", None)
            tile_w, tile_h = tile_sizes[idx]

            placeholder_img = Image.new("RGB", (tile_w, tile_h), color=(30, 30, 30))

            if not cover_id:
                imgs.append(placeholder_img)
                continue

            try:
                data = self.getCoverData(cover_id, str(tile_w), str(tile_h))
                if data:
                    img: Image.Image = Image.open(BytesIO(data))
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    img.thumbnail((tile_w, tile_h), Image.Resampling.LANCZOS)
                    bg: Image.Image = Image.new(
                        "RGB", (tile_w, tile_h), color=(30, 30, 30)
                    )
                    x = (tile_w - img.width) // 2
                    y = (tile_h - img.height) // 2
                    bg.paste(img, (x, y))
                    imgs.append(bg)
                else:
                    logger.warning(
                        f"Failed to get cover data for SID {cover_id} (playlist {playlist_id})."
                    )
                    imgs.append(placeholder_img)
            except Exception as e:
                logger.warning(
                    f"Error fetching/processing cover {cover_id} for playlist {playlist_id}: {e}"
                )
                imgs.append(placeholder_img)

        collage: Image.Image = Image.new("RGB", (w, h), color=(30, 30, 30))
        for idx, img in enumerate(imgs):
            if idx < len(paste_coords):
                collage.paste(img, paste_coords[idx])

        buf = BytesIO()
        try:
            collage.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Failed to save playlist cover collage to buffer: {e}")
            placeholder = Image.new("RGB", (width, height), color=(30, 30, 30))
            buf = BytesIO()
            placeholder.save(buf, format="JPEG")
            return buf.getvalue()

    def getArtistsName(self, artists: Optional[List[Artist]] = None) -> str:
        if not artists:
            return ""
        array: List[str] = [
            str(item.name) for item in artists if hasattr(item, "name") and item.name
        ]
        return ", ".join(array)

    def getFlag(
        self, data: Any, type: Type, short: bool = True, separator: str = " / "
    ) -> str:
        master = False
        atmos = False
        explicit = False

        if type == Type.Album or type == Type.Track:
            audio_quality = getattr(data, "audioQuality", None)
            if audio_quality == AudioQuality.HI_RES_LOSSLESS.value:
                master = True
            audio_modes = getattr(data, "audioModes", [])
            if (
                type == Type.Album
                and isinstance(audio_modes, list)
                and "DOLBY_ATMOS" in audio_modes
            ):
                atmos = True
            if getattr(data, "explicit", False) is True:
                explicit = True

        if not master and not atmos and not explicit:
            return ""

        array: List[str] = []
        if master:
            array.append("M" if short else "Master")
        if atmos:
            array.append("A" if short else "Dolby Atmos")
        if explicit:
            array.append("E" if short else "Explicit")
        return separator.join(array)

    def parseUrl(self, url: str) -> Tuple[Type, str]:
        if "tidal.com" not in url:
            return Type.Null, url
        url = url.lower()
        path_part = url.split("tidal.com/", 1)[-1]

        for item_type in Type:
            if item_type == Type.Null:
                continue
            type_name_lower = item_type.name.lower()
            if path_part.startswith(type_name_lower + "/"):
                id_part = (
                    path_part[len(type_name_lower) + 1 :].split("/")[0].split("?")[0]
                )
                if id_part:
                    return item_type, id_part

        logger.warning(f"Could not parse Tidal type/ID from URL path: {path_part}")
        potential_id = path_part.split("/")[-1].split("?")[0]
        return Type.Null, potential_id if potential_id else url

    def getByString(self, string: str) -> Tuple[Type, Any]:
        if aigpy.string.isNull(string):
            raise ValueError("Please enter something.")

        obj: Any = None
        etype: Type
        sid: str

        if "tidal.com" in string or "listen.tidal.com" in string:
            etype, sid = self.parseUrl(string)
            if etype != Type.Null:
                try:
                    obj = self.getTypeData(sid, etype)
                    if obj:
                        return etype, obj
                    else:
                        logger.warning(
                            f"getByString: getTypeData returned None for parsed type {etype} and ID {sid}."
                        )
                except Exception as e:
                    logger.warning(
                        f"getByString: Failed to get {etype.name} with ID {sid} from URL: {e}"
                    )
            search_id = sid if "sid" in locals() else string
        else:
            etype = Type.Null
            search_id = string

        if etype == Type.Null or obj is None:
            for item_type_to_try in Type:
                if item_type_to_try == Type.Null:
                    continue
                try:
                    obj = self.getTypeData(search_id, item_type_to_try)
                    if obj:
                        return item_type_to_try, obj
                except Exception:
                    continue

        raise Exception(f"No result found for '{string}' as a direct URL or ID.")


# Singleton
TIDAL_API = TidalAPI()


# Add type hints for parameters
def start_type(s_type: Type, item: Any):
    """
    Initiates download based on the type and item.
    For simplicity, this function calls the 'start' function from the download module.
    """
    try:
        from .download import start as start_download  # type: ignore[attr-defined]
    except ImportError as e:
        logger.error(f"Failed to import start function from .download: {e}")
        raise e

    start_download(item)
