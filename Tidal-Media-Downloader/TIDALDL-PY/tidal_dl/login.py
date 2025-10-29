#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   login.py
@Time    :   2025/07/29
@Author  :   Roo
@Version :   1.0
@Desc    :   Centralized login and initialization logic.
"""
import logging
import time
from .tidal import TIDAL_API
from .settings import SETTINGS, TOKEN
from .paths import getSettingsFilePath, getTokenPath
from .printf import Printf
from . import apiKey
from .logging_config import setup_logging

__all__ = [
    "initialize_and_login",
    "loginByConfig",
    "loginByWeb",
    "loginByAccessToken",
    "getLoginUrl",
    "pollForToken",
    "saveToken",  # --- MODIFICATION: Expose the new save function ---
]

logger = logging.getLogger(__name__)


def initialize_and_login():
    """
    Initializes settings, logging, and attempts to log in using stored configuration.
    This is the single source of truth for setting the API key.
    """
    setup_logging()
    SETTINGS.read(getSettingsFilePath())
    TOKEN.read(getTokenPath())
    
    # Prioritize the API key index stored in the token file.
    if TOKEN.apiKeyIndex is not None:
        logger.debug(f"Found apiKeyIndex '{TOKEN.apiKeyIndex}' in token file. Prioritizing it.")
        SETTINGS.apiKeyIndex = TOKEN.apiKeyIndex
    else:
        logger.debug("No apiKeyIndex in token file. Using index from settings.json.")

    logger.debug(f"Initial SETTINGS.apiKeyIndex: {SETTINGS.apiKeyIndex}")
    
    # Set the initial API key based on settings. This might fail, but getLoginUrl will handle retries.
    selected = apiKey.getItem(SETTINGS.apiKeyIndex)
    TIDAL_API.apiKey = selected
    
    logger.debug(
        f"Initially using API key: index={SETTINGS.apiKeyIndex}, platform={selected.get('platform')}, "
        f"formats={selected.get('formats')}, clientId={selected.get('clientId')}"
    )

    if not loginByConfig():
        Printf.info("Could not log in with stored credentials.")


def loginByConfig():
    """
    Attempts to log in using the stored session and access token.
    Refreshes the token if it has expired.
    """
    if TOKEN.accessToken is None:
        return False

    # --- MODIFICATION START: Ensure expiresAfter is a number ---
    expires_after = 0
    if TOKEN.expiresAfter is not None:
        try:
            expires_after = int(TOKEN.expiresAfter)
        except (ValueError, TypeError):
            expires_after = 0

    if time.time() > expires_after:
        if TOKEN.refreshToken:
            Printf.info("Access token has expired, attempting to refresh...")
            try:
                # The TIDAL_API.apiKey should already be set by initialize_and_login
                if TIDAL_API.refreshAccessToken(TOKEN.refreshToken):
                    # --- MODIFICATION START: Use the new save function ---
                    saveToken()
            
                    Printf.success("Token refreshed successfully.")
                    return True
                else:
                    Printf.err("Failed to refresh token.")
                    return False
            except Exception as e:
                Printf.err(f"An error occurred during token refresh: {e}")
                return False
        else:
            Printf.warning(
                "Access token has expired and no refresh token is available."
            )
            return False

    try:
        TIDAL_API.loginByAccessToken(TOKEN.accessToken, TOKEN.userid)
        Printf.success("Login successful using stored credentials.")
        return True
    except Exception as e:
        Printf.err(f"Login with stored token failed: {e}")
        return False


def getLoginUrl():
    """
    Gets the login URL for web-based authentication.
    It iterates through all valid API keys until one succeeds.
    """
    all_keys = apiKey.getItems()
    
    # Start with the currently configured index to try it first.
    start_index = SETTINGS.apiKeyIndex
    
    # Create a reordered list of indices to try, starting with the configured one, then wrapping around.
    ordered_indices = list(range(start_index, len(all_keys))) + list(range(0, start_index))

    for index in ordered_indices:
        key = all_keys[index]
        
        if key.get('valid') != 'True':
            continue

        Printf.info(f"Attempting login with API key: {key.get('platform', 'Unknown')}...")
        TIDAL_API.apiKey = key

        try:
            login_url = TIDAL_API.getDeviceCode()
            
            Printf.success(f"Successfully using API key: {key.get('platform', 'Unknown')}")
            
            if index != SETTINGS.apiKeyIndex:
                Printf.info(f"Updating preferred API key to index {index}.")
                SETTINGS.apiKeyIndex = index
                SETTINGS.save()
                
            return login_url
            
        except Exception as e:
            logger.warning(f"API key '{key.get('platform', 'Unknown')}' failed: {e}")
            Printf.warning(f"API key '{key.get('platform', 'Unknown')}' failed. Trying next...")
            continue

    raise RuntimeError("None of the available API keys were able to successfully authenticate. Please check your API key definitions.")


def pollForToken():
    """
    Polls for the authentication token after the user logs in via the web.
    """
    try:
        return TIDAL_API.checkAuthStatus()
    except Exception as e:
        raise e


# --- MODIFICATION START: Create a dedicated save function ---
def saveToken():
    """
    Saves the current session data from TIDAL_API.key to the TOKEN object and file.
    """
    try:
        TOKEN.userid = TIDAL_API.key.userId
        TOKEN.countryCode = TIDAL_API.key.countryCode
        TOKEN.accessToken = TIDAL_API.key.accessToken
        TOKEN.refreshToken = TIDAL_API.key.refreshToken
        TOKEN.expiresAfter = int(time.time()) + TIDAL_API.key.expiresIn
        TOKEN.apiKeyIndex = SETTINGS.apiKeyIndex
        TOKEN.save()
        logger.info("TIDAL token data saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save TIDAL token: {e}", exc_info=True)
        Printf.err(f"Could not save login session: {e}")


def loginByWeb():
    """
    Initiates a web-based login flow. (Primarily for CLI)
    """
    Printf.info("Starting web login...")
    try:
        url = getLoginUrl()
        Printf.info(f"Please visit this URL in your browser to log in:\n{url}")
        Printf.info("You have 5 minutes to complete the login.")

        timeout = TIDAL_API.key.authCheckTimeout
        interval = TIDAL_API.key.authCheckInterval
        try:
            timeout = int(timeout) if timeout is not None else 0
        except Exception:
            timeout = 0
        try:
            interval = int(interval) if interval is not None else 5
        except Exception:
            interval = 5

        if timeout <= 0:
            Printf.err("Device authorization did not initialize. This may indicate a problem with all available API keys.")
            return False

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                status = pollForToken()
            except Exception as e:
                Printf.err(f"TIDAL auth status error: {e}")
                return False

            if status == "SUCCESS":
                # --- MODIFICATION START: Use the new save function ---
                saveToken()
        
                Printf.success("Login successful!")
                return True
            elif status == "PENDING":
                time.sleep(max(1, interval))
            elif status == "SLOW_DOWN":
                interval += 5
                Printf.warning(f"Polling too frequently. Slowing down to {interval}s.")
                time.sleep(interval)
            else:
                Printf.err(f"Login failed with status: {status}")
                return False

        Printf.err("Login timed out.")
        return False

    except Exception as e:
        Printf.err(f"An error occurred during web login: {e}")
        return False


def loginByAccessToken():
    """
    Allows the user to log in using a manually provided access token.
    """
    Printf.info(
        "Enter your access token. You can get it from https://listen.tidal.com/v1/oauth2/token"
    )
    token = Printf.enter("accessToken:")
    if not token:
        Printf.warning("No access token entered.")
        return False

    try:
        TIDAL_API.loginByAccessToken(token)
        # --- MODIFICATION START: Use the new save function ---
        # Manually set expiresAfter and refreshToken as they are unknown here
        TIDAL_API.key.refreshToken = None
        TIDAL_API.key.expiresIn = 0
        saveToken()

        Printf.success("Login successful!")
        return True
    except Exception as e:
        Printf.err(f"Login by access token failed: {e}")
        return False