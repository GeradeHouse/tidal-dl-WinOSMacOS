# tidal_dl/gui/gui_playlist_tree_handler.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_playlist_tree_handler.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.0
import time # Added time for TTL check
@Desc    :   Manages the playlist QTreeWidget in the GUI.
"""

import logging
import os
from functools import partial
import time  # Added time for TTL check
import threading
import datetime  # Added datetime for parsing and sorting # --- Add cast ---
from typing import TYPE_CHECKING, List, Dict, Optional, Any

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
)  # <-- Added QTimer
from PyQt6.QtGui import (
    QIcon,
    QFont,
    QPainter,
    QPixmap,
    QColor,
    QBrush,
)  # Added QColor, QBrush
from PyQt6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QMenu,
    QWidget,
    QApplication,
    QPushButton,
    QLineEdit,
    QAbstractItemView, # Added this import
)  # Added QPushButton

# Import project components
from ..tidal import TIDAL_API, Type, Playlist, AudioQuality, Track
from ..printf import Printf
from .gui_utils import safeSetText
from ..cover_cache import PlaylistCoverCache
from .gui_cover_cache import CoverArtWorker, CoverCache
from ..settings import SETTINGS

from .gui_playlist_tree import PlaylistTreeWidget  # Import the new widget
from .gui_linking_handler import LinkingGuiHandler
from .gui_playlist_item_widget import PlaylistItemProgressWidget # MODIFIED: Import new widget
from typing import Any, cast  # Add cast

if TYPE_CHECKING:
    from .gui import MainView
    from .gui_download import DownloadHandler  # Import Download Handler
    from .task_queue_manager import TaskQueueManager # Import the new manager

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (more verbose GUI output for downloads)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

# --- Font Size Configuration for Playlist Items ---


# --- Custom Delegate for Playlist Tree Items ---
class PlaylistDelegate(QtWidgets.QStyledItemDelegate):
    """
    Custom delegate for playlist tree items.
    - Draws '...' button on root items and handles clicks.
    - Draws custom hover background only on the text area of items.
    """

    _BUTTON_TEXT = "..."  # Renamed for consistency, though not strictly necessary
    _BUTTON_WIDTH = 30
    _BUTTON_PADDING = 5

    def __init__(self, tree_handler: "PlaylistTreeHandler", parent_widget: QWidget):
        super().__init__(parent_widget)
        self.tree_handler = tree_handler
        self.hover_background_color = QColor("#3e3e43")  # Grey hover from QSS
        self.folder_child_background_color = QColor("#050507ED") # Subtle darker background for items in folders

    def paint(
        self,
        painter: QtGui.QPainter | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if painter is None:  # Add this check
            return
        # --- DEBUG ---
        # This initial log might show the font size *before* our delegate modification
        # tree_widget_for_log = self.parent()
        # if isinstance(tree_widget_for_log, QTreeWidget):
        #     item_for_log = tree_widget_for_log.itemFromIndex(index)
        #     if item_for_log and item_for_log not in [self.tree_handler.tidal_root_item, self.tree_handler.spotify_root_item]:
        #         logger.debug(f"[Delegate Paint PRE-MOD] Item: '{item_for_log.text(0)}', Option Font Size: {option.font.pointSizeF():.1f}, Item's Stored Font Size: {item_for_log.font(0).pointSizeF():.1f}")
        # --- END DEBUG ---

        # Save painter state
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

        # Prepare style option (important for default drawing)
        # self.initStyleOption(option, index) # Let Qt do this or do it if needed

        # Draw custom hover background for the text area
        # Corrected QStyle.StateFlag.State_MouseOver
        if option.state & QtWidgets.QStyle.StateFlag.State_MouseOver:
            # The widget for subElementRect should be the view (QTreeWidget)
            # Cast self.parent() to QTreeWidget for Pylance
            view_widget = cast(QTreeWidget, self.parent())
            if isinstance(view_widget, QWidget):
                # Calculate the rectangle for the text part of the item
                # This relies on the style correctly calculating where text is drawn.
                # QApplication.style() can be None, handle it.
                style = QApplication.style()
                if style:
                    text_rect = style.subElementRect(
                        QtWidgets.QStyle.SubElement.SE_ItemViewItemText,
                        option,
                        view_widget,
                    )
                    if text_rect.isValid():
                        # Draw rounded rectangle for hover background
                        painter.setBrush(QBrush(self.hover_background_color))
                        painter.setPen(
                            Qt.PenStyle.NoPen
                        )  # No outline for the rounded rect
                        painter.drawRoundedRect(
                            text_rect, 10.0, 6.0
                        )  # 10px corner radius

        # Let the base class draw the item content (icon, text, selection, etc.)
        # The QSS `color: white !important;` for hover should still apply to text.
        super().paint(painter, option, index)

        # --- Root Item Specific: Draw "..." button ---
        # This part is from the original RootItemDelegate
        if index.isValid():  # Ensure index is valid before proceeding
            # Corrected: self.parent() is the QTreeWidget
            # Cast self.parent() to QTreeWidget for Pylance
            tree_widget = cast(QTreeWidget, self.parent())
            if isinstance(tree_widget, QTreeWidget):
                item = tree_widget.itemFromIndex(index)
                if item:  # Ensure item is not None
                    is_root = (
                        item == self.tree_handler.tidal_root_item
                        or item == self.tree_handler.spotify_root_item
                    )
                    if is_root:
                        painter.save()  # Save painter state before drawing button
                        item_rect = option.rect  # Item's natural rectangle
                        button_x = (
                            item_rect.right()
                            - self._BUTTON_WIDTH
                            - self._BUTTON_PADDING
                        )
                        # Button uses the item's full height for vertical centering
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
                        font = item.font(0)  # Use item's font
                        font.setPointSize(
                            font.pointSize() + 3
                        )  # Make "..." slightly larger
                        painter.setFont(font)
                        painter.drawText(
                            button_rect_for_draw,
                            Qt.AlignmentFlag.AlignCenter,
                            self._BUTTON_TEXT,
                        )
                        painter.restore()  # Restore painter state
        painter.restore()

    def editorEvent(
        self,
        event: QEvent | None,
        model: QAbstractItemModel | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """Handles mouse clicks on the '...' button area."""
        # This part is from the original RootItemDelegate
        if (
            event
            and event.type() == QEvent.Type.MouseButtonRelease
            and isinstance(event, QtGui.QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
        ):

            # Corrected: self.parent() is the QTreeWidget
            # Cast self.parent() to QTreeWidget for Pylance
            tree_widget = cast(QTreeWidget, self.parent())
            if isinstance(tree_widget, QTreeWidget):
                item = tree_widget.itemFromIndex(index)
                if item:  # Ensure item is not None
                    is_tidal_root = item == self.tree_handler.tidal_root_item
                    is_spotify_root = item == self.tree_handler.spotify_root_item

                    if is_tidal_root or is_spotify_root:
                        item_rect = option.rect  # Item's natural rectangle
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
                                else:  # is_spotify_root
                                    self.tree_handler.showSpotifyPlaylistMenu(
                                        global_pos
                                    )
                                return True  # Event handled
                            else:
                                logger.error(
                                    "Could not get tree widget viewport for root item context menu."
                                )
                                return False

        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option: QtWidgets.QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Returns the size hint for the item, driven by its custom widget."""
        # First, get the default size hint from the base class
        original_hint = super().sizeHint(option, index)

        tree_widget = cast(QTreeWidget, self.parent())
        if isinstance(tree_widget, QTreeWidget):
            item = tree_widget.itemFromIndex(index)
            if item:
                # Check if there is a custom widget on this item
                widget = tree_widget.itemWidget(item, 0)
                if widget:
                    # If a widget exists, its size hint is the authority.
                    # We only care about its height.
                    return QSize(original_hint.width(), widget.sizeHint().height())
        
        # If no widget, return the default size
        return original_hint


# --- Playlist Tree Handler ---
class PlaylistTreeHandler(QObject):
    """
    Manages the playlist QTreeWidget, including population, interaction,
    context menus, sorting, and icon loading coordination.
    """

    # Signals
    tidalPlaylistSelected = pyqtSignal(Playlist)  # Emits Tidal Playlist object
    spotifyPlaylistSelected = pyqtSignal(dict)  # Emits Spotify playlist data dict
    requestTidalPlaylistDownload = pyqtSignal(
        list, AudioQuality
    )  # MODIFIED: Send list of playlists

    def __init__(
        self,
        playlist_tree_widget: PlaylistTreeWidget,
        cover_cache: CoverCache,  # Use the new singleton cache
        parent: "MainView",
    ) -> None:
        super().__init__(parent)
        self.main_view = parent
        self.playlist_tree_widget = playlist_tree_widget  # Store the container widget
        self.tree_widget: QTreeWidget = (
            playlist_tree_widget.tree_widget
        )  # Reference the actual QTreeWidget
        self.spotify_connect_button: QPushButton = (
            playlist_tree_widget.spotify_connect_button
        )  # Reference the button
        self.cover_cache = cover_cache # Use the passed-in singleton instance
        self.filter_input_widget: QLineEdit = (
            playlist_tree_widget.get_filter_input_widget()
        )  # Store reference to filter input
        self.download_handler: Optional["DownloadHandler"] = None
        self.linking_handler: Optional[LinkingGuiHandler] = None
        self.task_queue_manager: Optional["TaskQueueManager"] = None

        # MODIFIED: Add dictionaries to map IDs to items and widgets
        self.id_to_item: Dict[str, QTreeWidgetItem] = {}
        self.item_widgets: Dict[str, PlaylistItemProgressWidget] = {}

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
                if self.default_playlist_icon.isNull():
                    logger.warning(
                        f"Default playlist icon loaded but isNull() is true: {default_icon_path}"
                    )
            else:
                logger.warning(
                    f"Default playlist icon file not found at: {default_icon_path}"
                )
        except Exception as e:
            logger.error(f"Error loading default playlist icon: {e}", exc_info=True)
        
        # +++ Load Folder Icon (with manual scaling) +++
        self.folder_icon = QIcon()
        try:
            folder_icon_path = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__), "..", "assets", "icons", "folder-white.png"
                )
            )
            if os.path.exists(folder_icon_path):
                # Load raw pixmap
                base_pixmap = QPixmap(folder_icon_path)

                # Choose how big the folder should *look* inside the tree row
                # Change (20, 20) to whatever visual size you want
                scaled_pixmap = base_pixmap.scaled(
                    14, 14,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

                # Store the scaled icon
                self.folder_icon = QIcon(scaled_pixmap)
            else:
                logger.warning(f"Folder icon file not found at: {folder_icon_path}")
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
        """Sets the download handler for triggering downloads from context menus."""
        self.download_handler = handler

    def _setup_tree_widget(self) -> None:
        """Configures the QTreeWidget properties."""
        self.tree_widget.setColumnCount(1)
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setAnimated(True)
        
        # +++ Set initial icon size from settings +++
        icon_size = SETTINGS.playlistIconSize if SETTINGS.showPlaylistIcons else 0
        self.tree_widget.setIconSize(QSize(icon_size, icon_size))

        self.tree_widget.setRootIsDecorated(True)
        self.tree_widget.setSortingEnabled(False)
        self.tree_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_widget.setIndentation(15) # +++ INCREASED INDENTATION +++
        self.tree_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection) # Enable multi-select

        # Apply custom delegate for drawing root item buttons
        self.playlist_delegate = PlaylistDelegate(
            self, self.tree_widget
        )  # Use the new combined delegate
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
            # Resize root icon specifically - now 25x25
            pixmap = tidal_icon.pixmap(QSize(25, 25))
            self.tidal_root_item.setIcon(0, QIcon(pixmap))
        else:
            logger.warning(f"Tidal root icon not found at: {tidal_icon_path}")
        # self.tidal_root_item.setProperty("isRootItem", True) # type: ignore # REMOVE THIS LINE
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
            # Resize root icon specifically - now 25x25
            pixmap = spotify_icon.pixmap(QSize(25, 25))
            self.spotify_root_item.setIcon(0, QIcon(pixmap))
        else:
            logger.warning(f"Spotify root icon not found at: {spotify_icon_path}")
        # self.spotify_root_item.setProperty("isRootItem", True) # type: ignore # REMOVE THIS LINE
        self.spotify_root_item.setFlags(
            self.spotify_root_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
        )
        self.spotify_root_item.setHidden(True)

        self.tree_widget.update()

    def set_linking_handler(self, handler: LinkingGuiHandler) -> None:
        """Sets the reference to the LinkingGuiHandler."""
        self.linking_handler = handler

    def _connect_tree_signals(self) -> None:
        """Connects signals from the QTreeWidget."""
        self.tree_widget.itemClicked.connect(self.onPlaylistItemClicked)
        self.tree_widget.customContextMenuRequested.connect(self._handleTreeContextMenu)
        self.tree_widget.itemExpanded.connect(self._loadVisibleSpotifyIcons)
        self.tree_widget.itemCollapsed.connect(self._loadVisibleSpotifyIcons) # Also check on collapse
        scrollbar = self.tree_widget.verticalScrollBar()
        if scrollbar:
            scrollbar.valueChanged.connect(self._onSpotifyScroll)
            logger.debug(
                "Connected tree vertical scrollbar valueChanged to _onSpotifyScroll."
            )
        else:
            logger.warning(
                "Could not get vertical scrollbar for tree_widget to connect signal."
            )

        self.filter_input_widget.textChanged.connect(
            self._apply_playlist_filter
        )  # Connect filter input

    # --- Tidal Playlist Handling ---
    @pyqtSlot()
    def refreshTidalPlaylists(self) -> None:
        """Fetches and displays the user's TIDAL playlists."""
        logger.debug("Refreshing TIDAL playlists...")
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
        """Worker thread to fetch Tidal playlists."""
        playlists: List[Playlist] = []
        error_msg: Optional[str] = None
        try:
            playlists = TIDAL_API.getPlaylistSelf()
        except Exception as e:
            error_msg = f"Error fetching TIDAL playlists: {e}"
            logger.error(error_msg, exc_info=True)
            logger.error(error_msg)

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
        """Updates the tree widget with fetched Tidal playlists (runs on main thread)."""
        logger.debug(
            f"Updating Tidal playlist tree. Playlists: {len(playlists)}, Error: {error_msg}"
        )
        playlist_count = len(playlists)

        if error_msg:
            self.tidal_root_item.setText(0, "Tidal Playlists (Error)")
        else:
            self.tidal_root_item.setText(0, f"Tidal Playlists ({playlist_count})")

        if playlists:
            for playlist_summary in playlists:
                item = QTreeWidgetItem(self.tidal_root_item)

                # MODIFIED: Use custom widget instead of setText
                playlist_name = getattr(playlist_summary, "title", "Untitled Playlist")
                playlist_uuid = getattr(playlist_summary, "uuid", None)
                if not playlist_uuid:
                    continue

                widget = PlaylistItemProgressWidget(playlist_name)
                self.tree_widget.setItemWidget(item, 0, widget)
                self.id_to_item[str(playlist_uuid)] = item
                self.item_widgets[str(playlist_uuid)] = widget
                
                # MODIFICATION: Set default icon on the WIDGET
                widget.set_icon(self.default_music_icon)

                font_child = item.font(0)
                font_child.setFamily("Nationale")
                font_child.setWeight(QFont.Weight.Medium)  # Revert to Medium for Tidal
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
                    except ValueError as e:
                        logger.warning(
                            f"Could not parse created_date string '{created_date_str}': {e}"
                        )

                updated_dt = None
                if updated_date_str:
                    try:
                        if isinstance(updated_date_str, datetime.datetime):
                            updated_dt = updated_date_str
                        elif isinstance(updated_date_str, str):
                            updated_dt = datetime.datetime.fromisoformat(
                                updated_date_str.replace("Z", "+00:00")
                            )
                    except ValueError as e:
                        logger.warning(
                            f"Could not parse updated_date string '{updated_date_str}': {e}"
                        )
                
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

        logger.info("TIDAL playlists refreshed.")
        self._apply_playlist_filter()  # Apply filter after populating

    # --- Spotify Playlist Handling ---
    @pyqtSlot(list)
    def populate_spotify_playlists(self, playlists: List[Dict[str, Any]]) -> None:
        """Populates the tree with fetched Spotify playlists."""
        logger.debug(
            f"Populating Spotify playlist tree with {len(playlists)} playlists."
        )
        try:
            while self.spotify_root_item.childCount() > 0:
                self.spotify_root_item.removeChild(self.spotify_root_item.child(0))

            playlist_count = len(playlists)
            self.spotify_root_item.setText(0, f"Spotify Playlists ({playlist_count})")
            self.spotify_root_item.setHidden(False)

            # Dictionary to keep track of created folder items to avoid duplicates
            folder_items: Dict[str, QTreeWidgetItem] = {}

            for p_data in playlists:
                if not (isinstance(p_data, dict) and "name" in p_data and "id" in p_data):
                    continue

                playlist_name = p_data.get('name', 'Unknown Name')
                parent_item = self.spotify_root_item  # Default parent is the root
                is_folder_child = False

                # If the setting is enabled and the name contains our separator...
                if SETTINGS.spotifyUsePlaylistFolders and ' - ' in playlist_name:
                    # Split only on the first occurrence to get the folder name
                    folder_name = playlist_name.split(' - ', 1)[0].strip()
                    
                    if folder_name: # Proceed only if the folder name is not empty
                        is_folder_child = True
                        # Check if we've already created a folder item for this name
                        if folder_name not in folder_items:
                            # If not, create a new folder item
                            new_folder_item = QTreeWidgetItem(self.spotify_root_item)
                            new_folder_item.setText(0, folder_name)
                            
                            # Style the folder to make it look distinct (e.g., bold)
                            font = new_folder_item.font(0)
                            font.setWeight(QFont.Weight.Bold)
                            new_folder_item.setFont(0, font)
                            
                            # Set the folder icon
                            if SETTINGS.showPlaylistIcons and not self.folder_icon.isNull():
                                new_folder_item.setIcon(0, self.folder_icon)

                            # Store data to identify this as a folder item
                            new_folder_item.setData(0, Qt.ItemDataRole.UserRole, {"is_folder": True})
                            
                            # Store it in our dictionary
                            folder_items[folder_name] = new_folder_item
                        
                        # Set the parent for the actual playlist item to be this folder
                        parent_item = folder_items[folder_name]

                # Create the playlist item with the correct parent (either root or a folder)
                item = QTreeWidgetItem(parent_item)
                
                # MODIFIED: Use custom widget instead of setText
                playlist_id = p_data.get("id")
                if not playlist_id:
                    continue

                item_text = f"{playlist_name} ({p_data.get('tracks_total', '?')})"
                widget = PlaylistItemProgressWidget(item_text)
                self.tree_widget.setItemWidget(item, 0, widget)
                self.id_to_item[str(playlist_id)] = item
                self.item_widgets[str(playlist_id)] = widget

                font_child_spotify = item.font(0)
                font_child_spotify.setFamily("Nationale")
                font_child_spotify.setWeight(QFont.Weight.Normal)
                item.setFont(0, font_child_spotify)

                image_url = None
                images = p_data.get("images", [])
                if images:
                    image_url = images[-1].get("url")

                item_data_dict: Dict[str, Any] = {
                    "type": "spotify",
                    "data": p_data,
                    "image_url": image_url,
                    "icon_update_pending": False,
                    "is_folder_child": is_folder_child, # Mark if it's in a folder for styling
                }
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, item_data_dict)

                if SETTINGS.showPlaylistIcons:
                    if image_url:
                        cached_pixmap = self.cover_cache.get(image_url)
                        if cached_pixmap:
                            # MODIFICATION: Set icon on the WIDGET
                            widget.set_icon(QIcon(cached_pixmap))
                        else:
                            # MODIFICATION: Set default icon on the WIDGET
                            widget.set_icon(self.default_playlist_icon)
                            self._start_icon_fetch(item, "spotify", p_data.get("id"), image_url)
                    else:
                        # MODIFICATION: Set default icon on the WIDGET
                        widget.set_icon(self.default_music_icon)

            self._loadVisibleSpotifyIcons()
            if playlists:
                def _expand_spotify_root():
                    if self.spotify_root_item:
                        logger.debug(
                            f"Spotify root item expanded state BEFORE deferred setExpanded: {self.spotify_root_item.isExpanded()}"
                        )
                        self.spotify_root_item.setExpanded(True)
                        # Also expand all folder items
                        for folder_item in folder_items.values():
                            folder_item.setExpanded(True)
                        logger.debug(
                            f"Spotify root item expanded state AFTER deferred setExpanded: {self.spotify_root_item.isExpanded()}"
                        )
                    else:
                        logger.warning(
                            "Attempted deferred expansion, but spotify_root_item is None."
                        )
                QTimer.singleShot(0, _expand_spotify_root)
            self._apply_playlist_filter()

        except Exception as e:
            logger.error(f"Error updating Spotify playlist tree: {e}", exc_info=True)
            logger.error(f"GUI Error displaying Spotify playlists: {e}")
            self.update_spotify_root_item(logged_in=False, error=True)

    def update_spotify_root_item(
        self, logged_in: Optional[bool], error: bool = False
    ) -> None:
        """Updates the appearance and visibility of the Spotify root item."""

        # Define the update logic as a separate function or lambda
        # This function will be called by QTimer.singleShot
        def _do_update():
            logger.debug(
                f"Executing deferred UI update for spotify_root_item. logged_in={logged_in}, error={error}"
            )
            # Use the reference stored in the handler
            connect_button = self.spotify_connect_button

            # Check if the root item itself is valid before proceeding
            if not self.spotify_root_item:
                logger.error(
                    "spotify_root_item is None in _do_update. Cannot update UI."
                )
                return

            try:  # Add try-except around UI operations
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
                elif logged_in:  # This is the path taken (auth_result is True)
                    # No need to set text if just showing
                    self.spotify_root_item.setHidden(
                        False
                    )  # Makes the root item visible
                    logger.debug("Called self.spotify_root_item.setHidden(False)")
                    if connect_button:
                        connect_button.setVisible(False)  # Hides the connect button
                        logger.debug("Called connect_button.setVisible(False)")
                    self.spotify_root_item.setExpanded(
                        True
                    )  # Expand when logged in and visible
                    logger.debug(
                        f"Called self.spotify_root_item.setExpanded(True). Is expanded: {self.spotify_root_item.isExpanded()}"
                    )
                else:  # logged_in is False
                    self.spotify_root_item.setHidden(True)  # Hides the root item
                    logger.debug("Called self.spotify_root_item.setHidden(True)")
                    if connect_button:
                        connect_button.setVisible(True)  # Shows the connect button
                        logger.debug("Called connect_button.setVisible(True)")

                # Optional: Force layout update after changes if needed, but often not necessary
                # if self.tree_widget:
                #     self.tree_widget.updateGeometry()
                logger.debug("Deferred UI update for spotify_root_item completed.")

            except Exception as ui_update_error:
                # Log any error during the actual UI update
                logger.error(
                    f"Error during deferred UI update for spotify_root_item: {ui_update_error}",
                    exc_info=True,
                )

        # Schedule the update to run in the next event loop iteration (0 ms delay)
        QTimer.singleShot(0, _do_update)
        logger.debug("Scheduled UI update for spotify_root_item via QTimer.singleShot.")

    # --- Item Interaction ---
    @pyqtSlot(QTreeWidgetItem, int)
    def onPlaylistItemClicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handles clicks on items within the playlist tree widget."""
        if not item:
            return

        item_data = item.data(0, Qt.ItemDataRole.UserRole)

        # +++ START: FOLDER EXPANSION LOGIC +++
        # Check if the clicked item is a folder
        if isinstance(item_data, dict) and item_data.get("is_folder"):
            item.setExpanded(not item.isExpanded())
            return
        # +++ END: FOLDER EXPANSION LOGIC +++

        # Handle root item expansion
        if item in (self.tidal_root_item, self.spotify_root_item):
            item.setExpanded(not item.isExpanded())
            return

        item_name = self._get_name_from_item(item)
        logger.debug(
            f"Playlist item clicked: '{item_name}'. Data type: {type(item_data)}"
        )
        if item_data is None:
            logger.warning(
                f"Clicked playlist item '{item_name}' has no associated data."
            )
            logger.warning(f"Cannot process click on '{item_name}': Missing data.")
            return

        if hasattr(self.main_view, "table_handler"):
            self.main_view.table_handler.clear_table()
        else:
            logger.error("Table Handler not found on main_view.")

        setattr(self.main_view, "s_playlist_obj", item_data)
        setattr(self.main_view, "s_playlist", True)

        if self.linking_handler:
            self.linking_handler.update_link_button_state()
        else:
            logger.warning(
                "Linking handler not set in PlaylistTreeHandler, cannot update button state."
            )

        try:
            item_type = item_data.get("type", "tidal")
            if item_type == "spotify":
                playlist_data = item_data.get("data")
                playlist_id = None
                if playlist_data is not None:
                    playlist_id = playlist_data.get("id")
                if playlist_id:
                    logger.info(f"Spotify playlist selected: ID {playlist_id}")
                    self.spotifyPlaylistSelected.emit(item_data)
                    if hasattr(self.main_view, "spotify_gui_handler"):
                        self.main_view.spotify_gui_handler.fetchSpotifyTracks(
                            playlist_id
                        )
                    else:
                        logger.error("Spotify GUI Handler not found on main_view.")
                else:
                    logger.error(
                        f"Spotify playlist item clicked, but no ID found: {item_data}"
                    )
                    logger.error("Error loading Spotify playlist: Missing ID.")
                    setattr(self.main_view, "s_playlist_obj", None)

            elif item_type == "tidal":
                playlist_obj = item_data.get("data")
                if playlist_obj is not None and isinstance(playlist_obj, Playlist):
                    playlist_id = getattr(playlist_obj, "uuid", None)
                    logger.info(f"Tidal playlist selected: UUID {playlist_id}")
                    self.tidalPlaylistSelected.emit(playlist_obj)
                    self._displayTidalTracks(playlist_obj)
                else:
                    error_msg = f"Tidal playlist item clicked, but data invalid or None: {type(playlist_obj)}"
                    logger.error(error_msg)
                    logger.error(f"Error loading Tidal playlist: {error_msg}")
                    setattr(self.main_view, "s_playlist_obj", None)
            else:
                logger.warning(f"Unknown item type clicked: {item_type}")
                setattr(self.main_view, "s_playlist_obj", None)

        except Exception as e:
            error_msg = f"Error handling playlist item click for '{item_name}': {e}"
            logger.error(error_msg, exc_info=True)
            logger.error(f"Error processing playlist click: {e}")
            setattr(self.main_view, "s_playlist_obj", None)

    def _displayTidalTracks(self, playlist_obj: Playlist) -> None:
        """Fetches and displays tracks for a selected TIDAL playlist."""
        playlist_name = getattr(playlist_obj, "title", "Unknown Tidal Playlist")
        playlist_id = getattr(playlist_obj, "uuid", None)
        logger.debug(
            f"Fetching and displaying tracks for TIDAL playlist: '{playlist_name}' (UUID: {playlist_id})"
        )

        if not playlist_id:
            logger.error(f"Cannot display tracks for '{playlist_name}': Missing UUID.")
            if hasattr(self.main_view, "table_handler"):
                self.main_view.table_handler.show_error_message(
                    "Playlist missing identifier."
                )
            return

        if hasattr(self.main_view, "table_handler"):
            self.main_view.table_handler.show_loading_message(
                f"Loading tracks for '{playlist_name}'..."
            )
        else:
            logger.error("Table Handler not found on main_view.")
            return

        thread = threading.Thread(
            target=self._fetch_tidal_tracks_thread,
            args=(playlist_id, playlist_name),
            daemon=True,
        )
        thread.start()

    def _fetch_tidal_tracks_thread(self, playlist_id: str, playlist_name: str) -> None:
        """Worker thread to fetch Tidal tracks for a playlist."""
        tracks_full: List[Track] = []
        error_msg: Optional[str] = None
        try:
            logger.debug(f"Fetching TIDAL items for playlist ID: {playlist_id}")
            result = TIDAL_API.getItems(playlist_id, Type.Playlist)
            if result is None:
                raise ConnectionError(
                    f"API Error: getItems returned None for playlist ID {playlist_id}."
                )

            tracks_data, _ = result
            logger.info(
                f"Fetched {len(tracks_data) if tracks_data else 0} track items for TIDAL playlist {playlist_id}."
            )

            if tracks_data and isinstance(tracks_data[0], str):
                logger.info(f"Fetching details for {len(tracks_data)} tracks...")
                for t_idx, t_id_or_obj in enumerate(tracks_data):
                    track_id_str: Optional[str] = None
                    if isinstance(t_id_or_obj, Track):
                        if t_id_or_obj.id is not None:
                            track_id_str = str(t_id_or_obj.id)  # Ensure string
                    elif isinstance(t_id_or_obj, str):
                        track_id_str = t_id_or_obj

                    if track_id_str is None:
                        logger.warning(
                            f"Skipping track with invalid/None ID at index {t_idx}: {t_id_or_obj}"
                        )
                        continue  # Skip to next iteration if ID is None

                    # Now track_id_str is guaranteed to be a string if we reach here
                    track_obj = TIDAL_API.getTypeData(track_id_str, Type.Track)
                    if track_obj:
                        tracks_full.append(track_obj)
                    else:
                        # Use the confirmed string ID in the warning
                        logger.warning(
                            f"Could not get TIDAL track object for ID: {track_id_str}"
                        )
                    if (t_idx + 1) % 50 == 0:
                        logger.info(
                            f"Fetched details for {t_idx + 1}/{len(tracks_data)} tracks..."
                        )
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
            logger.error(error_msg)

        QtCore.QMetaObject.invokeMethod(
            self.main_view.table_handler,
            "populate_tidal_tracks",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(list, tracks_full),
            QtCore.Q_ARG(str, error_msg or ""),
        )

    # --- Context Menus ---
    @pyqtSlot(QPoint)
    def _handleTreeContextMenu(self, pos: QPoint) -> None:
        """Handles right-click context menu requests on the playlist tree."""
        selected_items = self.tree_widget.selectedItems()
        if not selected_items:
            return

        viewport = self.tree_widget.viewport()
        if not viewport:
            logger.error("Could not get tree widget viewport for context menu.")
            return
        
        global_pos = viewport.mapToGlobal(pos)
        
        # Check if a root item is part of the selection
        is_tidal_root_selected = any(item == self.tidal_root_item for item in selected_items)
        is_spotify_root_selected = any(item == self.spotify_root_item for item in selected_items)

        if is_tidal_root_selected:
            self.showTidalPlaylistMenu(global_pos)
        elif is_spotify_root_selected:
            self.showSpotifyPlaylistMenu(global_pos)
        else:
            self._playlistItemContextMenu(selected_items, global_pos)


    def showTidalPlaylistMenu(self, global_pos: QPoint) -> None:
        """Shows the context menu for the Tidal root item."""
        menu = QMenu(self.tree_widget)
        refresh_action = menu.addAction("Refresh Tidal Playlists")
        if refresh_action:  # Add this check
            refresh_action.triggered.connect(self.refreshTidalPlaylists)

        menu.addSeparator()
        sort_menu = menu.addMenu("Sort by")
        if sort_menu:  # Check if menu was created
            action_sort_created = sort_menu.addAction("Created Date")
            action_sort_updated = sort_menu.addAction("Updated Date")
            action_sort_alpha = sort_menu.addAction("Alphabetical")
            if action_sort_created:  # Check if action was created
                action_sort_created.triggered.connect(
                    lambda: self._sortTidalPlaylists("created")
                )
            if action_sort_updated:  # Check if action was created
                action_sort_updated.triggered.connect(
                    lambda: self._sortTidalPlaylists("updated")
                )
            if action_sort_alpha:  # Check if action was created
                action_sort_alpha.triggered.connect(
                    lambda: self._sortTidalPlaylists("alpha")
                )

        menu.popup(global_pos)

    def showSpotifyPlaylistMenu(self, global_pos: QPoint) -> None:
        """Shows the context menu for the Spotify root item."""
        menu = QMenu(self.tree_widget)
        refresh_action = menu.addAction("Refresh Spotify Playlists")
        if refresh_action:  # Check if action was created
            if hasattr(self.main_view, "spotify_gui_handler"):
                refresh_action.triggered.connect(
                    self.main_view.spotify_gui_handler.refreshSpotifyPlaylists
                )
                refresh_action.setEnabled(
                    self.main_view.spotify_gui_handler.spotify_api.sp is not None
                )
            else:
                refresh_action.setEnabled(False)
                logger.error("Spotify GUI Handler not found for context menu.")

        menu.addSeparator()
        sort_menu = menu.addMenu("Sort by")
        if sort_menu:  # Check if menu was created
            action_sort_alpha = sort_menu.addAction("Alphabetical")
            if action_sort_alpha:  # Add this check
                action_sort_alpha.triggered.connect(
                    lambda: self._sortSpotifyPlaylists("alpha")
                )

        menu.popup(global_pos)

    def _playlistItemContextMenu(self, items: List[QTreeWidgetItem], global_pos: QPoint) -> None:
        """Displays context menu for individual playlist items, supporting multi-select."""
        if not items:
            return

        menu = QMenu(self.tree_widget)
        
        # --- Filter out non-playlist items (like folders) ---
        playlist_items = []
        for item in items:
            item_data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict) and not item_data.get("is_folder"):
                playlist_items.append(item)
        
        if not playlist_items:
            return # No actual playlists selected

        num_selected = len(playlist_items)
        plural_s = "s" if num_selected > 1 else ""

        # --- Common Actions ---
        def do_copy():
            # MODIFIED: Get name from widget
            names = [self._get_name_from_item(item) for item in playlist_items]
            clipboard_text = "\n".join(filter(None, names))
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(clipboard_text)

        copy_action = menu.addAction(f"Copy Name{plural_s}")
        if copy_action:
            copy_action.triggered.connect(do_copy)

        # --- Service-Specific Actions ---
        first_item_data = playlist_items[0].data(0, Qt.ItemDataRole.UserRole)
        item_type = first_item_data.get("type", "tidal")
        
        # Ensure all selected items are of the same type
        all_same_type = all(item.data(0, Qt.ItemDataRole.UserRole).get("type") == item_type for item in playlist_items)

        if all_same_type and self.task_queue_manager:
            if item_type == "tidal":
                tidal_playlists = [item.data(0, Qt.ItemDataRole.UserRole).get("data") for item in playlist_items]
                tidal_playlists = [p for p in tidal_playlists if isinstance(p, Playlist)]
                
                if tidal_playlists:
                    download_menu = menu.addMenu(f"Download Playlist{plural_s} As...")
                    if download_menu:
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
                
                # Link Action
                link_action = menu.addAction(f"Link All Tracks in Playlist{plural_s}")
                if link_action:
                    link_action.triggered.connect(
                        partial(self.task_queue_manager.add_spotify_link_job, spotify_playlists_data)
                    )

                # Download Action
                download_menu = menu.addMenu(f"Download Playlist{plural_s} As...")
                if download_menu:
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
        logger.info(f"Attempting to sort Tidal playlists by: {sort_key}")
        if not self.tidal_root_item:
            return

        was_expanded = self.tidal_root_item.isExpanded()  # Store expanded state
        child_items = []
        for i in range(self.tidal_root_item.childCount()):
            child_items.append(self.tidal_root_item.child(i))

        if not child_items:
            logger.info("No Tidal playlists to sort.")
            return

        if sort_key == "alpha":
            # MODIFIED: Sort by widget name
            child_items.sort(key=lambda item: self._get_name_from_item(item).lower())
            logger.info("Sorting Tidal playlists alphabetically.")
        elif sort_key == "created":

            def get_sort_val_created(item_widget: QTreeWidgetItem) -> datetime.datetime:
                data = item_widget.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict):
                    dt_val = data.get("created_date")
                    if isinstance(dt_val, datetime.datetime):
                        return dt_val
                return datetime.datetime.min

            child_items.sort(key=get_sort_val_created, reverse=True)
            logger.info("Sorting Tidal playlists by created date (newest first).")
        elif sort_key == "updated":

            def get_sort_val_updated(item_widget: QTreeWidgetItem) -> datetime.datetime:
                data = item_widget.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict):
                    dt_val = data.get("updated_date")
                    if isinstance(dt_val, datetime.datetime):
                        return dt_val
                return datetime.datetime.min

            child_items.sort(key=get_sort_val_updated, reverse=True)
            logger.info("Sorting Tidal playlists by updated date (newest first).")
        else:
            logger.warning(f"Unknown Tidal sort key: {sort_key}")
            logger.warning(f"Cannot sort Tidal playlists by '{sort_key}'.")
            return

        # Re-add sorted items
        self.tidal_root_item.takeChildren()  # Remove all children
        for item in child_items:
            self.tidal_root_item.addChild(item)
        self.tidal_root_item.setExpanded(was_expanded)  # Restore expanded state
        logger.info(f"Tidal playlists sorted by {sort_key}.")

    def _sortSpotifyPlaylists(self, sort_key: str) -> None:
        logger.info(f"Attempting to sort Spotify playlists by: {sort_key}")
        if not self.spotify_root_item:
            return

        was_expanded = self.spotify_root_item.isExpanded()  # Store expanded state
        child_items = []
        for i in range(self.spotify_root_item.childCount()):
            child_items.append(self.spotify_root_item.child(i))

        if not child_items:
            logger.info("No Spotify playlists to sort.")
            return

        if sort_key == "alpha":
            # MODIFIED: Sort by widget name
            child_items.sort(key=lambda item: self._get_name_from_item(item).lower())
            logger.info("Sorting Spotify playlists alphabetically.")
        else:
            logger.warning(
                f"Unsupported Spotify sort key: {sort_key}. Spotify API does not readily provide date information for sorting user playlists by creation/update date."
            )
            Printf.warning(
                f"Cannot sort Spotify playlists by '{sort_key}'. Only alphabetical sorting is currently supported for Spotify playlists."
            )
            return

        self.spotify_root_item.takeChildren()
        for item in child_items:
            self.spotify_root_item.addChild(item)
        self.spotify_root_item.setExpanded(was_expanded)  # Restore expanded state
        logger.info(f"Spotify playlists sorted by {sort_key}.")

    # --- Icon Loading ---
    def _start_icon_fetch(
        self,
        item: QTreeWidgetItem,
        service: str,
        item_id: Optional[str],
        image_url: Optional[str],
    ) -> None:
        """Starts the background worker to fetch/load an icon."""
        item_name = self._get_name_from_item(item)
        # logger.debug(f"[_start_icon_fetch] Called for item: '{item_name}', service: {service}, item_id: '{item_id}', image_url: '{image_url}'")
        if not item_id:
            logger.warning(
                f"Cannot fetch icon for {service} item '{item_name}': Missing ID."
            )
            return
        
        # For services other than Tidal, if there's no image_url, we can't proceed.
        # For TIDAL, we allow a missing image_url so the worker can build a collage.
        if service != "tidal" and not image_url:
            logger.warning(
                f"Cannot fetch icon for {service} item '{item_name}': Missing image_url."
            )
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
            # Connect worker signals to handler slots
            worker.signals.cover_ready.connect(self._set_playlist_icon_from_worker)
            worker.signals.error.connect(self._handle_icon_error_from_worker)

            thread_pool = QThreadPool.globalInstance()
            if thread_pool:
                thread_pool.start(worker)
            else:
                logger.error(
                    "Could not get QThreadPool global instance to start icon worker."
                )
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
        """Slot to set the fetched icon on the corresponding tree item's widget."""
        if not url:
            return

        # Iterate through both Tidal and Spotify root items to find the matching item
        for root in [self.tidal_root_item, self.spotify_root_item]:
            # Iterate through all children, including those inside folders
            iterator = QtWidgets.QTreeWidgetItemIterator(root)
            while iterator.value():
                child = iterator.value()
                if child:
                    item_data = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
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
                        # Collage case: worker emits "tidal_playlist_<uuid>"
                        playlist_uuid = getattr(playlist_obj, 'uuid', '')
                        if playlist_uuid:
                            collage_key = f"tidal_playlist_{playlist_uuid}"
                            if url == collage_key:
                                match = True
                        # Normal case: direct cover URL
                        elif item_data.get("image_url") == url:
                            match = True
                elif service == 'spotify':
                    # For Spotify, the URL is direct.
                    if item_data.get("image_url") == url:
                        match = True

                if match:
                    # MODIFICATION: Get the WIDGET and set the icon on it
                    widget = self.tree_widget.itemWidget(child, 0)
                    if isinstance(widget, PlaylistItemProgressWidget):
                        icon = QIcon(pixmap)
                        if not icon.isNull():
                            widget.set_icon(icon)
                            item_name = self._get_name_from_item(child)
                            # logger.debug(f"Set icon for '{item_name}' from URL: {url}")
                    return # Found and updated, exit
                
                iterator += 1

    @pyqtSlot(str, str)
    def _handle_icon_error_from_worker(self, url: str, error_message: str) -> None:
        """Handles errors from the icon worker."""
        # This method doesn't need to change, because we already set a default
        # icon when the item was created. If fetching fails, it will just keep the default.
        # You can add logging here if you want.
        logger.warning(f"Failed to fetch icon for URL '{url}': {error_message}")

    @pyqtSlot()
    def _loadVisibleSpotifyIcons(self) -> None:
        """Loads icons for visible Spotify playlists in the tree, prioritizing stale cache and refreshing if needed."""
        if self.spotify_root_item.isHidden() or not self.spotify_root_item.isExpanded():
            return

        # logger.debug("Checking visible Spotify playlist icons...")
        viewport = self.tree_widget.viewport()
        if not viewport:  # Check if viewport is valid
            logger.warning("Could not get viewport for Spotify icon loading.")
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
                needs_refresh = item_data.get(
                    "icon_needs_refresh", True
                )  # Default to True if key missing
                is_pending = item_data.get("icon_update_pending", False)

                if needs_refresh and not is_pending:
                    image_url = item_data.get("image_url")
                    item_rect = self.tree_widget.visualItemRect(child_item)
                    if viewport_rect.intersects(item_rect):  # Check if item is visible
                        if image_url:
                            item_name = self._get_name_from_item(child_item)
                            # logger.debug(f"Lazy loading/refreshing icon for visible Spotify item: {item_name}")
                            item_data["icon_update_pending"] = True  # Mark as pending
                            child_item.setData(
                                0, QtCore.Qt.ItemDataRole.UserRole, item_data
                            )  # Save update
                            playlist_data = item_data.get("data")
                            playlist_id = None
                            if playlist_data is not None:
                                playlist_id = playlist_data.get("id")
                            self._start_icon_fetch(
                                child_item, "spotify", playlist_id, image_url
                            )
                        else:  # No image URL, but needs_refresh was true (e.g. no cache, no URL)
                            item_name = self._get_name_from_item(child_item)
                            logger.debug(f"Item {item_name} needs refresh but has no image URL. Setting default icon if none displayed.")
                            if (
                                not item_data.get("icon_displayed_from_cache", False)
                                and not self.default_playlist_icon.isNull()
                            ):
                                child_item.setIcon(0, self.default_playlist_icon)
                            # Mark as no longer needing refresh (as there's nothing to fetch)
                            item_data["icon_needs_refresh"] = False
                            item_data["icon_update_pending"] = False
                            child_item.setData(
                                0, QtCore.Qt.ItemDataRole.UserRole, item_data
                            )
            # else:
            #     logger.debug(f"Item {child_item.text(0)} does not need refresh or is already pending.")

    @pyqtSlot(int)
    def _onSpotifyScroll(self, value: int) -> None:
        """Loads icons for visible Spotify playlists when the scrollbar moves."""
        self._loadVisibleSpotifyIcons()

    # MODIFIED: Add new slots to handle job status updates
    @pyqtSlot(str, str, int)
    def on_job_started(self, playlist_id: str, action: str, total: int):
        """Slot to update a playlist item when a job starts."""
        widget = self.item_widgets.get(playlist_id)
        item = self.id_to_item.get(playlist_id)
        if widget and item:
            logger.debug(f"Job started for playlist {playlist_id}: {action} ({total} items)")
            widget.set_progress(0, total, action)
            widget.updateGeometry()
            item.setSizeHint(0, widget.sizeHint())
            self.tree_widget.openPersistentEditor(item, 0)

    @pyqtSlot(str, int)
    def on_job_progress(self, playlist_id: str, current: int):
        """Slot to update a playlist item's progress."""
        widget = self.item_widgets.get(playlist_id)
        if widget:
            total = widget.progress_bar.maximum()
            # Extract action text from the label (e.g., "Linking: 0/52" -> "Linking")
            action_text = widget.status_label.text().split(":")[0]
            widget.set_progress(current, total, action_text)

    @pyqtSlot(str)
    def on_job_finished(self, playlist_id: str):
        """Slot to reset a playlist item when a job finishes."""
        widget = self.item_widgets.get(playlist_id)
        item = self.id_to_item.get(playlist_id)
        if widget and item:
            logger.debug(f"Job finished for playlist {playlist_id}")
            self.tree_widget.closePersistentEditor(item, 0)
            widget.reset_state()
            item.setSizeHint(0, QSize(0, 0)) # Reset to default height

    def _get_name_from_item(self, item: Optional[QTreeWidgetItem]) -> str:
        """Helper to get the display name from an item's custom widget."""
        if not item:
            return ""
        widget = self.tree_widget.itemWidget(item, 0)
        if isinstance(widget, PlaylistItemProgressWidget):
            return widget.name_label.text()
        return item.text(0) # Fallback for root items

    # --- Playlist Filtering ---
    @pyqtSlot()
    def _apply_playlist_filter(self) -> None:
        """Applies the current filter text to both Tidal and Spotify playlist items."""
        filter_text = self.filter_input_widget.text().lower().strip()
        logger.debug(f"Applying filter: '{filter_text}'")

        self._filter_root_item_children(self.tidal_root_item, filter_text)
        self._filter_root_item_children(self.spotify_root_item, filter_text)

    def _filter_root_item_children(
        self, root_item: Optional[QTreeWidgetItem], filter_text: str
    ) -> None:
        """Helper function to filter children of a given root item."""
        if not root_item:
            return

        for i in range(root_item.childCount()):
            child = root_item.child(i)
            if child:
                # MODIFIED: Get name from widget
                item_text = self._get_name_from_item(child).lower()
                is_visible = (
                    not filter_text or filter_text in item_text
                )  # Show if no filter or if text matches
                child.setHidden(not is_visible)

    @pyqtSlot()
    def onPlaylistDisplaySettingsChanged(self):
        """Updates the playlist tree view when display settings are changed."""
        logger.info("Playlist display settings changed. Refreshing view.")
        
        # Update the icon size on the tree widget itself
        new_size = SETTINGS.playlistIconSize if SETTINGS.showPlaylistIcons else 0
        self.tree_widget.setIconSize(QSize(new_size, new_size))
        
        # Trigger a full refresh of both playlist sources to apply the new settings
        self.refreshTidalPlaylists()
        if hasattr(self.main_view, "spotify_gui_handler"):
            self.main_view.spotify_gui_handler.refreshSpotifyPlaylists()
