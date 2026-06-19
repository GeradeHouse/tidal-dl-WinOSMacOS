# tidal_dl/gui/gui_player_logic.py

import logging
import os
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from tidal_dl.settings import SETTINGS
from tidal_dl.tidal import TidalAPI, AudioQuality
from tidal_dl.gui.gui_audio_output import list_output_devices, resolve_output_device

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class PlayerLogic(QObject):
    trackChanged = pyqtSignal(dict)
    stateChanged = pyqtSignal(bool)
    positionChanged = pyqtSignal(int, int)
    volumeChangedSignal = pyqtSignal(int, bool)
    shuffleRepeatChanged = pyqtSignal(bool, bool)
    outputDeviceChanged = pyqtSignal(str)

    def __init__(self, tidal_api_instance: TidalAPI, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.tidal_api = tidal_api_instance
        self._current_track_info: Optional[Dict[str, Any]] = None
        self._is_playing: bool = False
        self._is_muted: bool = False
        self._is_shuffle: bool = False
        self._is_repeat: bool = False
        self._volume: int = 75

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(self._volume / 100.0)
        self.audio_output.setMuted(self._is_muted)

        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.errorOccurred.connect(self._on_player_error)

        self._last_position_ms = 0
        self._last_duration_ms = 0
        self._selected_output_device_id = getattr(SETTINGS, "playbackOutputDeviceId", "") or ""
        self._selected_output_device_name = getattr(
            SETTINGS,
            "playbackOutputDeviceName",
            "Default Playback Device",
        ) or "Default Playback Device"
        self._apply_output_device_by_id(self._selected_output_device_id, save=False)
        logger.info(
            "PlayerLogic: QtMultimedia player initialized output_device=%s",
            self._selected_output_device_name,
        )

        # Retained only as a fallback UI heartbeat. It no longer logs every tick.
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(1000)
        self._playback_timer.timeout.connect(self._emit_current_position)

    def get_output_devices(self) -> List[Dict[str, Any]]:
        return list_output_devices()

    def selected_output_device_id(self) -> str:
        return self._selected_output_device_id

    def _apply_output_device_by_id(self, device_id: str, save: bool = True) -> bool:
        requested_id = device_id or ""
        selected_device, device_id, target_name = resolve_output_device(requested_id)
        if requested_id and not device_id:
            logger.warning(
                "PlayerLogic: configured output device not found, falling back to default: %s",
                requested_id,
            )
        if selected_device is not None and not selected_device.isNull():
            self.audio_output.setDevice(selected_device)

        self._selected_output_device_id = device_id
        self._selected_output_device_name = target_name
        self.outputDeviceChanged.emit(target_name)

        if save:
            SETTINGS.playbackOutputDeviceId = device_id
            SETTINGS.playbackOutputDeviceName = target_name
            try:
                SETTINGS.save()
            except Exception as e:
                logger.warning("PlayerLogic: failed to save output device setting: %s", e)

        logger.info("PlayerLogic: selected output device: %s", target_name)
        return True

    def set_output_device(self, device_id: str):
        was_playing = self._is_playing
        self._apply_output_device_by_id(device_id, save=True)
        if was_playing:
            self.media_player.play()

    def _cleanup_player(self):
        logger.info("PlayerLogic: _cleanup_player invoked (has_source=%s)", bool(self.media_player.source().isValid()))
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self._playback_timer.stop()
        self._last_position_ms = 0
        self._last_duration_ms = 0

    def play_track(self, track_info: Dict[str, Any]):
        logger.info("PlayerLogic: Play track requested: %s", track_info.get("title", "Unknown"))
        self._cleanup_player()

        self._current_track_info = track_info
        track_id = track_info.get("id")
        logger.info("PlayerLogic: Preparing playback for track_id=%s", track_id)
        if not track_id:
            logger.error("PlayerLogic: Track ID missing, cannot play.")
            return

        try:
            preview_quality = AudioQuality.LOW
            logger.info(
                "PlayerLogic: Requesting AAC preview stream URL for track_id=%s quality=%s",
                track_id,
                preview_quality,
            )
            stream_url_info = self.tidal_api.getStreamUrl(
                id=str(track_id),
                quality=preview_quality,
            )
            if not stream_url_info or not stream_url_info.url:
                logger.error("PlayerLogic: Could not obtain stream URL for track_id: %s", track_id)
                return

            stream_url = stream_url_info.url
            logger.info("PlayerLogic: Initializing Qt playback with URL: %s", stream_url)
            self.media_player.setSource(QUrl(stream_url))
            self.audio_output.setVolume(0 if self._is_muted else self._volume / 100.0)
            self.audio_output.setMuted(self._is_muted)

            duration_ms_raw = track_info.get("duration_ms", 0)
            try:
                self._last_duration_ms = max(0, int(duration_ms_raw or 0))
            except (TypeError, ValueError):
                self._last_duration_ms = 0

            self.media_player.play()
            self._is_playing = True
            self.stateChanged.emit(True)
            self._playback_timer.start()
            self.trackChanged.emit(self._current_track_info)
            logger.info(
                "PlayerLogic: playback started output=%s muted=%s volume=%s",
                self._selected_output_device_name,
                self._is_muted,
                self._volume,
            )
        except Exception as e:
            logger.error("PlayerLogic: Error during stream URL fetching or playback setup: %s", e, exc_info=True)
            self._cleanup_player()

    def toggle_play_pause(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self._is_playing = False
        else:
            self.media_player.play()
            self._is_playing = True
        self.stateChanged.emit(self._is_playing)
        logger.info("PlayerLogic: Toggled pause. Is playing: %s", self._is_playing)

    def next_track(self):
        logger.warning("PlayerLogic: Next track functionality not yet implemented.")
        self._cleanup_player()

    def previous_track(self):
        if self.media_player.position() > 5000:
            self.media_player.setPosition(0)
        else:
            logger.warning("PlayerLogic: Previous track functionality not yet implemented.")
            self._cleanup_player()

    def seek_position(self, percentage: int):
        duration = self.media_player.duration() or self._last_duration_ms
        if duration > 0:
            target_ms = int((percentage / 1000.0) * duration)
            self.media_player.setPosition(target_ms)
            logger.info("PlayerLogic: Seek to %.1fs", target_ms / 1000.0)

    def set_volume(self, volume: int):
        self._volume = max(0, min(volume, 100))
        if not self._is_muted:
            self.audio_output.setVolume(self._volume / 100.0)
        self.volumeChangedSignal.emit(self._volume, self._is_muted)

    def toggle_mute(self, mute: bool):
        self._is_muted = mute
        self.audio_output.setMuted(self._is_muted)
        self.audio_output.setVolume(0 if self._is_muted else self._volume / 100.0)
        self.volumeChangedSignal.emit(self._volume, self._is_muted)

    def toggle_shuffle(self, shuffle_on: bool):
        self._is_shuffle = shuffle_on
        self.shuffleRepeatChanged.emit(self._is_shuffle, self._is_repeat)

    def toggle_repeat(self, repeat_on: bool):
        self._is_repeat = repeat_on
        self.shuffleRepeatChanged.emit(self._is_shuffle, self._is_repeat)

    def _on_position_changed(self, position_ms: int):
        self._last_position_ms = max(0, int(position_ms))
        self._emit_current_position()

    def _on_duration_changed(self, duration_ms: int):
        if duration_ms > 0:
            self._last_duration_ms = int(duration_ms)
        self._emit_current_position()

    def _emit_current_position(self):
        self.positionChanged.emit(self._last_position_ms, self._last_duration_ms)

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            logger.info("PlayerLogic: End of media reached.")
            self._is_playing = False
            self.stateChanged.emit(False)
            self._cleanup_player()
            if self._is_repeat and self._current_track_info:
                self.play_track(self._current_track_info)
            else:
                self.next_track()

    def _on_playback_state_changed(self, state):
        self._is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.stateChanged.emit(self._is_playing)

    def _on_player_error(self, error, error_string: str):
        if error != QMediaPlayer.Error.NoError:
            logger.error("PlayerLogic: playback error=%s message=%s", error, error_string)
