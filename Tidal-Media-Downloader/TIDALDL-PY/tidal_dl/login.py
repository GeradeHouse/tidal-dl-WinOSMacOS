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
]

logger = logging.getLogger(__name__)


def initialize_and_login():
    """
    Initializes settings, logging, and attempts to log in using stored configuration.
    """
    setup_logging()
    SETTINGS.read(getSettingsFilePath())
    TOKEN.read(getTokenPath())
    TIDAL_API.apiKey = apiKey.getItem(SETTINGS.apiKeyIndex)

    if not loginByConfig():
        Printf.info("Could not log in with stored credentials.")


def loginByConfig():
    """
    Attempts to log in using the stored session and access token.
    Refreshes the token if it has expired.
    """
    if TOKEN.accessToken is None:
        return False

    # Check if token is expired
    if time.time() > TOKEN.expiresAfter:
        if TOKEN.refreshToken:
            Printf.info("Access token has expired, attempting to refresh...")
            try:
                if TIDAL_API.refreshAccessToken(TOKEN.refreshToken):
                    TOKEN.accessToken = TIDAL_API.key.accessToken
                    TOKEN.expiresAfter = int(time.time()) + TIDAL_API.key.expiresIn
                    TOKEN.save()
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

    # Login with current access token
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
    """
    return TIDAL_API.getDeviceCode()


def pollForToken():
    """
    Polls for the authentication token after the user logs in via the web.
    """
    return TIDAL_API.checkAuthStatus()


def loginByWeb():
    """
    Initiates a web-based login flow.
    """
    Printf.info("Starting web login...")
    try:
        url = getLoginUrl()
        Printf.info(f"Please visit this URL in your browser to log in:\n{url}")
        Printf.info("You have 5 minutes to complete the login.")

        timeout = TIDAL_API.key.authCheckTimeout
        interval = TIDAL_API.key.authCheckInterval
        start_time = time.time()

        while time.time() - start_time < timeout:
            status = pollForToken()
            if status == "SUCCESS":
                TOKEN.userid = TIDAL_API.key.userId
                TOKEN.countryCode = TIDAL_API.key.countryCode
                TOKEN.accessToken = TIDAL_API.key.accessToken
                TOKEN.refreshToken = TIDAL_API.key.refreshToken
                TOKEN.expiresAfter = int(time.time()) + TIDAL_API.key.expiresIn
                TOKEN.save()
                Printf.success("Login successful!")
                return True
            elif status == "PENDING":
                time.sleep(interval)
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
        TOKEN.userid = TIDAL_API.key.userId
        TOKEN.countryCode = TIDAL_API.key.countryCode
        TOKEN.accessToken = TIDAL_API.key.accessToken
        TOKEN.refreshToken = None  # No refresh token with this method
        TOKEN.expiresAfter = 0
        TOKEN.save()
        Printf.success("Login successful!")
        return True
    except Exception as e:
        Printf.err(f"Login by access token failed: {e}")
        return False
