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
from xml.etree import ElementTree as ET

import os  # Needed for env overrides
from . import apiKey  # Use the central table

import logging

# Create a logger instance for this module
logger = logging.getLogger(__name__)
logger.setLevel(
    logging.ERROR
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

# Constants from the new implementation
BASE = "https://api.tidalhifi.com/v1"
AUTH_URL = "https://auth.tidal.com/v1/oauth2"


class TidalAPI(object):
    def __init__(self):
        # Treat LoginKey dynamically to avoid strict attribute-assignment complaints
        self.key: Any = LoginKey()  # type: ignore[assignment]
        
        # Initialize apiKey as an empty dictionary.
        # The key will be properly set by the logic in 'login.py' after settings are loaded.
        # This prevents the class from incorrectly selecting the invalid key at index 0 on startup.
        self.apiKey = {}

        logger.debug(f"TIDAL_API.apiKey initialized empty.")
        
        # Runtime scope (same default as your code)
        self._scope = os.getenv("TIDAL_SCOPE", "r_usr+w_usr+w_sub")

        # --- START MODIFICATION ---
        # Create a persistent session object for all requests
        self.session = requests.Session()
        # Set the "chameleon" User-Agent header for the entire session
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0"
        })
        # --- END MODIFICATION ---

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
        params: Optional[Dict[str, Any]] = None,
        urlpre: str = "https://api.tidalhifi.com/v1/",
        *,
        return_raw: bool = False,
    ) -> Union[Dict[str, Any], Tuple[Dict[str, Any], str]]:
        if params is None:
            params = {}
        """Internal method to perform GET requests. Optionally returns raw response text."""
        params["countryCode"] = self.key.countryCode
        errmsg: str = "Get operation err!"
        raw: str = ""
        result: Dict[str, Any] = {}
        respond: Optional[requests.Response] = None

        for attempt in range(0, 3):
            try:
                # --- MODIFICATION: Use the session object ---
                respond = self.session.get(urlpre + path, params=params, timeout=20)

                # FIX: Handle 404 gracefully for optional data like lyrics
                if respond.status_code == 404:
                    logger.debug(f"TIDAL API returned 404 Not Found for path: {path}")
                    if return_raw:
                        return {}, ""
                    else:
                        return {}
                
                if respond.status_code == 401:
                    raise Exception("TIDAL: Unauthorized - may be due to geo restrictions or quality not available with current subscription")
                if respond.status_code == 429:
                    retry_after = int(respond.headers.get('Retry-After', '2'))
                    wait_time = min(retry_after, 60)
                    logger.warning(f"Rate limited on GET {path}, waiting {wait_time}s")
                    time.sleep(wait_time)
                    continue
                
                # Raise other HTTP errors
                respond.raise_for_status()

                raw = respond.text
                if not raw.strip():
                    continue  # Retry if empty

                result = json.loads(raw)

                if not isinstance(result, dict):
                    logger.warning(f"__get__ received non-dict JSON response: {result}")
                    continue  # Retry if not a dictionary

                # If 'status' key exists, check for API errors
                if "status" in result and result["status"] != 200:
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
                if attempt >= 2:
                    raise Exception(errmsg) from e
                time.sleep(1)
                continue
            except Exception as e:
                logger.error(f"__get__ unexpected error: {e}", exc_info=True)
                errmsg += f" | Unexpected error: {e}"
                if attempt >= 2:
                    if respond is not None:
                        errmsg += f" | Last raw response: {respond.text[:100]}..."
                    raise Exception(errmsg) from e
                time.sleep(1)
                continue

        final_errmsg = f"Get operation failed after multiple retries: {errmsg}"
        if raw:
            final_errmsg += f" | Last raw response snippet: {raw[:100]}..."
        raise Exception(final_errmsg)

    def __getQuality__(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        urlpre: str = "https://api.tidalhifi.com/v1/",
    ) -> Dict[str, Any]:
        if params is None:
            params = {}
        # header = {"authorization": f"Bearer {self.key.accessToken}"} # REMOVED
        params["countryCode"] = self.key.countryCode
        errmsg: str = "GetQuality operation err!"
        respond: Optional[requests.Response] = None
        result: Dict[str, Any] = {}

        for index in range(0, 3):
            try:
                # --- MODIFICATION: Use the session object ---
                respond = self.session.get(urlpre + path, params=params, timeout=15)
                if (
                    respond.url.find("playbackinfo") != -1
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

    def __getItems__(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
        if params is None:
            params = {}
        params["limit"] = 100 # Use a larger limit as per new code
        params["offset"] = 0
        total: int = 0
        ret: List[Any] = []
        while True:
            data: Dict[str, Any] = self.__get__(
                path, params
            )
            if not data:
                print("[WARN] __getItems__: No data received from API.")
                return []
            
            # Handle two possible keys for total items
            if "totalNumberOfItems" in data:
                total = data["totalNumberOfItems"]
            elif "numberOfTracks" in data:
                total = data["numberOfTracks"]
            
            items = data.get("items")
            if not items or not isinstance(items, list):
                # If items are missing, it might be the only page, so return what we have
                return ret

            ret.extend(items)
            num = len(items)

            if total > 0 and len(ret) >= total:
                return ret[:total]

            if num < params["limit"]:
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
                # --- MODIFICATION: Use the session object ---
                response = self.session.post(urlpre + path, data=data, auth=auth, timeout=15)
                
                # Don't raise for status immediately, as we need to inspect the body for polling errors
                result = response.json()
                
                # If there's a non-200 status and it's not a known polling error, then raise
                if response.status_code != 200:
                    if result.get("error") == "authorization_pending":
                        return result # Return pending error to be handled by caller
                    if result.get("sub_status") == 1002: # Tidal-specific pending error
                        return result
                
                response.raise_for_status()  # Raises HTTPError for other 4xx/5xx responses
                return result
            except requests.exceptions.HTTPError as e:
                logger.error(f"__post__ HTTP error for URL: {e.request.url}")
                logger.error(f"Status Code: {e.response.status_code}")
                logger.error(f"Response Body: {e.response.text}")
                if index == 2:
                    raise e
                time.sleep(1)
            except requests.exceptions.RequestException as e:
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
        if not self.apiKey or "clientId" not in self.apiKey:
            raise RuntimeError("TIDAL API key not set yet. Load/select an apiKey before calling this method.")
        logger.debug("Using client_id: %s", self.apiKey.get("clientId"))
        data: Dict[str, str] = {
            "client_id": self.apiKey["clientId"],
            "scope": self._scope,
        }
        result = self.__post__("/device_authorization", data, urlpre=AUTH_URL)
        if result.get("status", 200) != 200:
            raise Exception(
                f"Device authorization failed. Status: {result.get('status')}, Message: {result.get('userMessage', 'N/A')}. Please choose another apikey."
            )

        device_code = result.get("deviceCode")
        user_code = result.get("userCode")
        verification_uri_complete = result.get("verificationUriComplete")
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
        return verification_uri_complete or f"https://{verification_uri}/{user_code}"

    def checkAuthStatus(self) -> str:
        """
        Checks the device authorization status.
        Returns: "SUCCESS", "PENDING", or raises on hard error.
        """
        if not self.apiKey or "clientId" not in self.apiKey or "clientSecret" not in self.apiKey:
            raise RuntimeError("TIDAL API key not set yet. Load/select an apiKey before calling this method.")
        
        data: Dict[str, Any] = {
            "client_id": self.apiKey["clientId"],
            "device_code": self.key.deviceCode,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "scope": self._scope,
        }
        auth = (self.apiKey["clientId"], self.apiKey["clientSecret"])

        result = self.__post__("/token", data, auth=auth, urlpre=AUTH_URL)

        # Handle pending authorization based on new logic
        if result.get("status", 200) != 200 and result.get("sub_status") == 1002:
            return "PENDING"
        if result.get("error") == "authorization_pending":
            return "PENDING"
        
        # Handle other errors
        if result.get("status", 200) != 200 or "error" in result:
            error_message = result.get("userMessage") or result.get("error_description", "Unknown error")
            raise Exception(f"Authentication failed: {error_message}")

        # Success - extract token data
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

        # Update the persistent session with the new Bearer token
        self.session.headers.update({"authorization": f"Bearer {self.key.accessToken}"})

        return "SUCCESS"

    def verifyAccessToken(self, accessToken: str) -> bool:
        header = {"authorization": f"Bearer {accessToken}"}
        try:
            # Use the main session object for consistency
            response = self.session.get(f"{BASE}/sessions", headers=header)
            response.raise_for_status()
            result = response.json()
            if result.get("status", 200) != 200:
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
        if not self.apiKey or "clientId" not in self.apiKey or "clientSecret" not in self.apiKey:
            raise RuntimeError("TIDAL API key not set yet. Load/select an apiKey before calling this method.")
        
        data: Dict[str, str] = {
            "client_id": self.apiKey["clientId"],
            "refresh_token": refreshToken,
            "grant_type": "refresh_token",
            "scope": self._scope,
        }
        auth = (self.apiKey["clientId"], self.apiKey["clientSecret"])
        try:
            result = self.__post__("/token", data, auth=auth, urlpre=AUTH_URL)

            if result.get("status", 200) != 200:
                raise Exception(f"Refresh failed: {result.get('userMessage', 'Unknown error')}")

            access_token = result.get("access_token")
            expires_in = result.get("expires_in")

            if not access_token:
                logger.error("refreshAccessToken response missing access token.")
                return False

            self.key.accessToken = access_token
            self.key.expiresIn = (expires_in or 0) + int(time.time())

            # Update the persistent session with the new Bearer token
            self.session.headers.update({"authorization": f"Bearer {self.key.accessToken}"})
            return True

        except Exception as e:
            logger.error(f"refreshAccessToken encountered an exception: {e}", exc_info=True)
            return False

    def loginByAccessToken(
        self, accessToken: str, userid: Union[str, int, None] = None
    ) -> None:
        # Temporarily set header for this one request
        temp_headers = dict(self.session.headers)
        temp_headers["authorization"] = f"Bearer {accessToken}"
        
        try:
            response = self.session.get(f"{BASE}/sessions", headers=temp_headers)
            response.raise_for_status()
            result = response.json()

            if result.get("status", 200) != 200:
                raise Exception(
                    f"Login failed! Status: {result.get('status')}, Message: {result.get('userMessage', 'N/A')}"
                )

            session_user_id = result.get("userId")
            country_code = result.get("countryCode")

            if session_user_id is None or country_code is None:
                raise Exception("Login failed: Session response missing userId or countryCode.")

            if userid is not None and str(session_user_id) != str(userid):
                raise Exception(
                    f"User mismatch! Token belongs to user {session_user_id}, expected {userid}. Please use your own access token."
                )

            self.key.userId = session_user_id
            self.key.countryCode = country_code
            self.key.accessToken = accessToken
            self.key.refreshToken = None # Refresh token is not known here
            self.key.expiresIn = 0

            # Update the persistent session with the new Bearer token
            self.session.headers.update({"authorization": f"Bearer {self.key.accessToken}"})

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
            typeStr = "ARTISTS,ALBUMS,TRACKS,PLAYLISTS"

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
        path_map = {
            Type.Playlist: f"playlists/{id}/items",
            Type.Album: f"albums/{id}/items",
            Type.Mix: f"mixes/{id}/items",
        }
        path = path_map.get(type)
        if not path:
            raise ValueError(f"invalid Type '{type}' for getItems!")

        raw_data = self.__getItems__(path)
        data = [item for item in raw_data if isinstance(item, dict)]

        tracks: List[Track] = []
        videos: List[Any] = []
        for item_dict in data:
            # Handle both { "item": {...}, "type": "track" } and direct track objects
            item_content = item_dict.get("item", item_dict)
            item_type = item_dict.get("type", "track") # Assume track if type is missing

            if not isinstance(item_content, dict):
                logger.warning(f"Skipping item with invalid content: {item_dict}")
                continue

            if item_type == "track":
                if item_content.get("streamReady"):
                    track_model = aigpy.model.dictToModel(item_content, Track())
                    if track_model:
                        track = cast(Track, track_model)
                        track.mediaMetadata = item_content.get("mediaMetadata", {})
                        tracks.append(track)
                    else:
                        logger.warning(f"Failed to convert track item to model: {item_content}")
                else:
                    logger.debug(f"Skipping track item not streamReady: {item_content.get('id')}")
            else:
                logger.debug(f"Skipping item with unknown type '{item_type}': {item_content.get('id')}")

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

    def _parse_dash_manifest(self, manifest_b64: str) -> dict:
        """Parse DASH XML manifest into a format compatible with getStreamUrl."""
        try:
            manifest_xml = base64.b64decode(manifest_b64).decode("utf-8")
            root = ET.fromstring(manifest_xml)

            ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}

            representation = root.find(".//mpd:Representation", ns)
            if representation is None:
                raise Exception("No Representation found in DASH manifest")

            codecs = representation.get("codecs", "flac")

            segment_template = representation.find(".//mpd:SegmentTemplate", ns)
            if segment_template is None:
                raise Exception("No SegmentTemplate found in DASH manifest")

            media_template = segment_template.get("media")
            if media_template is None:
                raise Exception("No media template found in DASH manifest")
            initialization = segment_template.get("initialization")
            start_number = int(segment_template.get("startNumber", "0"))

            timeline = segment_template.find("mpd:SegmentTimeline", ns)
            if timeline is None:
                raise Exception("No SegmentTimeline found in DASH manifest")

            segments = timeline.findall("mpd:S", ns)
            segment_urls = [initialization] if initialization else []
            segment_number = start_number

            for seg in segments:
                repeat = int(seg.get("r", "0"))
                num_segments = repeat + 1
                for _ in range(num_segments):
                    url = media_template.replace("$Number$", str(segment_number))
                    segment_urls.append(url)
                    segment_number += 1

            return {
                "urls": segment_urls,
                "codecs": codecs,
                "encryptionType": "NONE",
                "mimeType": f"audio/{codecs}",
            }
        except ET.ParseError as e:
            raise Exception(f"Failed to parse DASH XML manifest: {e}")
        except Exception as e:
            raise Exception(f"Error parsing DASH manifest: {e}")

    def getStreamUrl(self, id: str, quality: AudioQuality) -> StreamUrl:
        params: Dict[str, str] = {
            "audioquality": quality.value,
            "playbackmode": "STREAM",
            "assetpresentation": "FULL",
        }
        data: Dict[str, Any] = self.__get__(f"tracks/{str(id)}/playbackinfo", params)

        resp_model = aigpy.model.dictToModel(data, StreamRespond())
        if not resp_model:
            raise Exception("Failed to convert playback info response to StreamRespond model.")
        resp = cast(StreamRespond, resp_model)

        ret = cast(Any, StreamUrl())
        ret.trackid = resp.trackid
        ret.soundQuality = resp.audioQuality
        ret.manifestMimeType = resp.manifestMimeType

        if resp.manifest is None:
            raise Exception(f"Manifest is missing for track {id}. User message: {data.get('userMessage', 'N/A')}")

        manifest_b64 = resp.manifest
        manifest_mime = resp.manifestMimeType or "application/vnd.tidal.bts"

        manifest_data = {}
        try:
            if "dash+xml" in manifest_mime:
                manifest_data = self._parse_dash_manifest(manifest_b64)
            else:  # Default to bts
                manifest_decoded = base64.b64decode(manifest_b64).decode("utf-8")
                manifest_data = json.loads(manifest_decoded)
        except (Exception, binascii.Error, json.JSONDecodeError) as e:
            error_msg = data.get("userMessage", str(e))
            raise Exception(f"Failed to decode or parse manifest for track {id}: {error_msg}") from e

        urls = manifest_data.get("urls", [])
        if not isinstance(urls, list) or not urls:
            raise Exception("Manifest does not contain any URLs.")

        ret.url = urls[0]
        ret.urls = urls
        ret.codec = manifest_data.get("codecs")
        ret.encryptionKey = manifest_data.get("keyId") or manifest_data.get("encryptionKey", "")
        
        return ret

    def getQualityStreamUrl(self, id: str, quality: AudioQuality) -> StreamUrl:
        squality = AudioQuality.HI_RES_LOSSLESS.value
        paras: Dict[str, str] = {
            "audioquality": squality,
            "playbackmode": "STREAM",
            "assetpresentation": "FULL",
        }
        data: Dict[str, Any] = self.__getQuality__(
            f"tracks/{str(id)}/playbackinfo", paras
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

    def getTrackContributors(self, id: str) -> Dict[str, Any]:
        data: Dict[str, Any] = self.__get__(f"tracks/{str(id)}/contributors")
        return data

    def getCoverUrl(
        self, sid: Optional[str], width: str = "320", height: str = "320"
    ) -> str:
        if not sid:
            return ""
        
        # If 'sid' is already a full URL, return it directly.
        if sid.startswith("http"):
            return sid
        
        # The API can provide SIDs with slashes or hyphens. Normalize to hyphens for validation.
        hyphenated_sid = sid.replace('/', '-')

        # Check if 'hyphenated_sid' is a valid Tidal UUID.
        if not re.match(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            hyphenated_sid,
        ):
            logger.warning(
                f"Invalid cover SID format: '{sid}'. Not a valid UUID or a full URL."
            )
            return ""
            
        if not width.isdigit() or not height.isdigit():
            logger.warning(f"Invalid width/height for cover URL: {width}x{height}")
            width, height = "320", "320"
        
        # The final URL requires slashes and should be lowercase.
        url_path_sid = hyphenated_sid.lower().replace('-', '/')
        
        return f"https://resources.tidal.com/images/{url_path_sid}/{width}x{height}.jpg"
    
    def getCoverData(
        self, sid: Optional[str], width: str = "320", height: str = "320"
    ) -> bytes:
        url = self.getCoverUrl(sid, width, height)
        if not url:
            return b""
        try:
            # --- START OF MODIFICATION ---
            # Use the main, authenticated session to download the cover.
            # This automatically includes the User-Agent and Authorization: Bearer token.
            response = self.session.get(url, timeout=15)
            # --- END MODIFICATION ---

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