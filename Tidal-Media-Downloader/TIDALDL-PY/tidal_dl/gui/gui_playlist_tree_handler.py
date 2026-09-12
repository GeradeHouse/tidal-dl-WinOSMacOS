# tidal_dl/gui/gui_playlist_tree_handler.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_playlist_tree_handler.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.5
@Desc    :   Manages the playlist QTreeWidget in the GUI.
"""

import contextlib
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import threading
import datetime
import time
from typing import TYPE_CHECKING, List, Dict, Optional, Any, Set, Iterator, cast

import aigpy
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import (
    QObject,
    pyqtSignal,
    pyqtSlot,
    QPoint,
    Qt,
    QSize,
    QThreadPool,
    QEvent,
    QAbstractItemModel,
    QModelIndex,
    QTimer,
)
from PyQt6.QtGui import (
    QIcon,
    QFont,
    QPainter,
    QPixmap,
    QColor,
    QBrush,
)
from PyQt6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QMenu,
    QWidget,
    QApplication,
    QPushButton,
    QLineEdit,
    QAbstractItemView,
)

# Import project components
from tidal_dl.tidal import TIDAL_API, Type, Playlist, AudioQuality, Track
from tidal_dl.printf import Printf
from tidal_dl.gui.gui_cover_cache import CoverArtWorker, CoverCache
from tidal_dl.settings import SETTINGS
from tidal_dl.gui.gui_playlist_tree import PlaylistTreeWidget
from tidal_dl.gui.gui_linking_handler import LinkingGuiHandler
from tidal_dl.gui.gui_playlist_item_widget import PlaylistItemProgressWidget
from tidal_dl.gui.gui_quality_menu import DOWNLOAD_QUALITY_MENU_ITEMS
from tidal_dl.format import getPlaylistPath
from tidal_dl.paths import get_user_download_path
from tidal_dl.playlist_folders import get_linked_playlist_folder
from tidal_dl.gui.gui_playlist_folders import PlaylistFolderController

if TYPE_CHECKING:
    from tidal_dl.gui.gui_main import MainView
    from tidal_dl.gui.gui_download import DownloadHandler
    from tidal_dl.gui.gui_task_queue_manager import TaskQueueManager
    from tidal_dl.gui.gui_table_handler import TableHandler

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set specific level for this module

# Set up GUI logging with INFO level for this module
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from tidal_dl.gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

# --- Stylesheet for Context Menus ---
MENU_STYLESHEET = """
    QMenu {
        background-color: #2b2b2b;
        border: 1px solid #454545;
        border-radius: 6px;
        padding: 4px;
    }
    QMenu::item {
        background-color: transparent;
        padding: 6px 24px 6px 12px;
        border-radius: 4px;
        color: #e0e0e0;
    }
    QMenu::item:selected {
        background-color: #0078d4;
        color: #ffffff;
    }
    QMenu::separator {
        height: 1px;
        background: #454545;
        margin: 4px 0px;
    }
"""
TABLE_TRACKS_DRAG_MIME = "application/x-tidal-dl-table-track-rows"

# --- Custom Delegate for Playlist Tree Items ---
class PlaylistDelegate(QtWidgets.QStyledItemDelegate):
    """
    Custom delegate for playlist tree items.
    - Draws '...' button on root items and handles clicks.
    - Draws custom hover background only on the text area of items.
    """

    _BUTTON_TEXT = "..."
    _BUTTON_WIDTH = 30
    _BUTTON_PADDING = 5

    def __init__(self, tree_handler: "PlaylistTreeHandler", parent_widget: QWidget):
        super().__init__(parent_widget)
        self.tree_handler = tree_handler
        self.hover_background_color = QColor("#3e3e43")
        self.folder_child_background_color = QColor("#050507ED")

    def initStyleOption(
        self,
        option: Optional[QtWidgets.QStyleOptionViewItem],
        index: QModelIndex,
    ) -> None:
        if option is None:
            return
        super().initStyleOption(option, index)
        tree_widget = self.tree_handler.tree_widget
        item = tree_widget.itemFromIndex(index)
        if item is not None and isinstance(
            tree_widget.itemWidget(item, index.column()), PlaylistItemProgressWidget
        ):
            # The transparent row widget owns the title and cover. Keep model
            # data for searching/accessibility, but never paint a second copy.
            option.text = ""
            option.icon = QIcon()
            option.features &= ~(
                QtWidgets.QStyleOptionViewItem.ViewItemFeature.HasDisplay
                | QtWidgets.QStyleOptionViewItem.ViewItemFeature.HasDecoration
            )

    def paint(
        self,
        painter: QtGui.QPainter | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if painter is None:
            return

        painter.save()

        tree_widget = cast(QTreeWidget, self.parent())
        item: Optional[QTreeWidgetItem] = None
        item_data: Any = None

        if isinstance(tree_widget, QTreeWidget):
            item = tree_widget.itemFromIndex(index)
            if item:
                with contextlib.suppress(RuntimeError):
                    item_data = item.data(0, Qt.ItemDataRole.UserRole)

        if isinstance(item_data, dict) and item_data.get("is_folder_child"):
            painter.fillRect(option.rect, self.folder_child_background_color)

        if option.state & QtWidgets.QStyle.StateFlag.State_MouseOver:
            if isinstance(tree_widget, QWidget):
                style = QApplication.style()
                if style:
                    text_rect = style.subElementRect(
                        QtWidgets.QStyle.SubElement.SE_ItemViewItemText,
                        option,
                        tree_widget,
                    )
                    if text_rect.isValid():
                        painter.setBrush(QBrush(self.hover_background_color))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.drawRoundedRect(text_rect, 10.0, 6.0)

        super().paint(painter, option, index)

        # --- Root Item Specific: Draw "..." button ---
        if index.isValid():
            tree_widget = cast(QTreeWidget, self.parent())
            if isinstance(tree_widget, QTreeWidget):
                item = tree_widget.itemFromIndex(index)
                if item:
                    is_root = (
                        item == self.tree_handler.tidal_root_item
                        or item == self.tree_handler.spotify_root_item
                    )
                    if is_root:
                        painter.save()
                        item_rect = option.rect
                        button_x = (
                            item_rect.right()
                            - self._BUTTON_WIDTH
                            - self._BUTTON_PADDING
                        )
                        button_rect_for_draw = QtCore.QRect(
                            button_x,
                            item_rect.top(),
                            self._BUTTON_WIDTH,
                            item_rect.height(),
                        )

                        text_color = option.palette.color(
                            QtGui.QPalette.ColorRole.PlaceholderText
                        )
                        painter.setPen(text_color)
                        font = item.font(0)
                        font.setPointSize(font.pointSize() + 3)
                        painter.setFont(font)
                        painter.drawText(
                            button_rect_for_draw,
                            Qt.AlignmentFlag.AlignCenter,
                            self._BUTTON_TEXT,
                        )
                        painter.restore()
        painter.restore()

    def editorEvent(
        self,
        event: QEvent | None,
        model: QAbstractItemModel | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """Handles mouse clicks on the '...' button area."""
        if (
            event
            and event.type() == QEvent.Type.MouseButtonRelease
            and isinstance(event, QtGui.QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            tree_widget = cast(QTreeWidget, self.parent())
            if isinstance(tree_widget, QTreeWidget):
                item = tree_widget.itemFromIndex(index)
                if item:
                    is_tidal_root = item == self.tree_handler.tidal_root_item
                    is_spotify_root = item == self.tree_handler.spotify_root_item

                    if is_tidal_root or is_spotify_root:
                        item_rect = option.rect
                        button_x = (
                            item_rect.right()
                            - self._BUTTON_WIDTH
                            - self._BUTTON_PADDING
                        )
                        button_rect_for_hit_test = QtCore.QRect(
                            button_x,
                            item_rect.top(),
                            self._BUTTON_WIDTH,
                            item_rect.height(),
                        )

                        if button_rect_for_hit_test.contains(event.pos()):
                            viewport = tree_widget.viewport()
                            if viewport:
                                global_pos = viewport.mapToGlobal(
                                    button_rect_for_hit_test.bottomLeft()
                                )
                                if is_tidal_root:
                                    self.tree_handler.showTidalPlaylistMenu(global_pos)
                                else:
                                    self.tree_handler.showSpotifyPlaylistMenu(
                                        global_pos
                                    )
                                return True
                            else:
                                logger.error(
                                    "Could not get tree widget viewport for root item context menu."
                                )
                                return False

        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option: QtWidgets.QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Returns the size hint for the item, driven by its custom widget."""
        tree_widget = cast(QTreeWidget, self.parent())
        if isinstance(tree_widget, QTreeWidget):
            item = tree_widget.itemFromIndex(index)
            if item:
                widget = tree_widget.itemWidget(item, 0)
                if widget:
                    return widget.sizeHint()
        
        return super().sizeHint(option, index)


# --- Playlist Tree Handler ---
class PlaylistTreeHandler(QObject):
    """
    Manages the playlist QTreeWidget, including population, interaction,
    context menus, sorting, and icon loading coordination.
    """

    # Signals
    tidalPlaylistSelected = pyqtSignal(Playlist)
    spotifyPlaylistSelected = pyqtSignal(dict)
    requestTidalPlaylistDownload = pyqtSignal(list, AudioQuality)
    playlistDownloadCountResolved = pyqtSignal(str, int, int)

    def __init__(
        self,
        playlist_tree_widget: PlaylistTreeWidget,
        cover_cache: CoverCache,
        parent: "MainView",
    ) -> None:
        super().__init__(parent)
        self.main_view = parent
        self.playlist_tree_widget = playlist_tree_widget
        self.tree_widget: QTreeWidget = playlist_tree_widget.tree_widget
        self.spotify_connect_button: QPushButton = playlist_tree_widget.spotify_connect_button
        self.cover_cache = cover_cache
        self.filter_input_widget: QLineEdit = playlist_tree_widget.get_filter_input_widget()
        self.download_handler: Optional["DownloadHandler"] = None
        self.linking_handler: Optional[LinkingGuiHandler] = None
        self.task_queue_manager: Optional["TaskQueueManager"] = None
        self.table_handler: Optional["TableHandler"] = None
        self._active_context_menu: Optional[QMenu] = None

        self.id_to_item: Dict[str, QTreeWidgetItem] = {}
        self.item_widgets: Dict[str, PlaylistItemProgressWidget] = {}
        self.original_item_data: Dict[str, Dict[str, Any]] = {}
        self._spotify_playlist_cache: List[Dict[str, Any]] = []
        self._spotify_sort_mode = "default"
        self._spotify_drop_target_item: Optional[QTreeWidgetItem] = None
        self.active_job_actions: Dict[str, str] = {}  # Track active job actions per playlist ID
        self.active_job_totals: Dict[str, int] = {}   # Track total items per playlist ID
        self._pending_geometry_playlist_ids: Set[str] = set()
        self._geometry_update_timer = QTimer(self)
        self._geometry_update_timer.setSingleShot(True)
        self._geometry_update_timer.setInterval(16)
        self._geometry_update_timer.timeout.connect(self._flush_pending_geometry_updates)

        self._download_count_cache: Dict[str, tuple[int, int]] = {}
        self._download_count_generation: Dict[str, int] = {}
        self._download_count_pending: Set[str] = set()
        self._download_count_visibility_timer = QTimer(self)
        self._download_count_visibility_timer.setSingleShot(True)
        self._download_count_visibility_timer.setInterval(120)
        self._download_count_visibility_timer.timeout.connect(
            self._queue_visible_download_counts
        )
        self.playlistDownloadCountResolved.connect(
            self._on_playlist_download_count_resolved
        )
        self._download_count_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="playlist-download-count",
        )

        # --- Load Default Playlist Icon ---
        self.default_playlist_icon = QIcon(QPixmap())
        try:
            default_icon_path = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "assets",
                    "icons",
                    "default_playlist.png",
                )
            )
            if os.path.exists(default_icon_path):
                self.default_playlist_icon = QIcon(default_icon_path)
        except Exception as e:
            logger.error(f"Error loading default playlist icon: {e}", exc_info=True)
        
        # +++ Load Folder Icon +++
        self.folder_icon = QIcon()
        try:
            folder_icon_path = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__), "..", "assets", "icons", "folder-white.png"
                )
            )
            if os.path.exists(folder_icon_path):
                base_pixmap = QPixmap(folder_icon_path)
                scaled_pixmap = base_pixmap.scaled(
                    14, 14,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.folder_icon = QIcon(scaled_pixmap)
        except Exception as e:
            logger.error(f"Error loading folder icon: {e}", exc_info=True)

        # --- Load Default Music Note Icon ---
        self.default_music_icon = QIcon()
        try:
            music_icon_path = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__), "..", "assets", "icons", "music_note.png"
                )
            )
            if os.path.exists(music_icon_path):
                self.default_music_icon = QIcon(music_icon_path)
        except Exception as e:
            logger.error(f"Error loading default music note icon: {e}", exc_info=True)

        self._setup_tree_widget()
        self._connect_tree_signals()
        self.folder_controller = PlaylistFolderController(self)

    def _playlist_tree_selected_count(self) -> int:
        try:
            return len(self.tree_widget.selectedItems()) if self.tree_widget else 0
        except Exception:
            return 0

    def _is_multi_selection_playlist_click(self) -> bool:
        selected_count = self._playlist_tree_selected_count()
        modifiers = QApplication.keyboardModifiers()
        modifier_multi_select = bool(
            modifiers
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.ShiftModifier
            )
        )
        return selected_count > 1 or modifier_multi_select

    def _set_spotify_drop_target_widget_active(
        self,
        item: Optional[QTreeWidgetItem],
        active: bool,
    ) -> None:
        if item is None:
            return

        try:
            widget = self.tree_widget.itemWidget(item, 0)
        except RuntimeError:
            return

        if not isinstance(widget, QWidget):
            return

        with contextlib.suppress(RuntimeError):
            setter = getattr(widget, "set_drop_target_active", None)
            if callable(setter):
                setter(active)
            else:
                widget.setAttribute(
                    Qt.WidgetAttribute.WA_StyledBackground,
                    bool(active),
                )
                if active:
                    widget.setCursor(Qt.CursorShape.PointingHandCursor)
                    widget.setToolTip(
                        "Drop selected tracks to add them to this Spotify playlist."
                    )
                else:
                    widget.unsetCursor()
                    widget.setToolTip("")
                widget.update()

    def _clear_spotify_playlist_drop_target(self) -> None:
        item = self._spotify_drop_target_item
        self._spotify_drop_target_item = None

        if item is None:
            return

        with contextlib.suppress(RuntimeError):
            self._set_spotify_drop_target_widget_active(item, False)
            viewport = self.tree_widget.viewport()
            if viewport is not None:
                rect = self.tree_widget.visualItemRect(item)
                if rect.isValid():
                    viewport.update(rect.adjusted(-2, -2, 2, 2))

    def _set_spotify_playlist_drop_target(self, item: Optional[QTreeWidgetItem]) -> None:
        if item is self._spotify_drop_target_item:
            return

        self._clear_spotify_playlist_drop_target()

        if item is None:
            return

        with contextlib.suppress(RuntimeError):
            self._set_spotify_drop_target_widget_active(item, True)
            self._spotify_drop_target_item = item
            viewport = self.tree_widget.viewport()
            if viewport is not None:
                rect = self.tree_widget.visualItemRect(item)
                if rect.isValid():
                    viewport.update(rect.adjusted(-2, -2, 2, 2))

    def _spotify_playlist_payload_for_drop_item(
        self,
        item: Optional[QTreeWidgetItem],
    ) -> Optional[Dict[str, Any]]:
        if item is None:
            return None

        try:
            item_data = item.data(0, Qt.ItemDataRole.UserRole)
        except RuntimeError:
            return None

        if not isinstance(item_data, dict):
            return None
        if item_data.get("type") != "spotify":
            return None
        playlist_data = item_data.get("data")
        if not isinstance(playlist_data, dict):
            return None
        if not playlist_data.get("can_modify_items"):
            return None
        return playlist_data

    def _drag_rows_from_table_mime(self, event: QtCore.QEvent) -> List[int]:
        if not isinstance(event, (QtGui.QDragEnterEvent, QtGui.QDragMoveEvent, QtGui.QDropEvent)):
            return []
        mime_data = event.mimeData()
        if not mime_data or not mime_data.hasFormat(TABLE_TRACKS_DRAG_MIME):
            return []
        try:
            raw_payload = mime_data.data(TABLE_TRACKS_DRAG_MIME).data().decode("utf-8")
            payload = json.loads(raw_payload)
            rows = payload.get("rows", [])
            return [int(row) for row in rows if isinstance(row, int) or str(row).isdigit()]
        except Exception:
            logger.warning("Could not parse table-track drag payload.", exc_info=True)
            return []

    def eventFilter(self, a0: Optional[QObject], a1: Optional[QEvent]) -> bool:
        watched = a0
        event = a1
        if watched is None or event is None:
            return False

        try:
            viewport = self.tree_widget.viewport()
        except RuntimeError:
            self._spotify_drop_target_item = None
            return False

        if viewport is None:
            return False

        if watched is viewport and event.type() == QEvent.Type.Resize:
            self._schedule_visible_download_count_scan()

        if watched is viewport and event.type() in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.DragLeave,
            QEvent.Type.Drop,
        ):
            if event.type() == QEvent.Type.DragLeave:
                self._clear_spotify_playlist_drop_target()
                return True

            if not isinstance(event, (QtGui.QDragEnterEvent, QtGui.QDragMoveEvent, QtGui.QDropEvent)):
                self._clear_spotify_playlist_drop_target()
                return True

            rows = self._drag_rows_from_table_mime(event)
            if not rows:
                self._clear_spotify_playlist_drop_target()
                event.ignore()
                return True

            try:
                drag_event = cast(Any, event)
                pos = drag_event.position().toPoint()
                target_item = self.tree_widget.itemAt(pos)
                playlist_data = self._spotify_playlist_payload_for_drop_item(target_item)
            except RuntimeError:
                self._clear_spotify_playlist_drop_target()
                event.ignore()
                return True

            if isinstance(event, (QtGui.QDragEnterEvent, QtGui.QDragMoveEvent)):
                if playlist_data:
                    self._set_spotify_playlist_drop_target(target_item)
                    event.setDropAction(Qt.DropAction.CopyAction)
                    event.accept()
                    return True
                self._clear_spotify_playlist_drop_target()
                event.ignore()
                return True

            if event.type() == QEvent.Type.Drop and isinstance(event, QtGui.QDropEvent):
                self._clear_spotify_playlist_drop_target()

                if playlist_data:
                    spotify_handler = getattr(self.main_view, "spotify_gui_handler", None)
                    drop_rows = list(rows)
                    drop_playlist_data = dict(playlist_data)

                    event.setDropAction(Qt.DropAction.CopyAction)
                    event.accept()

                    if spotify_handler:
                        QTimer.singleShot(
                            0,
                            lambda: spotify_handler.addTableRowsToSpotifyPlaylist(
                                drop_playlist_data,
                                drop_rows,
                            ),
                        )
                    return True

                event.ignore()
                return True

        return super().eventFilter(watched, event)

    def set_task_queue_manager(self, manager: "TaskQueueManager"):
        self.task_queue_manager = manager

    def set_download_handler(self, handler: "DownloadHandler") -> None:
        self.download_handler = handler

    def _setup_tree_widget(self) -> None:
        self.tree_widget.setColumnCount(1)
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setAnimated(True)
        
        icon_size = SETTINGS.playlistIconSize if SETTINGS.showPlaylistIcons else 0
        self.tree_widget.setIconSize(QSize(icon_size, icon_size))

        self.tree_widget.setRootIsDecorated(True)
        self.tree_widget.setSortingEnabled(False)
        self.tree_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_widget.setIndentation(15)
        self.tree_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree_widget.setAcceptDrops(True)
        self.tree_widget.setDropIndicatorShown(True)
        self.tree_widget.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.tree_widget.setDefaultDropAction(Qt.DropAction.CopyAction)

        self.playlist_delegate = PlaylistDelegate(self, self.tree_widget)
        self.tree_widget.setItemDelegate(self.playlist_delegate)
        
        # --- Create Root Items ---
        self.tidal_root_item = QTreeWidgetItem(self.tree_widget)
        self.tidal_root_item.setText(0, "Tidal Playlists")
        font_root_tidal = self.tidal_root_item.font(0)
        font_root_tidal.setFamily("Nationale")
        font_root_tidal.setWeight(QFont.Weight.DemiBold)
        self.tidal_root_item.setFont(0, font_root_tidal)
        tidal_icon_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "assets", "icons", "icon-white-rgb.png"
            )
        )
        if os.path.exists(tidal_icon_path):
            tidal_icon = QIcon(tidal_icon_path)
            pixmap = tidal_icon.pixmap(QSize(25, 25))
            self.tidal_root_item.setIcon(0, QIcon(pixmap))
        
        self.tidal_root_item.setFlags(
            self.tidal_root_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
        )

        self.spotify_root_item = QTreeWidgetItem(self.tree_widget)
        self.spotify_root_item.setText(0, "Spotify Playlists")
        font_root_spotify = self.spotify_root_item.font(0)
        font_root_spotify.setFamily("Nationale")
        font_root_spotify.setWeight(QFont.Weight.DemiBold)
        self.spotify_root_item.setFont(0, font_root_spotify)
        spotify_icon_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "assets",
                "icons",
                "Spotify_Primary_Logo_RGB_White.png",
            )
        )
        if os.path.exists(spotify_icon_path):
            spotify_icon = QIcon(spotify_icon_path)
            pixmap = spotify_icon.pixmap(QSize(25, 25))
            self.spotify_root_item.setIcon(0, QIcon(pixmap))
        
        self.spotify_root_item.setFlags(
            self.spotify_root_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
        )
        self.spotify_root_item.setHidden(True)

        self.tree_widget.update()

    def set_table_handler(self, handler: "TableHandler") -> None:
        self.table_handler = handler
        self._schedule_visible_download_count_scan()

    def set_linking_handler(self, handler: "LinkingGuiHandler") -> None:
        self.linking_handler = handler
        if self.linking_handler:
            try:
                self.linking_handler.linkingStarted.connect(self.on_job_started)
                self.linking_handler.linkProgress.connect(self.on_job_progress)
                self.linking_handler.linkingFinished.connect(self.on_job_finished)
                self.linking_handler.playlistQueued.connect(self.set_playlist_queued)
            except Exception as e:
                logger.error(f"Error connecting linking handler signals: {e}")

    def _connect_tree_signals(self) -> None:
        self.tree_widget.itemClicked.connect(self.onPlaylistItemClicked)
        self.tree_widget.customContextMenuRequested.connect(self._handleTreeContextMenu)
        self.tree_widget.itemExpanded.connect(self._loadVisibleSpotifyIcons)
        self.tree_widget.itemCollapsed.connect(self._loadVisibleSpotifyIcons)
        self.tree_widget.itemExpanded.connect(self._schedule_visible_download_count_scan)
        self.tree_widget.itemCollapsed.connect(self._schedule_visible_download_count_scan)
        scrollbar = self.tree_widget.verticalScrollBar()
        if scrollbar:
            scrollbar.valueChanged.connect(self._onSpotifyScroll)

        viewport = self.tree_widget.viewport()
        if viewport:
            viewport.setAcceptDrops(True)
            viewport.installEventFilter(self)
        
        self.filter_input_widget.textChanged.connect(self._apply_playlist_filter)

    # --- Helper Methods ---
    def _connect_playlist_folder_button(
        self, widget: PlaylistItemProgressWidget, playlist_id: str
    ) -> None:
        if not self.folder_icon.isNull():
            widget.folder_button.setIcon(self.folder_icon)
        widget.openFolderRequested.connect(
            partial(self._open_playlist_folder, playlist_id)
        )
        widget.folder_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def show_folder_menu(position: QPoint) -> None:
            menu = QMenu(widget.folder_button)
            menu.setStyleSheet(MENU_STYLESHEET)
            self.folder_controller.add_menu(menu, playlist_id)
            self._popup_context_menu(menu, widget.folder_button.mapToGlobal(position))

        widget.folder_button.customContextMenuRequested.connect(show_folder_menu)
        QTimer.singleShot(0, partial(self.folder_controller.update_tooltip, playlist_id))

    def _open_playlist_folder(self, playlist_id: str) -> None:
        """Open this row's local folder without changing the selected playlist."""
        item = self.id_to_item.get(playlist_id)
        if item is None:
            return
        descriptor = self._get_playlist_download_count_descriptor(item)
        if descriptor is None:
            return
        _, _, playlist_name, _, playlist_context = descriptor

        try:
            linked_folder = get_linked_playlist_folder(playlist_context)
            if linked_folder:
                if os.path.isdir(linked_folder):
                    self._open_local_folder(linked_folder)
                else:
                    self.folder_controller.offer_missing(playlist_id, linked_folder)
                return
            download_root = os.path.abspath(
                get_user_download_path(SETTINGS.downloadPath)
            )
            candidates: List[str] = []
            persistence_manager = getattr(
                self.main_view, "link_persistence_manager", None
            )
            if persistence_manager is not None:
                folder_hint = persistence_manager.get_playlist_folder_hint(playlist_id)
                if folder_hint:
                    candidates.append(os.path.join(download_root, folder_hint))

            # Match current audio-type folders as well as the legacy layout.
            # Use the download formatter so custom names and UUIDs are respected.
            for audio_type in ("flac", "mp3", "m4a", "mp4", "aac", "unknown", ""):
                folder = getPlaylistPath(
                    playlist_context, os.path.join(download_root, audio_type)
                )
                if folder:
                    candidates.append(folder)

            folders: List[str] = []
            seen: Set[str] = set()
            for candidate in candidates:
                folder = os.path.abspath(candidate)
                key = os.path.normcase(os.path.realpath(folder))
                if key not in seen and os.path.isdir(folder):
                    seen.add(key)
                    folders.append(folder)
        except Exception:
            logger.exception("Could not resolve folder for playlist %s", playlist_id)
            QtWidgets.QMessageBox.warning(
                self.tree_widget,
                "Open playlist folder",
                "Could not find the local playlist folder. Check your download location settings.",
            )
            return

        if not folders:
            self.folder_controller.offer_missing(playlist_id)
            return

        if len(folders) == 1:
            self._open_local_folder(folders[0])
            return

        # A playlist can have separate FLAC/MP3/etc. folders. Let the user choose
        # instead of opening several file-manager windows or an arbitrary format.
        menu = QMenu(self.tree_widget)
        menu.setStyleSheet(MENU_STYLESHEET)
        menu.addSection("Open playlist folder")
        for folder in folders:
            try:
                label = os.path.relpath(folder, download_root)
            except ValueError:
                label = folder
            action = menu.addAction(self.folder_icon, label.replace("&", "&&"))
            if action is None:
                continue
            action.setToolTip(folder)
            action.triggered.connect(partial(self._open_local_folder, folder))
        widget = self.item_widgets.get(playlist_id)
        position = (
            widget.folder_button.mapToGlobal(widget.folder_button.rect().bottomLeft())
            if widget is not None
            else QtGui.QCursor.pos()
        )
        menu.exec(position)
        menu.deleteLater()

    def _open_local_folder(self, folder: str) -> None:
        # Qt uses Explorer on Windows, Finder on macOS, and the default file
        # manager on Linux. Local-file URLs safely handle spaces and Unicode.
        if not os.path.isdir(folder) or not QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(folder)
        ):
            QtWidgets.QMessageBox.warning(
                self.tree_widget,
                "Open playlist folder",
                f"Could not open the folder:\n{folder}",
            )

    def _get_name_from_item(self, item: QTreeWidgetItem) -> str:
        """Safely retrieves the playlist name without sidebar status text."""
        descriptor = self._get_playlist_download_count_descriptor(item)
        if descriptor is not None:
            return descriptor[2]

        widget = self.tree_widget.itemWidget(item, 0)
        if isinstance(widget, PlaylistItemProgressWidget):
            return widget.name_label.text()
        return item.text(0)

    @staticmethod
    def _format_playlist_item_text(
        playlist_name: str,
        total_tracks: int,
        downloaded_tracks: Optional[int] = None,
    ) -> str:
        safe_total = max(0, int(total_tracks))
        if safe_total == 0:
            return f"{playlist_name} (0/0 downloaded)"

        if downloaded_tracks is None or downloaded_tracks < 0:
            return f"{playlist_name} ({safe_total} total)"

        safe_downloaded = min(
            max(0, int(downloaded_tracks)),
            safe_total,
        )
        return f"{playlist_name} ({safe_downloaded}/{safe_total} downloaded)"

    def _get_playlist_download_count_descriptor(
        self,
        item: QTreeWidgetItem,
    ) -> Optional[tuple[str, str, str, int, Any]]:
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(item_data, dict):
            return None

        service = str(item_data.get("type") or "").strip().lower()
        if service == "tidal":
            playlist_obj = item_data.get("data")
            if not isinstance(playlist_obj, Playlist):
                return None

            playlist_id = str(
                getattr(playlist_obj, "uuid", "") or ""
            ).strip()
            if not playlist_id:
                return None

            playlist_name = str(
                getattr(playlist_obj, "title", "") or "Untitled Playlist"
            )
            try:
                total_tracks = max(
                    0,
                    int(getattr(playlist_obj, "numberOfTracks", 0) or 0),
                )
            except (TypeError, ValueError):
                total_tracks = 0

            return (
                playlist_id,
                "tidal",
                playlist_name,
                total_tracks,
                playlist_obj,
            )

        if service == "spotify":
            playlist_data = item_data.get("data")
            if not isinstance(playlist_data, dict):
                return None

            playlist_id = str(playlist_data.get("id") or "").strip()
            if not playlist_id:
                return None

            playlist_name = str(
                playlist_data.get("name") or "Unknown Name"
            )
            try:
                total_tracks = max(
                    0,
                    int(playlist_data.get("tracks_total") or 0),
                )
            except (TypeError, ValueError):
                total_tracks = 0

            return (
                playlist_id,
                "spotify",
                playlist_name,
                total_tracks,
                {
                    "type": "spotify",
                    "data": dict(playlist_data),
                },
            )

        return None

    def _schedule_visible_download_count_scan(self, *_args: Any) -> None:
        if self.table_handler is None:
            return
        self._download_count_visibility_timer.start()

    def _invalidate_playlist_download_count(self, playlist_id: str) -> None:
        normalized_playlist_id = str(playlist_id or "").strip()
        if not normalized_playlist_id:
            return

        self._download_count_generation[normalized_playlist_id] = (
            self._download_count_generation.get(normalized_playlist_id, 0) + 1
        )
        self._download_count_cache.pop(normalized_playlist_id, None)
        self._download_count_pending.discard(normalized_playlist_id)

    @pyqtSlot()
    def _queue_visible_download_counts(self) -> None:
        if self.table_handler is None:
            return

        viewport = self.tree_widget.viewport()
        if viewport is None:
            return

        viewport_rect = viewport.rect()

        for playlist_id, item in list(self.id_to_item.items()):
            if playlist_id in self.active_job_actions:
                continue

            try:
                if item.isHidden():
                    continue
                item_rect = self.tree_widget.visualItemRect(item)
            except RuntimeError:
                continue

            if not item_rect.isValid() or not viewport_rect.intersects(item_rect):
                continue

            descriptor = self._get_playlist_download_count_descriptor(item)
            if descriptor is None:
                continue

            (
                descriptor_playlist_id,
                service,
                _playlist_name,
                total_tracks,
                playlist_context,
            ) = descriptor
            if descriptor_playlist_id != playlist_id:
                continue

            cached = self._download_count_cache.get(playlist_id)
            if cached is not None and cached[1] == total_tracks:
                continue

            if playlist_id in self._download_count_pending:
                continue

            if total_tracks <= 0:
                self._download_count_cache[playlist_id] = (0, 0)
                continue

            generation = self._download_count_generation.get(playlist_id, 0)
            self._download_count_pending.add(playlist_id)

            try:
                self._download_count_executor.submit(
                    self._playlist_download_count_task,
                    playlist_id,
                    generation,
                    service,
                    playlist_context,
                    total_tracks,
                )
            except RuntimeError:
                self._download_count_pending.discard(playlist_id)

    def _playlist_download_count_task(
        self,
        playlist_id: str,
        generation: int,
        service: str,
        playlist_context: Any,
        total_tracks: int,
    ) -> None:
        downloaded_tracks = -1
        try:
            downloaded_tracks = self._compute_playlist_download_count(
                playlist_id,
                service,
                playlist_context,
                total_tracks,
            )
        except Exception as exc:
            logger.debug(
                "Playlist download count failed | playlist_id=%s service=%s error=%s",
                playlist_id,
                service,
                exc,
                exc_info=True,
            )

        with contextlib.suppress(RuntimeError):
            self.playlistDownloadCountResolved.emit(
                playlist_id,
                int(generation),
                int(downloaded_tracks),
            )

    def _compute_playlist_download_count(
        self,
        playlist_id: str,
        service: str,
        playlist_context: Any,
        total_tracks: int,
    ) -> int:
        table_handler = self.table_handler
        if table_handler is None:
            return -1

        tracks_for_check: List[Track] = []

        if service == "tidal":
            fetched_tracks, _ = TIDAL_API.getItems(
                str(playlist_id),
                Type.Playlist,
            )
            tracks_for_check = [
                track
                for track in (fetched_tracks or [])
                if isinstance(track, Track)
            ]

        elif service == "spotify":
            spotify_track_ids = (
                self.main_view.spotify_api.get_playlist_track_ids(
                    playlist_id
                )
            )
            if spotify_track_ids is None:
                return -1

            persisted_links = (
                self.main_view.link_persistence_manager.get_links_for_playlist(
                    playlist_id
                )
            )
            persisted_tracks = (
                persisted_links.get("tracks", {})
                if isinstance(persisted_links, dict)
                else {}
            )
            if not isinstance(persisted_tracks, dict):
                persisted_tracks = {}

            for spotify_track_id in spotify_track_ids:
                normalized_spotify_id = str(
                    spotify_track_id or ""
                ).strip()
                if not normalized_spotify_id:
                    continue

                link_info = persisted_tracks.get(normalized_spotify_id)
                if not isinstance(link_info, dict):
                    continue

                details = link_info.get("tidal_track_details")
                if not isinstance(details, dict):
                    continue

                score = link_info.get("score")
                candidates = link_info.get("candidates") or []
                link_status = link_info.get("link_status")
                requires_manual_review = (
                    score is not None
                    and isinstance(score, (int, float))
                    and score > 1
                    and isinstance(candidates, list)
                    and len(candidates) > 1
                    and link_status
                    not in {"manual_linked", "candidate_confirmed"}
                )
                if requires_manual_review:
                    continue

                try:
                    track_obj = aigpy.model.dictToModel(
                        details,
                        Track(),
                    )
                except Exception:
                    continue

                if not isinstance(track_obj, Track):
                    continue

                if getattr(track_obj, "id", None) is None:
                    fallback_id = str(
                        link_info.get("tidal_track_id")
                        or details.get("id")
                        or ""
                    ).strip()
                    if fallback_id:
                        setattr(track_obj, "id", fallback_id)

                if getattr(track_obj, "id", None) is None:
                    continue

                tracks_for_check.append(track_obj)

        else:
            return -1

        if not tracks_for_check:
            return 0

        non_completed_tracks = table_handler.filter_non_completed_tracks(
            tracks_for_check,
            playlist_context,
            allow_slow_fallback=False,
            reason="playlist_tree_download_count",
        )
        downloaded_tracks = len(tracks_for_check) - len(
            non_completed_tracks
        )
        return min(
            max(0, downloaded_tracks),
            max(0, int(total_tracks)),
        )

    @pyqtSlot(str, int, int)
    def _on_playlist_download_count_resolved(
        self,
        playlist_id: str,
        generation: int,
        downloaded_tracks: int,
    ) -> None:
        if generation != self._download_count_generation.get(
            playlist_id,
            0,
        ):
            return

        self._download_count_pending.discard(playlist_id)

        if playlist_id in self.active_job_actions:
            return

        item = self.id_to_item.get(playlist_id)
        if item is None:
            return

        descriptor = self._get_playlist_download_count_descriptor(item)
        if descriptor is None:
            return

        (
            descriptor_playlist_id,
            _service,
            playlist_name,
            total_tracks,
            _playlist_context,
        ) = descriptor
        if descriptor_playlist_id != playlist_id:
            return

        normalized_downloaded = (
            -1
            if downloaded_tracks < 0
            else min(
                max(0, int(downloaded_tracks)),
                max(0, total_tracks),
            )
        )
        self._download_count_cache[playlist_id] = (
            normalized_downloaded,
            total_tracks,
        )

        item_text = self._format_playlist_item_text(
            playlist_name,
            total_tracks,
            normalized_downloaded,
        )
        self.original_item_data.setdefault(
            playlist_id,
            {},
        )["text"] = item_text

        with contextlib.suppress(RuntimeError):
            item.setText(0, item_text)

        widget = self.item_widgets.get(playlist_id)
        if widget is not None:
            with contextlib.suppress(RuntimeError):
                widget.name_label.setText(item_text)
                widget.updateGeometry()
                widget.update()

    @pyqtSlot(str)
    def _apply_playlist_filter(self, text: Optional[str] = None) -> None:
        """Filters the playlist tree based on the input text."""
        if text is None:
            text = self.filter_input_widget.text()
        
        filter_text = text.lower().strip()
        
        def process_item(item: QTreeWidgetItem) -> bool:
            item_name = self._get_name_from_item(item).lower()
            match = filter_text in item_name
            
            visible_children = False
            for i in range(item.childCount()):
                child = item.child(i)
                if child and process_item(child):
                    visible_children = True
            
            should_show = match or visible_children
            item.setHidden(not should_show)
            
            if visible_children and filter_text:
                item.setExpanded(True)
            
            return should_show

        for i in range(self.tidal_root_item.childCount()):
            child = self.tidal_root_item.child(i)
            if child:
                process_item(child)
            
        for i in range(self.spotify_root_item.childCount()):
            child = self.spotify_root_item.child(i)
            if child:
                process_item(child)

        self._schedule_visible_download_count_scan()

    @pyqtSlot()
    def onPlaylistDisplaySettingsChanged(self):
        """Called when playlist display settings (like icon size) change."""
        icon_size = SETTINGS.playlistIconSize if SETTINGS.showPlaylistIcons else 0
        self.tree_widget.setIconSize(QSize(icon_size, icon_size))
        
        # Refresh icons if needed
        if SETTINGS.showPlaylistIcons:
            self._loadVisibleSpotifyIcons()
            # Also trigger refresh for Tidal if needed, though they usually load on start
        else:
            # Clear icons if disabled? Or just let size 0 hide them.
            pass

    # --- Tidal Playlist Handling ---
    @pyqtSlot()
    def refreshTidalPlaylists(self) -> None:
        logger.info("Refreshing TIDAL playlists...")

        stale_tidal_ids: List[str] = []
        for index in range(self.tidal_root_item.childCount()):
            child = self.tidal_root_item.child(index)
            if child is None:
                continue
            descriptor = self._get_playlist_download_count_descriptor(child)
            if descriptor is not None:
                stale_tidal_ids.append(descriptor[0])

        for playlist_id in stale_tidal_ids:
            self._invalidate_playlist_download_count(playlist_id)
            self.id_to_item.pop(playlist_id, None)
            self.item_widgets.pop(playlist_id, None)
            self.original_item_data.pop(playlist_id, None)

        while self.tidal_root_item.childCount() > 0:
            self.tidal_root_item.removeChild(self.tidal_root_item.child(0))
        self.tidal_root_item.setText(0, "Tidal Playlists (Loading...)")
        self.tidal_root_item.setExpanded(not SETTINGS.tidalStartCollapsed)
        QApplication.processEvents()

        thread = threading.Thread(
            target=self._fetch_tidal_playlists_thread, daemon=True
        )
        thread.start()

    def _fetch_tidal_playlists_thread(self) -> None:
        playlists: List[Playlist] = []
        error_msg: Optional[str] = None
        try:
            playlists = TIDAL_API.getPlaylistSelf()
        except Exception as e:
            error_msg = f"Error fetching TIDAL playlists: {e}"
            logger.error(error_msg, exc_info=True)

        QtCore.QMetaObject.invokeMethod(
            self,
            "_update_tidal_playlist_tree",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(list, playlists or []),
            QtCore.Q_ARG(str, error_msg or ""),
        )

    @pyqtSlot(list, str)
    def _update_tidal_playlist_tree(
        self, playlists: List[Playlist], error_msg: str
    ) -> None:
        playlist_count = len(playlists)

        if error_msg:
            self.tidal_root_item.setText(0, "Tidal Playlists (Error)")
        else:
            self.tidal_root_item.setText(0, f"Tidal Playlists ({playlist_count})")

        if playlists:
            widget_count = 0
            for playlist_summary in playlists:
                item = QTreeWidgetItem(self.tidal_root_item)
                playlist_name = getattr(playlist_summary, "title", "Untitled Playlist")
                playlist_uuid = getattr(playlist_summary, "uuid", None)
                if not playlist_uuid:
                    continue

                try:
                    total_tracks = max(
                        0,
                        int(getattr(playlist_summary, "numberOfTracks", 0) or 0),
                    )
                except (TypeError, ValueError):
                    total_tracks = 0

                item_text = self._format_playlist_item_text(
                    playlist_name,
                    total_tracks,
                )

                self.original_item_data[str(playlist_uuid)] = {
                    "text": item_text,
                    "icon": self.default_music_icon
                }
                
                widget = PlaylistItemProgressWidget(item_text)
                self.tree_widget.setItemWidget(item, 0, widget)
                self.id_to_item[str(playlist_uuid)] = item
                self.item_widgets[str(playlist_uuid)] = widget
                self._connect_playlist_folder_button(widget, str(playlist_uuid))
                widget_count += 1
                
                # Force widget layout update after setting as item widget
                widget.updateGeometry()
                try:
                    layout = widget.layout()
                    if layout is not None:
                        layout.invalidate()
                        layout.activate()
                except (AttributeError, RuntimeError):
                    # Layout might not be available during initial widget setup
                    # but we still need to trigger layout recalculation through updateGeometry()
                    pass
                
                # Connect geometry signal to resize handler
                widget.geometryRequest.connect(partial(self._on_widget_geometry_request, str(playlist_uuid)))
                
                widget.updateGeometry()
                item.setSizeHint(0, widget.sizeHint())
                widget.set_icon(self.default_music_icon)

                font_child = item.font(0)
                font_child.setFamily("Nationale")
                font_child.setWeight(QFont.Weight.Medium)
                item.setFont(0, font_child)

                created_date_str = getattr(playlist_summary, "created", None)
                updated_date_str = getattr(playlist_summary, "lastUpdated", None)

                created_dt = None
                if created_date_str:
                    try:
                        if isinstance(created_date_str, datetime.datetime):
                            created_dt = created_date_str
                        elif isinstance(created_date_str, str):
                            created_dt = datetime.datetime.fromisoformat(
                                created_date_str.replace("Z", "+00:00")
                            )
                    except ValueError:
                        pass

                updated_dt = None
                if updated_date_str:
                    try:
                        if isinstance(updated_date_str, datetime.datetime):
                            updated_dt = updated_date_str
                        elif isinstance(updated_date_str, str):
                            updated_dt = datetime.datetime.fromisoformat(
                                updated_date_str.replace("Z", "+00:00")
                            )
                    except ValueError:
                        pass
                
                playlist_data: Dict[str, Any] = {
                    "type": "tidal",
                    "data": playlist_summary,
                    "full_data_fetched": False,
                    "created_date": created_dt,
                    "updated_date": updated_dt,
                    "image_url": None,
                }
                item.setData(0, Qt.ItemDataRole.UserRole, playlist_data)
 
                if SETTINGS.showPlaylistIcons:
                    self._start_icon_fetch(
                        item,
                        "tidal",
                        item_id=playlist_uuid,
                        image_url=None,
                    )
            
            if widget_count > 0:
                logger.debug(f"🎯 WIDGETS: Created {widget_count} TIDAL playlist widgets")

        logger.info("TIDAL playlists refreshed.")
        self._apply_playlist_filter()

    def _get_spotify_playlist_sort_key(self, playlist_data: Dict[str, Any]) -> tuple[str, str, str]:
        name = str(playlist_data.get("name") or "").casefold()
        owner_name = str(playlist_data.get("owner_name") or playlist_data.get("owner") or "").casefold()
        playlist_id = str(playlist_data.get("id") or "")
        return (name, owner_name, playlist_id)

    def _get_ordered_spotify_playlists(self) -> List[Dict[str, Any]]:
        playlists = [playlist for playlist in self._spotify_playlist_cache if isinstance(playlist, dict)]
        if self._spotify_sort_mode == "alpha":
            return sorted(playlists, key=self._get_spotify_playlist_sort_key)
        return list(playlists)

    def _spotify_sort_label(self) -> str:
        if self._spotify_sort_mode == "alpha":
            return "Alphabetical"
        return "Default Spotify order"

    def adjust_spotify_playlist_track_count(
        self,
        playlist_id: str,
        delta: Optional[int] = None,
        total: Optional[int] = None,
    ) -> None:
        normalized_playlist_id = str(playlist_id or "").strip()
        if not normalized_playlist_id:
            return

        updated_playlist: Optional[Dict[str, Any]] = None
        next_total: Optional[int] = None
        for playlist in self._spotify_playlist_cache:
            if not isinstance(playlist, dict):
                continue
            if str(playlist.get("id") or "").strip() != normalized_playlist_id:
                continue

            try:
                previous_total = int(playlist.get("tracks_total") or 0)
            except (TypeError, ValueError):
                previous_total = 0

            if total is not None:
                try:
                    next_total = max(0, int(total))
                except (TypeError, ValueError):
                    next_total = previous_total
            else:
                try:
                    next_total = max(0, previous_total + int(delta or 0))
                except (TypeError, ValueError):
                    next_total = previous_total

            playlist["tracks_total"] = next_total
            updated_playlist = playlist
            break

        if not updated_playlist or next_total is None:
            return

        self._invalidate_playlist_download_count(normalized_playlist_id)
        self._schedule_visible_download_count_scan()

        playlist_name = str(updated_playlist.get("name") or "Unknown Name")
        item_text = self._format_playlist_item_text(
            playlist_name,
            next_total,
        )

        self.original_item_data.setdefault(normalized_playlist_id, {})["text"] = item_text

        item = self.id_to_item.get(normalized_playlist_id)
        if item is not None:
            with contextlib.suppress(RuntimeError):
                item.setText(0, item_text)
                item_data = item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(item_data, dict):
                    item_data["data"] = updated_playlist
                    item.setData(0, Qt.ItemDataRole.UserRole, item_data)

        widget = self.item_widgets.get(normalized_playlist_id)
        if widget is not None:
            with contextlib.suppress(RuntimeError):
                name_label = getattr(widget, "name_label", None)
                if name_label is not None:
                    name_label.setText(item_text)
                widget.updateGeometry()
                widget.update()

        active_playlist_obj = getattr(self.main_view, "s_playlist_obj", None)
        if isinstance(active_playlist_obj, dict) and active_playlist_obj.get("type") == "spotify":
            active_data = active_playlist_obj.get("data")
            if (
                isinstance(active_data, dict)
                and str(active_data.get("id") or "").strip() == normalized_playlist_id
            ):
                active_data["tracks_total"] = updated_playlist.get("tracks_total")

        with contextlib.suppress(RuntimeError):
            self.spotify_root_item.setText(0, f"Spotify Playlists ({len(self._spotify_playlist_cache)})")
            viewport = self.tree_widget.viewport()
            if viewport is not None:
                viewport.update()

    def _iter_spotify_folder_items(self) -> Iterator[QTreeWidgetItem]:
        if not self.spotify_root_item:
            return

        iterator = QtWidgets.QTreeWidgetItemIterator(self.spotify_root_item)
        while True:
            item = iterator.value()
            if item is None:
                break
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("is_folder"):
                yield item
            iterator += 1

    def _set_spotify_folders_expanded(self, expanded: bool) -> None:
        for folder_item in self._iter_spotify_folder_items():
            folder_item.setExpanded(expanded)

    # --- Spotify Playlist Handling ---
    @pyqtSlot(list)
    def populate_spotify_playlists(self, playlists: List[Dict[str, Any]], refresh_cache: bool = True) -> None:
        try:
            self._clear_spotify_playlist_drop_target()

            previous_spotify_playlist_ids = [
                str(playlist.get("id") or "").strip()
                for playlist in self._spotify_playlist_cache
                if isinstance(playlist, dict)
            ]

            if refresh_cache:
                for playlist_id in previous_spotify_playlist_ids:
                    if playlist_id:
                        self._invalidate_playlist_download_count(playlist_id)

            for playlist_id in previous_spotify_playlist_ids:
                if playlist_id:
                    self.id_to_item.pop(playlist_id, None)
                    self.item_widgets.pop(playlist_id, None)
                    self.original_item_data.pop(playlist_id, None)

            if refresh_cache:
                self._spotify_playlist_cache = [
                    dict(playlist)
                    for playlist in playlists
                    if isinstance(playlist, dict)
                ]

            playlists_to_render = self._get_ordered_spotify_playlists()

            while self.spotify_root_item.childCount() > 0:
                self.spotify_root_item.removeChild(self.spotify_root_item.child(0))

            playlist_count = len(self._spotify_playlist_cache)
            self.spotify_root_item.setText(0, f"Spotify Playlists ({playlist_count})")
            self.spotify_root_item.setHidden(False)

            folder_items: Dict[str, QTreeWidgetItem] = {}
            widget_count = 0

            for p_data in playlists_to_render:
                if not (isinstance(p_data, dict) and "name" in p_data and "id" in p_data):
                    continue

                playlist_name = p_data.get('name', 'Unknown Name')
                parent_item = self.spotify_root_item
                is_folder_child = False

                if SETTINGS.spotifyUsePlaylistFolders and ' - ' in playlist_name:
                    folder_name = playlist_name.split(' - ', 1)[0].strip()
                    
                    if folder_name:
                        is_folder_child = True
                        if folder_name not in folder_items:
                            new_folder_item = QTreeWidgetItem(self.spotify_root_item)
                            new_folder_item.setText(0, folder_name)
                            
                            font = new_folder_item.font(0)
                            font.setWeight(QFont.Weight.Bold)
                            new_folder_item.setFont(0, font)
                            
                            if SETTINGS.showPlaylistIcons and not self.folder_icon.isNull():
                                new_folder_item.setIcon(0, self.folder_icon)

                            new_folder_item.setData(
                                0,
                                Qt.ItemDataRole.UserRole,
                                {
                                    "type": "spotify_folder",
                                    "is_folder": True,
                                    "folder_name": folder_name,
                                },
                            )
                            folder_items[folder_name] = new_folder_item
                        
                        parent_item = folder_items[folder_name]

                item = QTreeWidgetItem(parent_item)
                playlist_id = p_data.get("id")
                if not playlist_id:
                    continue

                try:
                    total_tracks = max(
                        0,
                        int(p_data.get("tracks_total") or 0),
                    )
                except (TypeError, ValueError):
                    total_tracks = 0

                cached_count = self._download_count_cache.get(
                    str(playlist_id)
                )
                downloaded_tracks = (
                    cached_count[0]
                    if cached_count is not None
                    and cached_count[1] == total_tracks
                    else None
                )
                item_text = self._format_playlist_item_text(
                    playlist_name,
                    total_tracks,
                    downloaded_tracks,
                )
                
                current_icon = self.default_music_icon
                images = p_data.get("images", [])
                image_url = None
                if images:
                    image_url = images[-1].get("url") if images else None
                    if image_url:
                        cached_pixmap = self.cover_cache.get(image_url)
                        if cached_pixmap:
                            current_icon = QIcon(cached_pixmap)
                
                self.original_item_data[str(playlist_id)] = {
                    "text": item_text,
                    "icon": current_icon
                }
                
                widget = PlaylistItemProgressWidget(item_text)
                self.tree_widget.setItemWidget(item, 0, widget)
                self.id_to_item[str(playlist_id)] = item
                self.item_widgets[str(playlist_id)] = widget
                self._connect_playlist_folder_button(widget, str(playlist_id))
                widget_count += 1

                # Force widget layout update after setting as item widget
                widget.updateGeometry()
                try:
                    layout = widget.layout()
                    if layout is not None:
                        layout.invalidate()
                        layout.activate()
                except (AttributeError, RuntimeError):
                    # Layout might not be available during initial widget setup
                    # but we still need to trigger layout recalculation through updateGeometry()
                    pass
                
                # Update item size hint and force tree widget refresh
                item.setSizeHint(0, widget.sizeHint())
                try:
                    viewport = self.tree_widget.viewport()
                    if viewport is not None:
                        viewport.update()
                except (AttributeError, RuntimeError):
                    # Viewport might not be available during initial setup
                    pass

                # Connect geometry signal to resize handler
                widget.geometryRequest.connect(partial(self._on_widget_geometry_request, str(playlist_id)))

                widget.updateGeometry()
                item.setSizeHint(0, widget.sizeHint())

                font_child_spotify = item.font(0)
                font_child_spotify.setFamily("Nationale")
                font_child_spotify.setWeight(QFont.Weight.Normal)
                item.setFont(0, font_child_spotify)

                item_data_dict: Dict[str, Any] = {
                    "type": "spotify",
                    "data": p_data,
                    "image_url": image_url,
                    "icon_update_pending": False,
                    "is_folder_child": is_folder_child,
                }
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, item_data_dict)

                if SETTINGS.showPlaylistIcons:
                    if image_url:
                        cached_pixmap = self.cover_cache.get(image_url)
                        if cached_pixmap:
                            icon = QIcon(cached_pixmap)
                            widget.set_icon(icon)
                            if str(playlist_id) in self.original_item_data:
                                self.original_item_data[str(playlist_id)]["icon"] = icon
                        else:
                            widget.set_icon(self.default_playlist_icon)
                            self._start_icon_fetch(item, "spotify", p_data.get("id"), image_url)
                    else:
                        widget.set_icon(self.default_music_icon)
            
            self._loadVisibleSpotifyIcons()
            if playlists_to_render:
                def _expand_spotify_root():
                    if self.spotify_root_item:
                        self.spotify_root_item.setExpanded(True)
                        for folder_item in folder_items.values():
                            folder_item.setExpanded(True)
                QTimer.singleShot(0, _expand_spotify_root)
            self._apply_playlist_filter()

        except Exception as e:
            logger.error(f"Error updating Spotify playlist tree: {e}", exc_info=True)
            self.update_spotify_root_item(logged_in=False, error=True)

    def update_spotify_root_item(
        self, logged_in: Optional[bool], error: bool = False, attention: bool = False
    ) -> None:
        def _do_update():
            connect_button = self.spotify_connect_button
            if not self.spotify_root_item:
                return

            try:
                if error:
                    self.spotify_root_item.setText(0, "Spotify Playlists (Error)")
                    self.spotify_root_item.setHidden(False)
                    if connect_button:
                        connect_button.setVisible(False)
                        with contextlib.suppress(Exception):
                            self.main_view.playlist_tree_widget.set_spotify_connect_attention(False)
                elif logged_in is None:
                    self.spotify_root_item.setText(0, "Spotify (Loading...)")
                    self.spotify_root_item.setHidden(False)
                    if connect_button:
                        connect_button.setVisible(False)
                        with contextlib.suppress(Exception):
                            self.main_view.playlist_tree_widget.set_spotify_connect_attention(False)
                elif logged_in:
                    self.spotify_root_item.setHidden(False)
                    if connect_button:
                        connect_button.setVisible(False)
                        with contextlib.suppress(Exception):
                            self.main_view.playlist_tree_widget.set_spotify_connect_attention(False)
                    self.spotify_root_item.setExpanded(True)
                else:
                    self.spotify_root_item.setHidden(True)
                    if connect_button:
                        connect_button.setVisible(True)
                        with contextlib.suppress(Exception):
                            self.main_view.playlist_tree_widget.set_spotify_connect_attention(
                                attention,
                                "Spotify credentials need attention. Click to reconnect or open Settings.",
                            )

            except Exception as ui_update_error:
                logger.error(
                    f"Error during deferred UI update for spotify_root_item: {ui_update_error}",
                    exc_info=True,
                )

        QTimer.singleShot(0, _do_update)

    # --- Item Interaction ---
    @pyqtSlot(QTreeWidgetItem, int)
    def onPlaylistItemClicked(self, item: QTreeWidgetItem, column: int) -> None:
        if not item or not item.data(0, Qt.ItemDataRole.UserRole):
            return

        logger.debug(f"🎯🎯🎯 CLICK: Playlist item clicked: '{self._get_name_from_item(item)}'")
        logger.debug(f"🎯🎯🎯 CLICK: Item data type: {type(item.data(0, Qt.ItemDataRole.UserRole))}")
        logger.debug(f"🎯🎯🎯 CLICK: Item flags: {item.flags()}")
        logger.debug(f"🎯🎯🎯 CLICK: Item is hidden: {item.isHidden()}")
        logger.debug(f"🎯🎯🎯 CLICK: Item parent: {item.parent()}")
        parent = item.parent()
        logger.debug(f"🎯🎯🎯 CLICK: Item index: {parent.indexOfChild(item) if parent else 'No parent'}")

        item_data = item.data(0, Qt.ItemDataRole.UserRole)

        if isinstance(item_data, dict) and item_data.get("is_folder"):
            item.setExpanded(not item.isExpanded())
            return

        if item in (self.tidal_root_item, self.spotify_root_item):
            item.setExpanded(not item.isExpanded())
            return

        if item_data is None:
            return

        if self._is_multi_selection_playlist_click():
            logger.info(
                "Skipping playlist table load during multi-selection | selected_count=%s item=%s",
                self._playlist_tree_selected_count(),
                self._get_name_from_item(item),
            )
            return

        click_load_start = time.perf_counter()

        if self.table_handler:
            self.table_handler.clear_table()

        setattr(self.main_view, "s_playlist_obj", item_data)
        setattr(self.main_view, "s_playlist", True)

        if self.linking_handler:
            self.linking_handler.update_link_button_state()

        try:
            item_type = item_data.get("type", "tidal")
            if item_type == "spotify":
                playlist_data = item_data.get("data")
                playlist_id = None
                if playlist_data is not None:
                    playlist_id = playlist_data.get("id")
                if playlist_id:
                    self.spotifyPlaylistSelected.emit(item_data)
                    if hasattr(self.main_view, "spotify_gui_handler"):
                        self.main_view.spotify_gui_handler.fetchSpotifyTracks(
                            playlist_id
                        )
                else:
                    setattr(self.main_view, "s_playlist_obj", None)

            elif item_type == "tidal":
                playlist_obj = item_data.get("data")
                if playlist_obj is not None and isinstance(playlist_obj, Playlist):
                    self.tidalPlaylistSelected.emit(playlist_obj)
                    self._displayTidalTracks(playlist_obj)
                else:
                    setattr(self.main_view, "s_playlist_obj", None)
            else:
                setattr(self.main_view, "s_playlist_obj", None)

        except Exception as e:
            logger.error(f"Error handling playlist item click: {e}", exc_info=True)
            setattr(self.main_view, "s_playlist_obj", None)
        finally:
            elapsed_ms = (time.perf_counter() - click_load_start) * 1000
            if elapsed_ms > 500:
                logger.warning(
                    "Playlist click load was slow | elapsed_ms=%.1f item=%s selected_count=%s",
                    elapsed_ms,
                    self._get_name_from_item(item),
                    self._playlist_tree_selected_count(),
                )

    def _displayTidalTracks(self, playlist_obj: Playlist) -> None:
        playlist_name = getattr(playlist_obj, "title", "Unknown Tidal Playlist")
        playlist_id = getattr(playlist_obj, "uuid", None)

        if not playlist_id:
            if self.table_handler:
                self.table_handler.show_error_message(
                    "Playlist missing identifier."
                )
            return

        if self.table_handler:
            self.table_handler.show_loading_message(
                f"Loading tracks for '{playlist_name}'..."
            )

        thread = threading.Thread(
            target=self._fetch_tidal_tracks_thread,
            args=(playlist_id, playlist_name),
            daemon=True,
        )
        thread.start()

    def _fetch_tidal_tracks_thread(self, playlist_id: str, playlist_name: str) -> None:
        tracks_full: List[Track] = []
        error_msg: Optional[str] = None
        try:
            result = TIDAL_API.getItems(playlist_id, Type.Playlist)
            if result is None:
                raise ConnectionError(
                    f"API Error: getItems returned None for playlist ID {playlist_id}."
                )

            tracks_data, _ = result

            if tracks_data and isinstance(tracks_data[0], str):
                for t_idx, t_id_or_obj in enumerate(tracks_data):
                    track_id_str: Optional[str] = None
                    if isinstance(t_id_or_obj, Track):
                        if t_id_or_obj.id is not None:
                            track_id_str = str(t_id_or_obj.id)
                    elif isinstance(t_id_or_obj, str):
                        track_id_str = t_id_or_obj

                    if track_id_str is None:
                        continue

                    track_obj = TIDAL_API.getTypeData(track_id_str, Type.Track)
                    if track_obj:
                        tracks_full.append(track_obj)
            elif tracks_data and isinstance(tracks_data[0], Track):
                tracks_full = tracks_data
            else:
                raise TypeError("Received unexpected track data format from API.")

            if tracks_full:
                Printf.success(
                    f"Successfully loaded {len(tracks_full)} tracks for '{playlist_name}'."
                )
            else:
                Printf.warning(
                    f"No valid track details could be fetched for playlist '{playlist_name}'."
                )

        except Exception as e:
            error_msg = (
                f"Error fetching/processing Tidal tracks for '{playlist_name}': {e}"
            )
            logger.error(error_msg, exc_info=True)

        if self.table_handler:
            QtCore.QMetaObject.invokeMethod(
                self.table_handler,
                "populate_tidal_tracks",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(list, tracks_full),
                QtCore.Q_ARG(str, error_msg or ""),
            )

    # --- Context Menus ---
    @pyqtSlot(QPoint)
    def _handleTreeContextMenu(self, pos: QPoint) -> None:
        selected_items = self.tree_widget.selectedItems()
        if not selected_items:
            return

        viewport = self.tree_widget.viewport()
        if not viewport:
            return
        
        global_pos = viewport.mapToGlobal(pos)
        
        is_tidal_root_selected = any(item == self.tidal_root_item for item in selected_items)
        is_spotify_root_selected = any(item == self.spotify_root_item for item in selected_items)

        if is_tidal_root_selected:
            self.showTidalPlaylistMenu(global_pos)
        elif is_spotify_root_selected:
            self.showSpotifyPlaylistMenu(global_pos)
        else:
            self._playlistItemContextMenu(selected_items, global_pos)

    def _popup_context_menu(self, menu: QMenu, global_pos: QPoint) -> None:
        """
        Keeps popup QMenus alive while shown. This prevents PyQt garbage-collection
        edge cases and gives us one place to clear the reference.
        """
        self._active_context_menu = menu

        def _clear_active_menu() -> None:
            if self._active_context_menu is menu:
                self._active_context_menu = None

        menu.aboutToHide.connect(_clear_active_menu)
        menu.popup(global_pos)

    def showTidalPlaylistMenu(self, global_pos: QPoint) -> None:
        menu = QMenu(self.tree_widget)
        menu.setStyleSheet(MENU_STYLESHEET)
        
        refresh_action = menu.addAction("Refresh Tidal Playlists")
        if refresh_action:
            refresh_action.triggered.connect(self.refreshTidalPlaylists)

        menu.addSeparator()
        sort_menu = menu.addMenu("Sort by")
        if sort_menu:
            sort_menu.setStyleSheet(MENU_STYLESHEET)
            action_sort_created = sort_menu.addAction("Created Date")
            action_sort_updated = sort_menu.addAction("Updated Date")
            action_sort_alpha = sort_menu.addAction("Alphabetical")
            if action_sort_created:
                action_sort_created.triggered.connect(
                    lambda: self._sortTidalPlaylists("created")
                )
            if action_sort_updated:
                action_sort_updated.triggered.connect(
                    lambda: self._sortTidalPlaylists("updated")
                )
            if action_sort_alpha:
                action_sort_alpha.triggered.connect(
                    lambda: self._sortTidalPlaylists("alpha")
                )

        self._popup_context_menu(menu, global_pos)

    def showSpotifyPlaylistMenu(self, global_pos: QPoint) -> None:
        menu = QMenu(self.tree_widget)
        menu.setStyleSheet(MENU_STYLESHEET)

        create_action = menu.addAction("Create New Spotify Playlist")
        if create_action:
            if hasattr(self.main_view, "spotify_gui_handler"):
                create_action.triggered.connect(
                    self.main_view.spotify_gui_handler.createSpotifyPlaylistFromPrompt
                )
                create_action.setEnabled(
                    self.main_view.spotify_gui_handler.spotify_api.sp is not None
                )
            else:
                create_action.setEnabled(False)

        menu.addSeparator()
         
        refresh_action = menu.addAction("Refresh Spotify Playlists")
        if refresh_action:
            if hasattr(self.main_view, "spotify_gui_handler"):
                refresh_action.triggered.connect(
                    self.main_view.spotify_gui_handler.refreshSpotifyPlaylists
                )
                refresh_action.setEnabled(
                    self.main_view.spotify_gui_handler.spotify_api.sp is not None
                )
            else:
                refresh_action.setEnabled(False)

        active_filter = self.filter_input_widget.text().strip()
        if active_filter:
            menu.addSeparator()
            clear_filter_action = menu.addAction("Clear playlist filter")
            if clear_filter_action:
                clear_filter_action.triggered.connect(self.filter_input_widget.clear)

        menu.addSeparator()
        sort_menu = menu.addMenu(f"Sort by: {self._spotify_sort_label()}")
        if sort_menu:
            sort_menu.setStyleSheet(MENU_STYLESHEET)
            sort_action_group = QtGui.QActionGroup(sort_menu)
            sort_action_group.setExclusive(True)

            action_sort_default = sort_menu.addAction("Default Spotify order")
            action_sort_alpha = sort_menu.addAction("Alphabetical")

            for action, mode in (
                (action_sort_default, "default"),
                (action_sort_alpha, "alpha"),
            ):
                if action:
                    action.setCheckable(True)
                    action.setChecked(self._spotify_sort_mode == mode)
                    sort_action_group.addAction(action)
                    action.triggered.connect(
                        lambda _checked=False, selected_mode=mode: self._sortSpotifyPlaylists(selected_mode)
                    )

        folder_items = list(self._iter_spotify_folder_items())
        if folder_items:
            menu.addSeparator()

            expand_folders_action = menu.addAction("Expand all Spotify folders")
            if expand_folders_action:
                expand_folders_action.triggered.connect(
                    lambda: self._set_spotify_folders_expanded(True)
                )

            collapse_folders_action = menu.addAction("Collapse all Spotify folders")
            if collapse_folders_action:
                collapse_folders_action.triggered.connect(
                    lambda: self._set_spotify_folders_expanded(False)
                )

        self._popup_context_menu(menu, global_pos)

    def _playlistItemContextMenu(self, items: List[QTreeWidgetItem], global_pos: QPoint) -> None:
        if not items:
            return

        menu_start = time.perf_counter()
        playlist_items: List[QTreeWidgetItem] = []

        def _finish_menu_build_timing() -> None:
            try:
                self.main_view.endBusyOperation("Building playlist context menu")
            except Exception:
                pass

            elapsed_ms = (time.perf_counter() - menu_start) * 1000.0
            if elapsed_ms > 250.0:
                logger.warning(
                    "Playlist context menu build was slow | elapsed_ms=%.1f selected_count=%d",
                    elapsed_ms,
                    len(playlist_items),
                )

        try:
            self.main_view.beginBusyOperation("Building playlist context menu")
        except Exception:
            pass

        menu = QMenu(self.tree_widget)
        menu.setStyleSheet(MENU_STYLESHEET)
        
        for item in items:
            item_data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict) and not item_data.get("is_folder"):
                playlist_items.append(item)
        
        if not playlist_items:
            _finish_menu_build_timing()
            return

        num_selected = len(playlist_items)
        plural_s = "s" if num_selected > 1 else ""

        if num_selected == 1:
            descriptor = self._get_playlist_download_count_descriptor(playlist_items[0])
            if descriptor:
                self.folder_controller.add_menu(menu, descriptor[0])
                menu.addSeparator()

        def do_copy():
            names = [self._get_name_from_item(item) for item in playlist_items]
            clipboard_text = "\n".join(filter(None, names))
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(clipboard_text)

        copy_action = menu.addAction(f"Copy Name{plural_s}")
        if copy_action:
            copy_action.triggered.connect(do_copy)

        first_item_data = playlist_items[0].data(0, Qt.ItemDataRole.UserRole)
        item_type = first_item_data.get("type", "tidal")
        
        all_same_type = all(
            (item.data(0, Qt.ItemDataRole.UserRole) or {}).get("type") == item_type
            for item in playlist_items
        )

        if all_same_type and self.task_queue_manager:
            if item_type == "tidal":
                tidal_playlists = []
                for item in playlist_items:
                    item_data = item.data(0, Qt.ItemDataRole.UserRole)
                    if item_data is not None:
                        playlist_data = item_data.get("data")
                        if playlist_data is not None:
                            tidal_playlists.append(playlist_data)
                tidal_playlists = [p for p in tidal_playlists if isinstance(p, Playlist)]
                
                if tidal_playlists:
                    download_menu = menu.addMenu(f"Download Playlist{plural_s} As...")
                    if download_menu:
                        download_menu.setStyleSheet(MENU_STYLESHEET)

                        # Wrapper to update UI to "Queued" immediately before submitting download jobs
                        def queue_and_download_tidal(playlists_data, quality):
                            if not self.task_queue_manager:
                                return
                            for playlist_obj in playlists_data:
                                playlist_id = getattr(playlist_obj, "uuid", None)
                                if playlist_id:
                                    self.set_playlist_queued(str(playlist_id))
                            self.task_queue_manager.add_tidal_download_job(playlists_data, quality)

                        def queue_and_download_tidal_non_completed(playlists_data, quality):
                            if not self.task_queue_manager:
                                return
                            for playlist_obj in playlists_data:
                                playlist_id = getattr(playlist_obj, "uuid", None)
                                if playlist_id:
                                    self.set_playlist_queued(str(playlist_id))
                            self.task_queue_manager.add_tidal_download_job(
                                playlists_data,
                                quality,
                                non_completed_only=True,
                            )

                        dlQualities = DOWNLOAD_QUALITY_MENU_ITEMS
                        for text, quality_enum in dlQualities:
                            action = download_menu.addAction(text)
                            if action:
                                action.triggered.connect(
                                    partial(queue_and_download_tidal, tidal_playlists, quality_enum)
                                )

                        download_non_completed_menu = menu.addMenu(
                            f"Download non-completed Playlist{plural_s} As..."
                        )
                        if download_non_completed_menu:
                            download_non_completed_menu.setStyleSheet(MENU_STYLESHEET)
                            for text, quality_enum in dlQualities:
                                action = download_non_completed_menu.addAction(text)
                                if action:
                                    action.triggered.connect(
                                        partial(
                                            queue_and_download_tidal_non_completed,
                                            tidal_playlists,
                                            quality_enum,
                                        )
                                    )

            elif item_type == "spotify":
                spotify_playlists_data = [item.data(0, Qt.ItemDataRole.UserRole) for item in playlist_items]
                spotify_handler = getattr(self.main_view, "spotify_gui_handler", None)
                if spotify_handler and len(spotify_playlists_data) == 1:
                    first_playlist_payload = spotify_playlists_data[0]
                    first_playlist_data = (
                        first_playlist_payload.get("data", {})
                        if isinstance(first_playlist_payload, dict)
                        else {}
                    )
                    can_edit_details = bool(first_playlist_data.get("can_edit_details"))
                    can_modify_items = bool(first_playlist_data.get("can_modify_items"))

                    rename_action = menu.addAction("Rename Spotify Playlist")
                    if rename_action:
                        rename_action.setEnabled(can_edit_details)
                        rename_action.triggered.connect(partial(spotify_handler.renameSpotifyPlaylist, first_playlist_data))

                    edit_details_action = menu.addAction("Edit Spotify Playlist Details")
                    if edit_details_action:
                        edit_details_action.setEnabled(can_edit_details)
                        edit_details_action.triggered.connect(partial(spotify_handler.editSpotifyPlaylistDetails, first_playlist_data))

                    duplicate_action = menu.addAction("Duplicate Spotify Playlist")
                    if duplicate_action:
                        duplicate_action.triggered.connect(partial(spotify_handler.duplicateSpotifyPlaylist, first_playlist_data))

                    backup_action = menu.addAction("Create Backup Playlist")
                    if backup_action:
                        backup_action.triggered.connect(partial(spotify_handler.backupSpotifyPlaylist, first_playlist_data))

                    cleanup_duplicates_action = menu.addAction("Remove Duplicate Tracks")
                    if cleanup_duplicates_action:
                        cleanup_duplicates_action.setEnabled(can_modify_items)
                        cleanup_duplicates_action.triggered.connect(partial(spotify_handler.removeDuplicateTracksFromSpotifyPlaylist, first_playlist_data))

                    add_selected_action = menu.addAction("Add Selected Table Tracks to This Playlist")
                    if add_selected_action:
                        add_selected_action.setEnabled(can_modify_items)
                        add_selected_action.triggered.connect(
                            lambda _checked=False, playlist_data=first_playlist_data: spotify_handler.addSelectedRowsToSpotifyPlaylist(playlist_data)
                        )

                    clear_action = menu.addAction("Clear Spotify Playlist Tracks")
                    if clear_action:
                        clear_action.setEnabled(can_modify_items)
                        clear_action.triggered.connect(partial(spotify_handler.clearSpotifyPlaylist, first_playlist_data))

                    menu.addSeparator()

                    remove_action = menu.addAction("Remove from Spotify Library")
                    if remove_action:
                        remove_action.triggered.connect(partial(spotify_handler.unfollowSpotifyPlaylist, first_playlist_data))

                    menu.addSeparator()
                 
                link_action = menu.addAction(f"Link All Tracks in Playlist{plural_s}")
                if link_action:
                    # Wrapper to update UI to "Queued" immediately before submitting job
                    def queue_and_link(playlists_data):
                        if not self.task_queue_manager:
                            return
                        for p in playlists_data:
                            pid = p.get('data', {}).get('id')
                            if pid:
                                self.set_playlist_queued(pid)
                        self.task_queue_manager.add_spotify_link_job(playlists_data)

                    link_action.triggered.connect(
                        partial(queue_and_link, spotify_playlists_data)
                    )

                download_menu = menu.addMenu(f"Download Playlist{plural_s} As...")
                if download_menu:
                    download_menu.setStyleSheet(MENU_STYLESHEET)

                    # Wrapper to update UI to "Queued" immediately before submitting download jobs
                    def queue_and_download_spotify(playlists_data, quality):
                        if not self.task_queue_manager:
                            return
                        for p in playlists_data:
                            pid = p.get('data', {}).get('id')
                            if pid:
                                self.set_playlist_queued(str(pid))
                        self.task_queue_manager.add_spotify_download_job(playlists_data, quality)

                    def queue_and_download_spotify_non_completed(playlists_data, quality):
                        if not self.task_queue_manager:
                            return
                        for p in playlists_data:
                            pid = p.get('data', {}).get('id')
                            if pid:
                                self.set_playlist_queued(str(pid))
                        self.task_queue_manager.add_spotify_download_job(
                            playlists_data,
                            quality,
                            non_completed_only=True,
                        )

                    dlQualities = DOWNLOAD_QUALITY_MENU_ITEMS
                    for text, quality_enum in dlQualities:
                        action = download_menu.addAction(text)
                        if action:
                            action.triggered.connect(
                                partial(queue_and_download_spotify, spotify_playlists_data, quality_enum)
                            )

                    download_non_completed_menu = menu.addMenu(
                        f"Download non-completed Playlist{plural_s} As..."
                    )
                    if download_non_completed_menu:
                        download_non_completed_menu.setStyleSheet(MENU_STYLESHEET)
                        for text, quality_enum in dlQualities:
                            action = download_non_completed_menu.addAction(text)
                            if action:
                                action.triggered.connect(
                                    partial(
                                        queue_and_download_spotify_non_completed,
                                        spotify_playlists_data,
                                        quality_enum,
                                    )
                                )
        
        self._popup_context_menu(menu, global_pos)
        _finish_menu_build_timing()

    # --- Sorting ---
    def _sortTidalPlaylists(self, sort_key: str) -> None:
        if not self.tidal_root_item:
            return

        was_expanded = self.tidal_root_item.isExpanded()
        child_items = []
        for i in range(self.tidal_root_item.childCount()):
            child_items.append(self.tidal_root_item.child(i))

        if not child_items:
            return

        if sort_key == "alpha":
            child_items.sort(key=lambda item: self._get_name_from_item(item).lower())
        elif sort_key == "created":
            def get_sort_val_created(item_widget: QTreeWidgetItem) -> datetime.datetime:
                data = item_widget.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict):
                    dt_val = data.get("created_date")
                    if isinstance(dt_val, datetime.datetime):
                        return dt_val
                return datetime.datetime.min
            child_items.sort(key=get_sort_val_created, reverse=True)
        elif sort_key == "updated":
            def get_sort_val_updated(item_widget: QTreeWidgetItem) -> datetime.datetime:
                data = item_widget.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict):
                    dt_val = data.get("updated_date")
                    if isinstance(dt_val, datetime.datetime):
                        return dt_val
                return datetime.datetime.min
            child_items.sort(key=get_sort_val_updated, reverse=True)
        else:
            return

        self.tidal_root_item.takeChildren()
        for item in child_items:
            self.tidal_root_item.addChild(item)
        self.tidal_root_item.setExpanded(was_expanded)

    def _sortSpotifyPlaylists(self, sort_key: str) -> None:
        if not self.spotify_root_item:
            return

        if sort_key not in {"default", "alpha"}:
            Printf.warning(
                f"Cannot sort Spotify playlists by '{sort_key}'. Supported modes are default and alphabetical."
            )
            return

        self._spotify_sort_mode = sort_key

        if not self._spotify_playlist_cache:
            logger.info("Spotify playlist sort mode changed to %s, but no cached playlists are loaded.", self._spotify_sort_label())
            return

        self.populate_spotify_playlists(self._spotify_playlist_cache, refresh_cache=False)
        logger.info("Spotify playlists sorted by %s.", self._spotify_sort_label())

    # --- Icon Loading ---
    def _start_icon_fetch(
        self,
        item: QTreeWidgetItem,
        service: str,
        item_id: Optional[str],
        image_url: Optional[str],
    ) -> None:
        item_name = self._get_name_from_item(item)
        if not item_id:
            return
        
        if service != "tidal" and not image_url:
            if not self.default_playlist_icon.isNull():
                item.setIcon(0, self.default_playlist_icon)
            return

        try:
            worker = CoverArtWorker(
                url=image_url,
                cache=self.cover_cache,
                type=service,
                item_id=item_id,
                item_name=item_name,
            )
            worker.signals.cover_ready.connect(self._set_playlist_icon_from_worker)
            worker.signals.error.connect(self._handle_icon_error_from_worker)

            thread_pool = QThreadPool.globalInstance()
            if thread_pool:
                thread_pool.start(worker)
            else:
                if not self.default_playlist_icon.isNull():
                    item.setIcon(0, self.default_playlist_icon)
        except Exception as e:
            logger.error(
                f"Error submitting icon worker for {service} item '{item_name}': {e}",
                exc_info=True,
            )
            if not self.default_playlist_icon.isNull():
                item.setIcon(0, self.default_playlist_icon)

    @pyqtSlot(str, QPixmap)
    def _set_playlist_icon_from_worker(self, url: str, pixmap: QPixmap) -> None:
        if not url:
            return

        for root in [self.tidal_root_item, self.spotify_root_item]:
            iterator = QtWidgets.QTreeWidgetItemIterator(root)
            while iterator.value():
                child = iterator.value()
                if child:
                    item_data = child.data(0, QtCore.Qt.ItemDataRole.UserRole) if child else None
                else:
                    item_data = None
                if not isinstance(item_data, dict):
                    iterator += 1
                    continue

                service = item_data.get("type")
                match = False

                if service == 'tidal':
                    playlist_obj = item_data.get("data")
                    if playlist_obj is not None and isinstance(playlist_obj, Playlist):
                        playlist_uuid = getattr(playlist_obj, 'uuid', '')
                        if playlist_uuid:
                            collage_key = f"tidal_playlist_{playlist_uuid}"
                            if url == collage_key:
                                match = True
                        elif item_data.get("image_url") == url:
                            match = True
                elif service == 'spotify':
                    if item_data.get("image_url") == url:
                        match = True

                if match:
                    widget = self.tree_widget.itemWidget(child, 0)
                    if isinstance(widget, PlaylistItemProgressWidget):
                        icon = QIcon(pixmap)
                        if not icon.isNull():
                            widget.set_icon(icon)
                            
                            item_data = child.data(0, QtCore.Qt.ItemDataRole.UserRole) if child else None
                            if item_data is not None and isinstance(item_data, dict):
                                if item_data.get("type") == "tidal":
                                    playlist_obj = item_data.get("data")
                                    if playlist_obj is not None and isinstance(playlist_obj, Playlist):
                                        playlist_uuid = getattr(playlist_obj, 'uuid', '')
                                        if playlist_uuid and str(playlist_uuid) in self.original_item_data:
                                            self.original_item_data[str(playlist_uuid)]["icon"] = icon
                                elif item_data.get("type") == "spotify":
                                    playlist_data = item_data.get("data")
                                    if playlist_data is not None:
                                        playlist_id = playlist_data.get("id")
                                        if playlist_id and str(playlist_id) in self.original_item_data:
                                            self.original_item_data[str(playlist_id)]["icon"] = icon
                    return
                
                iterator += 1

    @pyqtSlot(str, str)
    def _handle_icon_error_from_worker(self, url: str, error_message: str) -> None:
        logger.warning(f"Failed to fetch icon for URL '{url}': {error_message}")

    @pyqtSlot()
    def _loadVisibleSpotifyIcons(self) -> None:
        if self.spotify_root_item.isHidden() or not self.spotify_root_item.isExpanded():
            return

        viewport = self.tree_widget.viewport()
        if not viewport:
            return
        viewport_rect = viewport.rect()

        for i in range(self.spotify_root_item.childCount()):
            child_item = self.spotify_root_item.child(i)
            if not child_item:
                continue

            item_data = child_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if not isinstance(item_data, dict):
                continue
                
            if item_data.get("type") == "spotify":
                needs_refresh = item_data.get("icon_needs_refresh", True)
                is_pending = item_data.get("icon_update_pending", False)

                if needs_refresh and not is_pending:
                    image_url = item_data.get("image_url")
                    item_rect = self.tree_widget.visualItemRect(child_item)
                    if viewport_rect.intersects(item_rect):
                        if image_url:
                            item_data["icon_update_pending"] = True
                            child_item.setData(
                                0, QtCore.Qt.ItemDataRole.UserRole, item_data
                            )
                            playlist_data = item_data.get("data")
                            playlist_id = None
                            if playlist_data is not None:
                                playlist_id = playlist_data.get("id")
                            self._start_icon_fetch(child_item, "spotify", playlist_id, image_url)
                        else:
                            if (
                                not item_data.get("icon_displayed_from_cache", False)
                                and not self.default_playlist_icon.isNull()
                            ):
                                child_item.setIcon(0, self.default_playlist_icon)
                else:
                    continue
                    
            try:
                # Use viewport.update(QRect) to avoid ambiguity with QAbstractItemView.update(QModelIndex)
                rect = self.tree_widget.visualItemRect(child_item)
                # Use the local 'viewport' variable which is already checked for None
                viewport.update(rect)
            except (AttributeError, RuntimeError):
                pass

    @pyqtSlot(int)
    def _onSpotifyScroll(self, value: int) -> None:
        self._loadVisibleSpotifyIcons()
        self._schedule_visible_download_count_scan()

    # --- Job Status Updates ---
    @pyqtSlot(str, str, int)
    def on_job_started(self, playlist_id: str, action: str, total: int):
        """Slot to update a playlist item when a job starts."""
        logger.debug(f"JOB_STARTED: Starting job for playlist {playlist_id}")
        
        self.active_job_actions[playlist_id] = action
        self.active_job_totals[playlist_id] = total
        
        if playlist_id in self.item_widgets:
            widget = self.item_widgets[playlist_id]
            widget.set_progress(0, total, action)
            
            # Ensure item is visible
            if playlist_id in self.id_to_item:
                item = self.id_to_item[playlist_id]
                parent = item.parent()
                if parent and not parent.isExpanded():
                    parent.setExpanded(True)
                self.tree_widget.scrollToItem(item)

    @pyqtSlot(str, int)
    def on_job_progress(self, playlist_id: str, current: int):
        """Slot to update progress on a playlist item."""
        if playlist_id in self.item_widgets:
            widget = self.item_widgets[playlist_id]
            action = self.active_job_actions.get(playlist_id, "Processing")
            total = self.active_job_totals.get(playlist_id, 0)
            widget.set_progress(current, total, action)

    @pyqtSlot(str)
    def on_job_finished(self, playlist_id: str):
        """Slot to reset a playlist item when a job finishes."""
        logger.debug(f"JOB_FINISHED: Playlist {playlist_id}")
        
        if playlist_id in self.active_job_actions:
            del self.active_job_actions[playlist_id]
        
        if playlist_id in self.active_job_totals:
            del self.active_job_totals[playlist_id]
            
        if playlist_id in self.item_widgets:
            widget = self.item_widgets[playlist_id]
            widget.reset_state()
            
            # Restore original icon if available
            if playlist_id in self.original_item_data:
                original_data = self.original_item_data[playlist_id]
                if "icon" in original_data:
                    widget.set_icon(original_data["icon"])

        self._invalidate_playlist_download_count(playlist_id)
        self._schedule_visible_download_count_scan()

    @pyqtSlot(str)
    def set_playlist_queued(self, playlist_id: str):
        """Sets the playlist item status to 'Queued'."""
        if playlist_id in self.item_widgets:
            widget = self.item_widgets[playlist_id]
            widget.set_queued()
            
            # Keep the parent expanded, but avoid aggressive auto-scrolling for large multi-select queues.
            if playlist_id in self.id_to_item:
                item = self.id_to_item[playlist_id]
                parent = item.parent()
                if parent and not parent.isExpanded():
                    parent.setExpanded(True)

    @pyqtSlot(str)
    def _on_widget_geometry_request(self, playlist_id: str):
        """
        Slot called when a widget requests a geometry update (e.g. expanded/collapsed).
        Forces the tree item to resize to fit the widget's new size.
        """
        normalized_playlist_id = str(playlist_id or "").strip()
        if not normalized_playlist_id:
            return

        self._pending_geometry_playlist_ids.add(normalized_playlist_id)
        if not self._geometry_update_timer.isActive():
            self._geometry_update_timer.start()

    @pyqtSlot()
    def _flush_pending_geometry_updates(self) -> None:
        if not self._pending_geometry_playlist_ids:
            return

        pending_ids = list(self._pending_geometry_playlist_ids)
        self._pending_geometry_playlist_ids.clear()

        updated_any = False
        for playlist_id in pending_ids:
            if playlist_id not in self.id_to_item or playlist_id not in self.item_widgets:
                continue

            item = self.id_to_item[playlist_id]
            widget = self.item_widgets[playlist_id]

            try:
                item.setSizeHint(0, widget.sizeHint())
                updated_any = True
            except RuntimeError:
                self.id_to_item.pop(playlist_id, None)
                self.item_widgets.pop(playlist_id, None)
                self.original_item_data.pop(playlist_id, None)

        if updated_any:
            self.tree_widget.doItemsLayout()  # type: ignore
            try:
                viewport = self.tree_widget.viewport()
                if viewport is not None:
                    viewport.update()
            except (AttributeError, RuntimeError):
                pass


