# tidal_dl/gui/gui_player_logic.py

import logging
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from ..tidal import TidalAPI, AudioQuality
from typing import Optional, Dict, Any

# --- START: New Imports for ffpyplayer ---
from ffpyplayer.player import MediaPlayer  # type: ignore
import imageio_ffmpeg
# --- END: New Imports ---

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (player logic operations)
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

class PlayerLogic(QObject):
    # Signals remain the same, so the UI doesn't need to change
    trackChanged = pyqtSignal(dict)
    stateChanged = pyqtSignal(bool)
    positionChanged = pyqtSignal(int, int)
    volumeChangedSignal = pyqtSignal(int, bool)
    shuffleRepeatChanged = pyqtSignal(bool, bool)

    def __init__(self, tidal_api_instance: TidalAPI, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.tidal_api = tidal_api_instance
        self._current_track_info: Optional[Dict[str, Any]] = None
        self._is_playing: bool = False
        self._is_muted: bool = False
        self._is_shuffle: bool = False
        self._is_repeat: bool = False
        self._volume: int = 75

        # --- START: Replace QMediaPlayer with ffpyplayer.MediaPlayer ---
        self.media_player: Optional[MediaPlayer] = None
        self.ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        # --- END: Replace QMediaPlayer ---

        # This timer is now CRITICAL. It will poll the ffpyplayer for status updates.
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(250)  # Update 4 times per second for smoother progress
        self._playback_timer.timeout.connect(self._on_playback_timer_tick)

    def _cleanup_player(self):
        """Safely close and release the existing MediaPlayer instance."""
        if self.media_player:
            self.media_player.close_player()
            self.media_player = None
        self._playback_timer.stop()

    def play_track(self, track_info: Dict[str, Any]):
        logger.info(f"PlayerLogic: Play track requested: {track_info.get('title', 'Unknown')}")
        self._cleanup_player()  # Stop any currently playing track

        self._current_track_info = track_info
        track_id = track_info.get("id")
        if not track_id:
            logger.error("PlayerLogic: Track ID missing, cannot play.")
            return

        try:
            stream_url_info = self.tidal_api.getStreamUrl(id=str(track_id), quality=AudioQuality.HIGH)
            if stream_url_info and stream_url_info.url:
                stream_url = stream_url_info.url
                logger.info(f"PlayerLogic: Initializing ffpyplayer with URL: {stream_url}")

                # Create a new MediaPlayer instance for the track
                self.media_player = MediaPlayer(
                    stream_url,
                    ff_opts={
                        'ff_command': self.ffmpeg_path,
                        'an': True, # Ensure audio is enabled
                        'sn': True, # Ensure subtitles are disabled
                    }
                )
                if self.media_player:  # Type guard for Pylance
                    self.media_player.set_volume(0 if self._is_muted else self._volume / 100.0)
                self._is_playing = True
                self.stateChanged.emit(True)
                self._playback_timer.start()
                self.trackChanged.emit(self._current_track_info)
            else:
                logger.error(f"PlayerLogic: Could not obtain stream URL for track_id: {track_id}.")
        except Exception as e:
            logger.error(f"PlayerLogic: Error during stream URL fetching or playback setup: {e}", exc_info=True)
            self._cleanup_player()

    def toggle_play_pause(self):
        if self.media_player:
            self.media_player.toggle_pause()
            self._is_playing = not self.media_player.get_pause()
            self.stateChanged.emit(self._is_playing)
            logger.info(f"PlayerLogic: Toggled pause. Is playing: {self._is_playing}")

    def next_track(self):
        logger.warning("PlayerLogic: Next track functionality not yet implemented.")
        self._cleanup_player()

    def previous_track(self):
        if self.media_player and self.media_player.get_pts() > 5.0: # More than 5 seconds in
            self.media_player.seek(0)
        else:
            logger.warning("PlayerLogic: Previous track functionality not yet implemented.")
            self._cleanup_player()

    def seek_position(self, percentage: int): # Accepts 0-1000
        if self.media_player:
            duration = self.media_player.get_metadata().get('duration', 0)
            if duration > 0:
                target_sec = (percentage / 1000.0) * duration
                self.media_player.seek(target_sec, relative=False)
                logger.info(f"PlayerLogic: Seek to {target_sec:.1f}s")

    def set_volume(self, volume: int): # Accepts 0-100
        self._volume = max(0, min(volume, 100))
        if self.media_player and not self._is_muted:
            self.media_player.set_volume(self._volume / 100.0)
        self.volumeChangedSignal.emit(self._volume, self._is_muted)

    def toggle_mute(self, mute: bool):
        self._is_muted = mute
        if self.media_player:
            self.media_player.set_volume(0 if self._is_muted else self._volume / 100.0)
        self.volumeChangedSignal.emit(self._volume, self._is_muted)

    def toggle_shuffle(self, shuffle_on: bool):
        self._is_shuffle = shuffle_on
        self.shuffleRepeatChanged.emit(self._is_shuffle, self._is_repeat)

    def toggle_repeat(self, repeat_on: bool):
        self._is_repeat = repeat_on
        self.shuffleRepeatChanged.emit(self._is_shuffle, self._is_repeat)

    def _on_playback_timer_tick(self):
        """Polls the MediaPlayer for status and emits signals."""
        if not self.media_player:
            self._playback_timer.stop()
            return

        # Check for end of stream
        if self.media_player.get_metadata().get('eof'):
            logger.info("PlayerLogic: End of file reached.")
            self._is_playing = False
            self.stateChanged.emit(False)
            self._cleanup_player()
            
            if self._is_repeat and self._current_track_info:
                self.play_track(self._current_track_info) # Replay
            else:
                self.next_track() # Or move to next
            return

        # Update position
        current_pos_sec = self.media_player.get_pts()
        total_dur_sec = self.media_player.get_metadata().get('duration', 0)
        
        current_pos_ms = int(current_pos_sec * 1000)
        total_dur_ms = int(total_dur_sec * 1000)
        
        self.positionChanged.emit(current_pos_ms, total_dur_ms)
