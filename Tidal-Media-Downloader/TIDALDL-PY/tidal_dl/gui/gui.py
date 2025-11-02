# tidal_dl/gui/gui.py

import logging
import sys
import threading
from typing import Optional, List, Any, Dict, Union, cast, TYPE_CHECKING, Callable # MODIFIED: Added Callable

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QScrollArea,
    QTreeWidget,
    QTextEdit,
    QLabel,
    QStackedWidget,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QApplication,
)
from PyQt6.QtCore import (
    Qt,
    QSize,
    pyqtSignal,
    QRect,
    QPoint,
    QThread,
    pyqtSlot,
    QEvent,
    QRectF,
)
from PyQt6.QtGui import (
    QPixmap,
    QPainter,
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QResizeEvent,
    QIcon,
)
from PyQt6 import QtWidgets, QtGui

from .gui_play_bar import PlayBarWidget
from .gui_player_logic import PlayerLogic
from ..tidal import Track, Playlist, AudioQuality, Type, TIDAL_API
from ..printf import Printf
from .. import paths
from ..settings import SETTINGS
from ..linking import LinkingWorker
from ..persistence import LinkPersistenceManager
from .gui_cover_cache import CoverCache
from ..spotify import SpotifyAPI

from .gui_settings import SettingsPage
from .gui_table import SplitterTable
from .gui_title_bar import CustomTitleBar
from .gui_auth_handler import AuthHandler
from .gui_search_handler import SearchHandler
from .gui_download import DownloadHandler
from .gui_linking_handler import LinkingGuiHandler
from .gui_spotify_handler import SpotifyGuiHandler
from .gui_navigation import NavigationHandler
from .gui_search import SearchBarWidget
from .gui_playlist_tree import PlaylistTreeWidget
from .gui_utils import show_info_message, enableGui, EmittingStream, append_text_to_output
from .gui_resize_handler import ResizeHandler
from .gui_event_handlers import MainViewEventHandlers
from .task_queue_manager import TaskQueueManager # Import the new manager
from .gui_logging import setup_gui_logger, get_gui_manager

if TYPE_CHECKING:
    from .gui_table_handler import TableHandler

logger_gui = logging.getLogger(__name__)
logger_gui.setLevel(logging.WARNING)

# Set up GUI logging with INFO level for this module (GUI core operations)
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class MainView(QWidget):
    s_linkingStarted = pyqtSignal(int)
    s_linkingFinished = pyqtSignal(int, object, object, object)
    s_linkingError = pyqtSignal(int, str)
    s_spotifyLoginFinished = pyqtSignal(object)
    s_spotifyPlaylistsFetched = pyqtSignal(list)
    s_spotifyTracksFetched = pyqtSignal(str, list)
    signal_actually_paused = pyqtSignal()
    

    auth_handler: AuthHandler
    table_handler: "TableHandler" # Use forward reference string

    # Download state used by core functions; the handler orchestrates UI/state transitions.
    download_active: bool = False
    download_paused: bool = False
    stop_requested: bool = False
    cancel_requested: bool = False
    pause_event: threading.Event = threading.Event()
    stop_event: threading.Event = threading.Event()

    linking_active: bool = False
    linking_stop_event: threading.Event = threading.Event()
    linking_worker: Optional[LinkingWorker] = None
    linking_thread: Optional[QThread] = None

    s_array: List[Any] = []
    s_type: Optional[Type] = None
    s_playlist: bool = False
    s_playlist_obj: Optional[Union[Playlist, Dict]] = None

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.event_handler = MainViewEventHandlers(self)
        logger_gui.debug("Initializing MainView...")

        try:
            image_path = paths.resource_path("assets/images/background_table.png")
            self.background_pixmap = QPixmap(image_path)
            if self.background_pixmap.isNull():
                logger_gui.error(f"Failed to load background image from: {image_path}")
        except Exception as e:
            logger_gui.error(f"Error loading background image: {e}", exc_info=True)
            self.background_pixmap = QPixmap()

        self.link_persistence_manager = LinkPersistenceManager()
        self.cover_cache = CoverCache()
        self.spotify_api = SpotifyAPI()
        self.player_logic = PlayerLogic(TIDAL_API, self)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self.initView()

        from .gui_playlist_tree_handler import PlaylistTreeHandler
        from .gui_table_handler import TableHandler # Import here
        self.auth_handler = AuthHandler(self.spotify_api, parent=self)
        self.settingsPage = SettingsPage(auth_handler=self.auth_handler, parent=self)
        self.tree_handler = PlaylistTreeHandler(
            self.playlist_tree_widget, self.cover_cache, parent=self
        )
        self.table_handler = TableHandler(
            self.tableWidget, self.link_persistence_manager, None, parent=self
        )
        self.linking_gui_handler = LinkingGuiHandler(
            TIDAL_API,
            self.table_handler,
            self.link_persistence_manager,
            self.c_btnLinkTracks,
            parent=self,
        )
        self.table_handler.set_linking_handler(self.linking_gui_handler)
        self.search_handler = SearchHandler(parent=self)
        self.download_handler = DownloadHandler(
            main_view=self,
            button_stack=self.c_downloadButtonStack,
            btn_download=self.c_btnDownload,
            btn_pause_resume=self.c_btnPauseResume,
            btn_stop=self.c_btnStop,
        )
        self.spotify_gui_handler = SpotifyGuiHandler(self, self.spotify_api)
        self.navigation_handler = NavigationHandler(
            self.stackedLayout, self.mainPage, self.settingsPage, parent=self
        )
        self.resize_handler = ResizeHandler(self, self.title_bar)
        
        # Initialize the Task Queue Manager
        self.task_queue_manager = TaskQueueManager(self)
        self.tree_handler.set_task_queue_manager(self.task_queue_manager)


        # --- Redirect stdout to the log widget ---
        self.stdout_stream = EmittingStream()
        self.stdout_stream.textWritten.connect(
            lambda text: append_text_to_output(self.c_printTextEdit, text)
        )
        sys.stdout = self.stdout_stream
        # --- End stdout redirection ---

        # --- NEW: Create and add a dedicated handler for the GUI Log Area ---
        gui_log_handler = logging.StreamHandler(self.stdout_stream)
        gui_log_handler.setLevel(logging.INFO)  # Set the level for the GUI log
        
        # Configure GUI handler with the new module-specific filtering system
        gui_manager = get_gui_manager()
        gui_manager.configure_gui_handler(gui_log_handler)
        
        # Optional: Add a simple formatter for a cleaner look in the GUI
        formatter = logging.Formatter('%(levelname)s: %(message)s')
        gui_log_handler.setFormatter(formatter)
        
        # Add this new handler to the root logger
        logging.getLogger().addHandler(gui_log_handler)
        logger_gui.info("GUI log handler configured with module-specific filtering.")
        # --- END NEW SECTION ---

        self.settingsPage.audio_combo = self.c_combTQuality
        self.tree_handler.set_download_handler(self.download_handler)
        self.table_handler.set_download_handler(self.download_handler)
        self.tree_handler.set_linking_handler(self.linking_gui_handler)
        self.stackedLayout.addWidget(self.settingsPage)

        self.setMinimumSize(1000, 600)
        self.resize(1500, 800)
        self.setWindowTitle("TIDAL-DL")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

        try:
            if SETTINGS:
                audio_quality_enum = getattr(
                    SETTINGS, "audioQuality", AudioQuality.HIGH
                )
                audio_idx = self.c_combTQuality.findData(audio_quality_enum)
                if audio_idx != -1:
                    self.c_combTQuality.setCurrentIndex(audio_idx)
        except Exception as e:
            logger_gui.error(f"Error setting initial quality combobox values: {e}")

        self._connect_handler_signals()

        # --- Start initial login checks after the UI is fully set up ---
        self.auth_handler.check_initial_logins()

        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.cover_cache._save_cache)
            logger_gui.debug(
                "Connected app aboutToQuit signal to cover_cache._save_cache."
            )

        logger_gui.debug("MainView initialization complete.")

    def initView(self):
        self.title_bar = CustomTitleBar(self)
        self.search_bar = SearchBarWidget(self)
        self.c_tableArea = QScrollArea()
        self.c_tableArea.setWidgetResizable(True)
        self.c_tableArea.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        initialColumnNames = ["#", "Title", "Artists", "Album", "Length", "Quality"]
        self.tableWidget = SplitterTable(initialColumnNames, self)
        self.tableWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tableWidget.setStyleSheet("QTableWidget { background: transparent; }")
        self.c_tableArea.setWidget(self.tableWidget)

        self.c_combTQuality = QComboBox()
        for item_enum_val in AudioQuality:
            self.c_combTQuality.addItem(
                Printf.map_quality(item_enum_val), item_enum_val
            )

        self.c_btnDownload = QPushButton("Download Selected")
        self.c_btnPauseResume = QPushButton("Pause")
        self.c_btnStop = QPushButton("Stop")
        transparent_action_button_style = (
            "QPushButton { background-color: transparent; color: white; padding: 5px 10px; "
            "border: 0px solid #555; border-radius: 3px; min-height: 20px; } "
            "QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); } "
            "QPushButton:pressed { background-color: rgba(255, 255, 255, 0.15); } "
            "QPushButton:disabled { background-color: transparent; color: #777; border: 1px solid #444; }"
        )
        self.c_btnDownload.setStyleSheet(transparent_action_button_style)
        self.c_btnPauseResume.setStyleSheet(transparent_action_button_style)
        self.c_btnStop.setStyleSheet(transparent_action_button_style)
        self.c_btnLinkTracks = QPushButton("Link Tracks")
        self.c_btnLinkTracks.setStyleSheet(transparent_action_button_style)
        self.c_btnLinkTracks.setVisible(False)

        self.c_downloadButtonStack = QStackedWidget()
        self.c_downloadButtonStack.setMaximumHeight(
            self.c_btnDownload.sizeHint().height() + 2
        )
        pauseStopWidget = QWidget()
        pauseStopLayout = QHBoxLayout(pauseStopWidget)
        pauseStopLayout.setContentsMargins(0, 0, 0, 0)
        pauseStopLayout.setSpacing(5)
        pauseStopLayout.addWidget(self.c_btnPauseResume)
        pauseStopLayout.addWidget(self.c_btnStop)
        self.c_downloadButtonStack.addWidget(self.c_btnDownload)
        self.c_downloadButtonStack.addWidget(pauseStopWidget)
        self.c_downloadButtonStack.setCurrentIndex(0)

        self.c_printTextEdit = QTextEdit()
        self.c_printTextEdit.setReadOnly(True)
        self.c_printTextEdit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.c_printTextEdit.setStyleSheet(
            "QTextEdit { background-color: rgba(36, 36, 41, 0.85); border: none; padding-left: 5px; }"
        )
        self.c_printTextEdit.setVisible(False)

        self.toggleLogButton = QPushButton("Show Log")
        self.toggleLogButton.setStyleSheet(
            "QPushButton { background-color: transparent; color: white; padding: 4px 8px; "
            "border: 0px solid #555; border-radius: 3px; min-height: 20px; } "
            "QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); } "
            "QPushButton:pressed { background-color: rgba(255, 255, 255, 0.15); }"
        )

        self.line2Grid = QHBoxLayout()
        self.line2Grid.setContentsMargins(0, 10, 0, 10)
        self.line2Grid.setSpacing(10)
        self.line2Grid.addWidget(QLabel("QUALITY:"))
        self.line2Grid.addWidget(self.c_combTQuality)
        self.line2Grid.addWidget(self.toggleLogButton)
        self.toggleLogButton.setMaximumHeight(self.c_combTQuality.sizeHint().height())
        self.line2Grid.addStretch(1)
        self.line2Grid.addWidget(self.c_btnLinkTracks)
        self.line2Grid.addWidget(self.c_downloadButtonStack)

        topAreaWidget = QWidget()
        topAreaLayout = QVBoxLayout(topAreaWidget)
        topAreaLayout.setContentsMargins(0, 0, 0, 0)
        topAreaLayout.setSpacing(0)
        topAreaLayout.addWidget(self.c_tableArea, 1)
        topAreaLayout.addLayout(self.line2Grid, 0)

        class OverlaySplitterHandle(QSplitterHandle):
            def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
                super().__init__(orientation, parent)
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

            def sizeHint(self):
                return (
                    QSize(super().sizeHint().width(), 5)
                    if self.orientation() == Qt.Orientation.Vertical
                    else QSize(10, super().sizeHint().height())
                )

            def paintEvent(self, a0: Optional[QPaintEvent]):
                if self.orientation() == Qt.Orientation.Vertical:
                    p = QPainter(self)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor("#aaaaaa"))
                    r = self.rect()
                    y = r.center().y()
                    dot_offset = 4
                    num_dots = 3
                    start_x = r.center().x() - ((num_dots - 1) * dot_offset) / 2
                    for i in range(num_dots):
                        p.drawEllipse(
                            QPoint(int(start_x + i * dot_offset), int(y)), 1, 1
                        )
                else:
                    super().paintEvent(a0)

        class CustomSplitter(QSplitter):
            def createHandle(self):
                return OverlaySplitterHandle(self.orientation(), self)

        self.verticalSplitter = CustomSplitter(Qt.Orientation.Vertical)
        self.verticalSplitter.addWidget(topAreaWidget)
        self.verticalSplitter.addWidget(self.c_printTextEdit)
        self.verticalSplitter.setStretchFactor(0, 1)
        self.verticalSplitter.setStretchFactor(1, 0)
        self.verticalSplitter.setSizes([999, 1])
        self.verticalSplitter.setHandleWidth(5)
        self.verticalSplitter.setStyleSheet(
            "QSplitter { background-color: transparent; } QSplitter::handle { background-color: #444; }"
        )

        self.funcGrid = QVBoxLayout()
        self.funcGrid.setContentsMargins(6, 0, 6, 10)
        self.funcGrid.setSpacing(0)
        searchBarLayout = QHBoxLayout()
        searchBarLayout.addStretch(1)
        searchBarLayout.addWidget(self.search_bar)
        self.funcGrid.addLayout(searchBarLayout)
        self.funcGrid.addWidget(self.verticalSplitter)
        self.funcGrid.setStretchFactor(self.verticalSplitter, 1)

        self.playlist_tree_widget = PlaylistTreeWidget(self)
        self.playlist_tree_widget.setMouseTracking(True)
        funcWidget = QWidget()
        funcWidget.setLayout(self.funcGrid)
        funcWidget.setMouseTracking(True)
        funcWidget.setStyleSheet("background-color: transparent;")
        mainSplitter = QSplitter(Qt.Orientation.Horizontal)
        mainSplitter.setMouseTracking(True)
        mainSplitter.addWidget(self.playlist_tree_widget)
        mainSplitter.addWidget(funcWidget)
        mainSplitter.setHandleWidth(7)
        mainSplitter.setSizes([325, 1500])
        mainSplitter.setStyleSheet(
            "QSplitter { background-color: transparent; } "
            "QSplitter::handle { background-color: transparent; border: none; }"
        )

        self.mainPage = QWidget()
        self.mainPage.setObjectName("mainPageWidget")
        self.mainPage.setMouseTracking(True)
        mainPageLayout = QHBoxLayout(self.mainPage)
        mainPageLayout.addWidget(mainSplitter)
        mainPageLayout.setContentsMargins(0, 0, 0, 0)
        self.mainPage.setStyleSheet("background-color: transparent;")
        self.stackedLayout = QStackedLayout()
        self.stackedLayout.addWidget(self.mainPage)

        main_container_widget = QWidget()
        main_container_widget.setObjectName("mainContainerWidget")
        main_container_widget.setMouseTracking(True)
        main_v_layout = QVBoxLayout(main_container_widget)
        main_v_layout.setContentsMargins(0, 0, 0, 0)
        main_v_layout.setSpacing(0)
        main_v_layout.addWidget(self.title_bar)
        main_v_layout.addLayout(self.stackedLayout)

        self.play_bar_widget = PlayBarWidget(self)
        self.play_bar_widget.setFixedHeight(100)
        main_v_layout.addWidget(self.play_bar_widget)

        top_level_layout = QVBoxLayout(self)
        top_level_layout.setContentsMargins(0, 0, 0, 0)
        top_level_layout.addWidget(main_container_widget)

        self.setStyleSheet(
            """
            MainView {
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                background-color: transparent;
            }
        """
        )

    def _connect_handler_signals(self):
        self.auth_handler.tidalLoginSuccess.connect(
            self.tree_handler.refreshTidalPlaylists
        )
        self.auth_handler.spotifyLoginFinished.connect(
            self.spotify_gui_handler.onSpotifyLoginFinished
        )
        self.playlist_tree_widget.spotify_connect_button.clicked.connect(
            lambda: self.auth_handler.trigger_spotify_login(check_cache_only=False)
        )
        self.search_bar.searchTriggered.connect(self._trigger_search)
        self.search_bar.liveSearchRequested.connect(
            self.search_handler.perform_live_search
        )
        self.search_bar.resultSelected.connect(
            self.search_handler._on_result_item_clicked
        )
        self.search_handler.searchResultsReady.connect(
            self.table_handler.populate_search_results
        )
        self.search_handler.liveSearchResultsReady.connect(
            self.search_bar._display_live_results
        )
        self.search_handler.data_fetched.connect(
            self.table_handler._populate_table_from_search
        )
        self.search_handler.searchFailed.connect(self.table_handler.show_error_message)
        self.search_handler.searchFailed.connect(
            lambda msg: logger.info(f"Search Error: {msg}")
        )
        self.tree_handler.tidalPlaylistSelected.connect(
            lambda pl: logger.info(f"Selected Tidal Playlist: {pl.title}")
        )
        self.tree_handler.spotifyPlaylistSelected.connect(
            lambda pl_data: logger.info(
                f"Selected Spotify Playlist: {pl_data['data']['name']}"
            )
        )
        self.tree_handler.requestTidalPlaylistDownload.connect(
            self.task_queue_manager.add_tidal_download_job
        )
        self.s_spotifyLoginFinished.connect(
            self.spotify_gui_handler.onSpotifyLoginFinished
        )
        self.s_spotifyPlaylistsFetched.connect(
            self.tree_handler.populate_spotify_playlists
        )
        self.s_spotifyTracksFetched.connect(
            self.table_handler.populate_spotify_tracks
        )
        self.settingsPage.spotifyCredentialsUpdated.connect(
            lambda: self.auth_handler.trigger_spotify_login(check_cache_only=False)
        )
        self.c_btnDownload.clicked.connect(self.download_handler.download)
        self.c_btnPauseResume.clicked.connect(
            self.download_handler.onPauseResumeClicked
        )
        self.c_btnStop.clicked.connect(self.download_handler.onStopClicked)
        self.linking_gui_handler.requestLinkingStart.connect(self.startLinkingWorker)
        self.tableWidget.candidateSelectedInSubRow.connect(
            self.linking_gui_handler.onManualLinkSelected
        )
        self.s_linkingStarted.connect(self.linking_gui_handler.onLinkingStarted)
        self.s_linkingFinished.connect(self.linking_gui_handler.onLinkingFinished)
        self.s_linkingError.connect(self.linking_gui_handler.onLinkingError)
        self.linking_gui_handler.manualLinkApplied.connect(
            self.table_handler.collapse_sub_row
        )
        self.tableWidget.itemSelectionChanged.connect(
            self.linking_gui_handler.update_link_button_state
        )
        self.tableWidget.itemDoubleClicked.connect(
            self._on_table_item_double_clicked
        )
        self.settingsPage.settingsClosedWithoutSaving.connect(
            self.navigation_handler.show_main_menu
        )
        self.settingsPage.settingsSavedAndClosed.connect(
            self.navigation_handler.show_main_menu
        )
        self.settingsPage.playlistDisplaySettingsChanged.connect(self.tree_handler.onPlaylistDisplaySettingsChanged)
        self.toggleLogButton.clicked.connect(self.toggle_log_console)
        self.title_bar.s_showSettings.connect(self.navigation_handler.show_settings)

        # Connect the fontSizeChanged signal from settings page to reapply stylesheet
        self.settingsPage.fontSizeChanged.connect(self.on_font_size_changed)

        self.player_logic.trackChanged.connect(self._on_player_track_changed)
        self.player_logic.stateChanged.connect(
            self.play_bar_widget.update_play_pause_button
        )
        self.player_logic.positionChanged.connect(self.play_bar_widget.update_progress)
        self.player_logic.volumeChangedSignal.connect(
            self.play_bar_widget.update_volume
        )
        self.player_logic.shuffleRepeatChanged.connect(
            self.play_bar_widget.update_shuffle_repeat_state
        )

        self.play_bar_widget.playPauseClicked.connect(
            self.player_logic.toggle_play_pause
        )
        self.play_bar_widget.nextClicked.connect(self.player_logic.next_track)
        self.play_bar_widget.previousClicked.connect(self.player_logic.previous_track)
        self.play_bar_widget.shuffleClicked.connect(self.player_logic.toggle_shuffle)
        self.play_bar_widget.repeatClicked.connect(self.player_logic.toggle_repeat)
        self.play_bar_widget.seekPositionChanged.connect(
            self.player_logic.seek_position
        )
        self.play_bar_widget.volumeChanged.connect(self.player_logic.set_volume)
        self.play_bar_widget.muteClicked.connect(self.player_logic.toggle_mute)

        # --- Add these lines to connect the progress signals ---

        # Connect Task Queue Manager signals to Playlist Tree Handler slots
        self.task_queue_manager.jobStarted.connect(self.tree_handler.on_job_started)
        self.task_queue_manager.jobFinished.connect(self.tree_handler.on_job_finished)

        # Connect progress signals from individual handlers to the Playlist Tree Handler
        self.download_handler.downloadProgress.connect(self.tree_handler.on_job_progress)
        self.linking_gui_handler.linkProgress.connect(self.tree_handler.on_job_progress)

    @pyqtSlot(QtWidgets.QTableWidgetItem)
    def _on_table_item_double_clicked(self, item: QtWidgets.QTableWidgetItem):
        if not item:
            return
        row = item.row()
        title_item = self.tableWidget.item(row, 1)
        if not title_item:
            return
        item_data = title_item.data(Qt.ItemDataRole.UserRole)
        track_to_play: Optional[Track] = None
        spotify_info: Optional[Dict[str, Any]] = None

        if isinstance(item_data, Track):
            track_to_play = item_data
        elif isinstance(item_data, dict) and item_data.get("type") == "spotify_track":
            spotify_info = item_data.get("data")
            valid_link_statuses = [
                "cached_linked",
                "found",
                "auto_linked",
                "manual_linked",
                "cached_linked_full",
                "cached_linked_id_fetched",
                "found_uncertain",
            ]
            if (
                spotify_info
                and item_data.get("link_status") in valid_link_statuses
                and "tidal_track" in item_data
                and isinstance(item_data.get("tidal_track"), Track)
            ):
                track_to_play = item_data.get("tidal_track")
            else:
                return

        if track_to_play:
            artist_names = "Unknown Artist"
            if track_to_play.artists and isinstance(track_to_play.artists, list):
                artist_names = ", ".join(
                    [a.name for a in track_to_play.artists if hasattr(a, "name")]
                )
            elif hasattr(track_to_play.artists, "name"):
                artist_names = track_to_play.artists.name

            player_track_info = {
                "id": track_to_play.id,
                "title": track_to_play.title,
                "artist": artist_names,
                "album_title": (
                    track_to_play.album.title if track_to_play.album else "Unknown Album"
                ),
                "duration_ms": (
                    track_to_play.duration * 1000 if track_to_play.duration else 0
                ),
                "album_art_id": (
                    track_to_play.album.cover if track_to_play.album else None
                ),
            }
            self.player_logic.play_track(player_track_info)
        elif spotify_info:
            self.play_bar_widget.set_track_info(
                f"[Spotify] {spotify_info.get('name', 'Unknown')}",
                ", ".join(spotify_info.get("artists", ["Unknown Artist"])),
            )
            self.play_bar_widget.update_progress(0, spotify_info.get("duration_ms", 0))

    @pyqtSlot(dict)
    def _on_player_track_changed(self, track_info: Dict[str, Any]):
        title = track_info.get("title", "Unknown Title")
        artist = track_info.get("artist", "Unknown Artist")
        album_art_id = track_info.get("album_art_id")
        pixmap = None
        if album_art_id:
            try:
                cover_bytes = TIDAL_API.getCoverData(album_art_id, "80", "80")
                if cover_bytes:
                    temp_pixmap = QPixmap()
                    if temp_pixmap.loadFromData(cover_bytes):
                        pixmap = temp_pixmap
            except Exception as e:
                logger_gui.error(
                    f"Error loading album art for play bar (ID: {album_art_id}): {e}"
                )
        self.play_bar_widget.set_track_info(title, artist, pixmap)

    @pyqtSlot(int)
    def on_font_size_changed(self, size: int):
        """Reapplies the global stylesheet with the new font size."""
        from . import gui_app_setup
        app = QApplication.instance()
        if app and isinstance(app, QApplication):
            logger_gui.info(f"Applying new font size from settings: {size}pt")
            gui_app_setup.apply_global_stylesheet(app, size)
        else:
            logger_gui.warning("Could not get QApplication instance to apply new font size.")

    def paintEvent(self, a0: Optional[QPaintEvent]):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Rounded clipping
        path = QtGui.QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor("#1E1E1E"))
        if hasattr(self, "background_pixmap") and not self.background_pixmap.isNull():
            target_rect = self.rect()
            scaled_pixmap = self.background_pixmap.scaled(
                target_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (target_rect.width() - scaled_pixmap.width()) / 2
            y = (target_rect.height() - scaled_pixmap.height()) / 2
            painter.drawPixmap(QPoint(int(x), int(y)), scaled_pixmap)

    def resizeEvent(self, a0: Optional[QResizeEvent]):
        self.update()
        super(MainView, self).resizeEvent(a0)

    def event(self, a0: Optional[QEvent]) -> bool:
        if self.event_handler.handle_event(a0):
            pass
        return super(MainView, self).event(a0)

    def keyPressEvent(self, a0: Optional[QKeyEvent]):
        if not self.event_handler.handle_keyPressEvent(a0):
            super(MainView, self).keyPressEvent(a0)

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if not self.event_handler.handle_mousePressEvent(a0):
            super(MainView, self).mousePressEvent(a0)

    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):
        if not self.event_handler.handle_mouseMoveEvent(a0):
            super(MainView, self).mouseMoveEvent(a0)

    def mouseReleaseEvent(self, a0: Optional[QMouseEvent]):
        if not self.event_handler.handle_mouseReleaseEvent(a0):
            super(MainView, self).mouseReleaseEvent(a0)

    def toggle_log_console(self):
        is_currently_visible = self.c_printTextEdit.isVisible()
        if is_currently_visible:
            self.c_printTextEdit.setVisible(False)
            self.toggleLogButton.setText("Show Log")
            if hasattr(self, "verticalSplitter"):
                current_sizes = self.verticalSplitter.sizes()
                if len(current_sizes) == 2:
                    self.verticalSplitter.setSizes(
                        [current_sizes[0] + current_sizes[1] - 1, 1]
                    )
        else:
            self.c_printTextEdit.setVisible(True)
            self.toggleLogButton.setText("Hide Log")
            if hasattr(self, "verticalSplitter"):
                total_height = self.verticalSplitter.height()
                if total_height > 0:
                    self.verticalSplitter.setSizes(
                        [int(total_height * 0.75), int(total_height * 0.25)]
                    )
                else:
                    self.verticalSplitter.setSizes([500, 150])

    @pyqtSlot(str)
    def _trigger_search(self, query: str):
        self.search_handler.perform_search(query)

    @pyqtSlot(list)
    def startLinkingWorker(self, tracks_to_link_data: list, on_finish_callback: Optional[Callable] = None): # MODIFIED: Added callback parameter
        if not tracks_to_link_data or self.linking_active:
            return
        self.linking_active = True
        self.linking_stop_event.clear()
        self.c_btnLinkTracks.setText("Stop Linking")
        try:
            self.c_btnLinkTracks.clicked.disconnect()
        except TypeError:
            pass
        self.c_btnLinkTracks.clicked.connect(
            self.linking_gui_handler.onStopLinkingClicked
        )
        self.c_btnLinkTracks.setEnabled(True)
        self.linking_worker = LinkingWorker(
            TIDAL_API, tracks_to_link_data, self.linking_stop_event
        )
        self.linking_thread = QThread(self)
        self.linking_worker.moveToThread(self.linking_thread)
        self.linking_worker.started.connect(self.s_linkingStarted)
        self.linking_worker.finished.connect(self.s_linkingFinished)
        self.linking_worker.error.connect(self.s_linkingError)
        self.linking_thread.started.connect(self.linking_worker.run)
        self.linking_worker.allTasksFinished.connect(self.linking_thread.quit)
        
        # MODIFIED: Connect the optional callback if it exists
        if on_finish_callback:
            self.linking_worker.allTasksFinished.connect(on_finish_callback)

        self.linking_worker.error.connect(self.linking_thread.quit)
        self.linking_thread.finished.connect(self.linking_worker.deleteLater)
        self.linking_thread.finished.connect(self.linking_thread.deleteLater)
        self.linking_thread.finished.connect(self._clearLinkingWorkerRefs)
        self.linking_thread.start()

    def _clearLinkingWorkerRefs(self):
        self.linking_worker = None
        self.linking_thread = None
        self.linking_active = False
        try:
            self.c_btnLinkTracks.clicked.disconnect()
        except TypeError:
            pass
        self.c_btnLinkTracks.clicked.connect(
            self.linking_gui_handler._handle_link_button_click
        )
        self.linking_gui_handler.update_link_button_state()
        
    def closeEvent(self, a0: Optional[QtGui.QCloseEvent]):
        """
        Handles the window close event to ensure graceful shutdown of background threads.
        """
        logger_gui.info("Close event triggered. Shutting down background threads...")

        # 1. Signal all workers to stop using their existing stop mechanisms.
        # It's safe to call these even if no download/linking is active.
        self.download_handler.onStopClicked()
        self.linking_gui_handler.onStopLinkingClicked()
        if self.task_queue_manager:
            self.task_queue_manager.stop_all_tasks()

        # 2. Specifically wait for the linking QThread to finish.
        # The download handler uses a ThreadPoolExecutor which is harder to wait for
        # from here, but its worker threads check the stop_event frequently.
        if self.linking_thread and self.linking_thread.isRunning():
            logger_gui.info("Waiting for linking thread to finish...")
            self.linking_thread.quit()  # Asks the thread's event loop to exit
            
            # Wait for the thread to actually terminate, with a timeout (e.g., 3 seconds)
            # This is the most critical step to prevent the error message.
            if not self.linking_thread.wait(3000):
                logger_gui.warning("Linking thread did not terminate in time. Forcing termination.")
                self.linking_thread.terminate() # Use as a last resort

        logger_gui.info("All background tasks signaled to stop. Proceeding with shutdown.")
        
        # 3. Accept the event to allow the window to close.
        if a0:
            a0.accept()


def main():
    """The main entry point for the GUI application."""
    from . import gui_app_setup

    gui_app_setup.initialize_settings_and_token()
    gui_app_setup.setup_global_exception_handler()
    if enableGui():
        return gui_app_setup.start_gui()
    else:
        message = "GUI dependencies (PyQt6) are not installed or found. Cannot start graphical interface."
        logger_gui.error(message)
        try:
            from PyQt6.QtWidgets import QMessageBox

            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Icon.Critical)
            msg_box.setText("Missing Dependencies")
            msg_box.setInformativeText(
                f"{message}\nPlease run 'pip install PyQt6' to fix."
            )
            msg_box.setWindowTitle("Error")
            msg_box.exec()
        except ImportError:
            print(message, file=sys.stderr)
            print(
                "\nTo use the GUI, please install the required packages:",
                file=sys.stderr,
            )
            print("  pip install PyQt6", file=sys.stderr)
            print("\nExiting.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())