"""Audio output device discovery helpers for the GUI player.

This module wraps QtMultimedia audio device APIs so playback routing logic stays
separate from the player state machine. Qt handles platform-specific audio
backends on Windows and macOS.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtMultimedia import QAudioDevice, QMediaDevices


DEFAULT_OUTPUT_DEVICE_ID = ""
DEFAULT_OUTPUT_DEVICE_NAME = "Default Playback Device"


def audio_device_id(device: QAudioDevice) -> str:
    """Return a stable-ish Qt audio-device identifier with a description fallback."""
    try:
        raw_id = device.id().data()
        if raw_id:
            return raw_id.decode("utf-8", errors="replace")
    except Exception:
        pass
    return device.description()


def list_output_devices() -> List[Dict[str, Any]]:
    """Return currently active output devices plus an explicit default option."""
    devices: List[Dict[str, Any]] = [
        {
            "id": DEFAULT_OUTPUT_DEVICE_ID,
            "name": DEFAULT_OUTPUT_DEVICE_NAME,
            "device": None,
            "default": True,
        }
    ]
    seen = {DEFAULT_OUTPUT_DEVICE_ID}

    for device in QMediaDevices.audioOutputs():
        device_id = audio_device_id(device)
        if device_id in seen:
            continue
        seen.add(device_id)
        devices.append(
            {
                "id": device_id,
                "name": device.description() or device_id,
                "device": device,
                "default": False,
            }
        )

    return devices


def resolve_output_device(device_id: str) -> tuple[Optional[QAudioDevice], str, str]:
    """Resolve a stored device id to a Qt device, falling back to system default."""
    normalized_id = device_id or DEFAULT_OUTPUT_DEVICE_ID

    if normalized_id:
        for device in QMediaDevices.audioOutputs():
            if audio_device_id(device) == normalized_id:
                return device, normalized_id, device.description() or normalized_id
        normalized_id = DEFAULT_OUTPUT_DEVICE_ID

    default_device = QMediaDevices.defaultAudioOutput()
    if default_device.isNull():
        return None, DEFAULT_OUTPUT_DEVICE_ID, DEFAULT_OUTPUT_DEVICE_NAME

    return default_device, DEFAULT_OUTPUT_DEVICE_ID, DEFAULT_OUTPUT_DEVICE_NAME
