# --- START OF FILE gui_app_setup.py ---

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_app_setup.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Handles application-level setup for the Tidal Media Downloader GUI.
"""

import sys

print("DEBUG_TRACE: gui_app_setup.py - Top level execution start", file=sys.stderr)

import os
import time
import logging
import traceback
import importlib.util
import types
from types import TracebackType
from typing import cast, Type, Set, Optional  # Added Optional


# --- Logging Setup (Call ASAP) ---
from ..logging_config import setup_logging

setup_logging()
print("DEBUG_TRACE: gui_app_setup.py - About to call setup_logging()", file=sys.stderr)
logging.info("Application starting...")  # Log *after* setup
print("DEBUG_TRACE: gui_app_setup.py - setup_logging() finished", file=sys.stderr)
# --- End Logging Setup ---


# Import necessary Qt components
from PyQt6.QtWidgets import QApplication, QMessageBox, QTextEdit
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6 import QtWidgets

# Import project components
import aigpy  # type: ignore
from ..settings import SETTINGS, TOKEN
from .gui import MainView

# Import paths module to get resource_path function
from .. import paths  # Import the paths module itself
from ..paths import (
    getSettingsFilePath,
    getTokenPath,
)  # Keep specific imports if needed elsewhere
from .gui_utils import EmittingStream, append_text_to_output  # Import utility
from .gui import MainView  # Moved from start_gui_application

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set specific level for this module

# ########## GLOBAL EXCEPTION HANDLING ##########


def handle_exception(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_traceback: Optional[TracebackType],  # Allow None for traceback
) -> None:
    """
    Global exception handler to catch unhandled errors, log them,
    and attempt to show a message to the user before potentially exiting.
    """
    # Ignore KeyboardInterrupt to allow normal Ctrl+C termination in console.
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)  # Call default handler
        return

    # Format the traceback into a string.
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

    # Log the critical error using the logger module.
    logger.critical(
        f"Unhandled exception caught by global handler:\n"
        f"Type: {exc_type.__name__}\n"
        f"Value: {exc_value}\n"
        f"Traceback:\n{error_msg}"
    )

    # Also attempt to print directly to stderr in case logger is broken.
    print(
        f"CRITICAL ERROR (GLOBAL HANDLER):\n"
        f"Type: {exc_type.__name__}\n"
        f"Value: {exc_value}\n"
        f"Traceback:\n{error_msg}",
        file=sys.__stderr__,
    )

    # Try to write the error to a dedicated log file.
    try:
        # Use profile path for error log for better portability/permissions
        error_log_path = os.path.join(paths.getProfilePath(), "gui_error.log")
        with open(error_log_path, "a", encoding="utf-8") as f:
            f.write(
                f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} (Unhandled Exception) ---\n"
            )
            f.write(error_msg + "\n")
    except Exception as log_e:
        # If logger to file fails, print another message to stderr.
        print(f"ERROR: Could not write to gui_error.log: {log_e}", file=sys.__stderr__)

    # Attempt to show a critical error message box to the user.
    try:
        app = QApplication.instance()  # Get the current application instance
        if app:
            # Create and show a message box.
            error_dialog = QMessageBox()
            error_dialog.setIcon(QMessageBox.Icon.Critical)
            error_dialog.setWindowTitle("Unhandled Application Error")
            error_dialog.setText(f"A critical error occurred:\n\n{exc_value}")
            error_dialog.setInformativeText(
                "The application might need to close. Please check the console output or 'gui_error.log' for details."
            )
            error_dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
            error_dialog.exec()
            # Optionally, attempt a graceful shutdown after user acknowledges.
            # app.quit()
    except Exception as msg_e:
        print(
            f"ERROR: Could not display error message box: {msg_e}", file=sys.__stderr__
        )


print(
    "DEBUG_TRACE: gui_app_setup.py - About to call load_initial_settings_and_token()",
    file=sys.stderr,
)


def register_global_exception_handler() -> None:
    """Assigns the custom global exception handler."""
    sys.excepthook = handle_exception
    logger.info("Global exception handler registered.")


# ########## APPLICATION SETUP FUNCTIONS ##########


def load_initial_settings_and_token() -> None:
    """Loads application settings and token information from files."""
    try:
        SETTINGS.read(getSettingsFilePath())
        logger.info(f"Settings loaded from: {getSettingsFilePath()}")
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
    print(
        "DEBUG_TRACE: gui_app_setup.py - load_initial_settings_and_token() finished",
        file=sys.stderr,
    )
    # Continue with default settings if loading fails

    try:
        TOKEN.read(getTokenPath())
        logger.info(f"Token loaded from: {getTokenPath()}")
    except Exception as e:
        logger.error(f"Failed to load token: {e}")
        # Application might still function if web login is used.


def create_application() -> QApplication:
    """Creates the QApplication instance."""
    # Disable color codes in console output when GUI is active (handled by Printf).
    aigpy.cmd.enableColor(False)  # type: ignore
    # The QApplication instance manages the GUI application's control flow and settings.
    app = QApplication(sys.argv)  # Pass command line arguments to Qt
    return app


def make_progress_bar_style(
    font_size: int = 12,
    bar_height: int = 20,
    # chunk_alpha parameter is no longer needed
) -> str:
    """
    Returns a QSS string for a QProgressBar with:
    • text at `font_size`px
    • fixed height `bar_height`px
    • solid chunk fill color.
    """
    # Define the solid color for the chunk
    chunk_solid_color = "#20867a"  # Your desired darker color

    return f"""
    QProgressBar {{
        /* force fixed height */
        min-height: {bar_height}px;
        max-height: {bar_height}px;

        border: 1px solid #444444; /* Keep a subtle border for the bar itself */
        border-radius: 3px;

        /* center & show the percentage text */
        text-align: center;
        qproperty-textVisible: true;

        /* font size for the % text */
        font-size: {font_size}px;

        /* bar background (trough) and text color */
        background-color: #242429; /* Dark background for the trough */
        color: #ffffff;            /* White text for percentage */
    }}
    QProgressBar::chunk {{
        /* chunk matches bar height */
        min-height: {bar_height}px;
        max-height: {bar_height}px;

        /* solid fill color for the chunk */
        background-color: {chunk_solid_color};
        border-radius: 2px; /* Slightly smaller radius than the bar for a nice inset look */
        margin: 1px; /* Optional: small margin to make the chunk appear inset within the bar's border */
    }}
    """


def apply_styling(app: QApplication) -> None:
    """Applies stylesheets to the application."""
    # --- Custom Scrollbar Styling ---
    # (Keeping scrollbar styles as they were)
    scrollbar_stylesheet = """
        QScrollBar:vertical {
            border: none;
            background: #121212; /* Dark surface color */
            width: 8px;          /* Width of the vertical scroll bar */
            margin: 0px 0px 0px 0px; /* Remove margins, handled by add/sub-line */
        }
        QScrollBar::handle:vertical {
            background: #2d2d2d; /* Thumb color */
            min-height: 20px;     /* Minimum height of the handle */
            border-radius: 4px;   /* Slightly less rounded corners */
            margin: 12px 0px 12px 0px; /* Add margin to keep handle away from buttons */
        }
        QScrollBar::handle:vertical:hover {
            background: #404040; /* Handle hover color */
        }
        QScrollBar::add-line:vertical {
            border: none;
            background: #1e1e1e; /* Button background */
            height: 12px;         /* Height of the arrow buttons */
            subcontrol-position: bottom;
            subcontrol-origin: margin;
        }
        QScrollBar::sub-line:vertical {
            border: none;
            background: #1e1e1e; /* Button background */
            height: 12px;         /* Height of the arrow buttons */
            subcontrol-position: top;
            subcontrol-origin: margin;
        }
        QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
             background: none; /* Hide default arrows */
             width: 0px; height: 0px;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none; /* Make the track area use scrollbar background */
        }

        /* Horizontal Scrollbar */
        QScrollBar:horizontal {
            border: none;
            background: #121212; /* Dark surface color */
            height: 8px;         /* Height of the horizontal scroll bar */
            margin: 0px 0px 0px 0px; /* Remove margins, handled by add/sub-line */
        }
        QScrollBar::handle:horizontal {
            background: #2d2d2d; /* Thumb color */
            min-width: 20px;      /* Minimum width of the handle */
            border-radius: 4px;   /* Slightly less rounded corners */
            margin: 0px 12px 0px 12px; /* Add margin to keep handle away from buttons */
        }
         QScrollBar::handle:horizontal:hover {
            background: #404040; /* Handle hover color */
        }
        QScrollBar::add-line:horizontal {
             border: none;
             background: #1e1e1e; /* Button background */
             width: 12px;          /* Width of the arrow buttons */
             subcontrol-position: right;
             subcontrol-origin: margin;
        }
         QScrollBar::sub-line:horizontal {
             border: none;
             background: #1e1e1e; /* Button background */
             width: 12px;          /* Width of the arrow buttons */
             subcontrol-position: left;
             subcontrol-origin: margin;
         }
         QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal {
             background: none; /* Hide default arrows */
             width: 0px; height: 0px;
         }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: none; /* Make the track area use scrollbar background */
        }
    """

    # --- Base Styling ---
    # (Keeping base styles as they were)
    base_style = """
        QWidget {
            background-color: #000000; /* Black background */
            color: #ffffff; /* Default white text for widgets */
        }
        /* QMessageBox Styling - START MODERNIZATION */
        QMessageBox {
            background-color: #2B2B2B; /* Dark background, slightly different from #0f0f0f for a bit more depth */
            color: #E0E0E0;            /* Default text color for the dialog (e.g., title if not overridden) */
            border: 1px solid #3c3c3c; /* Subtle border */
            border-radius: 8px;        /* Windows 11 style rounded corners for the dialog */
            font-family: "Segoe UI Variable", "Segoe UI", sans-serif; /* Modern Windows font */
            padding: 20px;             /* Increased padding for more breathing room */
        }

        /* Styles the main message text label (e.g., "Spotify Client ID and Secret are required.") */
        QMessageBox QLabel#qt_msgbox_label {
            color: #F0F0F0;             /* Bright text for the main message */
            background-color: transparent;
            font-size: 11pt;            /* Slightly larger font for the main message */
            font-weight: bold;
            padding-bottom: 10px;       /* Space between main message and informative text */
            /* qproperty-alignment is not needed if text is naturally left-aligned */
        }

        /* Styles the informative text label (the longer explanation) */
        /* The selector 'QMessageBox QLabel#qt_msgbox_info QLabel' might be too specific or incorrect.
           QMessageBox usually uses 'qt_msgbox_informativelabel' for the informative text.
           If the old selector worked, keep it. If not, try the one below. */
        QMessageBox QLabel#qt_msgbox_informativelabel { /* Common object name for informative text */
            color: #C0C0C0;             /* Slightly dimmer text for less critical details */
            background-color: transparent;
            font-size: 10pt;
            /* qproperty-alignment is not needed if text is naturally left-aligned */
        }
        /* Fallback if the above doesn't target informative text, try a more generic QLabel within QMessageBox,
           but this might affect other labels if present. Be cautious.
        QMessageBox > QLabel {
            color: #C0C0C0;
            font-size: 10pt;
        }
        */

        /* Styles the icon (e.g., the 'i' information icon) */
        QMessageBox QLabel#qt_msgboxex_icon_label { /* Common object name for the icon label */
            padding-right: 10px; /* Add some space between the icon and the text */
        }

        QMessageBox QPushButton {
            background-color: #0078D4; /* Windows 11 accent blue for buttons */
            color: white;
            border: 1px solid #005A9E; /* Slightly darker border for definition */
            border-radius: 6px;        /* Rounded corners for buttons */
            padding: 8px 20px;         /* Generous padding for buttons */
            min-width: 80px;           /* Ensure buttons have a decent minimum width */
            font-size: 10pt;
            font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
            margin-top: 10px;          /* Add some margin above the button bar */
        }

        QMessageBox QPushButton:hover {
            background-color: #005A9E; /* Darker shade on hover */
            border: 1px solid #003C6A;
        }

        QMessageBox QPushButton:pressed {
            background-color: #003C6A; /* Even darker when pressed */
        }

        QMessageBox QPushButton:focus { /* Optional: Custom focus indicator */
            outline: none; /* Remove default dotted outline */
            border: 2px solid #00BFFF; /* Example: Deep sky blue focus border */
        }
        /* QMessageBox Styling - END MODERNIZATION */
        QTableWidget {
            background-color: transparent;
            /* color: #ffffff; */ /* Default text color for table items can be inherited or set in ::item */
            border: none;
            gridline-color: transparent;
        }
        QTableWidget::item {
            background-color: transparent;
            alternate-background-color: transparent;
            border-bottom: 1px solid #2A2A2A;
            padding: 1px 1px;
            font-size: 9.0pt; /* MODIFIED: Was 10pt */
            /* color: #ffffff; */ /* REMOVED: Default item text color will be handled by Qt.ForegroundRole or inherited */
        }
        QTableWidget::item:selected {
            background-color: rgba(56, 56, 56, 0.8); /* Selection background ONLY */
            /* CRITICAL: DO NOT set 'color' (text color) here.
               This allows the programmatically set foreground color
               (e.g., red for manual_review_needed) to persist
               even when the item is selected. */
        }
        QTableWidget::item:hover {
            background-color: rgba(255, 255, 255, 0.05);
            /* For hover, you can choose:
               1. Don't set 'color': Programmatic color (red/white) persists.
               2. Set 'color: #ffffff;': Hovered items always have white text, overriding red.
               Let's go with option 1 for consistency with the selection behavior. */
        }

        /* --- QSS RULES FOR CUSTOM ROLE REMOVED --- */
        /* Styling for manualLinkRequired items will be handled programmatically */
        /* by SplitterTable._update_row_appearance_for_row */
        /* --- END REMOVED QSS RULE --- */

        QHeaderView::section {
             font-family: 'Nationale';
             font-weight: 700;
             font-size: 10pt;
             color: #ffffff;
             background-color: rgba(40, 40, 40, 0.85);
             padding: 8px 5px 12px 5px;
             border: none;
             border-bottom: 1px solid #2A2A2A;
             text-align: left;
        }
        QHeaderView::section:first {
            padding-left: 2px;
        }
        QTextEdit {
            background-color: #242429;
            color: #ffffff;
            border: none;
            font-family: Consolas, monospace;
            padding-left: 5px;
        }
        QComboBox::drop-down {
            border: none;
        }
        QSplitter::handle {
            background-color: #121212;
            height: 1px;
            width: 1px;
        }
    """

    # --- Widget-Specific Styling (including dropdown arrow fix) ---
    # Get the correct path for the dropdown icon using resource_path
    # Ensure forward slashes for QSS url() compatibility
    try:
        # Use the imported paths module to call resource_path
        down_arrow_icon_path = paths.resource_path(
            "assets/icons/icon-down-arrow.png"
        ).replace("\\", "/")
        logger.debug(f"Resolved dropdown icon path: {down_arrow_icon_path}")
    except Exception as e:
        logger.error(
            f"Failed to resolve resource path for dropdown icon: {e}. Using fallback."
        )
        down_arrow_icon_path = ""  # Fallback to no icon if path fails

    # Define widget-specific styles using an f-string for the icon path
    widget_stylesheet_chunk_1 = f"""
        /* Specific style for the playlist tree background */
        #playlistTreeWidget {{
            /* Make the tree widget itself transparent */
            background-color: transparent;
            border: none;
        }}
        /* Set the desired background on the viewport - REVERTING THIS LATER */
        /* Let's make viewport transparent for now to test parent painting */
        #playlistTreeWidget QAbstractItemView::viewport {{
             /* background-color: rgba(36, 36, 41, 0.85); */
             background-color: transparent; /* Make viewport transparent */
             border: none;
        }}
        QTreeWidget {{
            /* background-color: #242429; */ /* Base background for the tree widget area - REMOVED, set in gui.py */
            border: none; /* Remove default border */
            outline: none; /* Add this line */
        }}
        QTreeWidget::item {{ /* Default Tree Item Style */
             border: none;
             padding: 0px 4px 0px 12px; /* Top, Right, Bottom, Left padding */
             margin-bottom: 2px; /* Add vertical space between items */
             color: #ffffff;
             /* Explicitly set a default background for items if needed, e.g., transparent or a base color */
             /* background-color: transparent; */ /* Example */
        }}
QTreeWidget::item[isRootItem="true"] {{
            margin-bottom: 10px; /* Add space below root items */
        }}
    """
    # widget_stylesheet_chunk_2_hover_only is no longer needed as hover is handled by QSS :hover

    widget_stylesheet_chunk_2 = f"""
        QTreeWidget::item:hover {{
            /* background-color: #2d2d31 !important; */  /* REMOVED: Delegate will handle hover background */
            color: white !important;           /* QSS HOVER TEXT IS WHITE and important */
            /* border-radius: 10px; */ /* Remove the general border-radius */
            border-top-left-radius: 0px;
            border-bottom-left-radius: 0px;
            border-top-right-radius: 10px;
            border-bottom-right-radius: 10px;
        }}

        /* Tree selection style (Cyan text for active or inactive) */
        QTreeWidget::item:selected {{ /* Combined rule for selected items */
            /* background-color: transparent !important; */ /* Allow hover to override selected background */
            color: #33ffe9 !important;
            border-left: 3px solid #00E4E3; /* Added left border for selection */
            border-radius: 6px; margin: 1px 8px 1px 1px; padding: 1px; /* Adjusted left margin */
            outline: none; /* Remove dotted focus border */
        }}
        /* If you want selected items to also show yellow hover: */
        /*
        QTreeWidget::item:selected:hover {{
            background-color: yellow;
            color: black;
        }}
        */

        QTreeWidget::item:focus {{ /* Explicitly remove focus outline/border/background */
            outline: none;
            border: none;
            background-color: transparent; /* Ensure focus doesn't add a background */
        }}
        /* Add or modify this rule for the combined state */
        QTreeWidget::item:selected:focus {{
            outline: none; /* Remove outline */
            border: none;  /* Remove border */
            /* The background/color should be inherited from the :selected rule */
        }}
    """

    # This full widget_stylesheet is now mostly for other widgets, QTreeWidget::item specific parts are in chunks
    widget_stylesheet = f"""
         QPushButton {{
            font-family: 'Nationale';
            font-weight: 600;
            background-color: #323237; /* Set button background color */
            border: none; /* Optional: Remove default button border */
            padding: 5px 10px; /* Optional: Add some padding */
            border-radius: 3px; /* Optional: Slightly rounded corners */
         }}
         QPushButton:hover {{
            background-color: #404045; /* Darker gray on hover */
         }}
         QLineEdit, QComboBox {{ /* Style search bar and dropdown */
            background-color: #242429;
            border: 1px solid #444444;
            padding: 3px;
            border-radius: 3px;
         }}
         /* QComboBox::down-arrow styling removed for testing */
        #mainContainerWidget {{
            background-color: transparent;
        }}
        #mainPageWidget {{
            background-color: transparent;
        }}
        /* Also ensure splitters are transparent if needed */
        QSplitter {{
             /* background-color: transparent; */ /* REMOVED - Let splitter have default background */
        }}
    """

    # --- Progress Bar Styling ---
    # Call without the alpha parameter
    progress_bar_style = make_progress_bar_style(font_size=12, bar_height=18)

    # --- Header Sort Indicator Styling ---
    try:
        up_arrow_icon_path = paths.resource_path(
            "assets/icons/icon-up-arrow.png"
        ).replace("\\", "/")
        down_arrow_icon_path = paths.resource_path(
            "assets/icons/icon-down-arrow.png"
        ).replace("\\", "/")
        logger.debug(
            f"Resolved header indicator icon paths: UP={up_arrow_icon_path}, DOWN={down_arrow_icon_path}"
        )
        header_indicator_style = f"""
            QHeaderView::down-arrow {{
                image: url("{down_arrow_icon_path}");
                width: 12px;
                height: 12px;
                subcontrol-position: center right;
                padding-right: 5px;
            }}
            QHeaderView::up-arrow {{
                image: url("{up_arrow_icon_path}");
                width: 12px;
                height: 12px;
                subcontrol-position: center right;
                padding-right: 5px;
            }}
        """
    except Exception as e:
        logger.error(
            f"Failed to resolve resource paths for header indicator icons: {e}. Indicators may not appear."
        )
        header_indicator_style = ""  # Fallback to no style if paths fail

    # Combine all stylesheets
    # The main `widget_stylesheet` now contains general widget styles.
    # `widget_stylesheet_chunk_1` and `widget_stylesheet_chunk_2` contain QTreeWidget specific styles.
    combined_stylesheet = (
        base_style
        + scrollbar_stylesheet
        + widget_stylesheet_chunk_1  # Basic QTreeWidget and item styles
        + widget_stylesheet_chunk_2  # QTreeWidget item hover, selected, focus
        + widget_stylesheet  # Other general widget styles (QPushButton, QLineEdit, etc.)
        + header_indicator_style
        + progress_bar_style
    )

    # logger.debug(f"--- Combined Stylesheet START ---\n{combined_stylesheet}\n--- Combined Stylesheet END ---") # Too verbose for regular logs
    logger.debug(f"--- FINAL QSS BEING APPLIED TO APP ---")
    logger.debug(
        f"Combined Stylesheet Snippet for Table Items:\n"
        f"QTableWidget::item {{\n"
        f"    background-color: transparent; \n"
        f"    alternate-background-color: transparent; \n"
        f"    border-bottom: 1px solid #2A2A2A; \n"
        f"    padding: 1px 1px; \n"
        f"    font-size: 9.5pt; \n"  # MODIFIED
        f"    color: #ffffff; \n"
        f"}}\n"
        f"QTableWidget::item:selected {{\n"
        f"    background-color: rgba(56, 56, 56, 0.8);\n"
        f"}}\n"
        f"QTableWidget::item:hover {{\n"
        f"    background-color: rgba(255, 255, 255, 0.05);\n"
        f"}}\n"
        f"/* QTableWidget::item[manualLinkRequired] rules removed from QSS */\n"
        f"}}"
    )
    logger.debug(f"--- END OF SNIPPET ---")
    try:
        app.setStyleSheet(combined_stylesheet)
        logger.debug("Successfully applied combined stylesheets to app.")
    except Exception as e:
        logger.error(f"Error applying stylesheet to app: {e}", exc_info=True)
        raise


def load_custom_fonts(app: QApplication) -> None:
    """Loads custom fonts and sets the default application font."""
    # Use resource_path to get the correct font directory relative to the bundle/script
    try:
        font_dir = paths.resource_path("assets/fonts")
        logger.debug(f"Attempting to load fonts from directory: {font_dir}")
    except Exception as e:
        logger.error(
            f"Failed to resolve resource path for fonts directory: {e}. Cannot load custom fonts."
        )
        return  # Exit if font directory path cannot be determined

    font_files = [
        "nationale-regular.otf",
        "nationale-medium.otf",
        "nationale-demibold.otf",
        "nationale-bold.otf",
        "nationale-light.otf",
        "nationale-black.otf",
    ]
    loaded_font_families: Set[str] = set()

    for font_file in font_files:
        font_path = os.path.join(font_dir, font_file)
        if os.path.exists(font_path):
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id != -1:
                # Get all families associated with the font ID (usually one)
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    family = families[0]  # Use the first family name
                    loaded_font_families.add(family)
                    logger.debug(
                        f"Successfully loaded font: {font_file} (Family: {family})"
                    )
                else:
                    logger.error(f"Loaded font {font_file} but no family name found.")
            else:
                logger.error(
                    f"Failed to load font: {font_file} from path: {font_path} (QFontDatabase returned -1)"
                )
        else:
            logger.error(f"Font file not found: {font_path}")

    # --- Set Default Application Font ---
    if "Nationale" in loaded_font_families:
        default_font = QFont("Nationale", 11)  # Increased default size to 11pt
        default_font.setWeight(
            QFont.Weight.Normal
        )  # Explicitly set Regular weight (400)
        app.setFont(default_font)
        logger.info("Set default application font to Nationale Regular.")
    else:
        logger.error("Nationale font family failed to load. Using system default font.")


def setup_stdout_stderr_redirection(output_widget: QTextEdit) -> None:
    """Redirects stdout and stderr to the provided QTextEdit widget."""

    def write_to_output(text: str) -> None:
        append_text_to_output(output_widget, text)

    stdout_stream = EmittingStream()
    stdout_stream.textWritten.connect(write_to_output)
    sys.stdout = stdout_stream  # type: ignore # Ignore potential type mismatch

    stderr_stream = EmittingStream()
    stderr_stream.textWritten.connect(write_to_output)
    sys.stderr = stderr_stream  # type: ignore # Ignore potential type mismatch

    logger.info("Redirected stdout and stderr to GUI output widget.")


print(
    "DEBUG_TRACE: gui_app_setup.py - About to call start_gui_application()",
    file=sys.stderr,
)


def start_gui_application(main_view_class: Type[QtWidgets.QWidget]) -> int:
    """
    Initializes and starts the PyQt6 GUI application.

    Args:
        main_view_class: The class of the main window (e.g., MainView).
    """
    print(
        "DEBUG_TRACE: gui_app_setup.py - Inside start_gui_application()",
        file=sys.stderr,
    )
    logger.info("Starting GUI application setup...")
    try:
        # --- Create QApplication ---
        app = create_application()

        # --- Apply Styling ---
        apply_styling(app)

        # --- Load Custom Fonts ---
        load_custom_fonts(app)

        # --- Create Main Window ---
        # Import moved to top level
        print(
            "DEBUG_TRACE: gui_app_setup.py - BEFORE MainView instantiation",
            file=sys.stderr,
        )  # ADDED
        window: QtWidgets.QWidget = main_view_class()
        print(
            "DEBUG_TRACE: gui_app_setup.py - AFTER MainView instantiation",
            file=sys.stderr,
        )  # ADDED
        main_view_window = cast(MainView, window)  # Explicit cast for Pylance

        # --- Setup Stdout/Stderr Redirection ---
        # Requires the output widget to exist in the window instance
        if hasattr(window, "c_printTextEdit"):
            # Ensure the attribute is actually a QTextEdit
            output_widget = getattr(window, "c_printTextEdit")
            if isinstance(output_widget, QTextEdit):
                setup_stdout_stderr_redirection(output_widget)
            else:
                logger.error(
                    "Attribute 'c_printTextEdit' exists but is not a QTextEdit. Cannot redirect stdout/stderr."
                )
        else:
            logger.error(
                "Main window instance does not have 'c_printTextEdit'. Cannot redirect stdout/stderr."
            )

        # --- Show Main Window ---
        logger.info("Showing main window...")
        window.show()
        logger.info("Main window created and shown.")

        # --- Initial Checks (Moved to AuthHandler, triggered after window setup) ---
        if isinstance(window, MainView):
            main_view_window.auth_handler.check_initial_logins()
        else:
            logger.error(
                "Main window instance is not MainView."
            )  # Adjusted error message slightly

        # --- Start Event Loop ---
        logger.info(
            "Starting Qt application event loop (calling app.exec())..."
        )  # Log BEFORE
        exit_code = app.exec()  # Store the exit code
        # This log will only appear if the event loop exits *gracefully*
        logger.info(
            f"Qt application event loop finished normally with exit code {exit_code}."
        )  # Log AFTER
        return exit_code  # Return the actual exit code

    except Exception as e:
        # --- Critical Startup Error Handling ---
        error_str = traceback.format_exc()
        logger.critical(f"Critical error during GUI startup: {e}\n{error_str}")
        print(f"CRITICAL STARTUP ERROR: {e}\n{error_str}", file=sys.__stderr__)
        try:
            # Use profile path for error log
            error_log_path = os.path.join(paths.getProfilePath(), "gui_error.log")
            with open(error_log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} (GUI Startup Error) ---\n"
                )
                f.write(error_str + "\n")
        except Exception as log_e:
            print(
                f"ERROR: Could not write startup error to gui_error.log: {log_e}",
                file=sys.__stderr__,
            )

        try:
            # Ensure QApplication exists before showing message box
            if QApplication.instance():
                QMessageBox.critical(
                    None,
                    "Application Startup Failed",
                    f"A critical error prevented the GUI from starting:\n\n{e}\n\nCheck console or gui_error.log for details.",
                )
            else:
                # Fallback if QApplication itself failed
                print(
                    "ERROR: QApplication instance not available to show error message box.",
                    file=sys.__stderr__,
                )
        except Exception:
            pass  # Ignore errors showing the message box itself
        return 1  # Error code
