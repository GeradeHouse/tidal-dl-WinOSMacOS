# file: gui_custom_dialog.py
#!/usr/bin/env python
# -*- encoding: utf-8 -*-

import logging
import os
from typing import Optional, Callable, List, Tuple
from .. import paths

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QStyleOption,
    QStyle,
    QApplication,
    QCheckBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import (
    QPixmap,
    QIcon,
    QPainter,
    QFont,
    QColor,
    QPalette,
)

logger = logging.getLogger(__name__)

# Set up GUI logging
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class StyledWidget(QWidget):
    """A QWidget that ensures its stylesheet background is painted correctly."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, a0):
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        style = self.style() or QApplication.style()
        if style:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)


class ModernDarkDialog(QDialog):
    def __init__(
        self,
        title: str,
        main_message: str,
        informative_text: str = "",
        icon_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
        buttons: List[str] = ["OK"],  # Options: ["OK"], ["Yes", "No"]
        show_folder_path: Optional[str] = None,
        show_in_folder_func: Optional[Callable] = None,
        checkbox_text: Optional[str] = None,
        checkbox_checked: bool = False,
    ):
        super().__init__(parent)

        self.title_text = title
        self.main_message = main_message
        self.informative_text = informative_text
        self.icon_path = icon_path
        self.buttons = buttons
        self.show_folder_path = show_folder_path
        self.show_in_folder_func = show_in_folder_func
        self.checkbox_text = checkbox_text
        self.checkbox_checked = checkbox_checked
        self.checkbox: Optional[QCheckBox] = None

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
        self.content_widget.setObjectName("dialogContentWidget")
        
        # Robust background configuration
        palette = self.content_widget.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#222222"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#FFFFFF"))
        self.content_widget.setPalette(palette)
        self.content_widget.setAutoFillBackground(True)

        self.content_widget.setStyleSheet(
            """
            QWidget#dialogContentWidget {
                background-color: #222222;
                border: 1px solid #444444;
                border-radius: 8px;
                color: #FFFFFF;
            }
            """
        )
        self.main_layout.addWidget(self.content_widget)

        overall_content_layout = QVBoxLayout(self.content_widget)
        overall_content_layout.setContentsMargins(0, 0, 0, 0)
        overall_content_layout.setSpacing(0)

        # --- Top Widget (Title Bar) ---
        top_widget = QWidget(self)
        top_widget.setFixedHeight(50)
        top_widget.setStyleSheet("background-color: #222222; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom: 1px solid #333333;")
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

        # --- Middle Layout (Content) ---
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
            self.icon_label.setStyleSheet("background-color: transparent;")
            content_layout.addWidget(self.icon_label)

        text_layout = QVBoxLayout()
        self.main_message_label = QLabel(self.main_message, self)
        self.main_message_label.setFont(QFont("Segoe UI Variable", 12, QFont.Weight.Bold))
        self.main_message_label.setStyleSheet("color: #F0F0F0; background-color: transparent;")
        self.main_message_label.setWordWrap(True)
        text_layout.addWidget(self.main_message_label)

        if self.informative_text:
            self.informative_text_label = QLabel(self.informative_text, self)
            self.informative_text_label.setFont(QFont("Segoe UI Variable", 10))
            self.informative_text_label.setStyleSheet("color: #C0C0C0; background-color: transparent;")
            self.informative_text_label.setWordWrap(True)
            self.informative_text_label.setTextFormat(Qt.TextFormat.RichText)
            self.informative_text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            self.informative_text_label.setOpenExternalLinks(True)
            text_layout.addWidget(self.informative_text_label)

        if self.checkbox_text:
            self.checkbox = QCheckBox(self.checkbox_text)
            self.checkbox.setChecked(bool(self.checkbox_checked))
            self.checkbox.setFont(QFont("Segoe UI Variable", 10))
            self.checkbox.setStyleSheet(self._get_checkbox_style())
            text_layout.addWidget(self.checkbox)

        content_layout.addLayout(text_layout, 1)
        middle_layout.addLayout(content_layout)
        overall_content_layout.addLayout(middle_layout, 1)

        # --- Bottom Widget (Buttons) ---
        bottom_widget = QWidget(self)
        bottom_widget.setFixedHeight(60)
        bottom_widget.setStyleSheet("background-color: #222222; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; border-top: 1px solid #333333;")
        bottom_layout = QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(20, 0, 20, 0)

        # Optional "Show in Folder" button
        if self.show_folder_path:
            self.show_in_folder_button = QPushButton("Show in Folder")
            self.show_in_folder_button.setFont(QFont("Segoe UI Variable", 10))
            self.show_in_folder_button.setStyleSheet(self._get_button_style())
            folder_icon_path = paths.resource_path("assets/icons/folder-white.png")
            if os.path.exists(folder_icon_path):
                self.show_in_folder_button.setIcon(QIcon(folder_icon_path))
                self.show_in_folder_button.setIconSize(QSize(16, 16))

            if self.show_in_folder_func:
                show_func = self.show_in_folder_func
                self.show_in_folder_button.clicked.connect(lambda: show_func(self.show_folder_path))
            bottom_layout.addWidget(self.show_in_folder_button)

        bottom_layout.addStretch(1)

        # Dynamic Action Buttons
        if "Yes" in self.buttons and "No" in self.buttons:
            self.yes_button = QPushButton("Yes")
            self.yes_button.setFont(QFont("Segoe UI Variable", 10))
            self.yes_button.setStyleSheet(self._get_button_style(primary=True))
            self.yes_button.clicked.connect(self.accept)
            
            self.no_button = QPushButton("No")
            self.no_button.setFont(QFont("Segoe UI Variable", 10))
            self.no_button.setStyleSheet(self._get_button_style(primary=False))
            self.no_button.clicked.connect(self.reject)

            bottom_layout.addWidget(self.yes_button)
            bottom_layout.addWidget(self.no_button)
            self.yes_button.setFocus()
        else:
            # Default OK button
            self.ok_button = QPushButton("OK")
            self.ok_button.setFont(QFont("Segoe UI Variable", 10))
            self.ok_button.setStyleSheet(self._get_button_style(primary=True))
            self.ok_button.clicked.connect(self.accept)
            bottom_layout.addWidget(self.ok_button)
            self.ok_button.setFocus()

        overall_content_layout.addWidget(bottom_widget)
        self.setFixedSize(470, 320 if self.checkbox_text else 280)

    def _get_checkbox_style(self) -> str:
        """Returns the stylesheet for dialog checkboxes."""
        return """
            QCheckBox {
                color: #d6d6d6;
                spacing: 8px;
                padding: 4px 0px;
                background-color: transparent;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #666666;
                border-radius: 3px;
                background-color: #2d2d2d;
            }
            QCheckBox::indicator:hover {
                border: 1px solid #888888;
                background-color: #333333;
            }
            QCheckBox::indicator:checked {
                background-color: #00c8c8;
                border: 1px solid #00c8c8;
            }
        """

    def _get_button_style(self, primary=False):
        """Returns the stylesheet for buttons."""
        border_color = "#404040"
        hover_color = "#3f3f3f"
        
        # Optional: Make primary button slightly distinct if desired, 
        # currently keeping uniform dark theme as requested.
        return f"""
            QPushButton {{ 
                background-color: #2d2d2d; 
                color: white; 
                border: 1px solid {border_color}; 
                border-radius: 5px; 
                padding: 7px 20px; 
                min-width: 70px; 
            }}
            QPushButton:hover {{ background-color: {hover_color}; }}
            QPushButton:pressed {{ background-color: #1f1f1f; }}
            QPushButton:focus {{ outline: none; border: 1px solid #666666; }}
        """

    def is_checkbox_checked(self) -> bool:
        return bool(self.checkbox and self.checkbox.isChecked())

    def paintEvent(self, a0):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        super().paintEvent(a0)


class CustomQMessageBox:
    """
    A static wrapper class to replace QMessageBox with ModernDarkDialog.
    Provides standard methods: information, warning, critical, question.
    """

    @staticmethod
    def _get_icon(name: str) -> str:
        """Resolves icon path with fallback."""
        path = paths.resource_path(f"assets/icons/{name}")
        if not os.path.exists(path):
            # Fallback to info_icon if specific icon missing
            path = paths.resource_path("assets/icons/info_icon.png")
        return path

    @staticmethod
    def information(parent: Optional[QWidget], title: str, main_message: str, informative_text: str = "", icon_path: Optional[str] = None, show_folder_path: Optional[str] = None, show_in_folder_func: Optional[Callable] = None):
        """Displays an information dialog with an OK button."""
        icon = icon_path if icon_path else CustomQMessageBox._get_icon("success.png")
        dlg = ModernDarkDialog(
            title=title,
            main_message=main_message,
            informative_text=informative_text,
            icon_path=icon,
            parent=parent,
            buttons=["OK"],
            show_folder_path=show_folder_path,
            show_in_folder_func=show_in_folder_func
        )
        dlg.exec()

    @staticmethod
    def warning(parent: Optional[QWidget], title: str, main_message: str, informative_text: str = "", icon_path: Optional[str] = None):
        """Displays a warning dialog with an OK button."""
        icon = icon_path if icon_path else CustomQMessageBox._get_icon("info_icon.png")
        dlg = ModernDarkDialog(
            title=title,
            main_message=main_message,
            informative_text=informative_text,
            icon_path=icon,
            parent=parent,
            buttons=["OK"]
        )
        dlg.exec()

    @staticmethod
    def critical(parent: Optional[QWidget], title: str, main_message: str, informative_text: str = "", icon_path: Optional[str] = None):
        """Displays a critical error dialog with an OK button."""
        icon = icon_path if icon_path else CustomQMessageBox._get_icon("error.png")
        dlg = ModernDarkDialog(
            title=title,
            main_message=main_message,
            informative_text=informative_text,
            icon_path=icon,
            parent=parent,
            buttons=["OK"]
        )
        dlg.exec()

    @staticmethod
    def question(parent: Optional[QWidget], title: str, main_message: str, informative_text: str = "", icon_path: Optional[str] = None) -> bool:
        """
        Displays a question dialog with Yes/No buttons.
        Returns: True if 'Yes' was clicked, False otherwise.
        """
        icon = icon_path if icon_path else CustomQMessageBox._get_icon("question.png")
        dlg = ModernDarkDialog(
            title=title,
            main_message=main_message,
            informative_text=informative_text,
            icon_path=icon,
            parent=parent,
            buttons=["Yes", "No"]
        )
        result = dlg.exec()
        return result == QDialog.DialogCode.Accepted

    @staticmethod
    def question_with_checkbox(
        parent: Optional[QWidget],
        title: str,
        main_message: str,
        informative_text: str = "",
        checkbox_text: str = "",
        checkbox_checked: bool = False,
        icon_path: Optional[str] = None,
    ) -> Tuple[bool, bool]:
        """
        Displays a dark themed Yes/No question dialog with an optional checkbox.
        Returns: (accepted, checkbox_checked).
        """
        icon = icon_path if icon_path else CustomQMessageBox._get_icon("question.png")
        dlg = ModernDarkDialog(
            title=title,
            main_message=main_message,
            informative_text=informative_text,
            icon_path=icon,
            parent=parent,
            buttons=["Yes", "No"],
            checkbox_text=checkbox_text,
            checkbox_checked=checkbox_checked,
        )
        result = dlg.exec()
        return result == QDialog.DialogCode.Accepted, dlg.is_checkbox_checked()
