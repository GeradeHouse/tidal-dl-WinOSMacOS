import logging
import time
from PyQt6.QtWidgets import QStackedLayout, QWidget
from PyQt6.QtCore import QObject, pyqtSlot  # Import pyqtSlot correctly

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Set up GUI logging with INFO level for this module (navigation operations)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class NavigationHandler(QObject):
    """Handles switching between main application views."""

    # Add type hint for parent: Optional[QObject]
    def __init__(
        self,
        stacked_layout: QStackedLayout,
        main_page: QWidget,
        settings_page: QWidget,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.stacked_layout = stacked_layout
        self.main_page = main_page
        self.settings_page = settings_page
        logger.debug("NavigationHandler initialized.")

    @pyqtSlot()  # Decorator should now be recognized
    def show_settings(self):
        """Switches the view to the Settings page."""
        if self.stacked_layout and self.settings_page:
            logger.info("Settings navigation requested")
            started = time.perf_counter()
            try:
                self.stacked_layout.setCurrentWidget(self.settings_page)
            finally:
                logger.info(
                    "Settings page switch returned | elapsed_ms=%.1f",
                    (time.perf_counter() - started) * 1000.0,
                )
        else:
            logger.error("Cannot switch to settings: Layout or page missing.")

    @pyqtSlot()  # Decorator should now be recognized
    def show_main_menu(self):
        """Switches the view back to the Main page."""
        if self.stacked_layout and self.main_page:
            logger.debug("Switching view to Main page.")
            self.stacked_layout.setCurrentWidget(self.main_page)
        else:
            logger.error("Cannot switch to main menu: Layout or page missing.")
