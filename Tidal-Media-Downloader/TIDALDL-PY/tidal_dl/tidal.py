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
import base64
import binascii
import json
import logging
import os
import random
import re
import requests
import threading
import time
import urllib3
from io import BytesIO

import aigpy
from PIL import Image
from typing import (
    Any,
    cast,
    Dict,
    List,
    Literal,
    Optional,
    overload,
    Tuple,
    Union,
)
from xml.etree import ElementTree as ET

from tidal_dl import apiKey
from tidal_dl.enums import AudioQuality, Type
from tidal_dl.model import (
    Album,
    Artist,
    Lyrics,
    LoginKey,
    Mix,
    Playlist,
    SearchResult,
    StreamRespond,
    StreamUrl,
    Track,
)
from tidal_dl.settings import SETTINGS

# Create a logger instance for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

TIDAL_COVER_ALLOWED_SIZES: Tuple[int, ...] = (80, 160, 320, 640, 1280)

# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from tidal_dl.gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# SSL Warnings | retry number
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Constants from the new implementation
BASE = "https://api.tidalhifi.com/v1"
AUTH_URL = "https://auth.tidal.com/v1/oauth2"
OPENAPI_BASE = "https://openapi.tidal.com/v2"


class NonRetriableApiError(Exception):
    """Raised for API errors that should not be retried."""


class TidalAPI(object):
    def __init__(self):
        # Treat LoginKey dynamically to avoid strict attribute-assignment complaints
        self.key: Any = LoginKey()  # type: ignore[assignment]
        
        # Initialize apiKey as an empty dictionary.
        # The key will be properly set by the logic in 'login.py' after settings are loaded.
        # This prevents the class from incorrectly selecting the invalid key at index 0 on startup.
        self.apiKey = {}
        self._openapi_genre_cache: Dict[str, List[str]] = {}

        logger.debug(f"TIDAL_API.apiKey initialized empty.")
        
        # Runtime OAuth scope. Use OAuth-standard space-separated scopes; requests
        # encodes spaces correctly for application/x-www-form-urlencoded bodies.
        self._scope = os.getenv("TIDAL_SCOPE", "r_usr w_usr w_sub")

        # --- START MODIFICATION ---
        # Create a persistent session object for all requests
        self.session = requests.Session()
        # Set the "chameleon" User-Agent header for the entire session
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0"
        })
        self._cover_failure_lock = threading.Lock()
        self._cover_failure_until: Dict[str, float] = {}
        # --- END MODIFICATION ---

    def _is_cover_temporarily_blocked(self, cover_key: str) -> bool:
        now = time.monotonic()
        with self._cover_failure_lock:
            blocked_until = self._cover_failure_until.get(cover_key)
            if not blocked_until:
                return False
            if blocked_until <= now:
                self._cover_failure_until.pop(cover_key, None)
                return False
            return True

    def _mark_cover_failure(self, cover_key: str, ttl_seconds: int = 120) -> None:
        with self._cover_failure_lock:
            self._cover_failure_until[cover_key] = time.monotonic() + max(1, ttl_seconds)

    def _clear_cover_failure(self, cover_key: str) -> None:
        with self._cover_failure_lock:
            self._cover_failure_until.pop(cover_key, None)

    def _is_token_expired_response(self, response: requests.Response) -> bool:
        """Detects whether a 401 response indicates an expired access token."""
        if response.status_code != 401:
            return False

        try:
            payload = response.json()
        except Exception:
            payload = {}

        sub_status = payload.get("subStatus") or payload.get("sub_status")
        user_message = str(payload.get("userMessage", "")).lower()
        text = (response.text or "").lower()

        return (
            sub_status == 11003
            or "token has expired" in user_message
            or "expired" in user_message
            or '"substatus":11003' in text
            or '"sub_status":11003' in text
            or "token has expired" in text
        )

    def _get_request_optional_hint(self, path: str, urlpre: str) -> str:
        """Best-effort hint for whether a failing GET is optional/non-critical."""
        normalized_path = str(path or "").strip("/").lower()
        normalized_host = str(urlpre or "").lower()

        if normalized_path.endswith("/lyrics") or "listen.tidal.com" in normalized_host:
            return "known_optional:lyrics_metadata"
        if normalized_path.endswith("/contributors"):
            return "known_optional:contributors_metadata"
        if "/playbackinfo" in normalized_path:
            return "critical:playback_stream"
        if "/items" in normalized_path:
            return "unknown:items_endpoint_may_be_playlist_download_or_cover_collage"
        if normalized_path.startswith("users/") and normalized_path.endswith("/playlists"):
            return "critical:startup_playlist_refresh"
        return "unknown"

    def _log_unauthorized_get_diagnostic(
        self,
        *,
        path: str,
        urlpre: str,
        params: Dict[str, Any],
        response: requests.Response,
        token_expired_detected: bool,
    ) -> None:
        """Logs endpoint-level context for 401 responses without exposing tokens."""
        response_text = (response.text or "").replace("\r", " ").replace("\n", " ")
        response_text = response_text[:300]
        request_url = getattr(response, "url", "") or ""
        optional_hint = self._get_request_optional_hint(path, urlpre)
        api_profile = "unknown"
        try:
            api_profile = str(self.apiKey.get("platform", "unknown")) if self.apiKey else "unset"
        except Exception:
            api_profile = "unknown"

        redacted_params = dict(params or {})
        for sensitive_key in ("access_token", "token", "client_secret", "refresh_token"):
            if sensitive_key in redacted_params:
                redacted_params[sensitive_key] = "<redacted>"

        logger.error(
            "TIDAL_401_DIAG path=%s urlpre=%s status=%s request_url=%s optional_hint=%s "
            "token_expired_detected=%s api_profile=%s params=%s response_body=%s",
            path,
            urlpre,
            response.status_code,
            request_url,
            optional_hint,
            token_expired_detected,
            api_profile,
            redacted_params,
            response_text,
        )

    def _persist_runtime_token(self) -> None:
        """Persists refreshed runtime token data to token settings file."""
        try:
            from tidal_dl.settings import TOKEN

            if self.key.userId is not None:
                TOKEN.userid = self.key.userId
            if self.key.countryCode is not None:
                TOKEN.countryCode = self.key.countryCode
            if self.key.accessToken:
                TOKEN.accessToken = self.key.accessToken
            if self.key.refreshToken:
                TOKEN.refreshToken = self.key.refreshToken

            if self.key.expiresIn:
                TOKEN.expiresAfter = int(time.time()) + int(self.key.expiresIn)

            TOKEN.apiKeyIndex = SETTINGS.apiKeyIndex if isinstance(SETTINGS.apiKeyIndex, int) else 0
            TOKEN.save()
        except Exception as e:
            logger.warning(f"Could not persist refreshed token to file: {e}")

    def _try_refresh_after_unauthorized(self) -> bool:
        """Attempts a single token refresh for expired-token 401 responses."""
        refresh_token = self.key.refreshToken

        if not refresh_token:
            try:
                from tidal_dl.settings import TOKEN

                refresh_token = TOKEN.refreshToken
            except Exception:
                refresh_token = None

        if not refresh_token:
            logger.warning(
                "401 due to expired token, but no refresh token is available."
            )
            return False

        logger.info("Access token expired. Attempting automatic refresh...")
        refreshed = self.refreshAccessToken(refresh_token)
        if refreshed:
            self._persist_runtime_token()
            logger.info("Automatic token refresh succeeded.")
            return True

        logger.warning("Automatic token refresh failed.")
        return False

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
        refresh_attempted = False

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
                    token_expired_detected = self._is_token_expired_response(respond)
                    self._log_unauthorized_get_diagnostic(
                        path=path,
                        urlpre=urlpre,
                        params=params,
                        response=respond,
                        token_expired_detected=token_expired_detected,
                    )
                    if token_expired_detected:
                        if not refresh_attempted and self._try_refresh_after_unauthorized():
                            refresh_attempted = True
                            continue

                        raise NonRetriableApiError(
                            "TIDAL: Access token expired and refresh failed. Please log in again."
                        )

                    raise NonRetriableApiError(
                        "TIDAL: Unauthorized - may be due to geo restrictions or quality not available with current subscription"
                    )
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
            except NonRetriableApiError as e:
                logger.error(f"__get__ non-retriable error: {e}")
                errmsg += f" | {e}"
                if respond is not None and respond.text:
                    errmsg += f" | Last raw response: {respond.text[:200]}..."
                raise Exception(errmsg) from e
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
        refresh_attempted = False

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
                if respond.status_code == 401:
                    if self._is_token_expired_response(respond):
                        if not refresh_attempted and self._try_refresh_after_unauthorized():
                            refresh_attempted = True
                            continue
                        raise NonRetriableApiError(
                            "TIDAL: Access token expired and refresh failed during quality retrieval. Please log in again."
                        )

                    raise NonRetriableApiError(
                        "TIDAL: Unauthorized during quality retrieval."
                    )
                result = json.loads(respond.text)
                if not isinstance(result, dict):
                    continue
                if "status" not in result:
                    return result
                if "userMessage" in result and result["userMessage"] is not None:
                    errmsg += str(result["userMessage"])
                break
            except NonRetriableApiError as e:
                errmsg += f" | {e}"
                if respond is not None:
                    errmsg += f" | Last raw response: {respond.text[:200]}..."
                raise Exception(errmsg) from e
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

    def _post_oauth_token(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.apiKey or "clientId" not in self.apiKey or "clientSecret" not in self.apiKey:
            raise RuntimeError("TIDAL API key not set yet. Load/select an apiKey before calling this method.")

        token_data = dict(data)
        token_data["client_id"] = self.apiKey["clientId"]
        token_data["client_secret"] = self.apiKey["clientSecret"]

        try:
            return self.__post__("/token", token_data, urlpre=AUTH_URL)
        except Exception as client_secret_post_error:
            logger.warning(
                "OAuth token request using client_secret_post failed for API profile %s. Retrying with HTTP Basic auth.",
                self.apiKey.get("platform", "Unknown"),
            )
            legacy_data = dict(data)
            legacy_data["client_id"] = self.apiKey["clientId"]
            auth = (self.apiKey["clientId"], self.apiKey["clientSecret"])
            try:
                return self.__post__("/token", legacy_data, auth=auth, urlpre=AUTH_URL)
            except Exception as basic_auth_error:
                raise basic_auth_error from client_secret_post_error

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
        if not verification_uri_complete:
            verification_uri_complete = result.get("verification_uri_complete")
        if not verification_uri:
            verification_uri = result.get("verification_uri")
        expires_in = result.get("expiresIn")
        if expires_in is None:
            expires_in = result.get("expires_in")
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
            "device_code": self.key.deviceCode,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "scope": self._scope,
        }

        result = self._post_oauth_token(data)

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
        user_info = result.get("user") if isinstance(result.get("user"), dict) else {}
        access_token = result.get("access_token")
        refresh_token = result.get("refresh_token")
        expires_in = result.get("expires_in")

        if not access_token:
            raise Exception(
                "Authorization response missing required access token."
            )

        user_id = user_info.get("userId") or result.get("userId") or result.get("user_id")
        country_code = user_info.get("countryCode") or result.get("countryCode") or result.get("country_code")

        if user_id is None or country_code is None:
            session_header = {"authorization": f"Bearer {access_token}"}
            session_response = self.session.get(f"{BASE}/sessions", headers=session_header, timeout=15)
            session_response.raise_for_status()
            session_result = session_response.json()
            user_id = session_result.get("userId")
            country_code = session_result.get("countryCode")

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
            "refresh_token": refreshToken,
            "grant_type": "refresh_token",
            "scope": self._scope,
        }
        try:
            result = self._post_oauth_token(data)

            if result.get("status", 200) != 200:
                raise Exception(f"Refresh failed: {result.get('userMessage', 'Unknown error')}")

            access_token = result.get("access_token")
            expires_in = result.get("expires_in")
            country_code = result.get("countryCode") or result.get("country_code")
            user_id = result.get("userId") or result.get("user_id")

            if not access_token:
                logger.error("refreshAccessToken response missing access token.")
                return False

            self.key.accessToken = access_token
            # Store duration, not timestamp, to be consistent with checkAuthStatus and saveToken
            self.key.expiresIn = expires_in if expires_in is not None else 0
            
            # Capture new refresh token if provided (rolling tokens)
            new_refresh_token = result.get("refresh_token")
            if new_refresh_token:
                self.key.refreshToken = new_refresh_token

            if country_code is None or user_id is None:
                with requests.Session() as temp_session:
                    temp_session.headers.update(dict(self.session.headers))
                    temp_session.headers.update({"authorization": f"Bearer {access_token}"})
                    session_response = temp_session.get(f"{BASE}/sessions", timeout=15)
                    session_response.raise_for_status()
                    session_result = session_response.json()
                    country_code = session_result.get("countryCode")
                    user_id = session_result.get("userId")

            if country_code is not None:
                self.key.countryCode = country_code
            if user_id is not None:
                self.key.userId = user_id

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

    def __get_openapi__(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Best-effort OpenAPI v2 GET helper for metadata sidecar requests."""
        request_params = dict(params or {})
        url = f"{OPENAPI_BASE}/{path.lstrip('/')}"
        refresh_attempted = False

        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self.key.accessToken}",
                "Accept": "application/vnd.api+json",
            }
            try:
                response = self.session.get(
                    url,
                    params=request_params,
                    headers=headers,
                    timeout=15,
                )

                if response.status_code == 401:
                    if self._is_token_expired_response(response):
                        if not refresh_attempted and self._try_refresh_after_unauthorized():
                            refresh_attempted = True
                            continue
                    logger.debug(
                        "OpenAPI metadata request unauthorized for path=%s status=%s body=%s",
                        path,
                        response.status_code,
                        response.text[:300],
                    )
                    return {}

                if response.status_code in (403, 404):
                    logger.debug(
                        "OpenAPI metadata request unavailable for path=%s status=%s body=%s",
                        path,
                        response.status_code,
                        response.text[:300],
                    )
                    return {}

                if response.status_code == 429 and attempt == 0:
                    time.sleep(1)
                    continue

                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}

            except Exception as exc:
                logger.debug(
                    "OpenAPI metadata request failed for path=%s: %s",
                    path,
                    exc,
                    exc_info=True,
                )
                return {}

        return {}

    @staticmethod
    def _openapi_genre_names_from_payload(payload: Dict[str, Any]) -> List[str]:
        """Extract genre names from a JSON:API document if included resources are present."""
        names: List[str] = []

        def add_name(resource: Any) -> None:
            if not isinstance(resource, dict):
                return
            if str(resource.get("type", "")).lower() != "genres":
                return
            attributes = resource.get("attributes")
            if not isinstance(attributes, dict):
                return
            for key in ("name", "title", "label", "value"):
                value = attributes.get(key)
                if isinstance(value, str) and value.strip():
                    names.append(value.strip())
                    return

        add_name(payload.get("data"))

        included = payload.get("included")
        if isinstance(included, list):
            for resource in included:
                add_name(resource)

        return list(dict.fromkeys(names))

    @staticmethod
    def _openapi_genre_ids_from_payload(payload: Dict[str, Any]) -> List[str]:
        """Extract genre IDs from JSON:API data or relationships."""
        ids: List[str] = []

        def add_id(resource: Any) -> None:
            if not isinstance(resource, dict):
                return
            if str(resource.get("type", "")).lower() != "genres":
                return
            genre_id = resource.get("id")
            if genre_id is not None and str(genre_id).strip():
                ids.append(str(genre_id).strip())

        data = payload.get("data")
        if isinstance(data, list):
            for resource in data:
                add_id(resource)
        else:
            add_id(data)
            if isinstance(data, dict):
                relationships = data.get("relationships")
                if isinstance(relationships, dict):
                    genres_rel = relationships.get("genres")
                    if isinstance(genres_rel, dict):
                        rel_data = genres_rel.get("data")
                        if isinstance(rel_data, list):
                            for resource in rel_data:
                                add_id(resource)
                        else:
                            add_id(rel_data)

        return list(dict.fromkeys(ids))

    def getTrackGenresOpenApi(self, id: str, locale: str = "en-US") -> List[str]:
        """Return track genres from OpenAPI v2 as a best-effort metadata sidecar."""
        track_id = str(id or "").strip()
        if not track_id:
            return []

        if track_id in self._openapi_genre_cache:
            return self._openapi_genre_cache[track_id]

        country_code = str(getattr(self.key, "countryCode", "") or "").strip()
        params: Dict[str, Any] = {"include": "genres"}
        if country_code:
            params["countryCode"] = country_code

        track_payload = self.__get_openapi__(f"tracks/{track_id}", params=params)
        genre_names = self._openapi_genre_names_from_payload(track_payload)
        genre_ids = self._openapi_genre_ids_from_payload(track_payload)

        if not genre_names:
            relationship_params: Dict[str, Any] = {"include": "genres"}
            if country_code:
                relationship_params["countryCode"] = country_code
            relationship_payload = self.__get_openapi__(
                f"tracks/{track_id}/relationships/genres",
                params=relationship_params,
            )
            genre_names = self._openapi_genre_names_from_payload(relationship_payload)
            genre_ids.extend(self._openapi_genre_ids_from_payload(relationship_payload))
            genre_ids = list(dict.fromkeys(genre_ids))

        if not genre_names:
            for genre_id in genre_ids:
                genre_payload = self.__get_openapi__(
                    f"genres/{genre_id}",
                    params={"locale": locale},
                )
                genre_names.extend(self._openapi_genre_names_from_payload(genre_payload))

        genre_names = list(dict.fromkeys(name for name in genre_names if name))
        self._openapi_genre_cache[track_id] = genre_names
        return genre_names

    def enrichTrackGenresOpenApi(self, track: Track) -> Track:
        """Attach OpenAPI v2 genre metadata to an existing Track object when available."""
        if not isinstance(track, Track):
            return track

        existing_genres = getattr(track, "genres", None)
        existing_genre = getattr(track, "genre", None)
        if existing_genres or existing_genre:
            return track

        track_id = str(getattr(track, "id", "") or "").strip()
        if not track_id:
            return track

        try:
            genres = self.getTrackGenresOpenApi(track_id)
        except Exception as exc:
            logger.debug(
                "OpenAPI genre enrichment failed for track %s: %s",
                track_id,
                exc,
                exc_info=True,
            )
            return track

        if genres:
            setattr(track, "genres", genres)
            setattr(track, "genre", ", ".join(genres))

        return track

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
        track_obj = self.enrichTrackGenresOpenApi(cast(Track, track_obj))
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

    def _emptyLyrics(self, id: str) -> Lyrics:
        lyrics = Lyrics()
        lyrics.trackId = str(id)
        lyrics.lyrics = ""
        lyrics.subtitles = ""
        return lyrics

    def getLyrics(self, id: str) -> Lyrics:
        """Best-effort lyrics retrieval using the streamrip-style TIDAL host."""
        url = f"https://tidal.com/v1/tracks/{str(id)}/lyrics"
        params: Dict[str, Any] = {"countryCode": self.key.countryCode}

        try:
            response = self.session.get(url, params=params, timeout=15)

            if response.status_code in (401, 403, 404):
                logger.debug(
                    "Lyrics unavailable for track %s: status=%s body=%s",
                    id,
                    response.status_code,
                    (response.text or "").replace("\r", " ").replace("\n", " ")[:200],
                )
                return self._emptyLyrics(id)

            if not response.ok:
                logger.debug(
                    "Lyrics request failed for track %s: status=%s body=%s",
                    id,
                    response.status_code,
                    (response.text or "").replace("\r", " ").replace("\n", " ")[:200],
                )
                return self._emptyLyrics(id)

            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logger.debug("Lyrics response JSON decode failed for track %s: %s", id, e)
                return self._emptyLyrics(id)

            if not isinstance(data, dict):
                logger.debug("Lyrics response for track %s was not a JSON object.", id)
                return self._emptyLyrics(id)

            model = aigpy.model.dictToModel(data, Lyrics())
            if model is None:
                logger.debug("Could not convert lyrics response to Lyrics model for track %s.", id)
                return self._emptyLyrics(id)

            lyrics_model = cast(Lyrics, model)
            if lyrics_model.lyrics is None:
                lyrics_model.lyrics = ""
            if lyrics_model.subtitles is None:
                lyrics_model.subtitles = ""
            return lyrics_model

        except requests.exceptions.RequestException as e:
            logger.debug("Lyrics request exception for track %s: %s", id, e)
            return self._emptyLyrics(id)
        except Exception as e:
            logger.debug("Unexpected lyrics retrieval error for track %s: %s", id, e)
            return self._emptyLyrics(id)

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

        requested_size = max(int(width), int(height))
        normalized_size = next(
            (size for size in TIDAL_COVER_ALLOWED_SIZES if size >= requested_size),
            TIDAL_COVER_ALLOWED_SIZES[-1],
        )

        if normalized_size != int(width) or normalized_size != int(height):
            logger.debug(
                "Normalizing cover size request from %sx%s to %sx%s for SID %s",
                width,
                height,
                normalized_size,
                normalized_size,
                sid,
            )
        
        # The final URL requires slashes and should be lowercase.
        url_path_sid = hyphenated_sid.lower().replace('-', '/')
        
        return f"https://resources.tidal.com/images/{url_path_sid}/{normalized_size}x{normalized_size}.jpg"
    
    def getCoverData(
        self,
        sid: Optional[str],
        width: str = "320",
        height: str = "320",
        suppress_logs: bool = False,
    ) -> bytes:
        url = self.getCoverUrl(sid, width, height)
        if not url:
            return b""

        cover_key = f"{url}"
        if self._is_cover_temporarily_blocked(cover_key):
            logger.debug("Skipping cover fetch due to temporary failure cache: %s", cover_key)
            return b""
        try:
            # NOTE: TIDAL cover CDN can reject authenticated requests.
            # Fetch images without Authorization while keeping a browser-like User-Agent.
            if url.startswith("https://resources.tidal.com/images/"):
                ua = self.session.headers.get(
                    "User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0",
                )
                response = requests.get(
                    url,
                    timeout=15,
                    headers={
                        "User-Agent": ua,
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    },
                )
            else:
                response = self.session.get(url, timeout=15)

            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "image" not in content_type:
                if not suppress_logs:
                    logger.warning(
                        f"Expected image content type, got '{content_type}' for URL: {url}"
                    )
                return b""
            content = response.content
            if not content:
                if not suppress_logs:
                    logger.warning(f"Empty response content for cover URL: {url}")
                self._mark_cover_failure(cover_key, ttl_seconds=60)
                return b""
            self._clear_cover_failure(cover_key)
            return content
        except requests.exceptions.Timeout:
            if not suppress_logs:
                logger.error(f"Timeout fetching cover data for {sid} from {url}")
            self._mark_cover_failure(cover_key, ttl_seconds=45)
            return b""
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            if status_code == 403 and e.response is not None:
                request_headers = (
                    e.response.request.headers
                    if getattr(e.response, "request", None) is not None
                    else {}
                )
                req_auth_present = any(
                    k.lower() == "authorization" for k in request_headers.keys()
                )
                req_user_agent = request_headers.get("User-Agent", "")
                req_auth_prefix = request_headers.get("authorization", "")[:24]
                resp_content_type = e.response.headers.get("content-type", "")
                resp_server = e.response.headers.get("server", "")
                resp_text_snippet = (e.response.text or "").replace("\n", " ")[:180]

                if not suppress_logs:
                    logger.warning(
                        "COVER_DIAG_403 sid=%s size=%sx%s url=%s auth_present=%s auth_prefix=%s ua=%s resp_ct=%s resp_server=%s resp_body=%s",
                        sid,
                        width,
                        height,
                        url,
                        req_auth_present,
                        req_auth_prefix,
                        req_user_agent,
                        resp_content_type,
                        resp_server,
                        resp_text_snippet,
                    )
                self._mark_cover_failure(cover_key, ttl_seconds=180)
            elif status_code == 404:
                self._mark_cover_failure(cover_key, ttl_seconds=600)
            else:
                self._mark_cover_failure(cover_key, ttl_seconds=90)
            if not suppress_logs:
                logger.error(
                    f"HTTP error {status_code} fetching cover data for {sid} from {url}: {e}"
                )
            return b""
        except requests.exceptions.RequestException as e:
            if not suppress_logs:
                logger.error(f"Error fetching cover data for {sid} from {url}: {e}")
            self._mark_cover_failure(cover_key, ttl_seconds=90)
            return b""
        except Exception as e:
            if not suppress_logs:
                logger.error(
                    f"Unexpected error in getCoverData for {sid}: {e}", exc_info=True
                )
            self._mark_cover_failure(cover_key, ttl_seconds=60)
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
        max_quality = False
        atmos = False
        explicit = False

        if type == Type.Album or type == Type.Track:
            audio_quality = getattr(data, "audioQuality", None)
            if audio_quality == AudioQuality.HI_RES_LOSSLESS.value:
                max_quality = True
            audio_modes = getattr(data, "audioModes", [])
            if (
                type == Type.Album
                and isinstance(audio_modes, list)
                and "DOLBY_ATMOS" in audio_modes
            ):
                atmos = True
            if getattr(data, "explicit", False) is True:
                explicit = True

        if not max_quality and not atmos and not explicit:
            return ""

        array: List[str] = []
        if max_quality:
            array.append("M" if short else "Max")
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
        from tidal_dl.download import start as start_download  # type: ignore[attr-defined]
    except ImportError as e:
        logger.error(f"Failed to import start function from tidal_dl.download: {e}")
        raise e

    start_download(item)
