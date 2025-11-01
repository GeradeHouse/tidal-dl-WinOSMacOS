#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_resize_handler.py
@Time    :   2025-04-28
@Author  :   Roo Code
@Version :   1.0
@Desc    :   Handles window resizing logic for a frameless window.
"""
import logging
import time  # Add time import
from typing import Optional

from PyQt6 import QtCore, QtGui
from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QCursor  # Added QCursor

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module


class ResizeHandler:
    """Handles window resizing logic by processing mouse events."""

    THROTTLE_INTERVAL = 0.5  # 500ms - Define as class attribute

    def __init__(
        self,
        parent_window: QWidget,
        title_bar: Optional[QWidget],
        border_width: int = 5,
    ):  # Allow Optional QWidget
        """
        Initializes the ResizeHandler.

        Args:
            parent_window: The main window widget to be resized.
            title_bar: The custom title bar widget (to exclude its area from resizing). Can be None initially.
            border_width: The width of the border area sensitive to resizing clicks.
        """
        self.parent_window = parent_window
        self.title_bar = title_bar  # Can be None initially
        self.BORDER_WIDTH = border_width

        # --- Resizing State Variables ---
        self._resizing: bool = False
        self._resize_edge: Optional[Qt.Edge] = None
        self._resize_start_pos: QPoint = QPoint()
        self._resize_start_geometry: Optional[QRect] = None

        # --- Throttling Timers --- Initialize in __init__
        self._last_log_time_get_edge = 0.0
        self._last_log_time_move_status = 0.0

    def handle_mouse_press(self, event: QtGui.QMouseEvent) -> bool:
        """
        Handles mouse press events to initiate resizing.

        Args:
            event: The QMouseEvent.

        Returns:
            True if the event was handled (resizing started), False otherwise.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            # Pass local position to _get_resize_edge
            self._resize_edge = self._get_resize_edge(event.position().toPoint())
            if self._resize_edge:
                self._resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geometry = self.parent_window.geometry()
                logger.debug(f"Resize started on edge: {self._resize_edge}")
                event.accept()
                return True
        return False

    def handle_mouse_move(self, event: QtGui.QMouseEvent) -> bool:
        """
        Handles mouse move events for resizing or changing the cursor.

        Args:
            event: The QMouseEvent.

        Returns:
            True if the event was handled (resizing occurred or cursor changed), False otherwise.
        """
        pos = event.position().toPoint()
        global_pos = event.globalPosition().toPoint()
        handled = False
        current_cursor = self.parent_window.cursor().shape()  # Get current cursor shape

        # --- Throttled Logging for move status ---
        current_time = time.time()  # Use imported time
        # Access THROTTLE_INTERVAL via self
        log_move_status = (
            current_time - self._last_log_time_move_status > self.THROTTLE_INTERVAL
        )
        if log_move_status:
            logger.debug(
                f"ResizeHandler.handle_mouse_move: Entered. Pos={pos}. Resizing: {self._resizing}. Accepted: {event.isAccepted()}. Current Cursor: {current_cursor}"
            )
            # Don't update time here yet, update at the end of the block

        if self._resizing and self._resize_edge:
            delta = global_pos - self._resize_start_pos
            new_geometry = self._calculate_new_geometry(delta)
            if (
                new_geometry is not None
                and new_geometry != self.parent_window.geometry()
            ):
                # logger.debug(f"Current Geo: {self.parent_window.geometry()}, Delta: {delta}, Calc New Geo: {new_geometry}") # Can be noisy
                self.parent_window.setGeometry(new_geometry)
            if not event.isAccepted():  # Accept only if not already accepted
                event.accept()
                logger.debug("  ResizeHandler: Accepted event during resize.")
            handled = True  # Event handled by resizing

        elif not self._resizing:  # Only change cursor if not actively resizing
            # Pass local position to _get_resize_edge
            edge = self._get_resize_edge(event.position().toPoint())  # Use local pos
            if edge:
                cursor_shape = None
                # Check for diagonal cursors
                if edge == (Qt.Edge.TopEdge | Qt.Edge.LeftEdge) or edge == (
                    Qt.Edge.BottomEdge | Qt.Edge.RightEdge
                ):
                    cursor_shape = Qt.CursorShape.SizeFDiagCursor
                elif edge == (Qt.Edge.TopEdge | Qt.Edge.RightEdge) or edge == (
                    Qt.Edge.BottomEdge | Qt.Edge.LeftEdge
                ):
                    cursor_shape = Qt.CursorShape.SizeBDiagCursor
                # Check horizontal/vertical
                elif edge == Qt.Edge.TopEdge or edge == Qt.Edge.BottomEdge:
                    cursor_shape = Qt.CursorShape.SizeVerCursor
                elif edge == Qt.Edge.LeftEdge or edge == Qt.Edge.RightEdge:
                    cursor_shape = Qt.CursorShape.SizeHorCursor

                if cursor_shape:
                    if current_cursor != cursor_shape:
                        logger.debug(
                            f"  ResizeHandler: Edge detected: {edge}. Setting cursor: {cursor_shape}"
                        )
                        self.parent_window.setCursor(cursor_shape)
                    else:
                        logger.debug(
                            f"  ResizeHandler: Edge detected: {edge}. Cursor already {cursor_shape}. No change."
                        )
                else:
                    # This case should ideally not happen if edge detection is correct
                    logger.warning(
                        f"  ResizeHandler: Edge detected: {edge}, but no specific cursor shape matched. Unsetting."
                    )
                    if current_cursor != Qt.CursorShape.ArrowCursor:
                        self.parent_window.unsetCursor()

                if not event.isAccepted():  # Accept only if not already accepted
                    event.accept()
                    logger.debug(
                        "  ResizeHandler: Accepted event after setting/checking cursor."
                    )
                handled = True  # Event handled by setting cursor
            else:
                # If no edge is detected, unset the cursor only if it's not already the default arrow
                if current_cursor != Qt.CursorShape.ArrowCursor:
                    if log_move_status:  # Use the flag calculated at the start
                        logger.debug(
                            "  ResizeHandler: No edge detected. Unsetting cursor."
                        )
                    self.parent_window.unsetCursor()
                else:
                    if log_move_status:  # Use the flag calculated at the start
                        logger.debug(
                            "  ResizeHandler: No edge detected. Cursor already Arrow. No change."
                        )
                # We might still accept the event even if we didn't change the cursor,
                # to prevent further propagation if this handler is definitive.
                if not event.isAccepted():
                    event.accept()
                    logger.debug(
                        "  ResizeHandler: Accepted event after unsetting/checking cursor (no edge)."
                    )
                handled = True  # Event handled by unsetting/checking cursor

        # --- Update move status log time if logged ---
        if log_move_status:
            logger.debug(
                f"ResizeHandler.handle_mouse_move: Exiting. Handled: {handled}. Accepted: {event.isAccepted()}. Final Cursor: {self.parent_window.cursor().shape()}"
            )
            self._last_log_time_move_status = current_time  # Update time here

        return handled

    def handle_mouse_release(self, event: QtGui.QMouseEvent) -> bool:
        """
        Handles mouse release events to stop resizing.

        Args:
            event: The QMouseEvent.

        Returns:
            True if the event was handled (resizing stopped), False otherwise.
        """
        if event.button() == Qt.MouseButton.LeftButton and self._resizing:
            logger.debug("Resize finished.")
            self._resizing = False
            self._resize_edge = None
            self.parent_window.unsetCursor()  # Unset cursor when resizing finishes
            event.accept()
            return True
        return False

    def _get_resize_edge(
        self, pos: QPoint
    ) -> Optional[Qt.Edge]:  # Reverted input to QPoint
        """Determines which edge(s) the mouse position is near using local coordinates."""
        # Use local position relative to parent_window's rect
        rect = self.parent_window.rect()  # Get parent's local rect (0,0, width, height)

        # --- Throttled Logging for get_edge ---
        current_time = time.time()  # Use imported time
        # Access THROTTLE_INTERVAL via self
        log_get_edge = (
            current_time - self._last_log_time_get_edge > self.THROTTLE_INTERVAL
        )
        if log_get_edge:
            logger.debug(
                f"  _get_resize_edge: Pos={pos}, ParentRect={rect}"
            )  # Log local pos and parent rect
            # Don't update time here yet, update at the end

        # Check against parent's local dimensions
        on_left = pos.x() >= rect.left() and pos.x() < rect.left() + self.BORDER_WIDTH
        on_right = (
            pos.x() >= rect.right() - self.BORDER_WIDTH and pos.x() < rect.right()
        )
        # RE-ENABLE Top edge check using local coordinates relative to MainView
        on_top = pos.y() >= rect.top() and pos.y() < rect.top() + self.BORDER_WIDTH
        on_bottom = (
            pos.y() >= rect.bottom() - self.BORDER_WIDTH and pos.y() < rect.bottom()
        )
        if log_get_edge:  # Use the flag calculated earlier
            # Add on_top back to logging
            logger.debug(
                f"  _get_resize_edge: on_top={on_top}, on_bottom={on_bottom}, on_left={on_left}, on_right={on_right}"
            )
            self._last_log_time_get_edge = current_time  # Update time here

        edge = Qt.Edge(0)
        if on_top:
            edge |= Qt.Edge.TopEdge  # RE-ENABLE TopEdge check
        if on_bottom:
            edge |= Qt.Edge.BottomEdge
        if on_left:
            edge |= Qt.Edge.LeftEdge
        if on_right:
            edge |= Qt.Edge.RightEdge

        # Title bar conflict check is also not needed here.

        # Return detected edge(s) or None
        return edge if edge != Qt.Edge(0) else None

    def _calculate_new_geometry(self, delta: QPoint) -> Optional[QRect]:
        """Calculates the new window geometry based on the dragged edge/corner and delta."""
        if self._resize_start_geometry is None or self._resize_edge is None:
            return None

        logger.debug(
            f"Calculating geometry for edge: {self._resize_edge}, delta: {delta}"
        )  # Add this
        g = self._resize_start_geometry
        min_w, min_h = (
            self.parent_window.minimumSize().width(),
            self.parent_window.minimumSize().height(),
        )
        max_w = (
            self.parent_window.maximumSize().width()
            if self.parent_window.maximumSize().width() < 16777215
            else float("inf")
        )
        max_h = (
            self.parent_window.maximumSize().height()
            if self.parent_window.maximumSize().height() < 16777215
            else float("inf")
        )

        new_x, new_y, new_w, new_h = g.x(), g.y(), g.width(), g.height()
        logger.debug(
            f"  Start Geo: {g}, MinSize: ({min_w}, {min_h}), MaxSize: ({max_w}, {max_h})"
        )  # Add this

        # Handle Width Changes
        if self._resize_edge & Qt.Edge.LeftEdge:
            potential_w = g.width() - delta.x()
            clamped_w = max(min_w, min(potential_w, max_w))
            logger.debug(
                f"  LeftEdge: potential_w={potential_w}, clamped_w={clamped_w}"
            )  # Add this
            if clamped_w != g.width():
                new_x = g.right() - clamped_w
                logger.debug(f"  LeftEdge: new_x={new_x}")  # Add this
            new_w = clamped_w
        elif self._resize_edge & Qt.Edge.RightEdge:
            potential_w = g.width() + delta.x()
            new_w = max(min_w, min(potential_w, max_w))
            logger.debug(
                f"  RightEdge: potential_w={potential_w}, new_w={new_w}"
            )  # Add this

        # Handle Height Changes
        if self._resize_edge & Qt.Edge.TopEdge:
            potential_h = g.height() - delta.y()
            clamped_h = max(min_h, min(potential_h, max_h))
            logger.debug(f"  TopEdge: potential_h={potential_h}, clamped_h={clamped_h}")
            if clamped_h != g.height():
                new_y = g.bottom() - clamped_h
                logger.debug(f"  TopEdge: new_y={new_y}")
            new_h = clamped_h
        elif (
            self._resize_edge & Qt.Edge.BottomEdge
        ):  # Use elif for mutual exclusion with TopEdge (unless corner)
            potential_h = g.height() + delta.y()
            new_h = max(min_h, min(potential_h, max_h))
            logger.debug(
                f"  BottomEdge: potential_h={potential_h}, new_h={new_h}"
            )  # Add this

        final_rect = QRect(int(new_x), int(new_y), int(new_w), int(new_h))
        logger.debug(
            f"  Result: x={new_x}, y={new_y}, w={new_w}, h={new_h} -> {final_rect}"
        )  # Add this
        return final_rect
