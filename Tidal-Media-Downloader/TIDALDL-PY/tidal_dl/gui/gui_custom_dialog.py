# gui_custom_dialog.py

import logging
import os  # Added import for os
from typing import Optional, List, Callable
from tidal_dl import paths

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import (
    QPixmap,
    QIcon,
    QPainter,
    QColor,
    QPainterPath,
    QFont,
    QFontDatabase,
)  # Added QFontDatabase

logger = logging.getLogger(__name__)


class ModernDarkDialog(QDialog):
    def __init__(
        self,
        title: str,
        main_message: str,
        informative_text: str,
        icon_path: Optional[str] = None,  # For your 'i' icon
        parent: Optional[QWidget] = None,
        show_folder_path: Optional[str] = None,
        show_in_folder_func: Optional[Callable] = None,
    ):
        super().__init__(parent)

        self.title_text = (
            title  # Store for potential use, though not in a separate title bar
        )
        self.main_message = main_message
        self.informative_text = informative_text
        self.icon_path = icon_path
        self.show_folder_path = show_folder_path
        self.show_in_folder_func = show_in_folder_func

        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle(
            self.title_text
        )  # Sets window title, not visible in frameless
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)  # We will paint the background

        # --- Main Layout ---
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(
            0, 0, 0, 0
        )  # No margins for the QDialog itself
        self.main_layout.setSpacing(0)

        # --- Content Widget (for background color and rounded corners) ---
        # This widget will hold all content and have the dark background & border-radius
        self.content_widget = QWidget(self)
        # Apply QSS directly here for simplicity, or it can be part of a global style
        self.content_widget.setStyleSheet(
            f"""
            QWidget#dialogContentWidget {{
                background-color: transparent; /* Overall widget is transparent */
                border-radius: 8px;     /* Rounded corners for the whole dialog */
            }}
        """
        )
        self.content_widget.setObjectName("dialogContentWidget")
        self.main_layout.addWidget(self.content_widget)

        # --- Overall Layout for the three layers inside content_widget ---
        overall_content_layout = QVBoxLayout(self.content_widget)
        overall_content_layout.setContentsMargins(0, 0, 0, 0)
        overall_content_layout.setSpacing(0)  # No spacing between layers

        # --- Top Layer (Title Bar Area) ---
        top_widget = QWidget(self)
        top_widget.setFixedHeight(70)
        top_widget.setStyleSheet("background-color: #202020;")
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(15, 0, 15, 0)  # L/R padding for title area

        title_spacer = QLabel(self.title_text)
        title_font = QFont(
            "Segoe UI Variable", 10, QFont.Weight.Bold
        )  # Make title bold
        title_spacer.setFont(title_font)
        title_spacer.setStyleSheet(
            "color: #E0E0E0; background-color: transparent; padding-left: 5px;"
        )
        top_layout.addWidget(title_spacer)
        top_layout.addStretch(1)

        self.close_button = QPushButton("✕")
        self.close_button.setFixedSize(QSize(30, 30))
        self.close_button.setStyleSheet(
            """
            QPushButton {
                font-family: "Segoe UI Symbol", "Arial";
                font-size: 12pt;
                color: #AAAAAA;
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { color: #FFFFFF; background-color: #CC0000; }
            QPushButton:pressed { background-color: #990000; }
        """
        )
        self.close_button.clicked.connect(self.reject)
        top_layout.addWidget(self.close_button)
        overall_content_layout.addWidget(top_widget)

        # --- Middle Layer (Icon and Main Message) ---
        middle_widget = QWidget(self)
        middle_widget.setStyleSheet("background-color: #2b2b2b;")
        middle_layout = QVBoxLayout(middle_widget)
        middle_layout.setContentsMargins(20, 7, 40, 15)  # Reduced top margin
        middle_layout.setSpacing(15)  # Spacing from original content_v_layout

        # Define labels first, as their creation order might have been mixed with layout logic before
        self.main_message_label = QLabel(self.main_message, self)
        self.main_message_label.setFont(
            QFont("Segoe UI Variable", 13, QFont.Weight.Bold)
        )
        self.main_message_label.setStyleSheet(
            "color: #F0F0F0; background-color: transparent;"
        )
        self.main_message_label.setWordWrap(True)

        self.informative_text_label = QLabel(self.informative_text, self)
        self.informative_text_label.setFont(QFont("Segoe UI Variable", 10))
        self.informative_text_label.setStyleSheet(
            "color: #C0C0C0; background-color: transparent;"
        )
        self.informative_text_label.setWordWrap(True)

        # Icon label setup
        self.icon_label = None  # Initialize icon_label to None
        if self.icon_path:
            self.icon_label = QLabel(self)  # Create instance only if path exists
            pixmap = QPixmap(self.icon_path)
            if not pixmap.isNull():
                self.icon_label.setPixmap(
                    pixmap.scaled(
                        48,
                        48,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:  # Fallback if icon loading fails
                logger.warning(f"Could not load icon from: {self.icon_path}")
                self.icon_label.setText("ℹ")
                self.icon_label.setStyleSheet(
                    "font-size: 32pt; color: #0078D4; background-color: transparent;"
                )  # Fallback style
            self.icon_label.setFixedSize(48, 48)
            self.icon_label.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )  # Center content (text/pixmap) within the QLabel itself
            # Ensure icon background is transparent, appending to any existing style (like fallback)
            self.icon_label.setStyleSheet(
                self.icon_label.styleSheet() + "background-color: transparent;"
            )

        # --- Assemble Middle Layer Content ---
        # Stretches will vertically center the block of (main_message + icon_informative_text_layout)
        middle_layout.addStretch(1)

        # 1. Add Main Message Label
        # Added to QVBoxLayout (middle_layout), so it will be left-aligned by default and take available width.
        middle_layout.addWidget(self.main_message_label)

        # 2. Create a QHBoxLayout for Icon and Informative Text (placed below main_message_label)
        # This layout will be affected by middle_layout's setSpacing(15) for its top margin.
        icon_informative_text_layout = QHBoxLayout()
        icon_informative_text_layout.setSpacing(
            10
        )  # Spacing between icon and informative text

        if self.icon_label:
            # Add icon, aligned left and top within its cell in the QHBoxLayout
            icon_informative_text_layout.addWidget(
                self.icon_label,
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            )
        else:
            # If no icon, add a spacer to mimic icon width, so informative text aligns consistently
            icon_informative_text_layout.addSpacerItem(
                QSpacerItem(48, 1, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            )

        # Add informative text, it will take the remaining horizontal space (stretch factor 1)
        icon_informative_text_layout.addWidget(self.informative_text_label, 1)

        middle_layout.addLayout(icon_informative_text_layout)
        middle_layout.addStretch(1)
        overall_content_layout.addWidget(
            middle_widget, 1
        )  # Middle widget takes stretch factor

        # --- Bottom Layer (Button Area) ---
        bottom_widget = QWidget(self)
        bottom_widget.setFixedHeight(70)
        bottom_widget.setStyleSheet("background-color: #202020;")
        bottom_layout = QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(20, 0, 20, 0)  # L/R padding for button area

        if self.show_folder_path:
            self.show_in_folder_button = QPushButton("Show in Folder")
            self.show_in_folder_button.setFont(QFont("Segoe UI Variable", 10))

            # Set styles for the new button, assuming a similar look to the OK button
            self.show_in_folder_button.setStyleSheet(
                """
                QPushButton {
                    background-color: #2d2d2d;
                    color: white;
                    border: 1px solid #404040;
                    border-radius: 5px;
                    padding: 7px 15px; /* Adjust padding for icon */
                    min-width: 80px;
                }
                QPushButton:hover { background-color: #3f3f3f; }
                QPushButton:pressed { background-color: #1f1f1f; }
                QPushButton:focus { outline: none; border: 1px solid #404040; }
            """
            )

            # Set the icon for the "Show in Folder" button
            folder_icon_path = paths.resource_path("assets/icons/folder-white.png")
            if os.path.exists(folder_icon_path):
                self.show_in_folder_button.setIcon(QIcon(folder_icon_path))
                self.show_in_folder_button.setIconSize(QSize(16, 16))

            if self.show_in_folder_func:
                self.show_in_folder_button.clicked.connect(lambda: self.show_in_folder_func(self.show_folder_path))  # type: ignore
            bottom_layout.addWidget(self.show_in_folder_button)

        bottom_layout.addStretch(1)

        self.ok_button = QPushButton("OK")
        self.ok_button.setFont(QFont("Segoe UI Variable", 10))
        self.ok_button.setStyleSheet(
            """
            QPushButton {
                background-color: #2d2d2d; /* New background color */
                color: white;
                border: 1px solid #404040;  Subtle border */
                border-radius: 5px;
                padding: 7px 20px;
                min-width: 70px;
            }
            QPushButton:hover {
                background-color: #3f3f3f; /* Lighter shade for hover */
            }
            QPushButton:pressed {
                background-color: #1f1f1f; /* Darker shade for pressed */
            }
            QPushButton:focus {
                outline: none;
                border: 1px solid #404040; /* Match normal border, remove blue focus */
            }
        """
        )
        self.ok_button.clicked.connect(self.accept)
        bottom_layout.addWidget(self.ok_button)
        overall_content_layout.addWidget(bottom_widget)

        self.ok_button.setDefault(True)
        self.ok_button.setFocus()

        # Adjust dialog size: width fixed, height dynamic based on content
        # Total fixed height for top/bottom layers = 120px
        # Estimate middle content height, or let it adjust.
        # For now, let's use adjustSize() and see, then refine if needed.
        # self.setFixedSize(450, top_widget.height() + middle_widget.sizeHint().height() + bottom_widget.height() + 2)
        # self.adjustSize() # Let Qt try to determine the best size first
        # # If adjustSize is not enough, a fixed width and dynamic height might be better:
        # if self.width() < 450 : self.setFixedWidth(450)
        # self.setFixedHeight(top_widget.height() + middle_widget.sizeHint().height() + bottom_widget.height() + overall_content_layout.spacing() * 2)

        # Set fixed size with 5:4 aspect ratio (Width: 450, Height: 360)
        fixed_width = 450
        fixed_height = int((fixed_width / 5) * 4)
        self.setFixedSize(fixed_width, fixed_height)

    # Override paintEvent to draw custom rounded background for the QDialog itself
    # This is needed because WA_TranslucentBackground makes the QDialog's own background transparent.
    # The content_widget inside will have its own QSS-defined background.
    def paintEvent(self, a0):  # Changed event to a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Path for the dialog's shape (e.g., for a drop shadow or outer border if needed)
        # For now, we just ensure the area is clear if no shadow is drawn.
        # If you want a shadow, you'd draw a semi-transparent rounded rect here *before*
        # the content_widget is drawn by the normal Qt paint cycle.

        # Example: To ensure the area *outside* content_widget is transparent
        # path = QPainterPath()
        # path.addRoundedRect(self.rect(), 8, 8) # Match content_widget's radius
        # painter.fillPath(path, Qt.GlobalColor.transparent) # This might not be necessary

        super().paintEvent(a0)  # Changed event to a0


# Example Usage (for testing this file directly):
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    # Assuming paths.py is one level up from gui directory
    # Adjust the import based on your project structure if this script is moved
    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    from tidal_dl import paths  # Corrected import for paths

    app = QApplication(sys.argv)

    # --- Load custom fonts (example from your gui_app_setup.py) ---
    try:
        font_dir = paths.resource_path("assets/fonts")
        font_files = [
            "nationale-regular.otf",
            "nationale-medium.otf",
            "nationale-demibold.otf",
        ]
        for font_file in font_files:
            font_path = os.path.join(font_dir, font_file)
            if os.path.exists(font_path):
                QFontDatabase.addApplicationFont(font_path)
            else:
                print(f"Font file not found: {font_path}")  # Added for debugging
        app.setFont(QFont("Nationale", 11))
    except Exception as e:
        print(f"Could not load fonts: {e}")
    # --- End Font Loading ---

    # --- Icon Path ---
    # User provided path: 'Tidal-Media-Downloader\TIDALDL-PY\tidal_dl\assets\icons\info_icon.png'
    # This needs to be relative to the project root or an absolute path.
    # paths.resource_path should handle this if 'assets/icons/info_icon.png' is correct
    # relative to the assets directory known by paths.py
    info_icon_path = None  # Initialize
    try:
        # The path from user is 'Tidal-Media-Downloader\TIDALDL-PY\tidal_dl\assets\icons\info_icon.png'
        # paths.resource_path expects a path relative to the 'tidal_dl' package's resources.
        # So, if 'assets' is directly under 'tidal_dl', then 'assets/icons/info_icon.png' is correct.
        # If your project root is 'Tidal-Media-Downloader/TIDALDL-PY/',
        # and paths.py resolves 'assets' from 'Tidal-Media-Downloader/TIDALDL-PY/tidal_dl/assets',
        # then the following should work.
        info_icon_path = paths.resource_path("assets/icons/info_icon.png")

        # Create a placeholder if it doesn't exist for testing (only if path is resolved but file missing)
        if not os.path.exists(info_icon_path):
            print(
                f"Info icon not found at resolved path: {info_icon_path}. Creating placeholder."
            )
            placeholder_pixmap = QPixmap(64, 64)
            placeholder_pixmap.fill(Qt.GlobalColor.blue)
            placeholder_painter = QPainter(placeholder_pixmap)
            placeholder_painter.setPen(Qt.GlobalColor.white)
            placeholder_painter.setFont(QFont("Arial", 32, QFont.Weight.Bold))
            placeholder_painter.drawText(
                placeholder_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "i"
            )
            placeholder_painter.end()
            os.makedirs(os.path.dirname(info_icon_path), exist_ok=True)
            placeholder_pixmap.save(info_icon_path)
            print(f"Created placeholder icon at: {info_icon_path}")
        else:
            print(f"Found icon at: {info_icon_path}")

    except Exception as e:
        print(f"Error getting icon path: {e}")
        # Fallback if paths.resource_path fails or icon truly doesn't exist
        # Try a direct relative path from this script's location for testing if all else fails
        # This assumes gui_custom_dialog.py is in tidal_dl/gui/
        # and assets is tidal_dl/assets/
        fallback_icon_path = os.path.join(
            os.path.dirname(__file__), "..", "assets", "icons", "info_icon.png"
        )
        if os.path.exists(fallback_icon_path):
            info_icon_path = fallback_icon_path
            print(f"Using fallback icon path: {info_icon_path}")
        else:
            print(f"Fallback icon path also not found: {fallback_icon_path}")
            info_icon_path = None  # Ensure it's None if no icon found
    # --- End Icon Path ---

    dialog = ModernDarkDialog(
        title="Spotify Credentials Missing",
        main_message="Please enter Spotify Client ID and Secret in the 'Spotify Account Settings' section.",
        informative_text=(
            "After entering them, click 'Save' at the bottom of the settings page, then try connecting to Spotify again."
        ),
        icon_path=info_icon_path,
    )  # Pass your 'i' icon path here
    dialog.exec()
    sys.exit(app.exec())
