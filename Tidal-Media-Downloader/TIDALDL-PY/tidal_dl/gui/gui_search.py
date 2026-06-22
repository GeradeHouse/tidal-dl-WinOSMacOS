#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_search.py
@Time    :   2025/04/27
@Author  :   Roo
@Version :   1.0
@Contact :
@Desc    :   Search bar widget for the Tidal-DL GUI
"""
import logging
import re
import time
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Optional, Any, Dict, List, Tuple, Callable
from PyQt6 import sip
from PyQt6.QtWidgets import (
    QWidget,
    QLineEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QApplication,
    QSizePolicy,
    QStyleOption,
    QStyle,
    QPushButton,
    QStackedWidget,
)
from PyQt6.QtCore import (
    pyqtSignal,
    pyqtSlot,
    QTimer,
    Qt,
    QEvent,
    QSize,
    QObject,
    QPoint,
    QThreadPool,
    QRectF,
)
from PyQt6.QtGui import (
    QFocusEvent,
    QPixmap,
    QEnterEvent,
    QColor,
    QPalette,
    QResizeEvent,
    QAction,
    QIcon,
    QPainter,
    QPaintEvent,
    QPainterPath,
)
from tidal_dl.enums import Type
from tidal_dl.model import Track, Album, Artist
from tidal_dl.tidal import TIDAL_API
from tidal_dl import paths
from tidal_dl.gui.gui_cover_cache import CoverCache, CoverArtWorker

if TYPE_CHECKING:
    from tidal_dl.gui.gui_main import MainView

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (search operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


# --- Custom Widget for Search Results ---
class SearchResultItemWidget(QWidget):
    """Custom widget for displaying a single search result item."""

    def __init__(
        self, primary_text: str, secondary_text: str, item_type: str, parent=None
    ):
        super().__init__(parent)
        # Ensure the widget background is driven by stylesheet, not the default palette
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.item_type = item_type

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.primary_label = QLabel(primary_text)
        self.primary_label.setObjectName("SearchResultPrimaryText")
        self.primary_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self.secondary_label = QLabel(secondary_text)
        self.secondary_label.setObjectName("SearchResultSecondaryText")
        self.secondary_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        text_layout.addWidget(self.primary_label)
        text_layout.addWidget(self.secondary_label)
        text_layout.addStretch(1)

        layout.addLayout(text_layout)
        layout.addStretch(1)

        self.setLayout(layout)
        self.setFixedHeight(45)

    def set_icon(self, pixmap: QPixmap, from_url: bool = False):
        if from_url:
            self.icon_label.setPixmap(
                pixmap.scaled(
                    32,
                    32,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.icon_label.setPixmap(pixmap)

    def clear(self):
        self.primary_label.clear()
        self.secondary_label.clear()
        self.icon_label.clear()

    # Optional: Add hover effect handling if needed directly here
    # def enterEvent(self, event):
    #     # Apply hover background
    #     pass
    # def leaveEvent(self, event):
    #     # Remove hover background
    #     pass


class StyledListWidget(QListWidget):
    """
    A QListWidget subclass that overrides paintEvent to ensure its background,
    as defined by a stylesheet, is always painted correctly. This is a robust
    fix for rendering issues on macOS and Windows when the widget is a child
    of a translucent window.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Enable styled background for the list itself
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Enable styled background for the viewport (where items are actually drawn)
        vp = self.viewport()
        if vp is not None:
            vp.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

            # Make sure the viewport uses the same dark palette as the list, so even if
            # global styles change, the panel won't turn white.
            vp_palette = vp.palette()
            vp_palette.setColor(QPalette.ColorRole.Base, QColor("#252525"))
            vp_palette.setColor(QPalette.ColorRole.Text, QColor("#f0f0f0"))
            vp.setPalette(vp_palette)
            vp.setAutoFillBackground(True)

    def paintEvent(self, e: QPaintEvent | None) -> None:  # MODIFIED: signature exactly matches stub ("e")
        if e is None:
            # Forward the None to the base class to satisfy the stub contract
            super().paintEvent(e)
            return

        opt = QStyleOption()
        opt.initFrom(self)

        # Paint directly on the viewport – this is the surface that actually shows the items.
        vp = self.viewport()
        painter_target = vp if vp is not None else self
        painter = QPainter(painter_target)

        # Force the style to draw the primitive widget background, which respects
        # the 'background-color' property from the stylesheet.
        style = self.style() or QApplication.style()
        if style:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)

        # After ensuring the background is drawn, call the original paintEvent
        # to handle the drawing of items, scrollbars, and other decorations.
        super().paintEvent(e)  # MODIFIED: now passes 'e' that matches base class


class SearchBarWidget(QWidget):
    """
    A custom widget providing a search bar with type selection and live results.
    """

    searchTriggered = pyqtSignal(str)  # Signal for full search (only text)
    liveSearchRequested = pyqtSignal(str)  # Live search uses query only
    resultSelected = pyqtSignal(
        dict
    )  # Signal emits a dictionary with selected item info

    # Modify constructor to accept main_view
    def __init__(self, main_view: "MainView", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.main_view = main_view  # Store reference to MainView

        self.cover_cache = CoverCache()
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(5)
        self.active_workers = {}

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Search...")

        icon_path = paths.resource_path(
            "assets/icons/icon_search.png"
        )  # Use paths module
        search_icon = QIcon(icon_path)
        search_action = QAction(search_icon, "", self.search_input)  # Action with icon
        self.search_input.addAction(
            search_action, QLineEdit.ActionPosition.LeadingPosition
        )
        # --- End Search Icon ---

        # Create list widget as a child of MAIN_VIEW, not self
        # Use the custom StyledListWidget to fix background rendering issues.
        self.live_results_list = StyledListWidget(self.main_view)

        # Keep other list settings
        self.live_results_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.live_results_list.setMouseTracking(True)
        self.live_results_list.hide()  # Initially hidden
        # Set a maximum height for the results list (can be adjusted) - Increased by 1.5x
        self.live_results_list.setMaximumHeight(450)  # Was 300
        self.live_results_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )  # Allow horizontal expansion

        # Apply necessary styling directly to the list (important since it's not a child for styling)
        # MODIFIED STYLESHEET: Styles QListWidget::item directly for selection/hover
        self.live_results_list.setStyleSheet(
            """
            QListWidget {
                background-color: #252525;
                color: #f0f0f0; /* Default text color for items */
                border: 1px solid #444;
                border-radius: 8px;
                padding: 2px;
                outline: 0px;
            }
            QListView::viewport {
                background-color: #252525;
            }
            QListWidget::item {
                 background-color: transparent; /* Make item area transparent by default */
                 border: none;
                 padding: 0px;
                 margin: 0px;
            }
             QListWidget::item:selected { /* Style the item itself */
                 background-color: #005aaa; /* Selection color for the item's background */
             }
             QListWidget::item:hover { /* Style the item itself */
                 background-color: #3a3a3a; /* Hover color for the item's background */
             }
        """
        )

        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(500)  # 500ms delay for live search

        # REMOVED hide_results_timer - focus loss will be handled directly in eventFilter
        # self.hide_results_timer = QTimer(self)
        # self.hide_results_timer.setSingleShot(True)
        # self.hide_results_timer.setInterval(150)

        # Width expansion logic
        self._original_width = 268
        self._expanded_width = 398

        # --- Layout ---
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        search_bar_layout = QHBoxLayout()
        search_bar_layout.setContentsMargins(0, 0, 0, 0)
        search_bar_layout.setSpacing(5)

        self.search_input_container = QWidget()
        # Set initial fixed width instead of size policy
        self.search_input_container.setFixedWidth(self._original_width)
        # self.search_input_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred) # REMOVED
        search_input_layout = QHBoxLayout(self.search_input_container)
        search_input_layout.setContentsMargins(0, 0, 0, 0)
        search_input_layout.setSpacing(0)
        search_input_layout.addWidget(self.search_input)

        # Adjust padding in stylesheet instead of text margins
        # icon_width = self.search_icon_label.pixmap().width() # Removed
        # left_padding = 5 # Removed
        # right_padding = 5 # Removed
        # self.search_input.setTextMargins(icon_width + left_padding + right_padding, 0, 0, 0) # Removed

        # self.search_icon_label.setParent(self.search_input_container) # Removed
        # icon_y_offset = (self.search_input.sizeHint().height() - self.search_icon_label.height()) // 2 # Removed
        # self.search_icon_label.move(left_padding, icon_y_offset) # Removed
        # self.search_icon_label.raise_() # Removed

        search_bar_layout.addWidget(self.search_input_container)
        main_layout.addLayout(search_bar_layout)
        # *** live_results_list is NOT added to this layout ***

        self.setLayout(main_layout)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )

        # --- Connections & Event Filter ---
        self.search_input.textChanged.connect(
            self._on_text_changed
        )  # Connect text changes
        self.debounce_timer.timeout.connect(
            self._request_live_search
        )  # Connect debounce timer
        self.search_input.returnPressed.connect(self._on_enter_pressed)
        self.live_results_list.itemClicked.connect(
            self._on_result_item_clicked
        )  # Connect item click
        # REMOVED hide_results_timer connection
        # self.hide_results_timer.timeout.connect(self._hide_results_list)
        self.search_input.installEventFilter(
            self
        )  # Install event filter for input focus
        # Also filter events on the list to detect focus changes within it
        self.live_results_list.installEventFilter(self)  # Keep filtering list events

        # --- Initial Styling ---
        self._apply_styles()  # Apply styles to search bar components

    # Corrected eventFilter signature parameters and types to match QObject.eventFilter
    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """Filters events for the search input for hover and focus effects."""
        # Use descriptive names internally for clarity, checking for None
        watched = a0
        event = a1
        # Ensure watched and event are not None before proceeding
        if watched is not None and event is not None and watched == self.search_input:
            # logger.debug(f"Event Filter: Watched={watched}, Event Type={event.type()}") # Log event type
            # PyQt6 uses specific event classes like QEnterEvent
            if isinstance(event, QEnterEvent):
                # Apply hover style
                self.search_input.setProperty("state", "hover")
                self._repolish(self.search_input)
            elif event.type() == QEvent.Type.Leave:  # Leave event type is still valid
                # Remove hover style (unless focused)
                if not self.search_input.hasFocus():
                    self.search_input.setProperty("state", "default")
                    self._repolish(self.search_input)
            elif event.type() == QEvent.Type.FocusIn:
                logger.debug("Event Filter: FocusIn on search_input")  # Log FocusIn
                # Apply focus style and expand
                self.search_input.setProperty("state", "focus")
                self._repolish(self.search_input)
                # Expand width
                self._set_search_surface_expanded(True)
                # No icon label to move
                # No timer to stop
            elif event.type() == QEvent.Type.FocusOut:
                logger.debug(
                    f"Event Filter: FocusOut from search_input. Reason: {event.reason().name if isinstance(event, QFocusEvent) else 'Unknown'}"
                )

                # Schedule a check after the event loop has processed the focus change
                QTimer.singleShot(0, self._check_focus_after_search_input_lost_focus)
                # Do not hide the list here directly; let the timed check handle it.

                # Keep the expanded search surface stable while focus moves into the result list.
                self.search_input.setProperty(
                    "state",
                    "focus" if self.live_results_list.isVisible() else "default",
                )
                self._repolish(self.search_input)

        # Also handle FocusOut for the list widget itself
        elif (
            watched is not None
            and event is not None
            and watched == self.live_results_list
        ):
            if event.type() == QEvent.Type.FocusOut:
                logger.debug(
                    f"Event Filter: FocusOut from live_results_list. Reason: {event.reason().name if isinstance(event, QFocusEvent) else 'Unknown'}"
                )

                # Schedule a check
                QTimer.singleShot(0, self._check_focus_after_results_list_lost_focus)

        # Pass original arguments (a0, a1) to superclass method
        return super().eventFilter(a0, a1)

    def _on_text_changed(self, text: str):
        """Handles text changes in the search input, starts debounce timer."""
        # Start the timer whenever text changes
        self.debounce_timer.start()

    def _request_live_search(self):
        """Requests live search results based on current input and type."""
        # Get current text
        text = self.search_input.text().strip()
        # REMOVED: search_type = self.search_type_combo.currentData()
        if text:  # Check only for text for live search
            # Emit signal only if there's text
            self.liveSearchRequested.emit(text)  # Emit only text
        else:
            # Hide results if input is empty
            self._hide_results_list()

    def _on_enter_pressed(self):
        """Triggers the main search when Enter is pressed."""
        text = self.search_input.text().strip()
        # REMOVED: search_type = self.search_type_combo.currentData()
        if text:  # Check only for text
            self.live_results_list.hide()  # Hide live results if they were shown
            self.searchTriggered.emit(text)  # Emit only text

    def _display_live_results(self, results: list):
        """Displays the live search results using custom widgets, handling mixed types."""
        logger.debug("Entering _display_live_results...")
        self.live_results_list.clear()
        if results:
            # Set placeholder icon
            placeholder_icon = QPixmap(
                paths.resource_path("assets/icons/icon_search.png")
            )

            for i, result in enumerate(results):
                item = self.live_results_list.item(i)
                if not item:
                    item = QListWidgetItem(self.live_results_list)

                data_dict = {}
                primary_text, secondary_text, item_type_str = (
                    "Unknown",
                    "",
                    "suggestion",
                )

                if isinstance(result, Track):
                    primary_text = result.title
                    secondary_text = (
                        f"Track - {', '.join(artist.name for artist in result.artists) if isinstance(result.artists, list) else result.artists.name}"
                    )
                    item_type_str = "Track"
                    data_dict = {
                        "type": Type.Track,
                        "id": result.id,
                        "title": primary_text,
                    }
                    if (
                        hasattr(result, "album")
                        and result.album
                        and hasattr(result.album, "cover")
                    ):
                        cover_url = result.album.cover
                        if cover_url:
                            self._start_cover_art_download(
                                item,
                                cover_url,
                                item_type_str,
                                result.id,
                                item_name=primary_text,
                            )
                elif isinstance(result, Album):
                    primary_text = result.title
                    secondary_text = (
                        f"Album - {', '.join(artist.name for artist in result.artists) if isinstance(result.artists, list) else result.artists.name}"
                    )
                    item_type_str = "Album"
                    data_dict = {
                        "type": Type.Album,
                        "id": result.id,
                        "title": primary_text,
                    }
                    if hasattr(result, "cover"):
                        self._start_cover_art_download(
                            item,
                            result.cover,
                            item_type_str,
                            result.id,
                            item_name=primary_text,
                        )
                elif isinstance(result, Artist):
                    primary_text = result.name
                    secondary_text = "Artist"
                    item_type_str = "Artist"
                    data_dict = {
                        "type": Type.Artist,
                        "id": result.id,
                        "name": primary_text,
                    }
                    if hasattr(result, "picture"):
                        self._start_cover_art_download(
                            item,
                            result.picture,
                            item_type_str,
                            result.id,
                            item_name=primary_text,
                        )

                widget = SearchResultItemWidget(
                    primary_text or "", secondary_text, item_type_str
                )
                widget.set_icon(placeholder_icon)
                item.setSizeHint(widget.sizeHint())
                item.setData(Qt.ItemDataRole.UserRole, data_dict)
                self.live_results_list.setItemWidget(item, widget)

            if self.live_results_list.count() == 0:
                self.live_results_list.hide()
                return

            # Ensure list is visible before positioning
            if not self.live_results_list.isVisible():
                logger.debug("_display_live_results: Showing list.")
                self.live_results_list.show()

            # Update geometry using the new helper method
            self._update_results_list_geometry()
            # *** Raise the list within its parent (MainView) ***
            self.live_results_list.raise_()
            logger.debug("_display_live_results: Raised list widget.")

        else:
            logger.debug("_display_live_results: No results, hiding list.")
            self.live_results_list.hide()
        logger.debug("Exiting _display_live_results.")

    def _update_results_list_geometry(self):
        """Calculates and sets the geometry of the live results list relative to MainView."""
        if not self.live_results_list.isVisible():
            return

        # --- Calculate Position relative to MainView ---
        # 1. Get global position of the bottom-left of the search input container
        global_pos_ref_point = self.search_input_container.mapToGlobal(
            QPoint(0, self.search_input_container.height())
        )

        # 2. Convert global position to MainView's coordinate system
        parent_relative_pos = self.main_view.mapFromGlobal(global_pos_ref_point)

        # 3. Set list position and width (doubled)
        list_x = parent_relative_pos.x()
        list_y = parent_relative_pos.y() + 1  # Position 1 pixel below the container
        list_width = self._expanded_width * 2  # Stable width while moving between input and results.

        # --- Calculate Height ---
        content_height = 0
        if self.live_results_list.count() > 0:
            row_height = self.live_results_list.sizeHintForRow(0)
            if row_height <= 0:
                row_height = 45
            content_height = (
                row_height * self.live_results_list.count()
                + 2 * self.live_results_list.frameWidth()
                + 4
            )

        # Calculate height, capped by max height, then multiply by 1.5
        calculated_height = min(content_height, self.live_results_list.maximumHeight())
        list_height = int(calculated_height * 1.5)  # *** Increased height by 1.5x ***
        list_height = max(1, list_height)  # Prevent zero height

        logger.debug(
            f"Updating list geometry (MainView relative): x={list_x}, y={list_y}, width={list_width}, height={list_height}"
        )
        self.live_results_list.setGeometry(list_x, list_y, list_width, list_height)

    def resizeEvent(
        self, a0: QResizeEvent | None
    ):  # Match base class signature (parameter name 'a0')
        """Handle resize events for the SearchBarWidget."""
        super().resizeEvent(a0)  # Call base implementation first, passing a0
        # Reposition the results list if it's visible
        self._update_results_list_geometry()

    def _start_cover_art_download(
        self,
        item,
        url,
        item_type,
        item_id,
        item_name: Optional[str] = None,
    ):
        worker = CoverArtWorker(
            url,
            self.cover_cache,
            item_type,
            item_id,
            item_name=item_name,
        )
        worker.signals.cover_ready.connect(
            lambda u, p, i=item: self._on_cover_art_ready(i, u, p)
        )
        self.thread_pool.start(worker)

    def _on_cover_art_ready(self, item, url, pixmap):
        if sip.isdeleted(item):
            return
        widget = self.live_results_list.itemWidget(item)
        if isinstance(widget, SearchResultItemWidget):
            widget.set_icon(pixmap, from_url=True)

    def _on_result_item_clicked(self, item: QListWidgetItem):
        """Handles clicking on a live search result item."""
        try:
            logger.debug(
                f"Entered _on_result_item_clicked for item: {item.text() if item else 'None'}"
            )
            if not item or sip.isdeleted(item):
                logger.warning(
                    "Clicked item is not valid or has been deleted. Aborting."
                )
                return

            selected_data_dict = item.data(Qt.ItemDataRole.UserRole)
            if selected_data_dict:
                logger.debug(f"Item data found: {selected_data_dict}")
                display_text = selected_data_dict.get(
                    "title", selected_data_dict.get("name")
                )
                if display_text:
                    self.search_input.blockSignals(True)
                    self.search_input.setText(display_text)
                    self.search_input.blockSignals(False)

                logger.debug("Emitting resultSelected signal...")
                self.resultSelected.emit(selected_data_dict)
                logger.debug("resultSelected signal emitted.")
            else:
                logger.warning("No data found for the clicked item.")

            self._hide_results_list()
            logger.debug("Exiting _on_result_item_clicked normally.")

        except Exception:
            # Enhanced logging to capture the elusive crash
            logger.critical(
                "CRITICAL: Exception caught directly in _on_result_item_clicked!",
                exc_info=True,
            )
            # Fail-safe: do not re-raise from a direct UI event handler.
            # Re-raising here can crash the app for a single malformed list item.
            self._hide_results_list()
            return

    def _set_search_surface_expanded(self, expanded: bool) -> None:
        """Keeps the input and floating result list at a stable width."""
        target_width = self._expanded_width if expanded else self._original_width
        if self.search_input_container.width() != target_width:
            self.search_input_container.setFixedWidth(target_width)
        if self.live_results_list.isVisible():
            self._update_results_list_geometry()

    def _is_results_list_focus_target(self, widget: Optional[QWidget]) -> bool:
        if widget is None:
            return False
        if widget == self.live_results_list:
            return True

        current_widget = widget
        while current_widget:
            if current_widget == self.live_results_list:
                return True
            if isinstance(current_widget, QWidget):
                current_widget = current_widget.parentWidget()
            else:
                break
        return False

    def _hide_results_list(self):
        """Hides the results list and restores compact width when the input is inactive."""
        if self.live_results_list.isVisible():
            logger.debug("Hiding live results list.")
            self.live_results_list.hide()

        if not self.search_input.hasFocus():
            self._set_search_surface_expanded(False)
            self.search_input.setProperty("state", "default")
            self._repolish(self.search_input)

    def _repolish(self, widget):
        """Helper function to force style recalculation."""
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _check_focus_after_search_input_lost_focus(self):
        newly_focused_widget = QApplication.focusWidget()
        logger.debug(
            f"SearchBarWidget: _check_focus_after_search_input_lost_focus. Newly focused: {newly_focused_widget}"
        )

        if self._is_results_list_focus_target(newly_focused_widget):
            logger.debug(
                "Focus from search input moved to results list or its children. Keeping list visible."
            )
            self._set_search_surface_expanded(True)
            self.search_input.setProperty("state", "focus")
            self._repolish(self.search_input)
            return

        logger.debug(
            "Focus from search input moved away from results list. Hiding list."
        )
        self._hide_results_list()

    def _check_focus_after_results_list_lost_focus(self):
        newly_focused_widget = QApplication.focusWidget()
        logger.debug(
            f"SearchBarWidget: _check_focus_after_results_list_lost_focus. Newly focused: {newly_focused_widget}"
        )

        if newly_focused_widget == self.search_input:
            logger.debug(
                "FocusOut from list: focus moved back to input. Keeping visible."
            )
            self._set_search_surface_expanded(True)
            return

        if self._is_results_list_focus_target(newly_focused_widget):
            logger.debug(
                "FocusOut from list: focus moved to list child. Keeping visible."
            )
            self._set_search_surface_expanded(True)
            return

        logger.debug(
            "FocusOut from list: focus moved away from input and list/children. Hiding list."
        )
        self._hide_results_list()

    def _apply_styles(self):
        """Applies initial stylesheets to the widget and its components."""
        # Base styles - adjust colors and radii as needed
        border_radius = "8px"
        default_bg = "#1d1d21"  # Set to user-provided color
        default_fg = "#eeeeee"  # Light text
        border_color = "#333333"  # Adjusted border for the new background
        hover_border_color = "#555555"  # Adjusted hover border
        focus_border_color = "#0078d4"  # Standard Windows focus blue
        combo_bg = "#444444"
        # list_bg = "#252525"         # List styling is now applied directly
        item_bg = "#00000000"  # Fully transparent (8-digit hex)
        item_hover_bg = "#3a3a3a"  # Subtle hover grey
        item_selected_bg = "#005aaa"  # Darker selected blue
        primary_text_color = "#f0f0f0"  # Slightly off-white
        secondary_text_color = "#999999"  # Grey for secondary text

        # Using dynamic properties for state changes
        self.search_input.setProperty("state", "default")  # Initial state

        # Stylesheet for SearchBarWidget components (excluding the list)
        style_sheet = f"""
            SearchBarWidget {{
                background-color: transparent; /* Make widget background transparent */
            }}
            QLineEdit {{
                background-color: {default_bg};
                color: {default_fg};
                border: 1px solid {border_color};
                border-radius: {border_radius};
                padding: 5px; /* Base padding */
                padding-left: 25px; /* Add padding for the action icon */
            }}
            QLineEdit[state="hover"] {{
                border: 1px solid {hover_border_color};
            }}
            QLineEdit[state="focus"] {{
                border: 1px solid {focus_border_color};
            }}
            /* REMOVED QComboBox styles */
            /* QListWidget styling is now applied directly to self.live_results_list */
            /* QListWidget::item styling is now applied directly to self.live_results_list */
            /* QListWidget::item:selected/hover SearchResultItemWidget styling is REMOVED */

            SearchResultItemWidget {{ /* Base style for custom widget */
                background-color: {item_bg}; /* Default background (transparent) */
                border-radius: 4px; /* Apply radius to all items */
            }}
            #SearchResultPrimaryText {{
                font-weight: normal; /* Adjust as needed */
                color: {primary_text_color};
                background-color: transparent; /* Ensure no background interferes */
                padding-left: 5px; /* Add some padding */
            }}
            #SearchResultSecondaryText {{
                color: {secondary_text_color};
                font-size: 9pt; /* Smaller font size */
                background-color: transparent; /* Ensure no background interferes */
                padding-left: 5px; /* Add some padding */
            }}
        """
        self.setStyleSheet(style_sheet)
        # Ensure the input container also has a transparent background
        # self.search_input_container.setStyleSheet("background-color: transparent;") # Removed to test background issue


class KeywordSearchResultsController(QObject):
    """Encapsulates keyword/top-results UI state, ranking, and async cover loading."""

    TOP_RESULT_ICON_SIZE = 60
    TOP_RESULT_ARTIST_AVATAR_SIZE = 60
    TOP_RESULT_ITEM_HEIGHT = 84

    def __init__(
        self,
        parent: QWidget,
        cover_cache: CoverCache,
        top_results_list: QListWidget,
        albums_grid_list: QListWidget,
        search_results_tabs_widget: QWidget,
        search_results_stack: QStackedWidget,
        btn_tracks_results: QPushButton,
        btn_top_results: QPushButton,
        btn_albums_results: QPushButton,
        on_result_selected: Callable[[Dict[str, Any]], None],
    ) -> None:
        super().__init__(parent)
        self.cover_cache = cover_cache
        self.top_results_list = top_results_list
        self.albums_grid_list = albums_grid_list
        self.search_results_tabs_widget = search_results_tabs_widget
        self.search_results_stack = search_results_stack
        self.btn_tracks_results = btn_tracks_results
        self.btn_top_results = btn_top_results
        self.btn_albums_results = btn_albums_results
        self._on_result_selected = on_result_selected

        self._keyword_cover_generation: int = 0
        self._keyword_cover_subscribers: Dict[
            str, List[Tuple[QListWidget, QListWidgetItem, int]]
        ] = {}
        self._keyword_cover_workers: Dict[str, CoverArtWorker] = {}

        self.top_results_list.setIconSize(
            QSize(self.TOP_RESULT_ICON_SIZE, self.TOP_RESULT_ICON_SIZE)
        )
        self.top_results_list.itemClicked.connect(self.on_keyword_result_item_clicked)
        self.albums_grid_list.itemClicked.connect(self.on_keyword_result_item_clicked)

    def set_search_results_page(self, page: str) -> None:
        page_map = {"tracks": 0, "top": 1, "albums": 2}
        page_index = page_map.get(page, 0)
        self.search_results_stack.setCurrentIndex(page_index)

        self.btn_tracks_results.setChecked(page == "tracks")
        self.btn_top_results.setChecked(page == "top")
        self.btn_albums_results.setChecked(page == "albums")

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

    def _normalize_search_text(self, value: Any) -> str:
        if value is None:
            return ""
        normalized = re.sub(r"[^a-z0-9]+", " ", str(value).lower())
        return re.sub(r"\s+", " ", normalized).strip()

    def _contains_collab_term(self, normalized_text: str) -> bool:
        if not normalized_text:
            return False
        padded = f" {normalized_text} "
        collab_terms = (
            " and ",
            " & ",
            " feat ",
            " featuring ",
            " with ",
            " x ",
            " + ",
        )
        return any(term in padded for term in collab_terms)

    def _text_relevance_score(self, candidate_text: str, query: str) -> float:
        query_norm = self._normalize_search_text(query)
        candidate_norm = self._normalize_search_text(candidate_text)
        if not query_norm or not candidate_norm:
            return 0.0

        score = 0.0
        if candidate_norm == query_norm:
            score += 220.0
        if candidate_norm.startswith(query_norm):
            score += 95.0
        if query_norm in candidate_norm:
            score += 60.0

        query_tokens = [token for token in query_norm.split(" ") if token]
        candidate_tokens = {token for token in candidate_norm.split(" ") if token}
        if query_tokens:
            token_hits = sum(1 for token in query_tokens if token in candidate_tokens)
            score += (token_hits / len(query_tokens)) * 80.0

        score += SequenceMatcher(None, query_norm, candidate_norm).ratio() * 75.0

        extra_tokens = max(len(candidate_tokens) - len(query_tokens), 0)
        score -= extra_tokens * 4.0
        return score

    def _score_artist_for_query(self, artist_name: str, query: str) -> float:
        query_norm = self._normalize_search_text(query)
        artist_norm = self._normalize_search_text(artist_name)
        if not artist_norm:
            return 0.0

        score = self._text_relevance_score(artist_name, query) + 35.0

        if artist_norm == query_norm and query_norm:
            score += 160.0

        if self._contains_collab_term(artist_norm) and not self._contains_collab_term(
            query_norm
        ):
            score -= 165.0
            if query_norm and query_norm in artist_norm:
                score -= 85.0

        query_tokens = [token for token in query_norm.split(" ") if token]
        artist_tokens = {token for token in artist_norm.split(" ") if token}
        if query_tokens:
            token_hits = sum(1 for token in query_tokens if token in artist_tokens)
            if token_hits == 0:
                return 0.0
            if token_hits < len(query_tokens):
                score -= 60.0

        return score

    def _rank_artists_for_top_results(
        self, artists: list, query: str, limit: int
    ) -> List[Tuple[Any, float]]:
        query_norm = self._normalize_search_text(query)
        query_has_collab = self._contains_collab_term(query_norm)
        ranked_by_name: Dict[str, Tuple[Any, float]] = {}

        for artist in artists:
            artist_name = str(getattr(artist, "name", "") or "").strip()
            if not artist_name:
                continue

            artist_norm = self._normalize_search_text(artist_name)
            if not artist_norm:
                continue

            if (
                query_norm
                and not query_has_collab
                and self._contains_collab_term(artist_norm)
                and query_norm in artist_norm
            ):
                # Hide collaboration aliases unless the query explicitly asks for them.
                continue

            score = self._score_artist_for_query(artist_name, query)
            if score <= 0.0:
                continue

            existing = ranked_by_name.get(artist_norm)
            if existing is None or score > existing[1]:
                ranked_by_name[artist_norm] = (artist, score)

        ranked = sorted(
            ranked_by_name.values(),
            key=lambda item: item[1],
            reverse=True,
        )
        return ranked[:limit]

    def _add_disabled_info_item(self, target_list: QListWidget, text: str) -> None:
        info_item = QListWidgetItem(text)
        info_item.setFlags(info_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        target_list.addItem(info_item)

    def _create_circular_pixmap(self, pixmap: QPixmap, diameter: int) -> QPixmap:
        if pixmap.isNull() or diameter <= 0:
            return pixmap

        scaled = pixmap.scaled(
            diameter,
            diameter,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

        crop_x = max((scaled.width() - diameter) // 2, 0)
        crop_y = max((scaled.height() - diameter) // 2, 0)
        cropped = scaled.copy(crop_x, crop_y, diameter, diameter)

        result = QPixmap(diameter, diameter)
        result.fill(Qt.GlobalColor.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(QRectF(0.0, 0.0, float(diameter), float(diameter)))
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.end()

        return result

    def _prepare_keyword_icon_pixmap(
        self,
        list_widget: QListWidget,
        target_item: QListWidgetItem,
        pixmap: QPixmap,
    ) -> QPixmap:
        if pixmap.isNull():
            return pixmap

        if list_widget is self.top_results_list:
            payload = target_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(payload, dict) and payload.get("type") == Type.Artist:
                return self._create_circular_pixmap(
                    pixmap, self.TOP_RESULT_ARTIST_AVATAR_SIZE
                )
        return pixmap

    def _queue_keyword_cover_load(
        self,
        target_list: QListWidget,
        item: QListWidgetItem,
        cover_id: Optional[str],
        item_type: str,
        item_id: str,
        generation: int,
        item_name: Optional[str] = None,
    ) -> bool:
        if not cover_id:
            return False

        request_key = TIDAL_API.getCoverUrl(str(cover_id), "320", "320") or str(cover_id)
        cached = self.cover_cache.get(request_key)
        if cached and not cached.isNull():
            display_pixmap = self._prepare_keyword_icon_pixmap(target_list, item, cached)
            item.setIcon(QIcon(display_pixmap))
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
            item_name=item_name,
        )

        def _on_ready(_signal_key: str, pixmap: QPixmap, req_key=request_key) -> None:
            targets = self._keyword_cover_subscribers.pop(req_key, [])
            self._keyword_cover_workers.pop(req_key, None)
            for list_widget, target_item, target_generation in targets:
                if target_generation != self._keyword_cover_generation:
                    continue
                try:
                    if sip.isdeleted(list_widget) or sip.isdeleted(target_item):
                        continue
                    if list_widget.row(target_item) < 0:
                        continue
                    if not pixmap.isNull():
                        display_pixmap = self._prepare_keyword_icon_pixmap(
                            list_widget, target_item, pixmap
                        )
                        if not sip.isdeleted(target_item):
                            target_item.setIcon(QIcon(display_pixmap))
                except RuntimeError as exc:
                    logger.debug(
                        "Skipping stale keyword cover target after Qt object deletion: %s",
                        exc,
                    )
                    continue

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
    def on_keyword_search_ready(
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
        max_top_results = 12
        cover_attempts = 0

        top_candidates: List[Dict[str, Any]] = []

        for track in tracks[:max_top_tracks]:
            track_title = str(getattr(track, "title", "Unknown Track") or "Unknown Track")
            track_artists = self._format_artist_names(getattr(track, "artists", None))
            subtitle = f"Track • {track_artists}"
            track_score = (
                self._text_relevance_score(track_artists, query) * 1.2
                + self._text_relevance_score(track_title, query) * 0.45
                + 15.0
            )
            album_obj = getattr(track, "album", None)
            top_candidates.append(
                {
                    "score": track_score,
                    "priority": 2,
                    "text": f"{track_title}\n{subtitle}",
                    "payload": {
                        "type": Type.Track,
                        "id": getattr(track, "id", ""),
                        "title": track_title,
                    },
                    "cover_id": getattr(album_obj, "cover", None) if album_obj else None,
                    "cover_type": "Track",
                    "item_id": str(getattr(track, "id", "")),
                    "item_name": track_title,
                }
            )

        for album in albums[:max_top_albums]:
            album_title = str(getattr(album, "title", "Unknown Album") or "Unknown Album")
            album_artists = self._format_artist_names(
                getattr(album, "artists", getattr(album, "artist", None))
            )
            subtitle = f"Album • {album_artists}"
            album_score = (
                self._text_relevance_score(album_artists, query) * 1.15
                + self._text_relevance_score(album_title, query) * 0.5
                + 12.0
            )
            top_candidates.append(
                {
                    "score": album_score,
                    "priority": 1,
                    "text": f"{album_title}\n{subtitle}",
                    "payload": {
                        "type": Type.Album,
                        "id": getattr(album, "id", ""),
                        "title": album_title,
                    },
                    "cover_id": getattr(album, "cover", None),
                    "cover_type": "Album",
                    "item_id": str(getattr(album, "id", "")),
                    "item_name": album_title,
                }
            )

        ranked_artists = self._rank_artists_for_top_results(artists, query, max_top_artists)
        for artist, artist_score in ranked_artists:
            artist_name = str(getattr(artist, "name", "Unknown Artist") or "Unknown Artist")
            subtitle = "Artist"
            top_candidates.append(
                {
                    "score": artist_score,
                    "priority": 3,
                    "text": f"{artist_name}\n{subtitle}",
                    "payload": {
                        "type": Type.Artist,
                        "id": getattr(artist, "id", ""),
                        "name": artist_name,
                    },
                    "cover_id": getattr(artist, "picture", None),
                    "cover_type": "Artist",
                    "item_id": str(getattr(artist, "id", "")),
                    "item_name": artist_name,
                }
            )

        top_candidates.sort(
            key=lambda entry: (
                float(entry.get("score", 0.0)),
                int(entry.get("priority", 0)),
            ),
            reverse=True,
        )

        for candidate in top_candidates[:max_top_results]:
            top_item = QListWidgetItem(str(candidate.get("text", "")))
            top_item.setData(Qt.ItemDataRole.UserRole, candidate.get("payload", {}))
            top_item.setSizeHint(QSize(0, self.TOP_RESULT_ITEM_HEIGHT))

            cover_id = candidate.get("cover_id")
            if cover_id:
                cover_attempts += 1
                self._queue_keyword_cover_load(
                    self.top_results_list,
                    top_item,
                    str(cover_id),
                    str(candidate.get("cover_type", "")),
                    str(candidate.get("item_id", "")),
                    current_generation,
                    item_name=str(candidate.get("item_name", "")),
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
                    item_name=album_title,
                )

            self.albums_grid_list.addItem(album_item)

        if self.albums_grid_list.count() == 0:
            self._add_disabled_info_item(self.albums_grid_list, "No albums found.")

        logger.warning(
            "SEARCH_UI_PERF_DIAG query='%s' tracks=%s albums=%s artists=%s cover_attempts=%s total_ms=%.1f",
            query,
            len(tracks),
            len(albums),
            len(artists),
            cover_attempts,
            (time.perf_counter() - start) * 1000.0,
        )

        self.set_search_results_page("top")

    @pyqtSlot(QListWidgetItem)
    def on_keyword_result_item_clicked(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return

        item_type = payload.get("type")
        item_id = payload.get("id")
        if not item_type or not item_id:
            return

        self.set_search_results_page("tracks")
        self._on_result_selected(payload)

    @pyqtSlot(dict)
    def on_live_result_selected_for_view(self, result_data: Dict[str, Any]) -> None:
        _ = result_data
        self.reset_keyword_search_views()

    @pyqtSlot(list, str)
    def on_data_fetched_for_search_view(self, results: list, error_msg: str) -> None:
        _ = (results, error_msg)
        if self.search_results_tabs_widget.isVisible():
            self.set_search_results_page("tracks")

    def reset_keyword_search_views(self) -> None:
        self.search_results_tabs_widget.setVisible(False)
        self.top_results_list.clear()
        self.albums_grid_list.clear()
        self.set_search_results_page("tracks")
