# -*- coding: utf-8 -*-
"""
Handles specific event processing for the MainView class.
"""
import logging
from typing import Optional, cast

# Import enableGui first
from .gui_utils import enableGui

# Import PyQt6 components directly.
# Runtime errors if PyQt6 is not installed are expected.
# enableGui() checks within methods prevent usage if GUI is disabled.
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence, QMouseEvent


# Assuming logger is configured elsewhere and accessible,
# or passed in if necessary. For now, let's import it directly.
# If this causes issues, we might need to pass the logger instance.
logger = logging.getLogger(__name__)
logger.setLevel(
    logging.ERROR
)  # Set specific level for this module to only receive warnings


class MainViewEventHandlers:
    """Class to encapsulate event handling logic for MainView."""

    def __init__(self, main_view):
        """
        Initializes the event handler.

        Args:
            main_view: The MainView instance this handler is associated with.
        """
        self.main_view = main_view
        # We might need access to the resize handler directly
        # self.resize_handler = main_view.resize_handler # Assuming it exists

    # ########## LOW-LEVEL EVENT HANDLER (for Debugging) ##########
    def handle_event(self, event: Optional[QEvent]) -> bool:  # Use direct type hint
        """
        Handles low-level events, specifically logging MouseMove.
        This method is intended to be called from MainView.event().
        It returns False to indicate the main event handler should still call super().
        """
        # Check enableGui() before accessing attributes
        if (
            enableGui() and event and event.type() == QEvent.Type.MouseMove
        ):  # Use direct class attribute
            try:
                # Cast to QMouseEvent to access position details
                mouse_event = cast(QMouseEvent, event)  # Use direct type hint
                local_pos = mouse_event.position().toPoint()
                global_pos = mouse_event.globalPosition().toPoint()
                # Log mouse move events reaching MainView
                logger.debug(
                    f"MainView.event(MouseMove) via Handler: LocalPos={local_pos}, GlobalPos={global_pos}"
                )
            except Exception as e:
                logger.error(f"Error logging MouseMove in Handler.handle_event: {e}")

        # Return False so the original MainView.event() calls super()
        return False

    # ########## EVENT HANDLERS ##########
    def handle_keyPressEvent(
        self, event: Optional[QKeyEvent]
    ) -> bool:  # Use direct type hint
        """
        Handles key press events for the main window (Select All, Copy).

        Returns:
            True if the event was handled, False otherwise.
        """
        # Check enableGui() before accessing attributes
        if enableGui() and event is not None:
            if event.matches(
                QKeySequence.StandardKey.SelectAll
            ):  # Use direct class attribute
                # Check focus on the correct widget instance via main_view
                if self.main_view.tableWidget and self.main_view.tableWidget.hasFocus():
                    self.main_view.tableWidget.selectAll()  # Use table's own selectAll
                event.accept()
                return True  # Event handled
            elif event.matches(
                QKeySequence.StandardKey.Copy
            ):  # Use direct class attribute
                if self.main_view.tableWidget and self.main_view.tableWidget.hasFocus():
                    self.main_view.tableWidget.copySelection()  # Use table's copy method
                event.accept()
                return True  # Event handled
        # If we reach here, the event was not handled by this logic
        return False

    # ########## RESIZING EVENT HANDLERS ##########
    def handle_mousePressEvent(
        self, event: Optional[QMouseEvent]
    ) -> bool:  # Use direct type hint
        """
        Handles mouse press: delegates to resize handler and grabs mouse if resize starts.

        Returns:
            True if the event was handled (by resize handler), False otherwise.
        """
        if event:
            # Access resize_handler via the main_view instance
            if self.main_view.resize_handler.handle_mouse_press(event):
                # If resize started, grab the mouse on the main_view
                self.main_view.grabMouse()
                logger.debug("MainViewEventHandlers: Resize started, grabbed mouse.")
                return True  # Resize handler handled it (and accepted event)
        # If resize didn't start or event is None, indicate not handled
        return False

    def handle_mouseMoveEvent(
        self, event: Optional[QMouseEvent]
    ) -> bool:  # Use direct type hint
        """
        Handles mouse move: delegates to resize handler.

        Returns:
            True if the event was handled (by resize handler), False otherwise.
        """
        if event:
            # Access resize_handler via the main_view instance
            if self.main_view.resize_handler.handle_mouse_move(event):
                return True  # Resize handler handled it (resizing or cursor)
        # Indicate not handled
        return False

    def handle_mouseReleaseEvent(
        self, event: Optional[QMouseEvent]
    ) -> bool:  # Use direct type hint
        """
        Handles mouse release: delegates to resize handler and releases mouse if resize stops.

        Returns:
            True if the event was handled (by resize handler), False otherwise.
        """
        if event:
            # Access resize_handler via the main_view instance
            if self.main_view.resize_handler.handle_mouse_release(event):
                # If resize handler stopped resizing, release the mouse on main_view.
                self.main_view.releaseMouse()
                logger.debug("MainViewEventHandlers: Resize finished, released mouse.")
                # Event was accepted by handler
                return True  # Handled by resize handler

        # Indicate not handled
        return False
