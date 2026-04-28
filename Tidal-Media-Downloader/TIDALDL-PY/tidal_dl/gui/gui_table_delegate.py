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

TABLE_TEXT_MARGIN = 9

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
        option.features |= QtWidgets.QStyleOptionViewItem.ViewItemFeature.WrapText
        option.textElideMode = QtCore.Qt.TextElideMode.ElideRight

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        """Add breathing room around wrapped text inside each table row."""
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height() + (TABLE_TEXT_MARGIN * 2), 70))
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

        # If selected, pull out any custom brush and push it into the HighlightedText role
        if opt.state & QtWidgets.QStyle.StateFlag.State_Selected:
            brush = index.data(QtCore.Qt.ItemDataRole.ForegroundRole)
            if isinstance(brush, QtGui.QBrush):
                opt.palette.setBrush(QtGui.QPalette.ColorRole.HighlightedText, brush)

        opt.state &= ~QtWidgets.QStyle.StateFlag.State_Selected
        opt.state &= ~QtWidgets.QStyle.StateFlag.State_MouseOver

        # Finally paint the text with our modified palette and readable inset.
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            opt,
            painter,  # painter is guaranteed not None here
            opt.widget,
        )
