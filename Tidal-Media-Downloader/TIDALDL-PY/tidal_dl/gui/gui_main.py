# tidal_dl/gui/gui_main.py

import html
import logging
import os
import sys
import threading
import time
from contextlib import suppress
from typing import Optional, List, Any, Dict, Union, Callable, TYPE_CHECKING

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
    QStackedWidget,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QApplication,
    QMenu,
)
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
    QThread,
    pyqtSlot,
    QSize,
    QPoint,
    QEvent,
    QRectF,
    QTimer,
    QUrl,
)
from PyQt6.QtGui import (
    QPixmap,
    QPainter,
    QColor,
    QAction,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QResizeEvent,
    QCursor,
)
from PyQt6 import QtWidgets, QtGui, sip

from tidal_dl.gui.gui_player_bar import PlayBarWidget
from tidal_dl.gui.gui_player_logic import PlayerLogic
from tidal_dl.tidal import Track, Playlist, AudioQuality, Type, TIDAL_API
from tidal_dl.printf import Printf
from tidal_dl import paths
from tidal_dl.settings import SETTINGS
from tidal_dl.paths import getSettingsFilePath, get_user_download_path
from tidal_dl.linking import LinkingWorker
from tidal_dl.persistence import LinkPersistenceManager
from tidal_dl.gui.gui_cover_cache import CoverCache
from tidal_dl.gui.gui_playlist_table_header import PlaylistTableHeaderWidget
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
from .gui_search import SearchBarWidget, KeywordSearchResultsController
from .gui_playlist_tree import PlaylistTreeWidget
from .gui_utils import enableGui, EmittingStream, append_text_to_output
from .gui_quality_menu import DOWNLOAD_QUALITY_MENU_ITEMS
from .gui_custom_dialog import CustomQMessageBox, ModernDarkProgressDialog
from .gui_resize_handler import ResizeHandler
from .gui_event_handlers import MainViewEventHandlers
from .gui_task_queue_manager import TaskQueueManager # Import the new manager
from .gui_logging import setup_gui_logger, get_gui_manager
from .download_structure_migration import (
    DownloadStructureMigrationWorker,
    DownloadMigrationResult,
)

if TYPE_CHECKING:
    from tidal_dl.gui.gui_table_handler import TableHandler

# FIX: Move imports from local (__init__) to module level for PyInstaller compatibility
from tidal_dl.gui.gui_playlist_tree_handler import PlaylistTreeHandler
from tidal_dl.gui.gui_table_handler import TableHandler

logger_gui = logging.getLogger(__name__)
logger_gui.setLevel(logging.WARNING)

# Set up GUI logging with INFO level for this module (GUI core operations)
setup_gui_logger(__name__, logging.INFO)


class MainView(QWidget):
    # MODIFIED: Signals now include spotify_data (dict) for robust row identification
    s_linkingStarted = pyqtSignal(int, dict)
    s_linkingFinished = pyqtSignal(int, object, object, object, object)
    s_linkingError = pyqtSignal(int, str, dict)
    s_spotifyLoginFinished = pyqtSignal(object)
    s_spotifyPlaylistsFetched = pyqtSignal(list)
    s_spotifyTracksFetched = pyqtSignal(str, list)
    s_spotifyMutationStarted = pyqtSignal(str)
    s_spotifyMutationFinished = pyqtSignal(dict)
    s_spotifyMutationError = pyqtSignal(str)
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
    spotify_mutation_active: bool = False

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
        self._ui_freeze_last_tick = time.perf_counter()
        self._ui_freeze_monitor_timer: Optional[QTimer] = None
        self._deferred_linking_save_active = False
        self._busy_operation_depth = 0
        self._busy_cursor_active = False
        self._gui_closing = False
        self._original_stdout = sys.stdout
        self._gui_log_handler: Optional[logging.Handler] = None
        self.cover_cache = CoverCache()
        self.spotify_api = SpotifyAPI()
        self.player_logic = PlayerLogic(TIDAL_API, self)
        self._toast_timer: Optional[QTimer] = None

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
        if hasattr(self, "playlistHeaderWidget"):
            self.table_handler.set_playlist_header_widget(self.playlistHeaderWidget)
            self.playlistHeaderWidget.filterTextChanged.connect(
                self.table_handler.apply_playlist_filter_text
            )
            self.playlistHeaderWidget.set_scroll_target(self.tableWidget)
            if hasattr(self.tableWidget, "set_playlist_header_widget"):
                self.tableWidget.set_playlist_header_widget(self.playlistHeaderWidget)

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
        self.keyword_results_controller = KeywordSearchResultsController(
            parent=self,
            cover_cache=self.cover_cache,
            top_results_list=self.top_results_list,
            albums_grid_list=self.albums_grid_list,
            search_results_tabs_widget=self.search_results_tabs_widget,
            search_results_stack=self.search_results_stack,
            btn_tracks_results=self.btnTracksResults,
            btn_top_results=self.btnTopResults,
            btn_albums_results=self.btnAlbumsResults,
            on_result_selected=self.search_handler._on_result_item_clicked,
        )
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
        self._download_migration_thread: Optional[QThread] = None
        self._download_migration_worker: Optional[DownloadStructureMigrationWorker] = None
        self._download_migration_progress: Optional[ModernDarkProgressDialog] = None


        # --- Redirect stdout to the log widget ---
        self.stdout_stream = EmittingStream()
        self.stdout_stream.textWritten.connect(self._append_log_text_safely)
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
        self._gui_log_handler = gui_log_handler
        logger_gui.info("GUI log handler configured with module-specific filtering.")
        # --- END NEW SECTION ---

        self.settingsPage.audio_combo = self.c_combTQuality
        self.tree_handler.set_download_handler(self.download_handler)
        self.table_handler.set_download_handler(self.download_handler)
        self.tree_handler.set_linking_handler(self.linking_gui_handler)
        self.stackedLayout.addWidget(self.settingsPage)

        self.setMinimumSize(1000, 600)
        current_screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        display_width = current_screen.availableGeometry().width() if current_screen else 1700
        initial_width = display_width - 100 if display_width < 1800 else 1700
        self.resize(max(self.minimumWidth(), initial_width), 800)
        self._position_toast()
        self.setWindowTitle("TIDAL-DL")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

        try:
            if SETTINGS:
                audio_quality_enum = getattr(
                    SETTINGS, "audioQuality", AudioQuality.LOSSLESS
                )
                audio_idx = self.c_combTQuality.findData(audio_quality_enum)
                if audio_idx == -1:
                    audio_idx = self.c_combTQuality.findData(AudioQuality.LOSSLESS)
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
            app.aboutToQuit.connect(self._flushDeferredLinkingPersistence)
            logger_gui.debug(
                "Connected app aboutToQuit signal to cover cache and linking persistence flush handlers."
            )

        QTimer.singleShot(1500, lambda: self.start_download_structure_reorganization())
        self._start_ui_responsiveness_monitor()
        logger_gui.debug("MainView initialization complete.")

    @pyqtSlot(str)
    def beginBusyOperation(self, reason: str = "Loading") -> None:
        """
        Shows the standard OS/Qt wait cursor while short preparation work is active.
        This is intentionally reference-counted because several queue phases can overlap.
        """
        self._busy_operation_depth += 1

        if self._busy_cursor_active:
            return

        app = QApplication.instance()
        if not app:
            return

        try:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            self._busy_cursor_active = True
            QApplication.processEvents()
            logger_gui.debug(
                "Busy cursor enabled | reason=%s depth=%d",
                reason,
                self._busy_operation_depth,
            )
        except Exception:
            logger_gui.debug("Failed to enable busy cursor.", exc_info=True)

    @pyqtSlot(str)
    def endBusyOperation(self, reason: str = "Loading") -> None:
        """
        Restores the normal cursor when all tracked short preparation work is done.
        """
        self._busy_operation_depth = max(0, self._busy_operation_depth - 1)

        if self._busy_operation_depth > 0 or not self._busy_cursor_active:
            return

        try:
            QApplication.restoreOverrideCursor()
            self._busy_cursor_active = False
            logger_gui.debug("Busy cursor disabled | reason=%s", reason)
        except Exception:
            logger_gui.debug("Failed to restore cursor.", exc_info=True)
            self._busy_cursor_active = False

    def _start_ui_responsiveness_monitor(self) -> None:
        """
        Logs event-loop stalls so short GUI freezes can be tied to queue/download state.
        """
        if self._ui_freeze_monitor_timer is not None:
            return

        self._ui_freeze_last_tick = time.perf_counter()
        self._ui_freeze_monitor_timer = QTimer(self)
        self._ui_freeze_monitor_timer.setInterval(250)
        self._ui_freeze_monitor_timer.timeout.connect(self._check_ui_responsiveness)
        self._ui_freeze_monitor_timer.start()

    def _current_visible_playlist_label(self) -> str:
        try:
            context = getattr(self, "current_playlist_context", None)
            if context is None:
                context = getattr(self, "s_playlist_obj", None)
            if isinstance(context, dict):
                raw_data = context.get("data")
                data = raw_data if isinstance(raw_data, dict) else context
                return str(
                    data.get("name")
                    or data.get("title")
                    or data.get("id")
                    or "dict-context"
                )

            return str(
                getattr(context, "title", None)
                or getattr(context, "name", None)
                or getattr(context, "uuid", None)
                or getattr(context, "id", None)
                or "none"
            )
        except Exception:
            return "unknown"

    def _check_ui_responsiveness(self) -> None:
        now = time.perf_counter()
        elapsed_ms = (now - self._ui_freeze_last_tick) * 1000.0
        self._ui_freeze_last_tick = now

        if elapsed_ms < 900.0:
            return

        queue_manager = getattr(self, "task_queue_manager", None)
        current_job = getattr(queue_manager, "current_job", None) if queue_manager else None
        current_job_description = (
            current_job.get("description") if isinstance(current_job, dict) else None
        )
        queued_jobs = len(getattr(queue_manager, "task_queue", [])) if queue_manager else 0
        task_running = bool(getattr(queue_manager, "is_running_task", False)) if queue_manager else False

        logger_gui.warning(
            "UI responsiveness gap detected | elapsed_ms=%.1f download_active=%s linking_active=%s "
            "spotify_mutation_active=%s task_running=%s queued_jobs=%d current_job=%r visible_playlist=%s",
            elapsed_ms,
            getattr(self, "download_active", None),
            getattr(self, "linking_active", None),
            getattr(self, "spotify_mutation_active", None),
            task_running,
            queued_jobs,
            current_job_description,
            self._current_visible_playlist_label(),
        )

    def initView(self):
        self.title_bar = CustomTitleBar(self)
        self.search_bar = SearchBarWidget(self)
        self.c_tableArea = QScrollArea()
        self.c_tableArea.setWidgetResizable(True)
        self.c_tableArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.c_tableArea.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        initialColumnNames = [
            "#",
            "Title",
            "Artists",
            "Album",
            "Release Year",
            "BPM",
            "Key",
            "Genre",
            "Label",
            "Length",
            "Quality",
        ]
        self.playlistTableContainer = QWidget()
        self.playlistTableContainer.setObjectName("playlistTableContainer")
        self.playlistTableContainer.setStyleSheet(
            "QWidget#playlistTableContainer { background: transparent; border: none; }"
        )
        self.playlistTableLayout = QVBoxLayout(self.playlistTableContainer)
        self.playlistTableLayout.setContentsMargins(0, 0, 14, 0)
        self.playlistTableLayout.setSpacing(0)

        self.playlistHeaderWidget = PlaylistTableHeaderWidget(self.playlistTableContainer)
        self.playlistHeaderWidget.setVisible(False)
        self.playlistTableLayout.addWidget(self.playlistHeaderWidget, 0)

        self.tableWidget = SplitterTable(initialColumnNames, self)
        self.tableWidget.setProperty("spotifyReorderActive", False)
        self.tableWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.playlistTableLayout.addWidget(self.tableWidget, 1)

        self.c_tableArea.setWidget(self.playlistTableContainer)

        self.spotifyActionStatusLabel = QLabel("")
        self.spotifyActionStatusLabel.setVisible(False)
        self.spotifyActionStatusLabel.setFixedHeight(34)
        self.spotifyActionStatusLabel.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.spotifyActionStatusLabel.setStyleSheet(
            """
            QLabel {
                background-color: rgba(0, 200, 200, 0.12);
                color: #dffefe;
                border: 1px solid rgba(0, 200, 200, 0.28);
                border-radius: 10px;
                padding: 6px 12px;
                font-weight: 600;
            }
            """
        )

        self.c_combTQuality = QComboBox()
        for _, item_enum_val in DOWNLOAD_QUALITY_MENU_ITEMS:
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
        topAreaLayout.addWidget(self.spotifyActionStatusLabel, 0)
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
        self.top_results_list.setIconSize(
            QSize(
                KeywordSearchResultsController.TOP_RESULT_ICON_SIZE,
                KeywordSearchResultsController.TOP_RESULT_ICON_SIZE,
            )
        )
        self.top_results_list.setSpacing(6)
        self.top_results_list.setStyleSheet(
            "QListWidget {"
            "background-color: transparent;"
            "border: 1px solid rgba(255, 255, 255, 0.1);"
            "border-radius: 8px;"
            "padding: 10px;"
            "}"
            "QListWidget::item {"
            "padding: 10px;"
            "margin: 4px;"
            "border-radius: 10px;"
            "border: 1px solid rgba(255, 255, 255, 0.08);"
            "}"
            "QListWidget::item:hover {"
            "background-color: rgba(255, 255, 255, 0.08);"
            "}"
            "QListWidget::item:selected {"
            "background-color: rgba(255, 255, 255, 0.16);"
            "border: 1px solid rgba(255, 255, 255, 0.22);"
            "}"
        )
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
        self.search_results_stack = QStackedWidget()
        self.search_results_stack.addWidget(self.verticalSplitter)  # tracks view
        self.search_results_stack.addWidget(self.top_results_list)  # top results view
        self.search_results_stack.addWidget(self.albums_grid_list)  # albums view
        self._set_search_results_page("tracks")

        self.funcGrid = QVBoxLayout()
        self.funcGrid.setContentsMargins(6, 0, 12, 10)
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

        self.toastLabel = QLabel("", self)
        self.toastLabel.setObjectName("toastNotificationLabel")
        self.toastLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toastLabel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.toastLabel.setVisible(False)
        self.toastLabel.setStyleSheet(
            """
            QLabel#toastNotificationLabel {
                background-color: rgba(34, 34, 34, 235);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 17px;
                padding: 8px 18px;
                font-weight: 700;
            }
            """
        )

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
            SplitterTable[spotifyReorderActive="true"] {
                border: none;
                border-radius: 0px;
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
        self.search_handler.keywordSearchReady.connect(
            self.keyword_results_controller.on_keyword_search_ready
        )
        self.search_handler.liveSearchResultsReady.connect(
            self.search_bar._display_live_results
        )
        self.search_handler.data_fetched.connect(
            self.table_handler._populate_table_from_search
        )
        self.search_handler.data_fetched.connect(
            self.keyword_results_controller.on_data_fetched_for_search_view
        )
        self.search_handler.searchFailed.connect(self.table_handler.show_error_message)
        self.search_handler.searchFailed.connect(
            lambda msg: logger_gui.info(f"Search Error: {msg}")
        )
        self.search_bar.resultSelected.connect(
            self.keyword_results_controller.on_live_result_selected_for_view
        )
        self.tree_handler.tidalPlaylistSelected.connect(
            lambda pl: logger_gui.info(f"Selected Tidal Playlist: {pl.title}")
        )
        self.tree_handler.tidalPlaylistSelected.connect(
            lambda _pl: self.keyword_results_controller.reset_keyword_search_views()
        )
        self.tree_handler.spotifyPlaylistSelected.connect(
            lambda pl_data: logger_gui.info(
                f"Selected Spotify Playlist: {pl_data['data']['name']}"
            )
        )
        self.tree_handler.spotifyPlaylistSelected.connect(
            lambda _pl_data: self.keyword_results_controller.reset_keyword_search_views()
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
        self.s_spotifyMutationStarted.connect(
            self.spotify_gui_handler.onSpotifyMutationStarted
        )
        self.s_spotifyMutationFinished.connect(
            self.spotify_gui_handler.onSpotifyMutationFinished
        )
        self.s_spotifyMutationError.connect(
            self.spotify_gui_handler.onSpotifyMutationError
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
        self.settingsPage.settingsSavedAndClosed.connect(self._on_settings_saved_and_closed)
        self.settingsPage.playlistDisplaySettingsChanged.connect(self.tree_handler.onPlaylistDisplaySettingsChanged)
        self.settingsPage.playlistDisplaySettingsChanged.connect(self.table_handler.refresh_table_view)
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
        self.player_logic.outputDeviceChanged.connect(
            self.play_bar_widget.set_output_device_label
        )
        self.player_logic.shuffleRepeatChanged.connect(
            self.play_bar_widget.update_shuffle_repeat_state
        )
        self.play_bar_widget.set_output_device_label(
            getattr(self.player_logic, "_selected_output_device_name", "Default Playback Device")
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
        self.play_bar_widget.outputDeviceMenuRequested.connect(
            self._show_output_device_menu
        )

        # --- Add these lines to connect the progress signals ---

        # Connect Task Queue Manager signals to Playlist Tree Handler slots
        self.task_queue_manager.jobStarted.connect(self.tree_handler.on_job_started)
        self.task_queue_manager.jobFinished.connect(self.tree_handler.on_job_finished)

        # Connect progress signals from individual handlers to the Playlist Tree Handler
        self.download_handler.downloadProgress.connect(self.tree_handler.on_job_progress)
        self.download_handler.downloadStarted.connect(self.tree_handler.on_job_started)
        self.download_handler.downloadFinished.connect(self.tree_handler.on_job_finished)
        self.linking_gui_handler.linkProgress.connect(self.tree_handler.on_job_progress)

    @pyqtSlot(QPoint)
    def _show_output_device_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        devices = self.player_logic.get_output_devices()
        selected_id = self.player_logic.selected_output_device_id()

        for device_info in devices:
            device_id = str(device_info.get("id", "") or "")
            name = str(device_info.get("name", "Default Playback Device") or "Default Playback Device")
            action = menu.addAction(name)
            if action is None:
                continue
            action.setCheckable(True)
            action.setChecked(device_id == selected_id)
            action.triggered.connect(
                lambda _checked=False, selected_device_id=device_id: self.player_logic.set_output_device(selected_device_id)
            )

        menu.exec(global_pos)

    def _get_artist_names_for_track(self, track: Track) -> str:
        artists = getattr(track, "artists", None)
        if artists and isinstance(artists, list):
            names = [str(getattr(artist, "name", "")).strip() for artist in artists if getattr(artist, "name", None)]
            if names:
                return ", ".join(names)

        if hasattr(artists, "name") and getattr(artists, "name", None):
            return str(getattr(artists, "name"))

        artist = getattr(track, "artist", None)
        if hasattr(artist, "name") and getattr(artist, "name", None):
            return str(getattr(artist, "name"))

        return "Unknown Artist"

    def _play_tidal_track(self, track_to_play: Track) -> None:
        album = getattr(track_to_play, "album", None)
        duration = getattr(track_to_play, "duration", 0) or 0
        try:
            duration_ms = int(float(duration)) * 1000
        except (TypeError, ValueError):
            duration_ms = 0
        player_track_info = {
            "id": getattr(track_to_play, "id", None),
            "title": getattr(track_to_play, "title", "Unknown Title"),
            "artist": self._get_artist_names_for_track(track_to_play),
            "album_title": getattr(album, "title", None) if album else "Unknown Album",
            "duration_ms": duration_ms,
            "album_art_id": getattr(album, "cover", None) if album else None,
        }
        self.player_logic.play_track(player_track_info)

    def _get_spotify_artist_text(self, spotify_info: Dict[str, Any]) -> str:
        artists_raw = spotify_info.get("artists", [])
        names: List[str] = []
        if isinstance(artists_raw, list):
            for artist in artists_raw:
                if isinstance(artist, dict):
                    name = str(artist.get("name", "")).strip()
                else:
                    name = str(artist).strip()
                if name:
                    names.append(name)
        return ", ".join(names) if names else "Unknown Artist"

    def _show_spotify_track_placeholder(
        self,
        spotify_info: Dict[str, Any],
        title_prefix: str = "[Spotify]",
    ) -> None:
        duration_ms = spotify_info.get("duration_ms", 0) or 0
        try:
            duration_ms_int = int(duration_ms)
        except (TypeError, ValueError):
            duration_ms_int = 0
        self.play_bar_widget.set_track_info(
            f"{title_prefix} {spotify_info.get('name', 'Unknown')}",
            self._get_spotify_artist_text(spotify_info),
        )
        self.play_bar_widget.update_progress(0, duration_ms_int)

    def _get_playable_spotify_tidal_track(
        self,
        row: int,
        item_data: Dict[str, Any],
    ) -> Optional[Track]:
        playable_link_statuses = {
            "cached_linked",
            "found",
            "auto_linked",
            "manual_linked",
            "cached_linked_full",
            "cached_linked_id_fetched",
            "found_uncertain",
        }
        if item_data.get("link_status") not in playable_link_statuses:
            return None

        tidal_track = item_data.get("tidal_track")
        if isinstance(tidal_track, Track):
            return tidal_track

        tidal_track_id = item_data.get("tidal_track_id")
        if not tidal_track_id:
            return None

        try:
            fetched_track = TIDAL_API.getTrack(str(tidal_track_id), suppress_debug_prints=True)
        except Exception as exc:
            logger_gui.warning(
                "Could not fetch cached linked TIDAL track %s for playback: %s",
                tidal_track_id,
                exc,
            )
            return None

        if isinstance(fetched_track, Track) and getattr(fetched_track, "id", None):
            item_data["tidal_track"] = fetched_track
            title_item = self.tableWidget.item(row, 1)
            if title_item:
                title_item.setData(Qt.ItemDataRole.UserRole, item_data)
            return fetched_track

        return None

    def _get_current_spotify_playlist_id(self) -> Optional[str]:
        playlist_obj = self.s_playlist_obj
        if isinstance(playlist_obj, dict) and playlist_obj.get("type") == "spotify":
            playlist_data = playlist_obj.get("data", {})
            if isinstance(playlist_data, dict) and playlist_data.get("id"):
                return str(playlist_data.get("id"))
        return None

    def _should_auto_link_spotify_track_for_playback(self, item_data: Dict[str, Any]) -> bool:
        link_status = str(item_data.get("link_status") or "not_linked")
        non_auto_link_statuses = {
            "linking",
            "manual_review_needed",
            "candidates_only",
            "not_found",
        }
        return link_status not in non_auto_link_statuses

    def _start_spotify_link_then_play(self, row: int, spotify_info: Dict[str, Any]) -> None:
        if self.linking_active:
            logger_gui.info(
                "Spotify track playback auto-link skipped because another linking job is already active."
            )
            self._show_spotify_track_placeholder(spotify_info)
            return

        required_keys = ("name", "artists", "album", "id")
        if not all(key in spotify_info for key in required_keys):
            logger_gui.warning(
                "Spotify track playback auto-link skipped because required metadata is missing: %s",
                spotify_info,
            )
            self._show_spotify_track_placeholder(spotify_info)
            return

        playlist_id = self._get_current_spotify_playlist_id()
        if not playlist_id:
            logger_gui.info(
                "Spotify track playback auto-link skipped because no active Spotify playlist context was found."
            )
            self._show_spotify_track_placeholder(spotify_info)
            return

        target_spotify_id = str(spotify_info.get("id") or "")
        if not target_spotify_id:
            self._show_spotify_track_placeholder(spotify_info)
            return

        pending = {"done": False}

        def _cleanup_pending_connections() -> None:
            try:
                self.s_linkingFinished.disconnect(_on_linking_finished)
            except TypeError:
                pass
            try:
                self.s_linkingError.disconnect(_on_linking_error)
            except TypeError:
                pass

        def _is_target_spotify_track(finished_row: int, finished_spotify_data: Optional[Dict[str, Any]]) -> bool:
            if isinstance(finished_spotify_data, dict):
                finished_spotify_id = str(finished_spotify_data.get("id") or "")
                if finished_spotify_id:
                    return finished_spotify_id == target_spotify_id
            return finished_row == row

        def _on_linking_finished(
            finished_row: int,
            tidal_track: Optional[Track],
            candidates: Optional[List[Dict[str, Any]]],
            score: Optional[int],
            finished_spotify_data: Optional[Dict[str, Any]] = None,
        ) -> None:
            if pending["done"] or not _is_target_spotify_track(finished_row, finished_spotify_data):
                return

            pending["done"] = True
            _cleanup_pending_connections()

            if isinstance(tidal_track, Track):
                if candidates:
                    logger_gui.info(
                        "Spotify auto-link for playback returned an uncertain TIDAL match with candidates; manual review is required before playback. TIDAL track ID %s.",
                        getattr(tidal_track, "id", None),
                    )
                    self._show_spotify_track_placeholder(spotify_info)
                    return

                if score is not None and score > 1:
                    logger_gui.info(
                        "Spotify auto-link for playback returned an uncertain TIDAL match without alternatives; playing TIDAL track ID %s.",
                        getattr(tidal_track, "id", None),
                    )
                else:
                    logger_gui.info(
                        "Spotify auto-link for playback succeeded with TIDAL track ID %s.",
                        getattr(tidal_track, "id", None),
                    )
                self._play_tidal_track(tidal_track)
                return

            if candidates:
                logger_gui.info(
                    "Spotify auto-link for playback found manual candidates only; playback was not started."
                )
            else:
                logger_gui.info(
                    "Spotify auto-link for playback did not find a TIDAL match; playback was not started."
                )
            self._show_spotify_track_placeholder(spotify_info)

        def _on_linking_error(
            finished_row: int,
            error_message: str,
            finished_spotify_data: Optional[Dict[str, Any]],
        ) -> None:
            if pending["done"] or not _is_target_spotify_track(finished_row, finished_spotify_data):
                return

            pending["done"] = True
            _cleanup_pending_connections()
            logger_gui.warning(
                "Spotify auto-link for playback failed before playback could start: %s",
                error_message,
            )
            self._show_spotify_track_placeholder(spotify_info)

        def _on_link_worker_done() -> None:
            if pending["done"]:
                return

            pending["done"] = True
            _cleanup_pending_connections()
            logger_gui.info(
                "Spotify auto-link for playback ended before a link result was produced."
            )
            self._show_spotify_track_placeholder(spotify_info)

        logger_gui.info(
            "Auto-linking Spotify track before playback: %s",
            spotify_info.get("name", "Unknown"),
        )
        self._show_spotify_track_placeholder(spotify_info, "[Linking]")
        self.s_linkingFinished.connect(_on_linking_finished)
        self.s_linkingError.connect(_on_linking_error)
        self.startLinkingWorker([(row, spotify_info)], playlist_id, _on_link_worker_done)

    def _save_settings_safely(self) -> None:
        try:
            SETTINGS.save(getSettingsFilePath())
        except Exception:
            logger_gui.warning("Failed to save settings.", exc_info=True)

    def _download_structure_migration_needed(self) -> bool:
        if bool(getattr(SETTINGS, "downloadStructureMigrationDone", False)):
            return False
        if bool(getattr(SETTINGS, "downloadStructureMigrationDoNotRemind", False)):
            return False

        worker = DownloadStructureMigrationWorker(self)
        return bool(worker.build_plan())

    def start_download_structure_reorganization(
        self,
        *,
        force_prompt: bool = False,
        manual: bool = False,
    ) -> None:
        if self._download_migration_thread is not None:
            return

        if not force_prompt and not self._download_structure_migration_needed():
            return

        worker_probe = DownloadStructureMigrationWorker(self)
        plan_count = len(worker_probe.build_plan())
        if plan_count <= 0:
            if manual:
                CustomQMessageBox.information(
                    self,
                    "Download Folder Structure",
                    "No legacy downloads found.",
                    "No audio or lyric files need to be moved into the current folder structure.",
                )
            SETTINGS.downloadStructureMigrationDone = True
            self._save_settings_safely()
            return

        download_root = os.path.normpath(get_user_download_path(SETTINGS.downloadPath))
        download_root_link = QUrl.fromLocalFile(download_root).toString()
        escaped_download_root = html.escape(download_root)

        accepted, do_not_remind = CustomQMessageBox.question_with_checkbox(
            self,
            "Download Folder Structure Changed",
            "The download folder structure has changed.",
            (
                "Downloads are now grouped by audio type, for example flac, mp3, "
                f"m4a, and mp4. Matching .lrc lyric files will be moved with "
                f"their audio files when possible.\n\n{plan_count} legacy audio/lyrics "
                "file(s) can be moved into the new structure.\n\n"
                "Download root:<br>"
                f'<a href="{download_root_link}" style="color: #66ccff; '
                f'text-decoration: underline;">{escaped_download_root}</a>'
            ),
            checkbox_text="Do not remind again",
            checkbox_checked=False,
            rich_text=True,
        )
        if not accepted:
            if do_not_remind:
                SETTINGS.downloadStructureMigrationDoNotRemind = True
                self._save_settings_safely()
            return

        self._run_download_structure_migration()

    def _run_download_structure_migration(self) -> None:
        self._download_migration_thread = QThread(self)
        self._download_migration_worker = DownloadStructureMigrationWorker()
        self._download_migration_worker.moveToThread(self._download_migration_thread)

        self._download_migration_progress = ModernDarkProgressDialog(
            "Restructuring Downloads",
            "Preparing audio and lyric file reorganization...",
            "Cancel",
            self,
        )
        self._download_migration_progress.canceled.connect(
            lambda: logger_gui.warning(
                "Download-folder migration cancellation requested; current file operation will finish first."
            )
        )
        self._download_migration_progress.show()

        self._download_migration_thread.started.connect(
            self._download_migration_worker.run
        )
        self._download_migration_worker.progress.connect(
            self._on_download_migration_progress
        )
        self._download_migration_worker.finished.connect(
            self._on_download_migration_finished
        )
        self._download_migration_worker.finished.connect(
            self._download_migration_thread.quit
        )
        self._download_migration_thread.finished.connect(
            self._download_migration_worker.deleteLater
        )
        self._download_migration_thread.finished.connect(
            self._download_migration_thread.deleteLater
        )
        self._download_migration_thread.finished.connect(
            self._clear_download_migration_worker
        )
        self._download_migration_thread.start()
        if self._download_migration_progress:
            self._download_migration_progress.raise_()
            self._download_migration_progress.activateWindow()

    def _on_download_migration_progress(
        self,
        current: int,
        total: int,
        source_path: str,
    ) -> None:
        if not self._download_migration_progress:
            return
        self._download_migration_progress.setMaximum(max(total, 1))
        self._download_migration_progress.setValue(min(current, max(total, 1)))
        display_path = str(source_path or "")
        if len(display_path) > 105:
            display_path = f"...{display_path[-102:]}"
        self._download_migration_progress.setLabelText(
            f"Moving audio/lyrics files... {current}/{total}\n{display_path}"
        )

    def _on_download_migration_finished(self, result: DownloadMigrationResult) -> None:
        if self._download_migration_progress:
            self._download_migration_progress.setValue(
                self._download_migration_progress.maximum()
            )
            self._download_migration_progress.close()

        SETTINGS.downloadStructureMigrationDone = True
        SETTINGS.downloadStructureMigrationDoNotRemind = True
        self._save_settings_safely()

        destination_lines = [
            f"{folder}: {count} file(s)"
            for folder, count in sorted(result.destination_counts.items())
        ]
        if not destination_lines:
            destination_lines = ["No destination folders received moved audio/lyrics files."]

        detail_lines = [
            f"Download root: {result.root_path}",
            "",
            "Destination summary:",
            *destination_lines,
            "",
            f"Moved: {result.moved}",
            f"Skipped: {result.skipped}",
            f"Failed: {result.failed}",
        ]
        if result.failures:
            detail_lines.extend(["", "First skipped/failed paths:", *result.failures])

        CustomQMessageBox.information(
            self,
            "Download Folder Structure",
            "Download reorganization finished.",
            "\n".join(detail_lines),
        )

    def _clear_download_migration_worker(self) -> None:
        self._download_migration_thread = None
        self._download_migration_worker = None
        self._download_migration_progress = None

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
            if not isinstance(spotify_info, dict):
                return

            track_to_play = self._get_playable_spotify_tidal_track(row, item_data)
            if track_to_play is None:
                if not self._should_auto_link_spotify_track_for_playback(item_data):
                    self._show_spotify_track_placeholder(spotify_info)
                    return
                self._start_spotify_link_then_play(row, spotify_info)
                return

        if track_to_play:
            self._play_tidal_track(track_to_play)
        elif spotify_info:
            self._show_spotify_track_placeholder(spotify_info)

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

    def _position_toast(self) -> None:
        toast = getattr(self, "toastLabel", None)
        if not isinstance(toast, QLabel):
            return

        toast.adjustSize()
        width = max(220, toast.width())
        height = max(34, toast.height())
        toast.resize(width, height)
        x = max(0, (self.width() - width) // 2)
        y = 58
        toast.move(x, y)
        toast.raise_()

    @pyqtSlot(str)
    def _on_settings_saved_and_closed(self, message: str) -> None:
        self.navigation_handler.show_main_menu()
        QTimer.singleShot(120, lambda: self.show_toast(message or "Settings saved"))

    def show_toast(self, message: str, duration_ms: int = 2200) -> None:
        toast = getattr(self, "toastLabel", None)
        if not isinstance(toast, QLabel):
            return

        if self._toast_timer is not None:
            self._toast_timer.stop()

        toast.setText(message)
        toast.setVisible(True)
        self._position_toast()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(toast.hide)
        self._toast_timer.start(duration_ms)

    def resizeEvent(self, a0: Optional[QResizeEvent]):
        self.update()
        self._position_toast()
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

    @pyqtSlot(str)
    def _append_log_text_safely(self, text: str) -> None:
        if getattr(self, "_gui_closing", False):
            return

        log_widget = getattr(self, "c_printTextEdit", None)
        try:
            if log_widget is None or sip.isdeleted(log_widget):
                return
            append_text_to_output(log_widget, text)
        except RuntimeError:
            return

    def _disconnect_gui_logging(self) -> None:
        stream = getattr(self, "stdout_stream", None)
        if stream is not None:
            with suppress(TypeError, RuntimeError):
                stream.textWritten.disconnect(self._append_log_text_safely)

        handler = getattr(self, "_gui_log_handler", None)
        if handler is not None:
            with suppress(ValueError, RuntimeError):
                logging.getLogger().removeHandler(handler)
            with suppress(Exception):
                handler.close()
            self._gui_log_handler = None

        if getattr(sys, "stdout", None) is stream:
            sys.stdout = getattr(self, "_original_stdout", sys.__stdout__)

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
        if hasattr(self, "keyword_results_controller"):
            self.keyword_results_controller.set_search_results_page(page)
            return

        page_map = {"tracks": 0, "top": 1, "albums": 2}
        page_index = page_map.get(page, 0)
        self.search_results_stack.setCurrentIndex(page_index)

        self.btnTracksResults.setChecked(page == "tracks")
        self.btnTopResults.setChecked(page == "top")
        self.btnAlbumsResults.setChecked(page == "albums")

    @pyqtSlot(dict)
    def _on_live_result_selected_for_view(self, result_data: Dict[str, Any]) -> None:
        self.keyword_results_controller.on_live_result_selected_for_view(result_data)

    @pyqtSlot(list, str)
    def _on_data_fetched_for_search_view(self, results: list, error_msg: str) -> None:
        self.keyword_results_controller.on_data_fetched_for_search_view(results, error_msg)

    def _reset_keyword_search_views(self) -> None:
        self.keyword_results_controller.reset_keyword_search_views()

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
        
        if not self._deferred_linking_save_active:
            self.link_persistence_manager.begin_deferred_save("linking_worker")
            self._deferred_linking_save_active = True

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
        linking_max_workers_raw = getattr(SETTINGS, "linkingMaxWorkers", 2)
        try:
            linking_max_workers = int(linking_max_workers_raw)
        except (TypeError, ValueError):
            linking_max_workers = 2

        self.linking_worker = LinkingWorker(
            TIDAL_API,
            tracks_to_link_data,
            self.linking_stop_event,
            linking_max_workers,
        )
        self.linking_thread = QThread(self)
        self.linking_worker.moveToThread(self.linking_thread)
        self.linking_worker.started.connect(self.s_linkingStarted)
        self.linking_worker.finished.connect(self.s_linkingFinished)
        self.linking_worker.error.connect(self.s_linkingError)
        self.linking_thread.started.connect(self.linking_worker.run)
        self.linking_worker.allTasksFinished.connect(self._flushDeferredLinkingPersistence)
        self.linking_worker.allTasksFinished.connect(self.linking_thread.quit)
        
        # MODIFIED: Connect the optional callback if it exists
        if on_finish_callback:
            self.linking_worker.allTasksFinished.connect(on_finish_callback)

        self.linking_worker.error.connect(lambda *_args: self._flushDeferredLinkingPersistence())
        self.linking_worker.error.connect(self.linking_thread.quit)
        self.linking_thread.finished.connect(self.linking_worker.deleteLater)
        self.linking_thread.finished.connect(self.linking_thread.deleteLater)
        self.linking_thread.finished.connect(self._clearLinkingWorkerRefs)
        self.linking_thread.start()

    @pyqtSlot()
    def _flushDeferredLinkingPersistence(self) -> None:
        if not getattr(self, "_deferred_linking_save_active", False):
            return

        self._deferred_linking_save_active = False
        try:
            self.link_persistence_manager.end_deferred_save("linking_worker")
        except Exception:
            logger_gui.warning(
                "Failed to flush deferred linking persistence save.",
                exc_info=True,
            )

    def _clearLinkingWorkerRefs(self):
        self._flushDeferredLinkingPersistence()
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
        self._gui_closing = True
        self._disconnect_gui_logging()

        if self.table_handler:
            with suppress(Exception):
                self.table_handler.prepare_for_shutdown()

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

        self._flushDeferredLinkingPersistence()

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
