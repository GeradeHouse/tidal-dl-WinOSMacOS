import logging
import time
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QUrl

# REMOVE: from ..tidal import TIDAL_API as _TIDAL_API_INSTANCE, AudioQuality
from ..tidal import AudioQuality  # Keep AudioQuality if needed for defaults
from tidal_dl.tidal import TidalAPI  # Import the CLASS for type hinting
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from typing import Optional, Dict, Any

# REMOVE: TIDAL_API: TidalAPI = _TIDAL_API_INSTANCE

logger = logging.getLogger(__name__)


class PlayerLogic(QObject):
    # ... (signals remain the same) ...
    trackChanged = pyqtSignal(dict)
    stateChanged = pyqtSignal(bool)
    positionChanged = pyqtSignal(int, int)
    volumeChangedSignal = pyqtSignal(int, bool)
    shuffleRepeatChanged = pyqtSignal(bool, bool)

    # Modify __init__ to accept the main TIDAL_API instance
    def __init__(self, tidal_api_instance: TidalAPI, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.tidal_api = tidal_api_instance  # Store the passed-in instance
        self._current_track_info: Optional[Dict[str, Any]] = None
        self._is_playing: bool = False
        self._is_muted: bool = False
        self._is_shuffle: bool = False
        self._is_repeat: bool = False
        self._volume: int = 75  # 0-100
        self._current_position_ms: int = 0
        self._total_duration_ms: int = 0

        # QMediaPlayer setup
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(
            self._volume / 100.0
        )  # QAudioOutput volume is 0.0-1.0

        # Connect QMediaPlayer signals
        self.media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.errorOccurred.connect(self._on_player_error)
        # self.audio_output.volumeChanged.connect(self._on_audio_volume_changed) # We set volume directly
        # self.audio_output.mutedChanged.connect(self._on_audio_muted_changed) # We set mute directly

        self._playback_timer = QTimer(
            self
        )  # May still be needed for UI updates if QMediaPlayer signals are not frequent enough or for other logic
        self._playback_timer.setInterval(1000)  # Update every second
        self._playback_timer.timeout.connect(
            self._on_playback_timer_tick
        )  # This will need to be re-evaluated

    def play_track(self, track_info: Dict[str, Any]):
        logger.info(
            f"PlayerLogic: Play track requested: {track_info.get('title', 'Unknown')}"
        )
        self._current_track_info = track_info
        self._current_position_ms = 0

        track_id = track_info.get("id")
        if not track_id:
            logger.error("PlayerLogic: Track ID missing, cannot play.")
            self._on_player_error(QMediaPlayer.Error.ResourceError, "Track ID missing")
            return

        try:
            logger.debug(
                f"PlayerLogic: Attempting to get stream URL for track_id: {track_id}"
            )
            # Use the stored self.tidal_api instance
            # The check for initialization should ideally be done before calling play_track,
            # or PlayerLogic should have a way to know if the API is ready.
            # For now, let's assume if this method is called, the API instance passed is valid.
            # However, a direct check on the instance's properties might still be good.
            # The Pylance error was because `TIDAL_API.config` doesn't exist.
            # The actual check should be for `TIDAL_API.key.accessToken` or similar.
            if not (
                self.tidal_api and self.tidal_api.key and self.tidal_api.key.accessToken
            ):
                logger.error(
                    "PlayerLogic: Passed TIDAL_API instance not initialized or session not valid."
                )
                self._on_player_error(
                    QMediaPlayer.Error.ResourceError, "TIDAL API not ready."
                )
                return

            # Use self.tidal_api
            stream_url_info = self.tidal_api.getStreamUrl(
                id=str(track_id), quality=AudioQuality.HIGH
            )  # Example quality

            if stream_url_info and stream_url_info.url:
                stream_url = stream_url_info.url
                logger.info(f"PlayerLogic: Using stream URL: {stream_url}")
                self.media_player.setSource(QUrl(stream_url))
                self.media_player.play()  # QMediaPlayer will emit stateChanged
                self.trackChanged.emit(
                    self._current_track_info
                )  # Emit that a new track is being attempted
            else:
                logger.error(
                    f"PlayerLogic: Could not obtain stream URL for track_id: {track_id}. Info: {stream_url_info}"
                )
                self._on_player_error(
                    QMediaPlayer.Error.ResourceError, "Failed to get stream URL."
                )
                return

        except Exception as e:
            logger.error(
                f"PlayerLogic: Error during stream URL fetching or playback setup for track_id {track_id}: {e}",
                exc_info=True,
            )
            self._on_player_error(
                QMediaPlayer.Error.ResourceError, f"Error playing track: {e}"
            )

    def toggle_play_pause(self):
        if self.media_player.source().isEmpty():
            logger.warning(
                "PlayerLogic: Play/Pause toggled but no track loaded (no source)."
            )
            return

        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            logger.info("PlayerLogic: Paused.")
        else:
            self.media_player.play()
            logger.info("PlayerLogic: Playing.")
        # self.stateChanged will be emitted by _on_playback_state_changed

    def next_track(self):
        logger.info("PlayerLogic: Next track requested.")
        # This requires a proper play queue and logic to get the next track_info.
        # For now, we'll just stop the current track if one is playing.
        if self.media_player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self.media_player.stop()
        logger.warning(
            "PlayerLogic: Next track functionality requires a play queue (not yet implemented)."
        )
        # In a full implementation, you would get the next track_info from the queue
        # and call self.play_track(next_track_info)
        # For now, simulate playing a generic "next track" or clear current.
        # self.play_track({"id": "next_dummy_id", "title": "Next Track (Simulated)", "artist": "Various Artists", "duration_ms": 180000})

    def previous_track(self):
        logger.info("PlayerLogic: Previous track requested.")
        # Similar to next_track, this requires queue logic.
        # Often, pressing previous when a track is > X seconds in will restart the current track.
        # Pressing it again or if early in the track would go to the actual previous.
        if self.media_player.position() > 5000:  # More than 5 seconds in
            logger.info(
                "PlayerLogic: Restarting current track (previous pressed >5s in)."
            )
            self.media_player.setPosition(0)
            if (
                self.media_player.playbackState()
                != QMediaPlayer.PlaybackState.PlayingState
            ):
                self.media_player.play()  # Start playing if paused/stopped
        else:
            if (
                self.media_player.playbackState()
                != QMediaPlayer.PlaybackState.StoppedState
            ):
                self.media_player.stop()
            logger.warning(
                "PlayerLogic: Previous track functionality requires a play queue (not yet implemented)."
            )
            # In a full implementation, you would get the previous track_info from the queue
            # and call self.play_track(previous_track_info)
            # self.play_track({"id": "prev_dummy_id", "title": "Previous Track (Simulated)", "artist": "Various Artists", "duration_ms": 200000})

    def seek_position(self, percentage: int):  # Accepts percentage 0-1000 (0-100.0%)
        if self.media_player.duration() > 0:
            target_ms = int((percentage / 1000.0) * self.media_player.duration())
            self.media_player.setPosition(target_ms)
            logger.info(
                f"PlayerLogic: Seek to {target_ms / 1000:.1f}s ({percentage/10.0:.1f}%)"
            )
            # self.positionChanged will be emitted by _on_position_changed
        else:
            logger.warning("PlayerLogic: Cannot seek, duration unknown or zero.")

    def set_volume(self, volume: int):  # Accepts 0-100
        self._volume = max(0, min(volume, 100))
        self.audio_output.setVolume(self._volume / 100.0)
        if self.audio_output.isMuted() and self._volume > 0:
            self.audio_output.setMuted(False)
            self._is_muted = False  # Keep our internal state in sync
        logger.info(
            f"PlayerLogic: Volume set to {self._volume}%. Muted: {self.audio_output.isMuted()}"
        )
        self.volumeChangedSignal.emit(self._volume, self.audio_output.isMuted())

    def toggle_mute(self, mute: bool):
        self._is_muted = mute  # Update internal state first
        self.audio_output.setMuted(self._is_muted)
        logger.info(f"PlayerLogic: Mute toggled. Muted: {self._is_muted}")
        self.volumeChangedSignal.emit(self._volume, self._is_muted)

    def toggle_shuffle(self, shuffle_on: bool):
        self._is_shuffle = shuffle_on
        logger.info(f"PlayerLogic: Shuffle toggled. Shuffle on: {self._is_shuffle}")
        self.shuffleRepeatChanged.emit(self._is_shuffle, self._is_repeat)

    def toggle_repeat(self, repeat_on: bool):
        self._is_repeat = repeat_on
        logger.info(f"PlayerLogic: Repeat toggled. Repeat on: {self._is_repeat}")
        self.shuffleRepeatChanged.emit(self._is_shuffle, self._is_repeat)

    def _on_playback_timer_tick(self):
        # This timer is largely superseded by QMediaPlayer's own signals for playback
        # progress (positionChanged, durationChanged, mediaStatusChanged, playbackStateChanged).
        # It should primarily be used for UI updates or logic that QMediaPlayer doesn't cover,
        # or if a more frequent update than QMediaPlayer.positionChanged provides is needed
        # for a very smooth progress bar (though QMediaPlayer is usually sufficient).

        # If QMediaPlayer is handling playback, we don't need to manually update position here.
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            # If we needed to force an update or do something extra during playback tick:
            # current_pos = self.media_player.position()
            # total_dur = self.media_player.duration()
            # if self._current_position_ms != current_pos or self._total_duration_ms != total_dur:
            #     self._current_position_ms = current_pos
            #     self._total_duration_ms = total_dur
            #     self.positionChanged.emit(current_pos, total_dur)
            # logger.debug(f"PlayerLogic: Timer tick while playing. Position: {current_pos}/{total_dur}")
            pass  # QMediaPlayer.positionChanged handles this.
        elif (
            self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        ):
            # logger.debug("PlayerLogic: Timer tick while paused.")
            pass
        else:  # StoppedState
            # logger.debug("PlayerLogic: Timer tick while stopped.")
            # Ensure our timer stops if media is stopped and timer is only for playback progress.
            # This check might be redundant if _on_playback_state_changed already stops the timer.
            if self._playback_timer.isActive():
                # logger.debug("PlayerLogic: Stopping playback timer as media is stopped.")
                # self._playback_timer.stop() # Be cautious if timer has other roles.
                pass

    # QMediaPlayer Signal Handlers
    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus):
        logger.debug(f"PlayerLogic: Media status changed: {status}")
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            # This is a good place to get the duration if not already set,
            # or to enable UI elements that depend on media being loaded.
            self._total_duration_ms = self.media_player.duration()
            self.positionChanged.emit(
                self.media_player.position(), self._total_duration_ms
            )
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            track_title = "Unknown Track"
            if self._current_track_info:
                track_title = self._current_track_info.get("title", track_title)
            logger.info(f"PlayerLogic: Track '{track_title}' finished (EndOfMedia).")
            self._is_playing = False
            self.stateChanged.emit(self._is_playing)
            # Handle repeat or next track logic here
            if (
                self._is_repeat and self._current_track_info
            ):  # Ensure track_info exists for repeat
                logger.info("PlayerLogic: Repeating track.")
                self.media_player.setPosition(0)
                self.media_player.play()
            else:
                # Optionally, auto-play next track if shuffle/normal play
                # self.next_track()
                pass  # For now, just stop
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            logger.error("PlayerLogic: Invalid media.")
            self._is_playing = False
            self.stateChanged.emit(self._is_playing)
            # Potentially emit an error signal to the UI
        # Add handling for other statuses as needed (StalledMedia, BufferingMedia, etc.)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState):
        logger.debug(f"PlayerLogic: Playback state changed: {state}")
        new_is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        if self._is_playing != new_is_playing:
            self._is_playing = new_is_playing
            self.stateChanged.emit(self._is_playing)
            if not self._is_playing:
                self._playback_timer.stop()  # Stop our manual timer if QMediaPlayer stops
            else:
                self._playback_timer.start()  # Or start it if QMediaPlayer starts (if still using it)

    def _on_position_changed(self, position_ms: int):
        # This signal is emitted by QMediaPlayer as playback progresses
        self._current_position_ms = position_ms
        # logger.debug(f"PlayerLogic: Position changed (QMediaPlayer): {self._current_position_ms}ms / {self._total_duration_ms}ms")
        if self._total_duration_ms > 0:  # Ensure duration is known
            self.positionChanged.emit(
                self._current_position_ms, self._total_duration_ms
            )

    def _on_duration_changed(self, duration_ms: int):
        # This signal is emitted when the media duration is known
        logger.debug(f"PlayerLogic: Duration changed (QMediaPlayer): {duration_ms}ms")
        self._total_duration_ms = duration_ms
        # Emit positionChanged as well, as the total duration is now known/updated
        self.positionChanged.emit(self._current_position_ms, self._total_duration_ms)

    def _on_player_error(self, error: QMediaPlayer.Error, error_string: str = ""):
        # error_string might not always be provided depending on Qt version/bindings
        # For PyQt6.QtMultimedia.QMediaPlayer.errorOccurred, the signature is just (self, error)
        # Let's adjust to the common signature which is just the error enum
        logger.error(
            f"PlayerLogic: Player error: {error} - {self.media_player.errorString()}"
        )
        self._is_playing = False
        self.stateChanged.emit(self._is_playing)
        if self._current_track_info:
            logger.error(
                f"PlayerLogic: Error occurred while trying to play: {self._current_track_info.get('title')}"
            )
        # Stop playback and update UI
        self.media_player.stop()
        # Potentially emit a more specific error signal to the UI
