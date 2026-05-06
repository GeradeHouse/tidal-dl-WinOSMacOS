#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :  gui_table_delegate.py
@Date    :  2025/04/15
@Author  :  GeradeHouse
@Version :  1.0
@Desc    :  Custom item delegate for the SplitterTable to preserve foreground colors during selection.
"""

import logging
from typing import Optional

from PyQt6 import QtWidgets, QtCore, QtGui

TABLE_TEXT_MARGIN = 1

# --- Setup Logging ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

def _setup_gui_logging():
    try:
        from tidal_dl.gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        pass

_setup_gui_logging()


class HighlightPreservingDelegate(QtWidgets.QStyledItemDelegate):
    """Keep an item's Qt.ForegroundRole colour even when it is selected."""

    def _foreground_brush(self, index: QtCore.QModelIndex) -> Optional[QtGui.QBrush]:
        """Return the model foreground brush in a form the style palette can use."""
        foreground = index.data(QtCore.Qt.ItemDataRole.ForegroundRole)
        if isinstance(foreground, QtGui.QBrush):
            return foreground
        if isinstance(foreground, QtGui.QColor):
            return QtGui.QBrush(foreground)
        return None

    def _apply_foreground_brush(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        brush: QtGui.QBrush,
    ) -> None:
        """Force item foreground roles so QSS defaults do not hide per-cell colours."""
        for role in (
            QtGui.QPalette.ColorRole.Text,
            QtGui.QPalette.ColorRole.WindowText,
            QtGui.QPalette.ColorRole.HighlightedText,
        ):
            option.palette.setBrush(role, brush)

    def initStyleOption(
        self,
        option: Optional[QtWidgets.QStyleOptionViewItem],
        index: QtCore.QModelIndex,
    ) -> None:
        """Apply consistent readable table text alignment and wrapping."""
        if option is None:
            return
        super().initStyleOption(option, index)
        option.displayAlignment |= QtCore.Qt.AlignmentFlag.AlignVCenter
        option.features &= ~QtWidgets.QStyleOptionViewItem.ViewItemFeature.WrapText
        option.textElideMode = QtCore.Qt.TextElideMode.ElideRight

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        """Keep rows compact and single-line."""
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 40))
        return size

    def paint(
        self,
        painter: Optional[QtGui.QPainter],
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        # If painter is ever None (per PyQt6 signature), fall back to default behavior:
        if painter is None:
            super().paint(painter, option, index)
            return

        # --- START FIX ---
        # Get the style object, which might be None.
        actual_style: Optional[QtWidgets.QStyle]
        if option.widget is not None:
            actual_style = option.widget.style()
        else:
            actual_style = QtWidgets.QApplication.style()

        if actual_style is None:
            # This is a fallback if no style could be obtained.
            # It's highly unlikely for QApplication.style() to be None in a running app.
            logger.error(
                "HighlightPreservingDelegate: Critical error - QStyle object is None. Cannot paint item."
            )
            # Fall back to default painting behavior. Painter is guaranteed not None here.
            super().paint(painter, option, index)
            return

        # Now, actual_style is confirmed to be a QtWidgets.QStyle.
        style: QtWidgets.QStyle = actual_style
        # --- END FIX ---

        # Copy the incoming option so we don't mutate it in place
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        foreground_brush = self._foreground_brush(index)
        if foreground_brush is not None:
            self._apply_foreground_brush(opt, foreground_brush)

        # Draw the selection/hover panel over the full cell, then inset text for readability.
        background_option = QtWidgets.QStyleOptionViewItem(opt)
        background_option.text = ""
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            background_option,
            painter,
            background_option.widget,
        )

        opt.rect = style.subElementRect(
            QtWidgets.QStyle.SubElement.SE_ItemViewItemText,
            opt,
            opt.widget,
        ).adjusted(
            TABLE_TEXT_MARGIN,
            0,
            -TABLE_TEXT_MARGIN,
            0,
        )
        if opt.rect.width() <= 0:
            opt.rect = option.rect.adjusted(
                TABLE_TEXT_MARGIN,
                0,
                -TABLE_TEXT_MARGIN,
                0,
            )

        display_text = str(index.data(QtCore.Qt.ItemDataRole.DisplayRole) or "")
        if not display_text:
            return

        text_color = (
            foreground_brush.color()
            if foreground_brush is not None
            else opt.palette.color(QtGui.QPalette.ColorRole.Text)
        )
        font_metrics = QtGui.QFontMetrics(opt.font)
        elided_text = font_metrics.elidedText(
            display_text,
            opt.textElideMode,
            max(0, opt.rect.width()),
        )

        painter.save()
        painter.setFont(opt.font)
        painter.setPen(QtGui.QPen(text_color))
        alignment = opt.displayAlignment
        alignment_flags = alignment.value if hasattr(alignment, "value") else int(alignment)
        painter.drawText(opt.rect, alignment_flags, elided_text)
        painter.restore()
