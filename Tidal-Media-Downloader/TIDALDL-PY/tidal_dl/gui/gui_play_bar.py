import logging
import os  # Import os for path checking
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QSlider,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QPoint
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPaintEvent

from .. import paths  # For icon loading

logger = logging.getLogger(__name__)


class PlayBarWidget(QWidget):
    """
    UI for the bottom play bar.
    """

    # Signals for player control
    playPauseClicked = pyqtSignal()
    nextClicked = pyqtSignal()
    previousClicked = pyqtSignal()
    shuffleClicked = pyqtSignal(bool)
    repeatClicked = pyqtSignal(bool)
    seekPositionChanged = pyqtSignal(int)  # Emits percentage (0-1000)
    volumeChanged = pyqtSignal(int)  # Emits volume (0-100)
    muteClicked = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(70)  # Adjust height as needed
        self.setObjectName("PlayBarWidget")
        self._init_ui()
        self._is_playing = False
        self._is_muted = False
        self._is_shuffle = False
        self._is_repeat = False

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # 1. Album Art & Track Info (Left)
        track_info_widget = QWidget()
        track_info_layout = QHBoxLayout(track_info_widget)
        track_info_layout.setContentsMargins(0, 0, 0, 0)
        track_info_layout.setSpacing(10)
        track_info_layout.setAlignment(
            Qt.AlignmentFlag.AlignVCenter
        )  # Vertically center items

        self.album_art_label = QLabel()
        self.album_art_label.setFixedSize(60, 60)
        self.album_art_label.setScaledContents(True)
        self.album_art_label.setStyleSheet(
            "border-radius: 8px;"
        )  # Added for rounded corners
        self.album_art_label.setPixmap(
            QPixmap(paths.resource_path("assets/icons/default_playlist.png")).scaled(
                60,
                60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        track_info_layout.addWidget(self.album_art_label)

        title_artist_layout = QVBoxLayout()
        title_artist_layout.setSpacing(2)
        self.track_title_label = QLabel("No Track Loaded")
        self.track_title_label.setObjectName("PlayBarTrackTitle")
        self.track_artist_label = QLabel(" ")
        self.track_artist_label.setObjectName("PlayBarTrackArtist")

        title_artist_layout.addStretch(
            1
        )  # Add stretch before labels for vertical centering
        title_artist_layout.addWidget(self.track_title_label)
        title_artist_layout.addWidget(self.track_artist_label)
        title_artist_layout.addStretch(
            1
        )  # Add stretch after labels for vertical centering
        track_info_layout.addLayout(title_artist_layout)
        track_info_layout.addStretch()

        main_layout.addWidget(track_info_widget, 1)  # Stretch factor 1

        # 2. Player Controls (Center)
        player_controls_widget = QWidget()
        player_controls_layout = QVBoxLayout(player_controls_widget)
        player_controls_layout.setContentsMargins(0, 0, 0, 0)
        player_controls_layout.setSpacing(3)

        # Top row: Shuffle, Previous, Play/Pause, Next, Repeat
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(15)

        icon_size = QSize(22, 22)
        button_size = QSize(32, 32)

        self.shuffle_button = self._create_player_button(
            "assets/icons/shuffle_icon_white.png",
            "Shuffle",
            icon_size,
            button_size,
            checkable=True,
        )
        self.previous_button = self._create_player_button(
            "assets/icons/previous.png", "Previous", icon_size, button_size
        )
        self.play_pause_button = self._create_player_button(
            "assets/icons/play.png", "Play", icon_size, button_size
        )
        self.next_button = self._create_player_button(
            "assets/icons/next.png", "Next", icon_size, button_size
        )
        self.repeat_button = self._create_player_button(
            "assets/icons/repeat_icon_white.png",
            "Repeat",
            icon_size,
            button_size,
            checkable=True,
        )

        buttons_layout.addStretch()
        buttons_layout.addWidget(self.shuffle_button)
        buttons_layout.addWidget(self.previous_button)
        buttons_layout.addWidget(self.play_pause_button)
        buttons_layout.addWidget(self.next_button)
        buttons_layout.addWidget(self.repeat_button)
        buttons_layout.addStretch()
        player_controls_layout.addLayout(buttons_layout)

        # Bottom row: Progress Slider & Time Labels
        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(5)
        self.current_time_label = QLabel("0:00")
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 1000)  # Represents 0.0% to 100.0%
        self.total_time_label = QLabel("0:00")

        progress_layout.addWidget(self.current_time_label)
        progress_layout.addWidget(self.progress_slider)
        progress_layout.addWidget(self.total_time_label)
        player_controls_layout.addLayout(progress_layout)

        main_layout.addWidget(player_controls_widget, 2)  # Stretch factor 2

        # 3. Volume Controls (Right)
        volume_widget = QWidget()
        volume_layout = QHBoxLayout(volume_widget)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(5)

        self.mute_button = self._create_player_button(
            "assets/icons/audio_on_icon_white.png",
            "Mute",
            QSize(18, 18),
            QSize(28, 28),
            checkable=True,
        )
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(75)
        self.volume_slider.setFixedWidth(100)

        volume_layout.addStretch()
        volume_layout.addWidget(self.mute_button)
        volume_layout.addWidget(self.volume_slider)

        main_layout.addWidget(volume_widget, 1)  # Stretch factor 1

        # Connect signals
        self.play_pause_button.clicked.connect(self.playPauseClicked.emit)
        self.next_button.clicked.connect(self.nextClicked.emit)
        self.previous_button.clicked.connect(self.previousClicked.emit)
        self.shuffle_button.toggled.connect(self.shuffleClicked.emit)
        self.repeat_button.toggled.connect(self.repeatClicked.emit)
        self.progress_slider.sliderMoved.connect(
            self.seekPositionChanged.emit
        )  # sliderMoved for live seeking
        self.progress_slider.valueChanged.connect(
            self.seekPositionChanged.emit
        )  # valueChanged for programmatic changes
        self.volume_slider.valueChanged.connect(self.volumeChanged.emit)
        self.mute_button.toggled.connect(self.muteClicked.emit)

        self.apply_styles()

    def _create_player_button(
        self,
        icon_path_suffix: str,
        tooltip: str,
        icon_size: QSize,
        button_size: QSize,
        checkable: bool = False,
    ) -> QPushButton:
        button = QPushButton()
        try:
            full_icon_path = paths.resource_path(icon_path_suffix)
            if os.path.exists(full_icon_path):
                button.setIcon(QIcon(full_icon_path))
            else:
                logger.warning(
                    f"Icon not found: {full_icon_path}. Using text for '{tooltip}'."
                )
                button.setText(tooltip[0])  # Fallback to first letter
        except Exception as e:
            logger.error(f"Error loading icon {icon_path_suffix}: {e}")
            button.setText(tooltip[0])

        button.setIconSize(icon_size)
        button.setFixedSize(button_size)
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.setObjectName("PlayerControlButton")
        return button

    def set_track_info(
        self, title: str, artist: str, album_art_pixmap: Optional[QPixmap] = None
    ):
        self.track_title_label.setText(title)
        self.track_artist_label.setText(artist)
        if album_art_pixmap and not album_art_pixmap.isNull():
            self.album_art_label.setPixmap(
                album_art_pixmap.scaled(
                    50,
                    50,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.album_art_label.setPixmap(
                QPixmap(
                    paths.resource_path("assets/icons/default_playlist.png")
                ).scaled(
                    50,
                    50,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def update_play_pause_button(self, is_playing: bool):
        self._is_playing = is_playing
        icon_path_suffix = (
            "assets/icons/pause.png" if is_playing else "assets/icons/play.png"
        )
        tooltip = "Pause" if is_playing else "Play"
        try:
            full_icon_path = paths.resource_path(icon_path_suffix)
            if os.path.exists(full_icon_path):
                self.play_pause_button.setIcon(QIcon(full_icon_path))
            else:
                self.play_pause_button.setText("P" if is_playing else ">")
        except Exception as e:
            logger.error(f"Error updating play/pause icon {icon_path_suffix}: {e}")
            self.play_pause_button.setText("P" if is_playing else ">")
        self.play_pause_button.setToolTip(tooltip)

    def update_progress(self, current_ms: int, total_ms: int):
        self.current_time_label.setText(self._format_ms_to_time(current_ms))
        self.total_time_label.setText(self._format_ms_to_time(total_ms))
        if total_ms > 0:
            slider_position = int((current_ms / total_ms) * 1000)
            # Block signals to prevent emitting seekPositionChanged when setting programmatically
            self.progress_slider.blockSignals(True)
            self.progress_slider.setValue(slider_position)
            self.progress_slider.blockSignals(False)
        else:
            self.progress_slider.setValue(0)

    def _format_ms_to_time(self, ms: int) -> str:
        if ms < 0:
            ms = 0
        seconds = (ms // 1000) % 60
        minutes = (ms // (1000 * 60)) % 60
        hours = ms // (1000 * 60 * 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def update_volume(self, volume: int, is_muted: bool):
        self._is_muted = is_muted
        self.mute_button.setChecked(is_muted)
        # Update mute button icon based on is_muted
        if is_muted:
            mute_icon_path = "assets/icons/mute_icon_white.png"
        else:
            mute_icon_path = "assets/icons/audio_on_icon_white.png"
        self.mute_button.setIcon(QIcon(paths.resource_path(mute_icon_path)))

        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(0 if is_muted else volume)
        self.volume_slider.blockSignals(False)

    def update_shuffle_repeat_state(self, is_shuffle: bool, is_repeat: bool):
        self._is_shuffle = is_shuffle
        self._is_repeat = is_repeat
        self.shuffle_button.setChecked(is_shuffle)
        self.repeat_button.setChecked(is_repeat)
        # Update icons if you have different icons for active/inactive states

    def apply_styles(self):
        # The azure blue color used for playlist selection border is #00E4E3
        # Let's use a slightly less intense version for the slider fill, or the same one.
        # For example, #00B8B7 or stick with #00E4E3 if it looks good.
        # Let's try a common "media player blue" often seen, like #0078D4 or a vibrant cyan.
        # The playlist selection text is #33ffe9. The border is #00E4E3.
        # Let's use a color close to the border for the slider fill.
        slider_fill_color = "#00C0C0"  # A vibrant cyan, adjust as needed
        # Or, to match the playlist selection border exactly:
        # slider_fill_color = "#00E4E3"

        self.setStyleSheet(
            f"""
            #PlayBarWidget {{
                background-color: #181818; /* Dark background for the play bar */
                border-top: 1px solid #282828; /* Subtle top border */
            }}
            #PlayBarTrackTitle {{
                color: #ffffff;
                font-weight: bold;
                font-size: 10pt;
            }}
            #PlayBarTrackArtist {{
                color: #b3b3b3;
                font-size: 9pt;
            }}
            QLabel {{ /* For time labels */
                color: #b3b3b3;
                font-size: 8pt;
            }}
            #PlayerControlButton {{
                border: none;
                background-color: transparent;
                padding: 0px; /* Remove padding to let icon size dictate */
            }}
            #PlayerControlButton:hover {{
                /* background-color: #282828; */ /* Subtle hover, or remove if icons change */
            }}
            #PlayerControlButton:checked {{ /* For toggle buttons like shuffle/repeat/mute */
                /* background-color: #282828; */ /* Indicate active state, or change icon */
            }}
            QSlider::groove:horizontal {{
                border: 1px solid #282828;
                height: 4px; /* Groove height */
                background: #404040; /* Groove background */
                margin: 2px 0;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #b3b3b3; /* Handle color */
                border: 1px solid #b3b3b3;
                width: 10px; /* Handle width */
                height: 10px; /* Handle height to make it circular */
                margin: -3px 0; /* Adjust vertical position to center on groove */
                border-radius: 5px; /* Make it circular */
            }}
            QSlider::handle:horizontal:hover {{
                background: #ffffff;
                border: 1px solid #ffffff;
            }}
            QSlider::sub-page:horizontal {{ /* Part of the groove before the handle */
                background: {slider_fill_color}; /* MODIFIED COLOR HERE */
                border: 1px solid #282828; /* Keep border or make it match fill */
                height: 4px;
                border-radius: 2px;
            }}
            QSlider::add-page:horizontal {{ /* Part of the groove after the handle */
                background: #404040; /* Original groove color */
                border: 1px solid #282828;
                height: 4px;
                border-radius: 2px;
            }}
        """
        )
