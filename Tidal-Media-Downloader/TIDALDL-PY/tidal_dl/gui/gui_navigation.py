import logging
from PyQt6.QtWidgets import QStackedLayout, QWidget
from PyQt6.QtCore import QObject, pyqtSlot  # Import pyqtSlot correctly

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set specific level for this module


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
            logger.debug("Switching view to Settings page.")
            self.stacked_layout.setCurrentWidget(self.settings_page)
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
