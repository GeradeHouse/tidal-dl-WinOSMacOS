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

        # Copy the incoming option so we don't mutate it in place
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        # If selected, pull out any custom brush and push it into the HighlightedText role
        if opt.state & QtWidgets.QStyle.StateFlag.State_Selected:
            brush = index.data(QtCore.Qt.ItemDataRole.ForegroundRole)
            if isinstance(brush, QtGui.QBrush):
                opt.palette.setBrush(QtGui.QPalette.ColorRole.HighlightedText, brush)

        # --- START FIX ---
        # Get the style object, which might be None.
        actual_style: Optional[QtWidgets.QStyle]
        if opt.widget is not None:
            actual_style = opt.widget.style()
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

        # Finally paint with our modified palette
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            opt,
            painter,  # painter is guaranteed not None here
            opt.widget,
        )