# tidal_dl/gui/gui_playlist_table_header.py

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets


class PlaylistTableHeaderWidget(QtWidgets.QFrame):
    filterTextChanged = QtCore.pyqtSignal(str)
    playRequested = QtCore.pyqtSignal()
    shuffleRequested = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("playlistTableHeader")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)
        self._expanded_height = 198
        self._collapsed_height = 72
        self._compact_filter_height = 42
        self._collapse_trigger_scroll = 84
        self._filter_hide_trigger_scroll = self._collapse_trigger_scroll + self._compact_filter_height
        self._expanded_cover_size = 128
        self._collapsed_cover_size = 44
        self._header_wheel_pixels_per_step = 42.0
        self._current_cover_size = self._expanded_cover_size
        self._cover_pixmap = QtGui.QPixmap()
        self._background_pixmap = QtGui.QPixmap()
        self._suppress_filter_signal = False
        self._content_y_offset = 0
        self._compact_mode = False
        self._visual_scroll_offset = 0.0
        self._target_scroll_offset = 0.0
        self._scroll_animation_timer = QtCore.QTimer(self)
        self._scroll_animation_timer.setInterval(16)
        self._scroll_animation_timer.timeout.connect(self._advance_scroll_animation)
        self._scroll_target: Optional[QtWidgets.QAbstractScrollArea] = None

        self._background_label = QtWidgets.QLabel(self)
        self._background_label.setObjectName("playlistHeaderBackdrop")
        self._background_label.setScaledContents(True)
        blur_effect = QtWidgets.QGraphicsBlurEffect(self._background_label)
        blur_effect.setBlurRadius(42)
        self._background_label.setGraphicsEffect(blur_effect)
        self._background_label.lower()
        self._background_overlay = QtWidgets.QWidget(self)
        self._background_overlay.setObjectName("playlistHeaderOverlay")
        self._background_overlay.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        self._content_shell = QtWidgets.QWidget(self)
        self._content_shell.setObjectName("playlistHeaderContentShell")
        self._content_shell.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

        self._content_layout = QtWidgets.QVBoxLayout(self._content_shell)
        self._content_layout.setContentsMargins(28, 14, 24, 10)
        self._content_layout.setSpacing(10)
        self._top_row = QtWidgets.QHBoxLayout()
        self._top_row.setSpacing(14)
        self._top_row.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.cover_label = QtWidgets.QLabel()
        self.cover_label.setObjectName("playlistHeaderCover")
        self.cover_label.setFixedSize(self._expanded_cover_size, self._expanded_cover_size)
        self.cover_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setScaledContents(False)
        self._top_row.addWidget(self.cover_label, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        self._text_column = QtWidgets.QVBoxLayout()
        self._text_column.setSpacing(7)
        self._text_column.setAlignment(QtCore.Qt.AlignmentFlag.AlignBottom)
        self.owner_label = QtWidgets.QLabel("")
        self.owner_label.setObjectName("playlistHeaderOwner")
        self.owner_label.setWordWrap(False)
        self.title_label = QtWidgets.QLabel("")
        self.title_label.setObjectName("playlistHeaderTitle")
        self.title_label.setWordWrap(True)
        self.description_label = QtWidgets.QLabel("")
        self.description_label.setObjectName("playlistHeaderDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setMaximumHeight(34)
        self.meta_label = QtWidgets.QLabel("")
        self.meta_label.setObjectName("playlistHeaderMeta")
        self.meta_label.setWordWrap(False)

        self._button_row = QtWidgets.QHBoxLayout()
        self._button_row.setSpacing(12)
        self._button_row.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.play_button = self._make_primary_button("Play")
        self.shuffle_button = self._make_secondary_button("Shuffle")
        self.play_button.clicked.connect(self.playRequested.emit)
        self.shuffle_button.clicked.connect(self.shuffleRequested.emit)
        self._button_row.addWidget(self.play_button)
        self._button_row.addWidget(self.shuffle_button)
        self._button_row.addStretch(1)
        self._text_column.addStretch(1)
        self._text_column.addWidget(self.owner_label)
        self._text_column.addWidget(self.title_label)
        self._text_column.addWidget(self.description_label)
        self._text_column.addWidget(self.meta_label)
        self._text_column.addLayout(self._button_row)
        self._top_row.addLayout(self._text_column, 1)
        self._content_layout.addLayout(self._top_row, 1)

        self._filter_container = QtWidgets.QWidget()
        self._filter_container.setObjectName("playlistFilterContainer")
        self._filter_layout = QtWidgets.QHBoxLayout(self._filter_container)
        self._filter_layout.setContentsMargins(0, 0, 0, 0)
        self._filter_layout.setSpacing(0)
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setObjectName("playlistFilterEdit")
        self.filter_edit.setPlaceholderText("Filter playlist on title, artist or album")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._emit_filter_text_changed)
        self._filter_layout.addWidget(self.filter_edit)
        self._content_layout.addWidget(self._filter_container, 0)

        self.setStyleSheet("""
            QFrame#playlistTableHeader { background: transparent; border: none; }
            QLabel#playlistHeaderBackdrop { background-color: #030303; }
            QWidget#playlistHeaderOverlay { background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 rgba(0, 12, 15, 222), stop: 0.34 rgba(3, 24, 28, 208), stop: 0.62 rgba(8, 8, 9, 228), stop: 1 rgba(0, 0, 0, 248)); }
            QWidget#playlistHeaderContentShell { background: transparent; border: none; }
            QLabel#playlistHeaderCover { background-color: rgba(255, 255, 255, 0.045); border-radius: 7px; border: none; }
            QLabel#playlistHeaderOwner { color: rgba(255, 255, 255, 0.76); font-size: 10px; font-weight: 700; }
            QLabel#playlistHeaderTitle { color: #ffffff; font-size: 23px; font-weight: 900; letter-spacing: -0.5px; }
            QLabel#playlistHeaderDescription { color: rgba(255, 255, 255, 0.62); font-size: 10px; font-weight: 600; line-height: 138%; }
            QLabel#playlistHeaderMeta { color: rgba(255, 255, 255, 0.78); font-size: 9px; font-weight: 800; letter-spacing: 0.32px; }
            QLineEdit#playlistFilterEdit { min-height: 30px; border-radius: 10px; border: 1px solid rgba(255, 255, 255, 0.12); background-color: rgba(0, 0, 0, 0.50); color: #ffffff; padding: 0 13px; font-size: 11px; selection-background-color: rgba(0, 200, 200, 0.34); }
            QLineEdit#playlistFilterEdit:focus { border: 1px solid rgba(255, 255, 255, 0.24); background-color: rgba(0, 0, 0, 0.62); }
            QPushButton#playlistPrimaryButton { background-color: rgba(255, 255, 255, 0.96); color: #070707; border: none; border-radius: 13px; min-height: 26px; padding: 0 16px; font-size: 11px; font-weight: 900; }
            QPushButton#playlistPrimaryButton:hover { background-color: #ffffff; }
            QPushButton#playlistPrimaryButton[compactHeaderButton="true"] { border-radius: 13px; min-height: 26px; min-width: 32px; padding: 0; font-size: 0px; }
            QPushButton#playlistSecondaryButton { background-color: rgba(255, 255, 255, 0.10); color: #ffffff; border: none; border-radius: 13px; min-height: 26px; padding: 0 16px; font-size: 11px; font-weight: 900; }
            QPushButton#playlistSecondaryButton:hover { background-color: rgba(255, 255, 255, 0.16); }
            QPushButton#playlistSecondaryButton[compactHeaderButton="true"] { border-radius: 13px; min-height: 26px; min-width: 32px; padding: 0; font-size: 14px; font-weight: 900; }
        """)
        self._show_placeholder_cover()
        self._apply_dynamic_label_styles(False)

    def _make_play_icon(self, color: QtGui.QColor) -> QtGui.QIcon:
        pixmap = QtGui.QPixmap(18, 18)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(
            QtGui.QPolygon(
                [
                    QtCore.QPoint(7, 4),
                    QtCore.QPoint(7, 14),
                    QtCore.QPoint(14, 9),
                ]
            )
        )
        painter.end()
        return QtGui.QIcon(pixmap)

    def _make_primary_button(self, text: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setObjectName("playlistPrimaryButton")
        button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        button.setIcon(self._make_play_icon(QtGui.QColor(8, 8, 8)))
        button.setIconSize(QtCore.QSize(13, 13))
        button.setFixedHeight(26)
        return button

    def _make_secondary_button(self, text: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setObjectName("playlistSecondaryButton")
        button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        button.setFixedHeight(26)
        return button

    def _set_compact_button_state(self, compact: bool) -> None:
        self.play_button.setText("" if compact else "Play")
        self.shuffle_button.setText("⇄" if compact else "Shuffle")
        self.play_button.setIconSize(QtCore.QSize(14 if compact else 13, 14 if compact else 13))

        for button in (self.play_button, self.shuffle_button):
            button.setProperty("compactHeaderButton", compact)
            if compact:
                button.setFixedSize(32, 26)
            else:
                button.setMinimumWidth(0)
                button.setMaximumWidth(16777215)
                button.setFixedHeight(26)

            style = button.style()
            if style is None:
                continue
            style.unpolish(button)
            style.polish(button)

    def _apply_dynamic_label_styles(self, compact: bool) -> None:
        title_px = 14 if compact else 22
        meta_px = 9
        owner_px = 10
        description_px = 10
        title_spacing = "-0.16px" if compact else "-0.42px"

        self.owner_label.setStyleSheet(
            f"color: rgba(255, 255, 255, 0.76); font-size: {owner_px}px; font-weight: 700;"
        )
        self.title_label.setStyleSheet(
            f"color: #ffffff; font-size: {title_px}px; font-weight: 900; letter-spacing: {title_spacing};"
        )
        self.description_label.setStyleSheet(
            f"color: rgba(255, 255, 255, 0.62); font-size: {description_px}px; font-weight: 600; line-height: 140%;"
        )
        self.meta_label.setStyleSheet(
            f"color: rgba(255, 255, 255, 0.78); font-size: {meta_px}px; font-weight: 800; letter-spacing: 0.32px;"
        )

    def set_scroll_target(self, scroll_target: Optional[QtWidgets.QAbstractScrollArea]) -> None:
        self._scroll_target = scroll_target

    def _wheel_event_scroll_units(self, event: QtGui.QWheelEvent) -> float:
        pixel_delta = event.pixelDelta().y()
        if pixel_delta:
            return float(-pixel_delta) * 0.72

        angle_delta = event.angleDelta().y()
        if not angle_delta:
            return 0.0

        return float((-angle_delta / 120.0) * self._header_wheel_pixels_per_step)

    def consume_wheel_event_for_header(self, event: Optional[QtGui.QWheelEvent]) -> bool:
        if event is None:
            return False

        scroll_units = self._wheel_event_scroll_units(event)
        if abs(scroll_units) <= 0.01:
            return False

        scrollbar = self._scroll_target.verticalScrollBar() if self._scroll_target else None
        table_at_top = scrollbar is None or scrollbar.value() <= scrollbar.minimum()
        max_header_scroll = float(self._filter_hide_trigger_scroll)

        if scroll_units > 0 and self._target_scroll_offset < max_header_scroll:
            self._target_scroll_offset = min(max_header_scroll, self._target_scroll_offset + scroll_units)
            if not self._scroll_animation_timer.isActive():
                self._scroll_animation_timer.start()
            event.accept()
            return True

        if scroll_units < 0 and table_at_top and self._target_scroll_offset > 0:
            self._target_scroll_offset = max(0.0, self._target_scroll_offset + scroll_units)
            if not self._scroll_animation_timer.isActive():
                self._scroll_animation_timer.start()
            event.accept()
            return True

        return False

    def wheelEvent(self, a0: Optional[QtGui.QWheelEvent]) -> None:
        if self.consume_wheel_event_for_header(a0):
            return

        if a0 is None or self._scroll_target is None:
            if a0 is not None:
                super().wheelEvent(a0)
            return

        scrollbar = self._scroll_target.verticalScrollBar()
        if scrollbar is None:
            super().wheelEvent(a0)
            return

        scroll_units = self._wheel_event_scroll_units(a0)
        if abs(scroll_units) <= 0.01:
            super().wheelEvent(a0)
            return

        next_value = scrollbar.value() + int(scroll_units)
        scrollbar.setValue(max(scrollbar.minimum(), min(scrollbar.maximum(), next_value)))
        a0.accept()

    def _emit_filter_text_changed(self, text: str) -> None:
        if not self._suppress_filter_signal:
            self.filterTextChanged.emit(text)

    def clear_filter(self) -> None:
        self._suppress_filter_signal = True
        try:
            self.filter_edit.clear()
        finally:
            self._suppress_filter_signal = False
        self.filterTextChanged.emit("")

    def set_playlist_context(self, context: Any, tracks: Iterable[Any]) -> None:
        data = self._extract_context_data(context)
        tracks_list = list(tracks or [])
        track_count = data.get("tracks_total")
        if track_count is None:
            track_count = len(tracks_list)
        duration_text = self._format_total_duration(tracks_list)
        meta_parts = [f"{track_count} TRACKS"]
        if duration_text:
            meta_parts.append(duration_text)
        self.owner_label.setText(str(data.get("owner") or "Playlist"))
        self.title_label.setText(str(data.get("title") or "Playlist"))
        self.description_label.setText(str(data.get("description") or ""))
        self.description_label.setVisible(bool(self.description_label.text().strip()))
        self.meta_label.setText("  •  ".join(meta_parts))
        self._scroll_animation_timer.stop()
        self._compact_mode = False
        self._content_y_offset = 0
        self._visual_scroll_offset = 0.0
        self._target_scroll_offset = 0.0
        self._set_compact_button_state(False)
        self.setVisible(True)
        self._apply_scroll_visual_state(0.0)

    def clear_playlist(self) -> None:
        self.clear_filter()
        self._cover_pixmap = QtGui.QPixmap()
        self._background_pixmap = QtGui.QPixmap()
        self._show_placeholder_cover()
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)
        self.setVisible(False)

    def set_cover_pixmap(self, pixmap: QtGui.QPixmap) -> None:
        if pixmap.isNull():
            self._show_placeholder_cover()
            return
        self._cover_pixmap = pixmap
        self._background_pixmap = pixmap
        self._update_cover_pixmap()
        self._update_background_pixmap()

    def set_scroll_offset(self, value: int) -> None:
        self._target_scroll_offset = max(
            0.0,
            min(float(value), float(self._filter_hide_trigger_scroll)),
        )
        if abs(self._target_scroll_offset - self._visual_scroll_offset) <= 0.5:
            self._visual_scroll_offset = self._target_scroll_offset
            self._apply_scroll_visual_state(self._visual_scroll_offset)
            return
        if not self._scroll_animation_timer.isActive():
            self._scroll_animation_timer.start()

    def _advance_scroll_animation(self) -> None:
        delta = self._target_scroll_offset - self._visual_scroll_offset
        if abs(delta) <= 0.75:
            self._visual_scroll_offset = self._target_scroll_offset
            self._scroll_animation_timer.stop()
        else:
            self._visual_scroll_offset += delta * 0.32
        self._apply_scroll_visual_state(self._visual_scroll_offset)

    def _apply_scroll_visual_state(self, value: float) -> None:
        scroll_value = max(
            0.0,
            min(float(value), float(self._filter_hide_trigger_scroll)),
        )
        compact = scroll_value >= self._collapse_trigger_scroll
        compact_changed = compact != self._compact_mode
        self._compact_mode = compact

        collapse_travel = max(
            1,
            self._expanded_height - (self._collapsed_height + self._compact_filter_height),
        )

        if compact:
            filter_scroll = max(0.0, scroll_value - float(self._collapse_trigger_scroll))
            filter_progress = min(
                1.0,
                filter_scroll / max(1.0, float(self._compact_filter_height)),
            )
            filter_height = int(round(self._compact_filter_height * (1.0 - filter_progress)))
            filter_visible = filter_height > 2

            height = self._collapsed_height + max(0, filter_height)
            self._content_y_offset = 0
            self._current_cover_size = self._collapsed_cover_size
            self._filter_container.setVisible(filter_visible)
            self._filter_container.setMinimumHeight(0)
            self._filter_container.setMaximumHeight(max(0, filter_height))
            self.filter_edit.setVisible(filter_height >= 22)

            self.owner_label.setVisible(False)
            self.description_label.setVisible(False)
            self.meta_label.setVisible(True)
            self.title_label.setWordWrap(False)
            self.title_label.setMaximumHeight(18)
            self.meta_label.setMaximumHeight(13)
            self._content_layout.setContentsMargins(28, 6, 24, 6)
            self._content_layout.setSpacing(8 if filter_visible else 0)
            self._top_row.setSpacing(14)
            self._text_column.setSpacing(3)
            self._text_column.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
            self._button_row.setSpacing(8)
        else:
            progress = min(1.0, scroll_value / max(1.0, float(self._collapse_trigger_scroll)))
            eased = progress * progress * (3.0 - (2.0 * progress))
            travel = int(round(collapse_travel * eased))

            height = self._expanded_height - travel
            self._content_y_offset = -travel
            self._current_cover_size = self._expanded_cover_size
            self._filter_container.setVisible(True)
            self._filter_container.setMinimumHeight(0)
            self._filter_container.setMaximumHeight(16777215)
            self.filter_edit.setVisible(True)

            self.owner_label.setVisible(True)
            self.description_label.setVisible(bool(self.description_label.text().strip()))
            self.meta_label.setVisible(True)
            self.title_label.setWordWrap(True)
            self.title_label.setMaximumHeight(52)
            self.meta_label.setMaximumHeight(16)
            self._content_layout.setContentsMargins(28, 13, 24, 10)
            self._content_layout.setSpacing(10)
            self._top_row.setSpacing(14)
            self._text_column.setSpacing(7)
            self._text_column.setAlignment(QtCore.Qt.AlignmentFlag.AlignBottom)
            self._button_row.setSpacing(12)

        if compact_changed:
            self._set_compact_button_state(compact)

        self._apply_dynamic_label_styles(compact)
        self.cover_label.setFixedSize(self._current_cover_size, self._current_cover_size)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

        self._top_row.setAlignment(
            self.cover_label,
            QtCore.Qt.AlignmentFlag.AlignVCenter if compact else QtCore.Qt.AlignmentFlag.AlignTop,
        )
        self._update_content_shell_geometry()
        self._update_cover_pixmap()

    def _update_content_shell_geometry(self) -> None:
        if not hasattr(self, "_content_shell"):
            return

        if self._compact_mode:
            shell_height = max(1, self.height())
        else:
            shell_height = max(self._expanded_height, self.height())

        self._content_shell.setGeometry(
            0,
            self._content_y_offset,
            max(1, self.width()),
            max(1, shell_height),
        )

    def resizeEvent(self, a0: Optional[QtGui.QResizeEvent]) -> None:
        rect = self.rect()
        self._background_label.setGeometry(rect.adjusted(-56, -56, 56, 56))
        self._background_overlay.setGeometry(rect)
        self._update_content_shell_geometry()
        self._update_background_pixmap()
        super().resizeEvent(a0)

    def _extract_context_data(self, context: Any) -> Dict[str, Any]:
        if isinstance(context, dict):
            context_type = context.get("type")
            data = context.get("data")
            if context_type == "spotify" and isinstance(data, dict):
                return {"title": data.get("name"), "owner": data.get("owner_name") or data.get("owner"), "description": data.get("description"), "tracks_total": data.get("tracks_total")}
            if context_type == "tidal":
                return self._extract_object_context(data)
        return self._extract_object_context(context)

    def _extract_object_context(self, obj: Any) -> Dict[str, Any]:
        if isinstance(obj, dict):
            return {"title": obj.get("name") or obj.get("title"), "owner": obj.get("creator") or obj.get("owner") or obj.get("owner_name") or "TIDAL", "description": obj.get("description"), "tracks_total": obj.get("numberOfTracks") or obj.get("tracks_total")}
        return {"title": getattr(obj, "title", None) or getattr(obj, "name", None), "owner": getattr(obj, "creator", None) or getattr(obj, "owner", None) or "TIDAL", "description": getattr(obj, "description", None), "tracks_total": getattr(obj, "numberOfTracks", None)}

    def _format_total_duration(self, tracks: List[Any]) -> str:
        total_seconds = 0
        for track in tracks:
            data = track.get("data", track) if isinstance(track, dict) else track
            duration_ms = data.get("duration_ms") if isinstance(data, dict) else None
            if isinstance(duration_ms, int) and duration_ms > 0:
                total_seconds += int(duration_ms / 1000)
                continue
            duration = getattr(data, "duration", None)
            if isinstance(duration, int) and duration > 0:
                total_seconds += duration
        if total_seconds <= 0:
            return ""
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def _show_placeholder_cover(self) -> None:
        size = max(1, self._current_cover_size)
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtGui.QColor(22, 22, 24))
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 170), 2))
        font = painter.font()
        font.setPointSize(max(24, int(size * 0.26)))
        font.setWeight(QtGui.QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "♫")
        painter.end()
        self.cover_label.setPixmap(pixmap)
        self._background_label.setPixmap(QtGui.QPixmap())

    def _square_crop(self, pixmap: QtGui.QPixmap) -> QtGui.QPixmap:
        if pixmap.isNull():
            return pixmap
        side = min(pixmap.width(), pixmap.height())
        x = max(0, int((pixmap.width() - side) / 2))
        y = max(0, int((pixmap.height() - side) / 2))
        return pixmap.copy(x, y, side, side)

    def _update_cover_pixmap(self) -> None:
        if self._cover_pixmap.isNull():
            self._show_placeholder_cover()
            return
        cropped = self._square_crop(self._cover_pixmap)
        scaled = cropped.scaled(self._current_cover_size, self._current_cover_size, QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding, QtCore.Qt.TransformationMode.SmoothTransformation)
        self.cover_label.setPixmap(scaled)

    def _update_background_pixmap(self) -> None:
        if self._background_pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return

        scaled = self._background_pixmap.scaled(
            max(1, self.width() + 112),
            max(1, self.height() + 112),
            QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )

        darkened = QtGui.QPixmap(scaled.size())
        darkened.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(darkened)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, scaled)
        painter.fillRect(darkened.rect(), QtGui.QColor(0, 0, 0, 118))

        horizontal_vignette = QtGui.QLinearGradient(0, 0, darkened.width(), 0)
        horizontal_vignette.setColorAt(0.0, QtGui.QColor(0, 0, 0, 118))
        horizontal_vignette.setColorAt(0.28, QtGui.QColor(0, 0, 0, 12))
        horizontal_vignette.setColorAt(0.64, QtGui.QColor(0, 0, 0, 88))
        horizontal_vignette.setColorAt(1.0, QtGui.QColor(0, 0, 0, 230))
        painter.fillRect(darkened.rect(), horizontal_vignette)

        vertical_vignette = QtGui.QLinearGradient(0, 0, 0, darkened.height())
        vertical_vignette.setColorAt(0.0, QtGui.QColor(0, 0, 0, 76))
        vertical_vignette.setColorAt(0.42, QtGui.QColor(0, 0, 0, 0))
        vertical_vignette.setColorAt(1.0, QtGui.QColor(0, 0, 0, 210))
        painter.fillRect(darkened.rect(), vertical_vignette)
        painter.end()

        self._background_label.setPixmap(darkened)
