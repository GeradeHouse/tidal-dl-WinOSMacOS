import logging
import sys  # Add sys import if not already present at the top of gui.py
import threading  # Add threading import if not already present
from typing import Optional, List, Any, Dict, Union, cast

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
)  # Added QApplication
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
)  # Added QRectF
from PyQt6.QtGui import (
    QPixmap,
    QPainter,
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QResizeEvent,
    QIcon,
    QPainterPath,
)
from PyQt6 import QtWidgets, QtGui  # Added QtGui

from .gui_play_bar import PlayBarWidget
from .gui_player_logic import PlayerLogic
from ..model import Track, Playlist  # For type checking
from ..printf import Printf
from .. import paths
from ..settings import SETTINGS  # Assuming SETTINGS is imported for quality combobox
from ..tidal import AudioQuality, Type, TIDAL_API  # For quality and type enums
from ..linking import LinkingWorker
from ..persistence import LinkPersistenceManager
from ..cover_cache import PlaylistCoverCache
from ..spotify import SpotifyAPI


# Import other necessary GUI components and handlers
from .gui_settings import SettingsPage
from .gui_table import SplitterTable
from .gui_title_bar import CustomTitleBar
from .gui_auth_handler import AuthHandler
from .gui_playlist_tree_handler import PlaylistTreeHandler
from .gui_search_handler import SearchHandler
from .gui_table_handler import TableHandler
from .gui_download import DownloadHandler
from .gui_linking_handler import LinkingGuiHandler
from .gui_spotify_handler import SpotifyGuiHandler
from .gui_navigation import NavigationHandler
from .gui_search import SearchBarWidget
from .gui_playlist_tree import PlaylistTreeWidget
from .gui_utils import show_info_message, enableGui
from .gui_resize_handler import ResizeHandler
from .gui_event_handlers import MainViewEventHandlers

# This import was added in a previous step and is necessary
import logging  # Ensure logging is imported

logger_gui = logging.getLogger(__name__)  # Use a distinct logger name if needed
logger_gui.setLevel(logging.WARNING)  # Set specific level for this module


class MainView(QWidget):
    s_linkingStarted = pyqtSignal(int)
    s_linkingFinished = pyqtSignal(int, object, object, object)
    s_linkingError = pyqtSignal(int, str)
    s_spotifyLoginFinished = pyqtSignal(object)
    s_spotifyPlaylistsFetched = pyqtSignal(list)
    s_spotifyTracksFetched = pyqtSignal(str, list)
    s_downloadEnd = pyqtSignal(str, bool, str, object)
    signal_actually_paused = pyqtSignal()

    download_active: bool = False
    auth_handler: AuthHandler
    download_paused: bool = False
    stop_requested: bool = False
    cancel_requested: bool = False
    download_thread: Optional[threading.Thread] = None
    pause_event: threading.Event = threading.Event()
    stop_event: threading.Event = threading.Event()
    linking_active: bool = False
    linking_stop_event: threading.Event = threading.Event()
    linking_worker: Optional[LinkingWorker] = None
    linking_thread: Optional[QThread] = None
    _original_s_array_before_ctx_dl: Optional[List[Any]] = None
    _original_s_type_before_ctx_dl: Optional[Type] = None
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
        self.playlist_cache_manager = PlaylistCoverCache()
        self.spotify_api = SpotifyAPI()

        # Pass the main TIDAL_API instance to PlayerLogic
        self.player_logic = PlayerLogic(TIDAL_API, self)  # MODIFIED HERE

        # --- Crucial Main Window Setup ---
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(
            False
        )  # Important for custom painting / transparent background

        # +++ Add Debug Logs for MainView +++
        logger_gui.debug(f"MainView windowFlags: {self.windowFlags()}")
        logger_gui.debug(
            f"MainView WA_TranslucentBackground: {self.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)}"
        )
        logger_gui.debug(f"MainView autoFillBackground: {self.autoFillBackground()}")
        # +++ End Debug Logs +++

        self.initView()  # UI setup (this will set the stylesheet further down)

        self.auth_handler = AuthHandler(self.spotify_api, parent=self)
        self.settingsPage = SettingsPage(auth_handler=self.auth_handler, parent=self)
        self.tree_handler = PlaylistTreeHandler(
            self.playlist_tree_widget, self.playlist_cache_manager, parent=self
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

        self.settingsPage.audio_combo = self.c_combTQuality
        self.tree_handler.set_download_handler(self.download_handler)
        self.table_handler.set_download_handler(self.download_handler)
        self.tree_handler.set_linking_handler(self.linking_gui_handler)
        self.stackedLayout.addWidget(self.settingsPage)

        self.setMinimumSize(1000, 600)
        self.resize(1500, 800)  # Initial size
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
        logger_gui.debug("MainView initialization complete.")
        # +++ Add Debug Log for MainView Stylesheet AFTER it's set in initView +++
        logger_gui.debug(
            f"MainView effective stylesheet (after initView): {self.styleSheet()}"
        )
        # +++ End Debug Log +++

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
        transparent_action_button_style = """QPushButton { background-color: transparent; color: white; padding: 5px 10px; border: 0px solid #555; border-radius: 3px; min-height: 20px; } QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); } QPushButton:pressed { background-color: rgba(255, 255, 255, 0.15); } QPushButton:disabled { background-color: transparent; color: #777; border: 1px solid #444; }"""
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
            """QPushButton { background-color: transparent; color: white; padding: 4px 8px; border: 0px solid #555; border-radius: 3px; min-height: 20px; } QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); } QPushButton:pressed { background-color: rgba(255, 255, 255, 0.15); }"""
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
                    [
                        p.drawEllipse(
                            QPoint(int(start_x + i * dot_offset), int(y)), 1, 1
                        )
                        for i in range(num_dots)
                    ]
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
            "QSplitter { background-color: transparent; } QSplitter::handle { background-color: transparent; border: none; }"
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

        # --- Play Bar Integration ---
        self.play_bar_widget = PlayBarWidget(self)
        self.play_bar_widget.setFixedHeight(100)  # Increased height
        main_v_layout.addWidget(self.play_bar_widget)
        # --- End Play Bar Integration ---

        top_level_layout = QVBoxLayout(self)
        top_level_layout.setContentsMargins(0, 0, 0, 0)
        top_level_layout.addWidget(main_container_widget)

        # Corrected indentation for the following lines:
        # Stylesheet for MainView
        # The background-color here might be ignored by paintEvent when WA_TranslucentBackground is True.
        # We will explicitly paint the background in paintEvent.
        # The border-radius is still important for QPainterPath clipping.
        self.setStyleSheet(
            """
            MainView {
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                
                /* Set to transparent; paintEvent will handle the actual fill */
                background-color: transparent;
            }
        """
        )
        logger_gui.debug(
            "initView UI setup complete. MainView stylesheet set (bg transparent, paintEvent will draw red)."
        )

    def _connect_handler_signals(self):
        logger_gui.debug("Connecting handler signals...")
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
        # OLD, WRONG connection: self.search_bar.resultSelected.connect(self.table_handler.populate_search_results)
        # NEW, CORRECT connection: Route the signal to the SearchHandler to initiate the fetch thread
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
            lambda msg: Printf.info(f"Search Error: {msg}")
        )
        self.tree_handler.tidalPlaylistSelected.connect(
            lambda pl: Printf.info(f"Selected Tidal Playlist: {pl.title}")
        )
        self.tree_handler.spotifyPlaylistSelected.connect(
            lambda pl_data: Printf.info(
                f"Selected Spotify Playlist: {pl_data['data']['name']}"
            )
        )
        self.tree_handler.requestTidalPlaylistDownload.connect(
            self.download_handler.startContextMenuDownload
        )
        self.s_spotifyLoginFinished.connect(
            self.spotify_gui_handler.onSpotifyLoginFinished
        )
        self.s_spotifyPlaylistsFetched.connect(
            self.spotify_gui_handler.onSpotifyPlaylistsFetched
        )
        self.s_spotifyTracksFetched.connect(
            self.spotify_gui_handler.onSpotifyTracksFetched
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
        )  # Connect table double click
        self.settingsPage.settingsClosedWithoutSaving.connect(
            self.navigation_handler.show_main_menu
        )
        self.settingsPage.settingsSavedAndClosed.connect(
            self.navigation_handler.show_main_menu
        )
        self.s_downloadEnd.connect(self.download_handler.downloadEnd)  # type: ignore
        self.signal_actually_paused.connect(self.download_handler.onActuallyPaused)
        self.toggleLogButton.clicked.connect(self.toggle_log_console)
        self.title_bar.s_showSettings.connect(self.navigation_handler.show_settings)

        # --- Connect PlayerLogic signals to PlayBarWidget slots ---
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

        # --- Connect PlayBarWidget signals to PlayerLogic slots ---
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

        logger_gui.debug("Signal connections established.")

    # --- New methods for Play Bar interaction ---
    @pyqtSlot(QtWidgets.QTableWidgetItem)  # Use QtWidgets.QTableWidgetItem
    def _on_table_item_double_clicked(
        self, item: QtWidgets.QTableWidgetItem
    ):  # Use QtWidgets.QTableWidgetItem
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
            # --- START MODIFICATION ---
            # Add 'cached_linked_full' and 'cached_linked_id_fetched' to the list of valid statuses
            # Also, ensure 'tidal_track' key exists before checking its type.
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
            ):  # Check 'tidal_track' exists and is Track
                track_to_play = item_data.get("tidal_track")
            # --- END MODIFICATION ---
            elif spotify_info:  # Check spotify_info is not None
                logger_gui.info(
                    f"Spotify track '{spotify_info.get('name', 'Unknown')}' is not linked or Tidal track object missing. Cannot play. Link Status: {item_data.get('link_status')}, Tidal Track Type: {type(item_data.get('tidal_track'))}"
                )
                return
            else:  # spotify_info was None
                logger_gui.info("Spotify track data is missing. Cannot play.")
                return

        if track_to_play:
            logger_gui.info(
                f"Double-clicked on Tidal track: {track_to_play.title}. Requesting playback."
            )
            # Ensure track_to_play.artists is a list and not None before iterating
            artist_names = "Unknown Artist"
            if track_to_play.artists and isinstance(track_to_play.artists, list):
                artist_names = ", ".join(
                    [a.name for a in track_to_play.artists if hasattr(a, "name")]
                )
            elif hasattr(
                track_to_play.artists, "name"
            ):  # Handle single artist object case
                artist_names = track_to_play.artists.name

            player_track_info = {
                "id": track_to_play.id,
                "title": track_to_play.title,
                "artist": artist_names,
                "album_title": (
                    track_to_play.album.title
                    if track_to_play.album
                    else "Unknown Album"
                ),
                "duration_ms": (
                    track_to_play.duration * 1000 if track_to_play.duration else 0
                ),
                "album_art_id": (
                    track_to_play.album.cover if track_to_play.album else None
                ),
            }
            self.player_logic.play_track(player_track_info)
        elif (
            spotify_info
        ):  # spotify_info is guaranteed to be non-None here due to earlier checks
            logger_gui.info(
                f"Double-clicked on unlinked Spotify track: {spotify_info.get('name', 'Unknown')}"
            )
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
                cover_bytes = TIDAL_API.getCoverData(
                    album_art_id, "80", "80"
                )  # Request 80x80
                if cover_bytes:
                    temp_pixmap = QPixmap()
                    if temp_pixmap.loadFromData(cover_bytes):
                        pixmap = temp_pixmap
            except Exception as e:
                logger_gui.error(
                    f"Error loading album art for play bar (ID: {album_art_id}): {e}"
                )
        self.play_bar_widget.set_track_info(title, artist, pixmap)

    def paintEvent(self, a0: Optional[QPaintEvent]):  # Corrected parameter name to a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Define the path for the rounded rectangle of MainView
        # This path will be used for clipping.
        path = QtGui.QPainterPath()
        # The radius should match what you want for the overall window shape.
        # If CustomTitleBar has 10px top radius, MainView should also have 10px top radius.
        path.addRoundedRect(QRectF(self.rect()), 10, 10)  # 10px radius for all corners

        # Clip the painter to this rounded path.
        # All subsequent drawing operations will be confined to this shape.
        painter.setClipPath(path)

        # 1. Explicitly paint the base background color (#1E1E1E)
        painter.fillRect(self.rect(), QColor("#1E1E1E"))
        logger_gui.debug(
            "MainView paintEvent: Filled with #1E1E1E, clipped to rounded rect."
        )

        # 2. Re-enable pixmap drawing. It will be drawn on top of the #1E1E1E fill
        #    and will also be clipped by the same path.
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
            logger_gui.debug(
                "MainView paintEvent: Drew background_pixmap, clipped to rounded rect."
            )
        else:
            logger_gui.debug(
                "MainView paintEvent: background_pixmap not available or is null."
            )

        # No super().paintEvent(a0) call is needed here.

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

    def mousePressEvent(self, a0: Optional[QMouseEvent]):  # Corrected type hint
        if not self.event_handler.handle_mousePressEvent(a0):
            super(MainView, self).mousePressEvent(a0)

    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):  # Corrected type hint
        if not self.event_handler.handle_mouseMoveEvent(a0):
            super(MainView, self).mouseMoveEvent(a0)

    def mouseReleaseEvent(self, a0: Optional[QMouseEvent]):  # Corrected type hint
        if not self.event_handler.handle_mouseReleaseEvent(a0):
            super(MainView, self).mouseReleaseEvent(a0)

    def toggle_log_console(self):
        is_currently_visible = self.c_printTextEdit.isVisible()
        if is_currently_visible:
            self.c_printTextEdit.setVisible(False)
            self.toggleLogButton.setText("Show Log")
            if hasattr(self, "verticalSplitter"):
                self.last_splitter_sizes = self.verticalSplitter.sizes()
                current_sizes = self.verticalSplitter.sizes()
                if len(current_sizes) == 2:
                    self.verticalSplitter.setSizes(
                        [current_sizes[0] + current_sizes[1] - 1, 1]
                    )
        else:
            self.c_printTextEdit.setVisible(True)
            self.toggleLogButton.setText("Hide Log")
            if hasattr(self, "verticalSplitter"):
                if (
                    hasattr(self, "last_splitter_sizes")
                    and isinstance(self.last_splitter_sizes, list)
                    and len(self.last_splitter_sizes) == 2
                    and self.last_splitter_sizes[1] > 0
                ):
                    self.verticalSplitter.setSizes(self.last_splitter_sizes)
                else:
                    total_height = self.verticalSplitter.height()
                    if total_height > 0:
                        self.verticalSplitter.setSizes(
                            [int(total_height * 0.75), int(total_height * 0.25)]
                        )
                    else:
                        self.verticalSplitter.setSizes([500, 150])
            if hasattr(self, "verticalSplitter"):
                widget_0 = self.verticalSplitter.widget(0)
                widget_1 = self.verticalSplitter.widget(1)
                if widget_0:
                    widget_0.updateGeometry()
                if widget_1:
                    widget_1.updateGeometry()
                self.verticalSplitter.updateGeometry()
                parent_widget = self.verticalSplitter.parentWidget()
                if parent_widget:
                    parent_layout = parent_widget.layout()
                    if parent_layout:
                        parent_layout.activate()

    @pyqtSlot(str)
    def _trigger_search(self, query: str):
        self.search_handler.perform_search(query)

    @pyqtSlot(list)
    def startLinkingWorker(self, tracks_to_link_data: list):
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


def main():
    """The main entry point for the GUI application."""
    from .gui_app_setup import (
        load_initial_settings_and_token,
        register_global_exception_handler,
        start_gui_application,
    )

    load_initial_settings_and_token()
    register_global_exception_handler()
    if enableGui():
        return start_gui_application(MainView)
    else:
        message = "GUI dependencies (PyQt6) are not installed or found. Cannot start graphical interface."
        logger_gui.error(message)
        # Use a print statement that works in bundled apps without a console
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
            # Fallback to console if PyQt6 itself is missing
            print(message, file=sys.stderr)
            print(
                "\nTo use the GUI, please install the required packages:",
                file=sys.stderr,
            )
            print("  pip install PyQt6", file=sys.stderr)
            print("\nExiting.", file=sys.stderr)
        return 1


# This block allows the script to be run directly for testing/development
if __name__ == "__main__":
    sys.exit(main())
