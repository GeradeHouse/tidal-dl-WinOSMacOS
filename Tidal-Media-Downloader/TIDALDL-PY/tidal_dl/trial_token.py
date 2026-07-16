#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
Temporary bundled TIDAL trial-token support.

Security boundary:
- The installer payload is only obfuscated/encrypted for static packaging.
- Runtime persistence uses Windows DPAPI and is separate from the normal token path.
- A determined reverse engineer can still recover any token that a local client can use.
"""

import base64
import ctypes
import ctypes.wintypes
import datetime
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from typing import Any, Optional

from Crypto.Cipher import AES

from tidal_dl.paths import resource_path
from tidal_dl.settings import TokenSettings

logger = logging.getLogger(__name__)

TRIAL_TOKEN_MAX_AGE_SECONDS = 10 * 24 * 60 * 60

_TRIAL_TOKEN_DIR_NAME = "trial-token"
_TRIAL_TOKEN_APP_DIR_NAME = "Tidal-DL GUI"
_TRIAL_BUNDLE_RESOURCE_DIR = "trial_token"
_TRIAL_BUNDLE_FILENAME = ".tidal-dl.trial-token.bundle"
_TRIAL_INSTALL_MARKER_FILENAME = ".tidal-dl.trial-token-install.json"
_TRIAL_STATE_FILENAME = "trial-token-state.json"
_TRIAL_DPAPI_TOKEN_FILENAME = "trial-token.dat"

_TRIAL_DPAPI_ENTROPY = b"Tidal-DL GUI trial token v1"
_TRIAL_BUNDLE_SECRET_PARTS = (
    "Tidal",
    "-",
    "DL",
    "::",
    "GUI",
    "::",
    "Trial",
    "::",
    "Token",
    "::",
    "2026",
)

_TRIAL_TOKEN_ACTIVE = False


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _is_windows() -> bool:
    return sys.platform == "win32"


def _get_trial_storage_dir() -> Optional[str]:
    if not _is_windows():
        return None

    base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base_dir:
        logger.warning("LOCALAPPDATA and APPDATA are unavailable. Trial token support is disabled.")
        return None

    return os.path.join(base_dir, _TRIAL_TOKEN_APP_DIR_NAME, _TRIAL_TOKEN_DIR_NAME)


def _ensure_trial_storage_dir() -> Optional[str]:
    trial_dir = _get_trial_storage_dir()
    if not trial_dir:
        return None

    os.makedirs(trial_dir, exist_ok=True)
    return trial_dir


def _trial_storage_path(filename: str) -> Optional[str]:
    trial_dir = _ensure_trial_storage_dir()
    if not trial_dir:
        return None
    return os.path.join(trial_dir, filename)


def _trial_bundle_appdata_path() -> Optional[str]:
    return _trial_storage_path(_TRIAL_BUNDLE_FILENAME)


def _trial_install_marker_appdata_path() -> Optional[str]:
    return _trial_storage_path(_TRIAL_INSTALL_MARKER_FILENAME)


def _trial_state_path() -> Optional[str]:
    return _trial_storage_path(_TRIAL_STATE_FILENAME)


def _trial_dpapi_token_path() -> Optional[str]:
    return _trial_storage_path(_TRIAL_DPAPI_TOKEN_FILENAME)


def _trial_bundle_resource_path() -> str:
    return resource_path(os.path.join(_TRIAL_BUNDLE_RESOURCE_DIR, _TRIAL_BUNDLE_FILENAME))


def _safe_delete_file(path: Optional[str]) -> None:
    if not path:
        return

    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        logger.warning("Could not delete trial token file: %s", path, exc_info=True)


def cleanup_trial_token_storage() -> None:
    trial_dir = _get_trial_storage_dir()

    for path in (
        _trial_bundle_appdata_path(),
        _trial_install_marker_appdata_path(),
        _trial_state_path(),
        _trial_dpapi_token_path(),
        _trial_bundle_resource_path(),
    ):
        _safe_delete_file(path)

    if trial_dir and os.path.isdir(trial_dir):
        try:
            shutil.rmtree(trial_dir)
        except Exception:
            logger.debug("Could not remove non-empty trial token directory: %s", trial_dir)


def is_trial_token_active() -> bool:
    return _TRIAL_TOKEN_ACTIVE


def clear_trial_token_runtime(token: Optional[TokenSettings] = None, delete_local: bool = False) -> None:
    global _TRIAL_TOKEN_ACTIVE

    _TRIAL_TOKEN_ACTIVE = False

    if token is not None:
        token.userid = None
        token.countryCode = None
        token.accessToken = None
        token.refreshToken = None
        token.expiresAfter = 0
        token.apiKeyIndex = 0

    if delete_local:
        cleanup_trial_token_storage()


def _parse_timestamp(value: object) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        pass

    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _read_json_file(path: Optional[str]) -> Optional[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        logger.warning("Could not read trial token JSON file: %s", path, exc_info=True)
        return None

    if isinstance(data, dict):
        return data
    return None


def _write_json_file(path: Optional[str], data: dict[str, Any]) -> None:
    if not path:
        return

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def _get_or_create_trial_start_timestamp() -> Optional[float]:
    state_path = _trial_state_path()
    state = _read_json_file(state_path)
    state_timestamp = _parse_timestamp((state or {}).get("firstInstalledAtEpoch"))
    if state_timestamp is not None:
        return state_timestamp

    marker = _read_json_file(_trial_install_marker_appdata_path())
    marker_timestamp = _parse_timestamp((marker or {}).get("installedAtEpoch"))
    if marker_timestamp is None:
        marker_timestamp = _parse_timestamp((marker or {}).get("installedAt"))

    if marker_timestamp is None:
        marker_timestamp = time.time()

    try:
        _write_json_file(
            state_path,
            {
                "firstInstalledAtEpoch": marker_timestamp,
                "maxAgeDays": 10,
            },
        )
    except Exception:
        logger.warning("Could not write trial token state file.", exc_info=True)

    return marker_timestamp


def _trial_is_expired() -> bool:
    start_timestamp = _get_or_create_trial_start_timestamp()
    if start_timestamp is None:
        return True

    age_seconds = max(0.0, time.time() - start_timestamp)
    if age_seconds > TRIAL_TOKEN_MAX_AGE_SECONDS:
        logger.info("TIDAL trial token expired after 10 days.")
        cleanup_trial_token_storage()
        return True

    return False


def _secret_bytes() -> bytes:
    return hashlib.sha256("".join(_TRIAL_BUNDLE_SECRET_PARTS).encode("utf-8")).digest()


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def _unwrap_bundle_key(bundle: dict[str, Any]) -> bytes:
    salt = _b64decode(str(bundle["salt"]))
    wrapped_key = _b64decode(str(bundle["wrappedKey"]))
    iterations = int(bundle.get("iterations", 200000))

    mask = hashlib.pbkdf2_hmac(
        "sha256",
        _secret_bytes(),
        salt,
        iterations,
        dklen=len(wrapped_key),
    )

    return bytes(a ^ b for a, b in zip(wrapped_key, mask))


def _decrypt_trial_bundle(path: str) -> Optional[str]:
    try:
        bundle = _read_json_file(path)
        if not bundle:
            return None

        if int(bundle.get("version", 0)) != 1:
            logger.warning("Unsupported TIDAL trial token bundle version.")
            return None

        key = _unwrap_bundle_key(bundle)
        nonce = _b64decode(str(bundle["nonce"]))
        tag = _b64decode(str(bundle["tag"]))
        ciphertext = _b64decode(str(bundle["ciphertext"]))

        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(b"Tidal-DL GUI trial token bundle v1")
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
        return plaintext.decode("utf-8")
    except Exception:
        logger.warning("Could not decrypt TIDAL trial token bundle.", exc_info=True)
        return None


def _materialize_resource_bundle_if_needed() -> Optional[str]:
    appdata_bundle_path = _trial_bundle_appdata_path()
    if appdata_bundle_path and os.path.exists(appdata_bundle_path):
        return appdata_bundle_path

    resource_bundle_path = _trial_bundle_resource_path()
    if not os.path.exists(resource_bundle_path):
        return None

    if not appdata_bundle_path:
        return resource_bundle_path

    try:
        os.makedirs(os.path.dirname(appdata_bundle_path), exist_ok=True)
        shutil.copy2(resource_bundle_path, appdata_bundle_path)
        _safe_delete_file(resource_bundle_path)
        return appdata_bundle_path
    except Exception:
        logger.warning("Could not move bundled trial token into AppData.", exc_info=True)
        return resource_bundle_path


def _dpapi_blob(data: bytes) -> _DATA_BLOB:
    buffer = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))


def _dpapi_protect(data: bytes) -> bytes:
    data_blob = _dpapi_blob(data)
    entropy_blob = _dpapi_blob(_TRIAL_DPAPI_ENTROPY)
    out_blob = _DATA_BLOB()

    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(data_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    data_blob = _dpapi_blob(data)
    entropy_blob = _dpapi_blob(_TRIAL_DPAPI_ENTROPY)
    out_blob = _DATA_BLOB()

    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(data_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _read_dpapi_trial_token_text() -> Optional[str]:
    path = _trial_dpapi_token_path()
    if not path or not os.path.exists(path):
        return None

    try:
        with open(path, "rb") as handle:
            protected = handle.read()
        if not protected:
            return None
        return _dpapi_unprotect(protected).decode("utf-8")
    except Exception:
        logger.warning("Could not decrypt DPAPI trial token.", exc_info=True)
        _safe_delete_file(path)
        return None


def _write_dpapi_trial_token_text(token_text: str) -> bool:
    path = _trial_dpapi_token_path()
    if not path:
        return False

    try:
        protected = _dpapi_protect(token_text.encode("utf-8"))
        with open(path, "wb") as handle:
            handle.write(protected)
        return True
    except Exception:
        logger.warning("Could not write DPAPI trial token.", exc_info=True)
        return False


def _decode_token_payload_text(token_text: str) -> Optional[dict[str, Any]]:
    text = token_text.strip()
    if not text:
        return None

    try:
        decoded = base64.b64decode(text).decode("utf-8")
        data = json.loads(decoded)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        return None

    return None


def _token_to_payload_text(token: TokenSettings) -> str:
    data = {
        "userid": token.userid,
        "countryCode": token.countryCode,
        "accessToken": token.accessToken,
        "refreshToken": token.refreshToken,
        "expiresAfter": token.expiresAfter,
        "apiKeyIndex": token.apiKeyIndex,
    }
    return json.dumps(data, separators=(",", ":"))


def _copy_payload_to_token(data: dict[str, Any], token: TokenSettings) -> bool:
    access_token = data.get("accessToken")
    if not access_token:
        return False

    token.userid = data.get("userid")
    token.countryCode = data.get("countryCode")
    token.accessToken = access_token
    token.refreshToken = data.get("refreshToken")

    try:
        token.expiresAfter = int(data.get("expiresAfter") or 0)
    except (TypeError, ValueError):
        token.expiresAfter = 0

    api_key_index = data.get("apiKeyIndex")
    token.apiKeyIndex = api_key_index if isinstance(api_key_index, int) else 0
    return True


def persist_trial_token(token: TokenSettings) -> bool:
    if not _is_windows():
        return False

    if _trial_is_expired():
        return False

    token_text = _token_to_payload_text(token)
    return _write_dpapi_trial_token_text(token_text)


def apply_trial_token_if_available(token: TokenSettings) -> bool:
    global _TRIAL_TOKEN_ACTIVE

    _TRIAL_TOKEN_ACTIVE = False

    if not _is_windows():
        return False

    if token.accessToken:
        return False

    if _trial_is_expired():
        return False

    token_text = _read_dpapi_trial_token_text()

    if token_text is None:
        bundle_path = _materialize_resource_bundle_if_needed()
        if not bundle_path or not os.path.exists(bundle_path):
            return False

        token_text = _decrypt_trial_bundle(bundle_path)
        if not token_text:
            cleanup_trial_token_storage()
            return False

        if _write_dpapi_trial_token_text(token_text):
            _safe_delete_file(bundle_path)

    payload = _decode_token_payload_text(token_text)
    if not payload:
        cleanup_trial_token_storage()
        return False

    if not _copy_payload_to_token(payload, token):
        cleanup_trial_token_storage()
        return False

    _TRIAL_TOKEN_ACTIVE = True
    logger.info("Loaded temporary TIDAL trial token from isolated trial storage.")
    return True
