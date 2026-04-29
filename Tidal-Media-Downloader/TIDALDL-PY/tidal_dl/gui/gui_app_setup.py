#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_app_setup.py
@Time    :   2025/04/15
@Author  :   GeradeHouse
@Version :   1.0
@Contact :   gerade.house@gmail.com
@Desc    :   Handles the setup, initialization, and global exception handling for the GUI application.
"""
import logging
import os
import sys
import time
import traceback
from typing import TYPE_CHECKING, Any, Optional, cast

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, pyqtSlot, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QIcon

# Import project components
from ..paths import getProfilePath, getSettingsFilePath, getTokenPath, resource_path
from ..settings import SETTINGS
from ..login import TOKEN, initialize_and_login
from ..logging_config import setup_logging as setup_logging_file
from .gui_custom_dialog import CustomQMessageBox

if TYPE_CHECKING:
    from tidal_dl.gui.gui_main import MainView

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set specific level for this module

# Set up GUI logging with INFO level for this module
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from tidal_dl.gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()

# --- Global Exception Handling ---

def show_critical_error_dialog(
    title: str, text: str, informative_text: str, detailed_text: str
) -> None:
    """Displays a critical error message box."""
    # Remove HTML tags from text for main_message
    clean_text = text.replace("<b>", "").replace("</b>", "")
    CustomQMessageBox.critical(
        None, "Critical Error", title, f"{clean_text}\n\n{informative_text}\n\nDetails:\n{detailed_text}"
    )


def global_exception_handler(exctype: Any, value: Any, tb: Any) -> None:
    """
    Custom global exception handler to catch and log all unhandled exceptions.
    """
    # Format the traceback
    traceback_details = "".join(traceback.format_exception(exctype, value, tb))

    # Log the critical error
    log_message = f"Unhandled exception caught by global handler:\nType: {exctype.__name__}\nValue: {value}\nTraceback:\n{traceback_details}"
    logger.critical(log_message)

    # Prepare user-friendly messages
    error_title = "A critical error occurred:"
    error_text = f"<b>{exctype.__name__}:</b> {value}"
    informative_text = "The application might need to close. Please check the console output or 'gui_error.log' for details."

    # Show the dialog
    show_critical_error_dialog(
        error_title, error_text, informative_text, traceback_details
    )

    # Also log to a dedicated file for easier debugging
    try:
        log_path = os.path.join(getProfilePath(), "gui_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"--- Log Entry: {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(log_message)
            f.write("\n\n")
    except Exception as log_e:
        logger.error(f"Failed to write to emergency log file: {log_e}")

    # Cleanly exit the application
    app = QApplication.instance()
    if app:
        app.quit()


def setup_global_exception_handler() -> None:
    """Sets the custom global exception handler."""
    sys.excepthook = global_exception_handler
    logger.info("Global exception handler set.")


# --- Application Initialization ---

def initialize_settings_and_token() -> None:
    """
    Initializes global SETTINGS and TOKEN objects by reading from their files.
    This must be called before any other part of the application uses them.
    """
    logger.debug("Initializing SETTINGS and TOKEN...")
    settings_path = getSettingsFilePath()
    token_path = getTokenPath()
    logger.debug(f"Settings path: {settings_path}")
    logger.debug(f"Token path: {token_path}")
    SETTINGS.read(settings_path)
    TOKEN.read(token_path)
    logger.debug("SETTINGS and TOKEN initialized.")


def get_qapp_instance() -> Optional[QApplication]:
    """
    Safely gets the QApplication instance.
    Returns the existing instance or None if no instance exists.
    """
    return cast(Optional[QApplication], QApplication.instance())


def create_qapp_instance(args: Optional[list] = None) -> QApplication:
    """
    Creates a new QApplication instance if one doesn't already exist.
    """
    instance = get_qapp_instance()
    if instance is None:
        logger.debug("No QApplication instance found. Creating a new one.")
        if args is None:
            args = sys.argv
        # Set application attributes for better high-DPI scaling and styling
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        instance = QApplication(args)
    else:
        logger.debug("Returning existing QApplication instance.")
    return instance


# --- Logging Setup ---

def setup_logging(log_level: int = logging.INFO) -> None:
    """
    Configures the logging for the GUI application.
    """
    global logger
    setup_logging_file()
    logger = logging.getLogger(__name__)
    
    # Set up GUI logging with INFO level for this module (app setup operations need visibility)
    from tidal_dl.gui.gui_logging import setup_gui_logger
    setup_gui_logger(__name__, logging.INFO)
    logger.info("GUI logging configured.")


# --- Asynchronous Task Handling ---

class WorkerSignals(QObject):
    """
    Defines signals available from a running worker thread.
    """
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)
    progress = pyqtSignal(int)


class Worker(QRunnable):
    """
    Worker thread for executing long-running tasks without blocking the GUI.
    """
    def __init__(self, fn, *args, **kwargs):
        super(Worker, self).__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

        # Add the callback to our kwargs if the target function supports it
        if "progress_callback" in self.fn.__code__.co_varnames:
            self.kwargs["progress_callback"] = self.signals.progress

    @pyqtSlot()
    def run(self):
        """
        Initialise the runner function with passed args, kwargs.
        """
        try:
            result = self.fn(*self.args, **self.kwargs)
        except:
            traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.signals.error.emit((exctype, value, traceback.format_exc()))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


# --- Global Stylesheet ---
def apply_global_stylesheet(app: QApplication, font_size: int = 11):
    """Applies the global dark theme stylesheet to the application."""
    stylesheet = """
        /* General Window and Text */
        QWidget {{
            color: #ffffff; /* Default text color to white */
            font-family: "Nationale";
            font-size: {font_size}pt;
        }}

        /* ScrollBar Styling */
        QScrollBar:vertical {{
            border: none;
            background: #242429;
            width: 10px;
            margin: 0px 0px 0px 0px;
        }}
        QScrollBar::handle:vertical {{
            background: #555;
            min-height: 20px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: #666;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            border: none;
            background: none;
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none;
        }}

        QScrollBar:horizontal {{
            border: none;
            background: #242429;
            height: 10px;
            margin: 0px 0px 0px 0px;
        }}
        QScrollBar::handle:horizontal {{
            background: #555;
            min-width: 20px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: #666;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            border: none;
            background: none;
            width: 0px;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: none;
        }}

        /* Tree Widget Styling */
        QTreeWidget {{
            background-color: transparent;
            border: none;
            color: #ffffff; /* Ensure text is white */
            show-decoration-selected: 1;
        }}
        QTreeWidget::item {{
            padding: 4px 0px;
            color: #ffffff; /* Explicitly set item text color */
        }}
        QTreeWidget::item:hover {{
            background-color: #2d2d31;
            color: #ffffff !important; /* Ensure text stays white on hover */
        }}
        QTreeWidget::item:selected {{
            background-color: #3a3a3f;
        }}
        QTreeView::branch {{
            background: transparent;
        }}

        /* Table Widget Styling */
        QTableWidget {{
            background-color: transparent;
            gridline-color: rgba(255, 255, 255, 0.12);
            color: #f0f0f0;
            border: none;
            outline: 0;
            selection-background-color: rgba(58, 58, 63, 0.88);
            selection-color: #ffffff;
        }}
        QHeaderView::section {{
            background-color: rgba(36, 36, 41, 0.92);
            color: #d0d0d0;
            padding: 3px 5px;
            border: 1px solid rgba(255, 255, 255, 0.10);
            font-weight: bold;
        }}
        QTableWidget::item {{
            padding: 0px;
            border: none;
        }}
        QTableWidget::item:hover {{
            background-color: rgba(255, 255, 255, 0.045);
        }}
        QTableWidget::item:selected {{
            background-color: rgba(58, 58, 63, 0.88);
        }}
        QTableWidget::item:focus {{
            outline: 0;
            border: none;
        }}

        /* Other Widgets */
        QLineEdit {{
            background-color: #242429;
            border: 1px solid #444;
            border-radius: 3px;
            padding: 3px;
        }}
        QComboBox {{
            background-color: #2d2d31;
            border: 1px solid #444;
            border-radius: 3px;
            padding: 1px 18px 1px 3px;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 15px;
            border-left-width: 1px;
            border-left-color: #444;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }}
        QComboBox QAbstractItemView {{
            background-color: #2d2d31;
            border: 1px solid #444;
            selection-background-color: #3a3a3f;
        }}
        QMenu {{
            background-color: #2d2d31;
            border: 1px solid #444;
            color: white;
        }}
        QMenu::item:selected {{
            background-color: #3a3a3f;
        }}
    """
    app.setStyleSheet(stylesheet.format(font_size=font_size))
    logger.info(f"Global dark stylesheet applied with font size {font_size}pt.")


# --- Main Application Runner ---

class AppRunner:
    """
    Main class to set up and run the GUI application.
    """
    def __init__(self, log_level: int = logging.INFO):
        self.log_level = log_level
        self.app: Optional[QApplication] = None
        self.main_view: Optional["MainView"] = None
        self.thread_pool = QThreadPool()
        logger.info(
            f"Multithreading with maximum {self.thread_pool.maxThreadCount()} threads."
        )

    def setup(self) -> None:
        """
        Complete setup of the application environment.
        """
        # Correct order: Set up logging first, then initialize everything else.
        setup_logging(self.log_level)
        setup_global_exception_handler()
        
        # This function now handles settings, tokens, AND login attempts.
        initialize_and_login()
        
        self.app = create_qapp_instance()

        if self.app:
            apply_global_stylesheet(self.app, SETTINGS.fontSize)
            try:
                icon_path = resource_path("assets/icons/icon-tidal-dl-gui.png")
                if os.path.exists(icon_path):
                    self.app.setWindowIcon(QIcon(icon_path))
                else:
                    logger.warning(f"Application icon not found at: {icon_path}")
            except Exception as e:
                logger.error(f"Failed to set application icon: {e}")

    def run(self) -> int:
        """
        Creates the main window and starts the application event loop.
        """
        if not self.app:
            raise RuntimeError(
                "Application has not been set up. Call setup() before run()."
            )

        from tidal_dl.gui.gui_main import MainView

        self.main_view = MainView()
        self.main_view.show()

        exit_code = self.app.exec()
        logger.info(f"Application exiting with code {exit_code}.")
        return exit_code

    def execute_task(
        self,
        fn: Any,
        on_result: Any,
        on_error: Optional[Any] = None,
        on_finished: Optional[Any] = None,
    ) -> None:
        """
        Executes a function in a background thread.
        """
        worker = Worker(fn)
        worker.signals.result.connect(on_result)
        if on_error:
            worker.signals.error.connect(on_error)
        if on_finished:
            worker.signals.finished.connect(on_finished)
        self.thread_pool.start(worker)


# --- GUI Entry Point ---

def start_gui(log_level: int = logging.INFO) -> int:
    """
    The main entry point for starting the GUI application.
    """
    runner = AppRunner(log_level)
    runner.setup()
    return runner.run()
