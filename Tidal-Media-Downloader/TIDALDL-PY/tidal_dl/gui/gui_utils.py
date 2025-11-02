#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_utils.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Utility functions and classes for the Tidal Media Downloader GUI.
"""

import importlib.util
import logging
import sys
from typing import Optional
import re

from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtWidgets import QMessageBox, QTreeWidgetItem, QTextEdit, QWidget, QLabel
from PyQt6.QtWidgets import QTreeWidgetItem
from PyQt6.QtGui import QTextCursor
from .gui_custom_dialog import ModernDarkDialog
from .. import paths
from aigpy import systemHelper
import os
import subprocess
from typing import Callable

from ..settings import SETTINGS

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (utility operations)
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

# ########## GUI INITIALIZATION CHECK ##########


def enableGui() -> bool:
    """
    Checks if the necessary GUI libraries (PyQt6) are available.

    Returns:
        bool: True if PyQt6 seems available, False otherwise.
    """
    try:
        # Check 1: Can the PyQt6 package be found?
        if importlib.util.find_spec("PyQt6") is None:
            raise ImportError("PyQt6 specification not found.")
        # If check passes, GUI can likely be enabled.
        logger.debug("GUI dependencies (PyQt6) found.")
        return True
    except ImportError as e:
        # Log the specific import error for debugging.
        logger.error(
            f"GUI dependencies not met. Failed to import or find required package: {e}"
        )
        return False
    except Exception as e:
        # Catch any other unexpected errors during the check.
        logger.error(
            f"An unexpected error occurred while checking GUI dependencies: {e}"
        )
        return False


# ########## HELPER FUNCTIONS ##########


def safeLen(obj) -> int:
    """
    Safely returns the length of an object.

    Args:
        obj: The object whose length is needed.

    Returns:
        int: The length of the object if it has a `__len__` method and is not None,
             otherwise returns 0.
    """
    try:
        return len(obj) if obj is not None else 0
    except TypeError:
        # Object might not have a __len__ method
        return 0


def safeSetText(widget, text: str | None):
    """
    Safely sets the text of a widget, handling None values.

    Attempts to set the text of the provided widget. If the widget is a
    QTreeWidgetItem, it sets the text of the first column (index 0).
    If the input `text` is None, it sets an empty string. Catches
    AttributeError if the widget doesn't have a `setText` method.

    Args:
        widget: The Qt widget (e.g., QLabel, QLineEdit, QTreeWidgetItem)
                on which to set the text.
        text (str | None): The text string to set, or None.
    """
    display_text = text if text is not None else ""
    try:
        if isinstance(widget, QTreeWidgetItem):
            # QTreeWidgetItem requires specifying the column index.
            widget.setText(0, display_text)
        else:
            # Assumes other widgets have a standard setText method.
            widget.setText(display_text)
    except AttributeError:
        logger.warning(
            f"safeSetText called on an object without a setText method or incompatible type: {type(widget)}"
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in safeSetText for widget {type(widget)}: {e}",
            exc_info=True,
        )


def show_info_message(
    parent: Optional[QWidget],
    title: str,
    main_message: str,
    informative_text: str,
    icon_path: str,
    show_folder_path: Optional[str] = None,
):
    """
    Displays a custom informational dialog.
    Links in the informative text are automatically clickable.
    """
    dialog = ModernDarkDialog(
        title=title,
        main_message=main_message,
        informative_text=informative_text,
        icon_path=icon_path,
        parent=parent,
        show_folder_path=show_folder_path,
        show_in_folder_func=show_in_folder,
    )
    dialog.exec()


def show_in_folder(path: str):
    """
    Opens the specified path in the default file explorer.
    """
    try:
        norm_path = os.path.normpath(path)
        if sys.platform == "win32":
            logger.debug(f"Opening path on Windows: '{norm_path}'")
            os.startfile(norm_path)
        elif sys.platform == "darwin":
            command = ["open", norm_path]
            logger.debug(f"Opening path on macOS with command: {' '.join(command)}")
            subprocess.Popen(command)
        else:  # Assumes Linux or other Unix-like for xdg-open
            command = ["xdg-open", norm_path]
            logger.debug(f"Opening path on Linux with command: {' '.join(command)}")
            subprocess.Popen(command)
    except FileNotFoundError as e:
        logger.error(
            f"Could not open folder. The command was not found: {e}. "
            "Please ensure the corresponding file manager utility (e.g., xdg-open) is installed."
        )
    except Exception as e:
        logger.error(
            f"An unexpected error occurred while trying to open folder '{path}': {e}",
            exc_info=True,
        )


def format_duration_ms(ms: Optional[int]) -> str:
    """Formats duration in milliseconds to MM:SS string."""
    if ms is None:
        return "-"
    try:
        seconds = int(ms / 1000)
        minutes = seconds // 60
        seconds %= 60
        return f"{minutes:02}:{seconds:02}"
    except (ValueError, TypeError):
        logger.warning(f"Could not format duration from ms: {ms}")
        return "-"


# ########## UTILITY CLASSES ##########


class EmittingStream(QObject):
    """
    A QObject stream that emits a signal for text written to it.

    This class redirects stdout and stderr to a QTextEdit widget in the GUI
    by emitting the `textWritten` signal whenever its `write` method is called.
    It also writes the text to the original standard output (`sys.__stdout__`)
    to ensure messages still appear in the console if the application was
    launched from one.

    Signals:
        textWritten (str): Emitted when text is written to the stream.
    """

    textWritten = pyqtSignal(str)

    def write(self, text: str):
        """
        Writes text to the original stdout and emits a signal with the text.

        Args:
            text (str): The text to write and emit.
        """
        # Ensure output still goes to the console where the app was launched.
        try:
            if sys.__stdout__ is not None:
                sys.__stdout__.write(text)
                sys.__stdout__.flush()
        except Exception as e:
            # Fallback if writing to original stdout fails
            # Also check stderr before attempting to print the error message
            if sys.__stderr__ is not None:
                print(f"Error writing to sys.__stdout__: {e}", file=sys.__stderr__)
            # If stderr is also None, we can't easily report the error.
            # Consider logger or another fallback if this case needs handling.

        # Emit the signal to update the GUI.
        self.textWritten.emit(str(text))

    def flush(self):
        """
        Flushes the stream.

        This method is required for compatibility with the standard stream
        interface but may not perform any specific action for this GUI stream.
        It includes flushing the original stdout for completeness.
        """
        try:
            if sys.__stdout__ is not None:
                sys.__stdout__.flush()
        except Exception:
            pass  # Ignore errors flushing original stdout


def append_text_to_output(text_edit: QTextEdit, text: str):
    """
    Appends text to the output log QTextEdit widget.
    Only appends progress bar text if SETTINGS.showProgress is True.

    Args:
        text_edit (QTextEdit): The QTextEdit widget to append to.
        text (str): The text to append to the log view.
    """
    if text_edit:  # Check if the widget exists
        if not SETTINGS.showProgress:
            # Check if the text looks like a progress bar update
            progress_pattern = re.compile(r"\d+%.*|^\[.*\]")
            if progress_pattern.search(text):
                return  # Skip appending progress bar text

        cursor = text_edit.textCursor()
        # Move cursor to the end to append text.
        cursor.movePosition(QTextCursor.MoveOperation.End)
        # Insert the text.
        cursor.insertText(text)
        # Ensure the cursor (and thus the latest text) is visible.
        text_edit.ensureCursorVisible()