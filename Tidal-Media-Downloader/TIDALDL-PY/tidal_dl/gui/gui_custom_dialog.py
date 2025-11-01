# gui_custom_dialog.py

import logging
import os
from typing import Optional, Callable
from .. import paths

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QWidget,
    QStyleOption,
    QStyle,
    QApplication,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import (
    QPixmap,
    QIcon,
    QPainter,
    QFont,
    QFontDatabase,
)

logger = logging.getLogger(__name__)


class StyledWidget(QWidget):
    """A QWidget that ensures its stylesheet background is painted correctly, especially on macOS."""
    def paintEvent(self, a0):
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        style = self.style() or QApplication.style()
        if style:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        super().paintEvent(a0)


class ModernDarkDialog(QDialog):
    def __init__(
        self,
        title: str,
        main_message: str,
        informative_text: str,
        icon_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
        show_folder_path: Optional[str] = None,
        show_in_folder_func: Optional[Callable] = None,
    ):
        super().__init__(parent)

        self.title_text = title
        self.main_message = main_message
        self.informative_text = informative_text
        self.icon_path = icon_path
        self.show_folder_path = show_folder_path
        self.show_in_folder_func = show_in_folder_func

        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle(self.title_text)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.content_widget = StyledWidget(self)
        self.content_widget.setStyleSheet(
            """
            QWidget#dialogContentWidget {
                background-color: #2b2b2b;
                border-radius: 8px;
            }
            """
        )
        self.content_widget.setObjectName("dialogContentWidget")
        self.main_layout.addWidget(self.content_widget)

        overall_content_layout = QVBoxLayout(self.content_widget)
        overall_content_layout.setContentsMargins(0, 0, 0, 0)
        overall_content_layout.setSpacing(0)

        top_widget = QWidget(self)
        top_widget.setFixedHeight(50)
        top_widget.setStyleSheet("background-color: #202020; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(15, 0, 15, 0)

        title_label = QLabel(self.title_text)
        title_font = QFont("Segoe UI Variable", 10, QFont.Weight.Bold)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #E0E0E0; background-color: transparent;")
        top_layout.addWidget(title_label)
        top_layout.addStretch(1)

        self.close_button = QPushButton("✕")
        self.close_button.setFixedSize(QSize(30, 30))
        self.close_button.setStyleSheet(
            """
            QPushButton { font-family: "Segoe UI Symbol", "Arial"; font-size: 12pt; color: #AAAAAA; background-color: transparent; border: none; border-radius: 4px; }
            QPushButton:hover { color: #FFFFFF; background-color: #CC0000; }
            QPushButton:pressed { background-color: #990000; }
            """
        )
        self.close_button.clicked.connect(self.reject)
        top_layout.addWidget(self.close_button)
        overall_content_layout.addWidget(top_widget)

        middle_layout = QVBoxLayout()
        middle_layout.setContentsMargins(20, 20, 20, 20)
        middle_layout.setSpacing(15)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(15)

        if self.icon_path:
            self.icon_label = QLabel(self)
            pixmap = QPixmap(self.icon_path)
            if not pixmap.isNull():
                self.icon_label.setPixmap(pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.icon_label.setFixedSize(48, 48)
            self.icon_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            content_layout.addWidget(self.icon_label)

        text_layout = QVBoxLayout()
        self.main_message_label = QLabel(self.main_message, self)
        self.main_message_label.setFont(QFont("Segoe UI Variable", 12, QFont.Weight.Bold))
        self.main_message_label.setStyleSheet("color: #F0F0F0;")
        self.main_message_label.setWordWrap(True)
        text_layout.addWidget(self.main_message_label)

        self.informative_text_label = QLabel(self.informative_text, self)
        self.informative_text_label.setFont(QFont("Segoe UI Variable", 10))
        self.informative_text_label.setStyleSheet("color: #C0C0C0;")
        self.informative_text_label.setWordWrap(True)
        
        # --- MODIFICATION START: Fix for clickable link ---
        logger.debug(f"Configuring informative_text_label. Text contains '</a>': {'</a>' in self.informative_text}")
        self.informative_text_label.setTextFormat(Qt.TextFormat.RichText)
        self.informative_text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.informative_text_label.setOpenExternalLinks(True)
        logger.debug(f"Set openExternalLinks to: {self.informative_text_label.openExternalLinks()}")
        # --- MODIFICATION END ---
        
        text_layout.addWidget(self.informative_text_label)
        
        content_layout.addLayout(text_layout, 1)
        middle_layout.addLayout(content_layout)
        overall_content_layout.addLayout(middle_layout, 1)

        bottom_widget = QWidget(self)
        bottom_widget.setFixedHeight(60)
        bottom_widget.setStyleSheet("background-color: #202020; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;")
        bottom_layout = QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(20, 0, 20, 0)

        if self.show_folder_path:
            self.show_in_folder_button = QPushButton("Show in Folder")
            self.show_in_folder_button.setFont(QFont("Segoe UI Variable", 10))
            self.show_in_folder_button.setStyleSheet(
                """
                QPushButton { background-color: #2d2d2d; color: white; border: 1px solid #404040; border-radius: 5px; padding: 7px 15px; min-width: 80px; }
                QPushButton:hover { background-color: #3f3f3f; }
                QPushButton:pressed { background-color: #1f1f1f; }
                QPushButton:focus { outline: none; border: 1px solid #404040; }
                """
            )
            folder_icon_path = paths.resource_path("assets/icons/folder-white.png")
            if os.path.exists(folder_icon_path):
                self.show_in_folder_button.setIcon(QIcon(folder_icon_path))
                self.show_in_folder_button.setIconSize(QSize(16, 16))

            # --- MODIFICATION START: Fix for optional callable ---
            if self.show_in_folder_func:
                # Assign to a local variable to help the type checker
                # understand it's not None inside the lambda.
                show_func = self.show_in_folder_func
                self.show_in_folder_button.clicked.connect(lambda: show_func(self.show_folder_path))
            # --- MODIFICATION END ---
            bottom_layout.addWidget(self.show_in_folder_button)

        bottom_layout.addStretch(1)

        self.ok_button = QPushButton("OK")
        self.ok_button.setFont(QFont("Segoe UI Variable", 10))
        self.ok_button.setStyleSheet(
            """
            QPushButton { background-color: #2d2d2d; color: white; border: 1px solid #404040; border-radius: 5px; padding: 7px 20px; min-width: 70px; }
            QPushButton:hover { background-color: #3f3f3f; }
            QPushButton:pressed { background-color: #1f1f1f; }
            QPushButton:focus { outline: none; border: 1px solid #404040; }
            """
        )
        self.ok_button.clicked.connect(self.accept)
        bottom_layout.addWidget(self.ok_button)
        overall_content_layout.addWidget(bottom_widget)

        self.ok_button.setDefault(True)
        self.ok_button.setFocus()
        self.setFixedSize(450, 280)

    # --- MODIFICATION START: Fix for incompatible override ---
    def paintEvent(self, a0):
    # --- MODIFICATION END ---
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        super().paintEvent(a0)


# Example Usage (for testing this file directly):
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    from tidal_dl import paths

    app = QApplication(sys.argv)

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
                print(f"Font file not found: {font_path}")
        app.setFont(QFont("Nationale", 11))
    except Exception as e:
        print(f"Could not load fonts: {e}")

    info_icon_path = None
    try:
        info_icon_path = paths.resource_path("assets/icons/info_icon.png")
        if not os.path.exists(info_icon_path):
            print(f"Info icon not found at resolved path: {info_icon_path}. Creating placeholder.")
            placeholder_pixmap = QPixmap(64, 64)
            placeholder_pixmap.fill(Qt.GlobalColor.blue)
            placeholder_painter = QPainter(placeholder_pixmap)
            placeholder_painter.setPen(Qt.GlobalColor.white)
            placeholder_painter.setFont(QFont("Arial", 32, QFont.Weight.Bold))
            placeholder_painter.drawText(placeholder_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "i")
            placeholder_painter.end()
            os.makedirs(os.path.dirname(info_icon_path), exist_ok=True)
            placeholder_pixmap.save(info_icon_path)
            print(f"Created placeholder icon at: {info_icon_path}")
        else:
            print(f"Found icon at: {info_icon_path}")
    except Exception as e:
        print(f"Error getting icon path: {e}")
        fallback_icon_path = os.path.join(os.path.dirname(__file__), "..", "assets", "icons", "info_icon.png")
        if os.path.exists(fallback_icon_path):
            info_icon_path = fallback_icon_path
            print(f"Using fallback icon path: {info_icon_path}")
        else:
            print(f"Fallback icon path also not found: {fallback_icon_path}")
            info_icon_path = None

    dialog = ModernDarkDialog(
        title="Spotify Credentials Missing",
        main_message="Please enter Spotify Client ID and Secret in the 'Spotify Account Settings' section.",
        informative_text=(
            "After entering them, click 'Save' at the bottom of the settings page, then try connecting to Spotify again."
        ),
        icon_path=info_icon_path,
    )
    dialog.exec()
    sys.exit(app.exec())