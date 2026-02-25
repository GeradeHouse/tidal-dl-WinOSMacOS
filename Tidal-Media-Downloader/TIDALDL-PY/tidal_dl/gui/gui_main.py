# tidal_dl/gui/gui_main.py

import logging
import sys
import threading
import time
from typing import Optional, List, Any, Dict, Union, Callable, TYPE_CHECKING, Tuple

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QButtonGroup,
    QComboBox,
    QScrollArea,
    QTextEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QApplication,
)
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
    QThread,
    QThreadPool,
    pyqtSlot,
    QSize,
    QPoint,
    QEvent,
    QRectF,
)
from PyQt6.QtGui import (
    QPixmap,
    QPainter,
    QColor,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QResizeEvent,
    QPainterPath,
)
from PyQt6 import QtWidgets, QtGui

from tidal_dl.gui.gui_player_bar import PlayBarWidget
from tidal_dl.gui.gui_player_logic import PlayerLogic
from tidal_dl.tidal import Track, Playlist, AudioQuality, Type, TIDAL_API
from tidal_dl.printf import Printf
from tidal_dl import paths
from tidal_dl.settings import SETTINGS
from tidal_dl.linking import LinkingWorker
from tidal_dl.persistence import LinkPersistenceManager
from tidal_dl.gui.gui_cover_cache import CoverCache, CoverArtWorker
from tidal_dl.spotify import SpotifyAPI

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
from .gui_utils import enableGui, EmittingStream, append_text_to_output
from .gui_custom_dialog import CustomQMessageBox
from .gui_resize_handler import ResizeHandler
from .gui_event_handlers import MainViewEventHandlers
from .gui_task_queue_manager import TaskQueueManager # Import the new manager
from .gui_logging import setup_gui_logger, get_gui_manager

if TYPE_CHECKING:
    from tidal_dl.gui.gui_table_handler import TableHandler

# FIX: Move imports from local (__init__) to module level for PyInstaller compatibility
from tidal_dl.gui.gui_playlist_tree_handler import PlaylistTreeHandler
from tidal_dl.gui.gui_table_handler import TableHandler

logger_gui = logging.getLogger(__name__)
logger_gui.setLevel(logging.WARNING)

# Set up GUI logging with INFO level for this module (GUI core operations)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class MainView(QWidget):
    # MODIFIED: Signals now include spotify_data (dict) for robust row identification
    s_linkingStarted = pyqtSignal(int, dict)
    s_linkingFinished = pyqtSignal(int, object, object, object, object)
    s_linkingError = pyqtSignal(int, str, dict)
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
        self._keyword_cover_generation: int = 0
        self._keyword_cover_subscribers: Dict[
            str, List[Tuple[QListWidget, QListWidgetItem, int]]
        ] = {}
        self._keyword_cover_workers: Dict[str, CoverArtWorker] = {}
        self.spotify_api = SpotifyAPI()
        self.player_logic = PlayerLogic(TIDAL_API, self)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self.initView()

        # FIX: Remove local imports since they're now at module level
        self.auth_handler = AuthHandler(self.spotify_api, parent=self)
        self.settingsPage = SettingsPage(auth_handler=self.auth_handler, parent=self)
        
        # Initialize the Task Queue Manager first
        self.task_queue_manager = TaskQueueManager(self)
        
        # Create PlaylistTreeHandler first (before TableHandler to avoid circular dependency)
        self.tree_handler = PlaylistTreeHandler(
            self.playlist_tree_widget, self.cover_cache, parent=self
        )
        self.tree_handler.set_task_queue_manager(self.task_queue_manager)
        
        # Create TableHandler with None for linking_handler initially
        self.table_handler = TableHandler(
            self.tableWidget, self.link_persistence_manager, None, parent=self
        )
        
        # Inject table_handler reference into playlist tree handler to avoid circular access
        self.tree_handler.set_table_handler(self.table_handler)
        
        # Create LinkingGuiHandler and set it on both handlers
        self.linking_gui_handler = LinkingGuiHandler(
            TIDAL_API,
            self.table_handler,
            self.link_persistence_manager,
            self.c_btnLinkTracks,
            parent=self,
        )
        self.table_handler.set_linking_handler(self.linking_gui_handler)
        
        # Now set linking handler on playlist tree handler
        self.tree_handler.set_linking_handler(self.linking_gui_handler)
        
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
                    c = QColor()
                    c.setNamedColor("#aaaaaa")
                    p.setBrush(c)
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

        self.search_results_tabs_widget = QWidget()
        self.search_results_tabs_layout = QHBoxLayout(self.search_results_tabs_widget)
        self.search_results_tabs_layout.setContentsMargins(4, 6, 4, 8)
        self.search_results_tabs_layout.setSpacing(8)

        self.search_results_tab_group = QButtonGroup(self)
        self.search_results_tab_group.setExclusive(True)

        tab_button_style = (
            "QPushButton {"
            "background-color: rgba(255, 255, 255, 0.04);"
            "color: #E8E8E8;"
            "border: 1px solid rgba(255, 255, 255, 0.12);"
            "border-radius: 12px;"
            "padding: 6px 12px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            "background-color: rgba(255, 255, 255, 0.1);"
            "}"
            "QPushButton:checked {"
            "background-color: rgba(255, 255, 255, 0.18);"
            "border: 1px solid rgba(255, 255, 255, 0.35);"
            "color: #FFFFFF;"
            "}"
        )

        self.btnTopResults = QPushButton("Top results")
        self.btnTracksResults = QPushButton("Tracks")
        self.btnAlbumsResults = QPushButton("Albums")
        for button in (
            self.btnTopResults,
            self.btnTracksResults,
            self.btnAlbumsResults,
        ):
            button.setCheckable(True)
            button.setStyleSheet(tab_button_style)
            self.search_results_tab_group.addButton(button)
            self.search_results_tabs_layout.addWidget(button)

        self.search_results_tabs_layout.addStretch(1)
        self.btnTopResults.clicked.connect(lambda: self._set_search_results_page("top"))
        self.btnTracksResults.clicked.connect(
            lambda: self._set_search_results_page("tracks")
        )
        self.btnAlbumsResults.clicked.connect(
            lambda: self._set_search_results_page("albums")
        )
        self.search_results_tabs_widget.setVisible(False)

        self.top_results_list = QListWidget()
        self.top_results_list.setObjectName("keywordTopResultsList")
        self.top_results_list.setStyleSheet(
            "QListWidget {"
            "background-color: transparent;"
            "border: 1px solid rgba(255, 255, 255, 0.1);"
            "border-radius: 8px;"
            "padding: 6px;"
            "}"
            "QListWidget::item {"
            "padding: 8px;"
            "margin: 2px;"
            "border-radius: 6px;"
            "}"
            "QListWidget::item:selected {"
            "background-color: rgba(255, 255, 255, 0.14);"
            "}"
        )
        self.top_results_list.itemClicked.connect(self._on_keyword_result_item_clicked)

        self.albums_grid_list = QListWidget()
        self.albums_grid_list.setObjectName("keywordAlbumsGridList")
        self.albums_grid_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.albums_grid_list.setFlow(QListWidget.Flow.LeftToRight)
        self.albums_grid_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.albums_grid_list.setMovement(QListWidget.Movement.Static)
        self.albums_grid_list.setWrapping(True)
        self.albums_grid_list.setSpacing(12)
        self.albums_grid_list.setWordWrap(True)
        self.albums_grid_list.setIconSize(QSize(160, 160))
        self.albums_grid_list.setStyleSheet(
            "QListWidget {"
            "background-color: transparent;"
            "border: 1px solid rgba(255, 255, 255, 0.1);"
            "border-radius: 8px;"
            "padding: 8px;"
            "}"
            "QListWidget::item {"
            "width: 170px;"
            "height: 220px;"
            "padding: 6px;"
            "margin: 4px;"
            "border-radius: 6px;"
            "}"
            "QListWidget::item:selected {"
            "background-color: rgba(255, 255, 255, 0.14);"
            "}"
        )
        self.albums_grid_list.itemClicked.connect(self._on_keyword_result_item_clicked)

        self.search_results_stack = QStackedWidget()
        self.search_results_stack.addWidget(self.verticalSplitter)  # tracks view
        self.search_results_stack.addWidget(self.top_results_list)  # top results view
        self.search_results_stack.addWidget(self.albums_grid_list)  # albums view
        self._set_search_results_page("tracks")

        self.funcGrid = QVBoxLayout()
        self.funcGrid.setContentsMargins(6, 0, 6, 10)
        self.funcGrid.setSpacing(0)
        searchBarLayout = QHBoxLayout()
        searchBarLayout.addStretch(1)
        searchBarLayout.addWidget(self.search_bar)
        self.funcGrid.addLayout(searchBarLayout)
        self.funcGrid.addWidget(self.search_results_tabs_widget)
        self.funcGrid.addWidget(self.search_results_stack)
        self.funcGrid.setStretchFactor(self.search_results_stack, 1)

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
        self.search_bar.searchTriggered.connect(self.search_handler.perform_search_async)
        self.search_bar.resultSelected.connect(
            self.search_handler._on_result_item_clicked
        )
        self.search_handler.searchResultsReady.connect(
            self.table_handler.populate_search_results
        )
        self.search_handler.keywordSearchReady.connect(self._on_keyword_search_ready)
        self.search_handler.liveSearchResultsReady.connect(
            self.search_bar._display_live_results
        )
        self.search_handler.data_fetched.connect(
            self.table_handler._populate_table_from_search
        )
        self.search_handler.data_fetched.connect(self._on_data_fetched_for_search_view)
        self.search_handler.searchFailed.connect(self.table_handler.show_error_message)
        self.search_handler.searchFailed.connect(
            lambda msg: logger_gui.info(f"Search Error: {msg}")
        )
        self.search_bar.resultSelected.connect(self._on_live_result_selected_for_view)
        self.tree_handler.tidalPlaylistSelected.connect(
            lambda pl: logger_gui.info(f"Selected Tidal Playlist: {pl.title}")
        )
        self.tree_handler.tidalPlaylistSelected.connect(
            lambda _pl: self._reset_keyword_search_views()
        )
        self.tree_handler.spotifyPlaylistSelected.connect(
            lambda pl_data: logger_gui.info(
                f"Selected Spotify Playlist: {pl_data['data']['name']}"
            )
        )
        self.tree_handler.spotifyPlaylistSelected.connect(
            lambda _pl_data: self._reset_keyword_search_views()
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
        self.download_handler.downloadStarted.connect(self.tree_handler.on_job_started)
        self.download_handler.downloadFinished.connect(self.tree_handler.on_job_finished)
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
        from tidal_dl.gui import gui_app_setup
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
        bg_color = QColor()
        bg_color.setNamedColor("#1E1E1E")
        painter.fillRect(self.rect(), bg_color)
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

    def _set_search_results_page(self, page: str) -> None:
        page_map = {"tracks": 0, "top": 1, "albums": 2}
        page_index = page_map.get(page, 0)
        self.search_results_stack.setCurrentIndex(page_index)

        self.btnTracksResults.setChecked(page == "tracks")
        self.btnTopResults.setChecked(page == "top")
        self.btnAlbumsResults.setChecked(page == "albums")

    def _format_artist_names(self, artists_value: Any) -> str:
        if isinstance(artists_value, list):
            names = [
                artist.name
                for artist in artists_value
                if hasattr(artist, "name") and getattr(artist, "name")
            ]
            return ", ".join(names) if names else "Unknown Artist"
        if hasattr(artists_value, "name"):
            return str(artists_value.name)
        if isinstance(artists_value, str):
            return artists_value
        return "Unknown Artist"

    def _get_cover_pixmap(self, cover_id: Optional[str], width: int, height: int) -> Optional[QPixmap]:
        if not cover_id:
            return None

        start = time.perf_counter()
        try:
            cover_url = TIDAL_API.getCoverUrl(str(cover_id), str(width), str(height))
            if not cover_url:
                return None

            cached = self.cover_cache.get(cover_url)
            if cached and not cached.isNull():
                return cached

            cover_data = TIDAL_API.getCoverData(str(cover_id), str(width), str(height))
            if not cover_data:
                return None

            pixmap = QPixmap()
            if not pixmap.loadFromData(cover_data):
                return None

            self.cover_cache.set(cover_url, pixmap)
            return pixmap
        except Exception as cover_err:
            logger_gui.debug(
                f"Could not load cover art for '{cover_id}' ({width}x{height}): {cover_err}"
            )
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if elapsed_ms >= 120.0:
                logger_gui.warning(
                    "SEARCH_UI_COVER_PERF_DIAG cover_id=%s size=%sx%s elapsed_ms=%.1f",
                    cover_id,
                    width,
                    height,
                    elapsed_ms,
                )
        return None

    def _add_disabled_info_item(self, target_list: QListWidget, text: str) -> None:
        info_item = QListWidgetItem(text)
        info_item.setFlags(info_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        target_list.addItem(info_item)

    def _queue_keyword_cover_load(
        self,
        target_list: QListWidget,
        item: QListWidgetItem,
        cover_id: Optional[str],
        item_type: str,
        item_id: str,
        generation: int,
    ) -> bool:
        if not cover_id:
            return False

        request_key = TIDAL_API.getCoverUrl(str(cover_id), "320", "320") or str(cover_id)
        cached = self.cover_cache.get(request_key)
        if cached and not cached.isNull():
            item.setIcon(QIcon(cached))
            return True

        # Drop stale requests from older keyword searches.
        if generation != self._keyword_cover_generation:
            return False

        subscribers = self._keyword_cover_subscribers.setdefault(request_key, [])
        subscribers.append((target_list, item, generation))
        if len(subscribers) > 1:
            return True

        worker = CoverArtWorker(
            url=str(cover_id),
            cache=self.cover_cache,
            type=item_type,
            item_id=str(item_id),
        )

        def _on_ready(_signal_key: str, pixmap: QPixmap, req_key=request_key) -> None:
            targets = self._keyword_cover_subscribers.pop(req_key, [])
            self._keyword_cover_workers.pop(req_key, None)
            for list_widget, target_item, target_generation in targets:
                if target_generation != self._keyword_cover_generation:
                    continue
                if list_widget.row(target_item) < 0:
                    continue
                if not pixmap.isNull():
                    target_item.setIcon(QIcon(pixmap))

        def _on_error(_signal_key: str, _error: str, req_key=request_key) -> None:
            self._keyword_cover_subscribers.pop(req_key, None)
            self._keyword_cover_workers.pop(req_key, None)

        worker.signals.cover_ready.connect(_on_ready)
        worker.signals.error.connect(_on_error)

        self._keyword_cover_workers[request_key] = worker

        pool = QThreadPool.globalInstance()
        if pool:
            pool.start(worker)
        else:
            self._keyword_cover_subscribers.pop(request_key, None)
            self._keyword_cover_workers.pop(request_key, None)

        return True

    @pyqtSlot(str, list, list, list)
    def _on_keyword_search_ready(
        self,
        query: str,
        tracks: list,
        albums: list,
        artists: list,
    ) -> None:
        start = time.perf_counter()
        self._keyword_cover_generation += 1
        current_generation = self._keyword_cover_generation

        # Invalidate any pending subscribers from older result sets.
        self._keyword_cover_subscribers.clear()

        self.top_results_list.clear()
        self.albums_grid_list.clear()

        self.search_results_tabs_widget.setVisible(True)

        max_top_tracks = 6
        max_top_albums = 6
        max_top_artists = 6
        cover_attempts = 0

        for track in tracks[:max_top_tracks]:
            track_title = getattr(track, "title", "Unknown Track")
            track_artists = self._format_artist_names(getattr(track, "artists", None))
            subtitle = f"Track • {track_artists}"
            top_item = QListWidgetItem(f"{track_title}\n{subtitle}")
            top_item.setData(
                Qt.ItemDataRole.UserRole,
                {"type": Type.Track, "id": getattr(track, "id", ""), "title": track_title},
            )

            album_obj = getattr(track, "album", None)
            track_cover_id = getattr(album_obj, "cover", None) if album_obj else None
            if track_cover_id:
                cover_attempts += 1
                self._queue_keyword_cover_load(
                    self.top_results_list,
                    top_item,
                    track_cover_id,
                    "Track",
                    str(getattr(track, "id", "")),
                    current_generation,
                )
            self.top_results_list.addItem(top_item)

        for album in albums[:max_top_albums]:
            album_title = getattr(album, "title", "Unknown Album")
            album_artists = self._format_artist_names(
                getattr(album, "artists", getattr(album, "artist", None))
            )
            subtitle = f"Album • {album_artists}"
            top_item = QListWidgetItem(f"{album_title}\n{subtitle}")
            top_item.setData(
                Qt.ItemDataRole.UserRole,
                {"type": Type.Album, "id": getattr(album, "id", ""), "title": album_title},
            )

            album_cover_id = getattr(album, "cover", None)
            if album_cover_id:
                cover_attempts += 1
                self._queue_keyword_cover_load(
                    self.top_results_list,
                    top_item,
                    album_cover_id,
                    "Album",
                    str(getattr(album, "id", "")),
                    current_generation,
                )
            self.top_results_list.addItem(top_item)

        for artist in artists[:max_top_artists]:
            artist_name = getattr(artist, "name", "Unknown Artist")
            subtitle = "Artist"
            top_item = QListWidgetItem(f"{artist_name}\n{subtitle}")
            top_item.setData(
                Qt.ItemDataRole.UserRole,
                {"type": Type.Artist, "id": getattr(artist, "id", ""), "name": artist_name},
            )

            artist_cover_id = getattr(artist, "picture", None)
            if artist_cover_id:
                cover_attempts += 1
                self._queue_keyword_cover_load(
                    self.top_results_list,
                    top_item,
                    artist_cover_id,
                    "Artist",
                    str(getattr(artist, "id", "")),
                    current_generation,
                )
            self.top_results_list.addItem(top_item)

        if self.top_results_list.count() == 0:
            self._add_disabled_info_item(self.top_results_list, "No top results available.")

        for album in albums:
            album_title = getattr(album, "title", "Unknown Album")
            album_item = QListWidgetItem(album_title)
            album_item.setTextAlignment(
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            )
            album_item.setData(
                Qt.ItemDataRole.UserRole,
                {"type": Type.Album, "id": getattr(album, "id", ""), "title": album_title},
            )
            album_item.setSizeHint(QSize(176, 220))

            album_cover_id = getattr(album, "cover", None)
            if album_cover_id:
                cover_attempts += 1
                self._queue_keyword_cover_load(
                    self.albums_grid_list,
                    album_item,
                    album_cover_id,
                    "Album",
                    str(getattr(album, "id", "")),
                    current_generation,
                )

            self.albums_grid_list.addItem(album_item)

        if self.albums_grid_list.count() == 0:
            self._add_disabled_info_item(self.albums_grid_list, "No albums found.")

        logger_gui.warning(
            "SEARCH_UI_PERF_DIAG query='%s' tracks=%s albums=%s artists=%s cover_attempts=%s total_ms=%.1f",
            query,
            len(tracks),
            len(albums),
            len(artists),
            cover_attempts,
            (time.perf_counter() - start) * 1000.0,
        )

        self._set_search_results_page("top")

    @pyqtSlot(QListWidgetItem)
    def _on_keyword_result_item_clicked(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return

        item_type = payload.get("type")
        item_id = payload.get("id")
        if not item_type or not item_id:
            return

        self._set_search_results_page("tracks")
        self.search_handler._on_result_item_clicked(payload)

    @pyqtSlot(dict)
    def _on_live_result_selected_for_view(self, result_data: Dict[str, Any]) -> None:
        _ = result_data
        self._reset_keyword_search_views()

    @pyqtSlot(list, str)
    def _on_data_fetched_for_search_view(self, results: list, error_msg: str) -> None:
        _ = (results, error_msg)
        if self.search_results_tabs_widget.isVisible():
            self._set_search_results_page("tracks")

    def _reset_keyword_search_views(self) -> None:
        self.search_results_tabs_widget.setVisible(False)
        self.top_results_list.clear()
        self.albums_grid_list.clear()
        self._set_search_results_page("tracks")

    @pyqtSlot(str)
    def _trigger_search(self, query: str):
        if query.startswith("http://") or query.startswith("https://"):
            self._reset_keyword_search_views()

    @pyqtSlot(list, str, object)  # tracks_to_link_data, playlist_id, on_finish_callback
    def startLinkingWorker(self, tracks_to_link_data: list, playlist_id: Optional[str] = None, on_finish_callback: Optional[Callable] = None):
        if not tracks_to_link_data or self.linking_active:
            return
        
        # CRITICAL FIX: Set processing playlist ID and TOTAL count if provided by TaskQueueManager
        # This ensures that jobs started via the Tree Context Menu (which bypass LinkingGuiHandler.startLinkingSelectedTracks)
        # have the correct total count for the progress bar cleanup logic.
        if playlist_id:
            self.linking_gui_handler._current_processing_playlist_id = playlist_id
            self.linking_gui_handler._processed_counters[playlist_id] = 0
            self.linking_gui_handler._total_counts[playlist_id] = len(tracks_to_link_data)
            logger_gui.debug(f"🔴🔴🔴 SETTING: Stored processing playlist ID and TOTAL ({len(tracks_to_link_data)}) in startLinkingWorker: {playlist_id}")
        
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

        if self.table_handler:
            try:
                self.table_handler.shutdown_background_workers(wait=False)
            except Exception as ex:
                logger_gui.debug(
                    f"Failed to shut down table handler background workers cleanly: {ex}",
                    exc_info=True,
                )

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
    from tidal_dl.gui import gui_app_setup

    gui_app_setup.initialize_settings_and_token()
    gui_app_setup.setup_global_exception_handler()
    if enableGui():
        return gui_app_setup.start_gui()
    else:
        message = "GUI dependencies (PyQt6) are not installed or found. Cannot start graphical interface."
        logger_gui.error(message)
        try:
            CustomQMessageBox.critical(
                None,
                "Error",
                "Missing Dependencies",
                f"{message}\nPlease run 'pip install PyQt6' to fix."
            )
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
