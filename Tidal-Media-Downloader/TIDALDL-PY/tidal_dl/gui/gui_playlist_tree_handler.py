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

import logging
import os
import sys
from functools import partial
import threading
import datetime
from typing import TYPE_CHECKING, List, Dict, Optional, Any, cast

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

    def paint(
        self,
        painter: QtGui.QPainter | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if painter is None:
            return

        painter.save()

        # +++ START: CUSTOM BACKGROUND FOR FOLDER CHILDREN +++
        tree_widget = cast(QTreeWidget, self.parent())
        if isinstance(tree_widget, QTreeWidget):
            item = tree_widget.itemFromIndex(index)
            if item:
                item_data = item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(item_data, dict) and item_data.get("is_folder_child"):
                    painter.fillRect(option.rect, self.folder_child_background_color)
        # +++ END: CUSTOM BACKGROUND +++

        # Draw custom hover background for the text area
        if option.state & QtWidgets.QStyle.StateFlag.State_MouseOver:
            view_widget = cast(QTreeWidget, self.parent())
            if isinstance(view_widget, QWidget):
                style = QApplication.style()
                if style:
                    text_rect = style.subElementRect(
                        QtWidgets.QStyle.SubElement.SE_ItemViewItemText,
                        option,
                        view_widget,
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
        original_hint = super().sizeHint(option, index)

        tree_widget = cast(QTreeWidget, self.parent())
        if isinstance(tree_widget, QTreeWidget):
            item = tree_widget.itemFromIndex(index)
            if item:
                widget = tree_widget.itemWidget(item, 0)
                if widget:
                    return QSize(original_hint.width(), widget.sizeHint().height())
        
        return original_hint


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

        self.id_to_item: Dict[str, QTreeWidgetItem] = {}
        self.item_widgets: Dict[str, PlaylistItemProgressWidget] = {}
        self.original_item_data: Dict[str, Dict[str, Any]] = {}
        self.active_job_actions: Dict[str, str] = {}  # Track active job actions per playlist ID
        self.active_job_totals: Dict[str, int] = {}   # Track total items per playlist ID

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
        scrollbar = self.tree_widget.verticalScrollBar()
        if scrollbar:
            scrollbar.valueChanged.connect(self._onSpotifyScroll)
        
        self.filter_input_widget.textChanged.connect(self._apply_playlist_filter)

    # --- Helper Methods ---
    def _get_name_from_item(self, item: QTreeWidgetItem) -> str:
        """Safely retrieves the name of the playlist item, checking for custom widget."""
        widget = self.tree_widget.itemWidget(item, 0)
        if isinstance(widget, PlaylistItemProgressWidget):
            return widget.name_label.text()
        return item.text(0)

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

                self.original_item_data[str(playlist_uuid)] = {
                    "text": playlist_name,
                    "icon": self.default_music_icon
                }
                
                widget = PlaylistItemProgressWidget(playlist_name)
                self.tree_widget.setItemWidget(item, 0, widget)
                self.id_to_item[str(playlist_uuid)] = item
                self.item_widgets[str(playlist_uuid)] = widget
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

    # --- Spotify Playlist Handling ---
    @pyqtSlot(list)
    def populate_spotify_playlists(self, playlists: List[Dict[str, Any]]) -> None:
        try:
            while self.spotify_root_item.childCount() > 0:
                self.spotify_root_item.removeChild(self.spotify_root_item.child(0))

            playlist_count = len(playlists)
            self.spotify_root_item.setText(0, f"Spotify Playlists ({playlist_count})")
            self.spotify_root_item.setHidden(False)

            folder_items: Dict[str, QTreeWidgetItem] = {}
            widget_count = 0

            for p_data in playlists:
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

                            new_folder_item.setData(0, Qt.ItemDataRole.UserRole, {"is_folder": True})
                            folder_items[folder_name] = new_folder_item
                        
                        parent_item = folder_items[folder_name]

                item = QTreeWidgetItem(parent_item)
                playlist_id = p_data.get("id")
                if not playlist_id:
                    continue

                item_text = f"{playlist_name} ({p_data.get('tracks_total', '?')})"
                
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
            if playlists:
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
        self, logged_in: Optional[bool], error: bool = False
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
                elif logged_in is None:
                    self.spotify_root_item.setText(0, "Spotify (Loading...)")
                    self.spotify_root_item.setHidden(False)
                    if connect_button:
                        connect_button.setVisible(False)
                elif logged_in:
                    self.spotify_root_item.setHidden(False)
                    if connect_button:
                        connect_button.setVisible(False)
                    self.spotify_root_item.setExpanded(True)
                else:
                    self.spotify_root_item.setHidden(True)
                    if connect_button:
                        connect_button.setVisible(True)

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

        menu.popup(global_pos)

    def showSpotifyPlaylistMenu(self, global_pos: QPoint) -> None:
        menu = QMenu(self.tree_widget)
        menu.setStyleSheet(MENU_STYLESHEET)
        
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

        menu.addSeparator()
        sort_menu = menu.addMenu("Sort by")
        if sort_menu:
            sort_menu.setStyleSheet(MENU_STYLESHEET)
            action_sort_alpha = sort_menu.addAction("Alphabetical")
            if action_sort_alpha:
                action_sort_alpha.triggered.connect(
                    lambda: self._sortSpotifyPlaylists("alpha")
                )

        menu.popup(global_pos)

    def _playlistItemContextMenu(self, items: List[QTreeWidgetItem], global_pos: QPoint) -> None:
        if not items:
            return

        menu = QMenu(self.tree_widget)
        menu.setStyleSheet(MENU_STYLESHEET)
        
        playlist_items = []
        for item in items:
            item_data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict) and not item_data.get("is_folder"):
                playlist_items.append(item)
        
        if not playlist_items:
            return

        num_selected = len(playlist_items)
        plural_s = "s" if num_selected > 1 else ""

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
                        dlQualities = [
                            ("M4a (Low - 96k)", AudioQuality.LOW),
                            ("M4a (High - 320k)", AudioQuality.HIGH),
                            ("MP3 (High - 320k)", AudioQuality.MP3),
                            ("FLAC (Lossless CD)", AudioQuality.LOSSLESS),
                            ("FLAC (Hi-Res)", AudioQuality.HI_RES_LOSSLESS),
                            ("Highest Available Quality", AudioQuality.HIGHEST),
                        ]
                        for text, quality_enum in dlQualities:
                            action = download_menu.addAction(text)
                            if action:
                                action.triggered.connect(
                                    partial(self.task_queue_manager.add_tidal_download_job, tidal_playlists, quality_enum)
                                )

            elif item_type == "spotify":
                spotify_playlists_data = [item.data(0, Qt.ItemDataRole.UserRole) for item in playlist_items]
                
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
                    dlQualities = [
                        ("M4a (Low - 96k)", AudioQuality.LOW),
                        ("M4a (High - 320k)", AudioQuality.HIGH),
                        ("MP3 (High - 320k)", AudioQuality.MP3),
                        ("FLAC (Lossless CD)", AudioQuality.LOSSLESS),
                        ("FLAC (Hi-Res)", AudioQuality.HI_RES_LOSSLESS),
                        ("Highest Available Quality", AudioQuality.HIGHEST),
                    ]
                    for text, quality_enum in dlQualities:
                        action = download_menu.addAction(text)
                        if action:
                            action.triggered.connect(
                                partial(self.task_queue_manager.add_spotify_download_job, spotify_playlists_data, quality_enum)
                            )
        
        menu.popup(global_pos)

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

        was_expanded = self.spotify_root_item.isExpanded()
        child_items = []
        for i in range(self.spotify_root_item.childCount()):
            child_items.append(self.spotify_root_item.child(i))

        if not child_items:
            return

        if sort_key == "alpha":
            child_items.sort(key=lambda item: self._get_name_from_item(item).lower())
        else:
            Printf.warning(
                f"Cannot sort Spotify playlists by '{sort_key}'. Only alphabetical sorting is currently supported for Spotify playlists."
            )
            return

        self.spotify_root_item.takeChildren()
        for item in child_items:
            self.spotify_root_item.addChild(item)
        self.spotify_root_item.setExpanded(was_expanded)

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

    @pyqtSlot(str)
    def set_playlist_queued(self, playlist_id: str):
        """Sets the playlist item status to 'Queued'."""
        if playlist_id in self.item_widgets:
            widget = self.item_widgets[playlist_id]
            widget.set_queued()
            
            # Ensure item is visible
            if playlist_id in self.id_to_item:
                item = self.id_to_item[playlist_id]
                parent = item.parent()
                if parent and not parent.isExpanded():
                    parent.setExpanded(True)
                self.tree_widget.scrollToItem(item)

    @pyqtSlot(str)
    def _on_widget_geometry_request(self, playlist_id: str):
        """
        Slot called when a widget requests a geometry update (e.g. expanded/collapsed).
        Forces the tree item to resize to fit the widget's new size.
        """
        if playlist_id in self.id_to_item and playlist_id in self.item_widgets:
            item = self.id_to_item[playlist_id]
            widget = self.item_widgets[playlist_id]
            
            # Update the size hint for the item based on the widget's new size
            item.setSizeHint(0, widget.sizeHint())
            
            # Force the tree to re-layout items to accommodate the new height
            self.tree_widget.doItemsLayout()  # type: ignore


