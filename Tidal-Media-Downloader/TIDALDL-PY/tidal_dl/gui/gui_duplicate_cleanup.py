"""Background duplicate scan with explicit review; no automatic deletion."""
from __future__ import annotations

import os
from collections import defaultdict

from PyQt6 import QtCore, QtGui, QtWidgets

from ..duplicate_cleanup import QUARANTINE_NAME, inside, scan_duplicates, quarantine_selected, delete_selected
from ..format import getPlaylistPath
from ..paths import get_user_download_path
from ..settings import SETTINGS


class DuplicateWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, main_view, root, selections=None, parent=None, *, permanent=False):
        super().__init__(parent)
        self.main_view = main_view
        self.root = root
        self.selections = selections
        self.permanent = permanent
        # Capture only playlist names/IDs on the GUI thread; the worker reads
        # track metadata from local persistence, never from Spotify or widgets.
        tree_handler = getattr(main_view, "tree_handler", None)
        self.playlist_names = {
            str(playlist["id"]): str(playlist.get("name") or "")
            for playlist in getattr(tree_handler, "_spotify_playlist_cache", [])
            if isinstance(playlist, dict) and playlist.get("id")
        }

    def run(self):
        try:
            if self.selections is not None:
                if self.permanent:
                    self.result.emit(delete_selected(self.root, self.selections, self.isInterruptionRequested))
                else:
                    self.result.emit(quarantine_selected(self.root, self.selections))
                return
            self.progress.emit("Reading locally cached Spotify metadata (no Spotify API calls)...")
            download_root = get_user_download_path(SETTINGS.downloadPath)
            pm = self.main_view.link_persistence_manager
            playlists = pm.get_cached_spotify_playlists_for_cleanup()
            for playlist_id, name in self.playlist_names.items():
                playlist = playlists.setdefault(
                    playlist_id, {"id": playlist_id, "name": name, "folder_hint": "", "tracks": []}
                )
                if name:
                    playlist["name"] = name
            folders = defaultdict(list)
            warnings = [
                "Cache-only scan: no Spotify API calls were made. Cached metadata may be incomplete or outdated; "
                "current Spotify playlist membership is not verified."
            ]
            if not playlists:
                warnings.append("No locally cached Spotify playlists or track metadata are available. No files were changed.")
            for playlist in playlists.values():
                if self.isInterruptionRequested():
                    self.result.emit(([], ["Scan cancelled; no files were changed."]))
                    return
                context = {"type": "spotify", "data": playlist}
                name = playlist["name"] or playlist["id"]
                tracks = playlist["tracks"]
                if not tracks:
                    warnings.append(f"No cached Spotify track metadata; skipped playlist: {name}")
                    continue
                candidates = []
                if playlist["name"]:
                    for audio_type in ("", "flac", "mp3", "m4a", "mp4", "aac", "unknown"):
                        folder = getPlaylistPath(context, os.path.join(download_root, audio_type))
                        if folder:
                            candidates.append(folder)
                hint = playlist["folder_hint"]
                if hint:
                    candidates.append(os.path.join(download_root, hint))
                # Support a user-selected Playlists root outside the saved location.
                relative = getPlaylistPath(context, "__playlist_root__") if playlist["name"] else None
                if relative:
                    relative = os.path.relpath(relative, "__playlist_root__")
                    parts = relative.split(os.sep)
                    if parts and parts[0].casefold() == "playlists":
                        parts = parts[1:]
                    if parts:
                        candidates.append(os.path.join(self.root, *parts))
                candidates = {os.path.abspath(p) for p in candidates if inside(p, self.root) and os.path.isdir(p)}
                if not candidates:
                    warnings.append(f"No local folder could be mapped from cached metadata; skipped playlist: {name}")
                    continue
                self.progress.emit(f"Using {len(tracks)} cached Spotify track(s): {name}")
                for folder in candidates:
                    folders[folder].extend(tracks)
            pairs, scan_warnings = scan_duplicates(
                self.root, folders, self.progress.emit, self.isInterruptionRequested
            )
            warnings.extend(scan_warnings)
            warnings.append(f"Scanned {len(folders)} mapped playlist folder(s). Unmapped folders and unrelated local tracks were left untouched.")
            if self.isInterruptionRequested():
                pairs = []
                warnings.append("Scan cancelled; no files were changed.")
            self.result.emit((pairs, warnings))
        except Exception as exc:
            self.failed.emit(str(exc))


class DuplicateCleanupDialog(QtWidgets.QDialog):
    def __init__(self, main_view, parent=None):
        super().__init__(parent)
        self.main_view = main_view
        self.worker = None
        self.pairs = []
        self.scan_root = ""
        self._busy = False
        self._selected_rows = set()
        self._permanent_operation = False
        self.setWindowTitle("Review local Spotify playlist duplicates")
        self._apply_theme(self)
        self.resize(1150, 650)
        self.setMinimumSize(760, 480)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        explanation = QtWidgets.QLabel(
            "Uses locally cached Spotify metadata only; no Spotify API calls or login required. "
            "The cache may be incomplete or outdated. Files without a cached match are NOT deletion candidates. "
            "Compare files only within each mapped Spotify playlist folder. "
            "Different playlist folders are never deduplicated against each other.\n"
            "Nothing is selected automatically. Metadata-only matches are possible duplicates, not proof of identical audio. "
            "Listen before choosing; keep the filename used by VirtualDJ. Close audio/tagging applications before cleanup."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        row = QtWidgets.QHBoxLayout()
        root = os.path.join(get_user_download_path(SETTINGS.downloadPath), "flac", "Playlists")
        self.root_edit = QtWidgets.QLineEdit(root)
        self.root_edit.setMinimumWidth(0)
        self.browse = QtWidgets.QPushButton("Choose playlist root...")
        self.browse.clicked.connect(self.choose_root)
        self.scan = QtWidgets.QPushButton("Scan cache and local files")
        self.scan.setToolTip("Use only locally cached Spotify track metadata. Missing data is skipped, never fetched.")
        self.scan.clicked.connect(self.start_scan)
        row.addWidget(self.root_edit, 1)
        row.addWidget(self.browse)
        row.addWidget(self.scan)
        layout.addLayout(row)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Selected", "Spotify track / evidence", "File A", "File B", "Duration A / B", "Keep"]
        )
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )

        vertical_header = self.table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
            vertical_header.setDefaultSectionSize(
                max(56, self.table.fontMetrics().lineSpacing() * 2 + 16)
            )

        header = self.table.horizontalHeader()
        if header is not None:
            header.setSectionsMovable(False)
            header.setStretchLastSection(False)
            header.setMinimumSectionSize(60)
            header.setDefaultAlignment(
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter
            )
            header.setSectionResizeMode(
                QtWidgets.QHeaderView.ResizeMode.Interactive
            )
            header.setSectionResizeMode(
                0, QtWidgets.QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(0, 100)
            header.resizeSection(1, 250)
            header.setSectionResizeMode(
                2, QtWidgets.QHeaderView.ResizeMode.Stretch
            )
            header.setSectionResizeMode(
                3, QtWidgets.QHeaderView.ResizeMode.Stretch
            )
            header.setSectionResizeMode(
                4, QtWidgets.QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(4, 145)
            header.setSectionResizeMode(
                5, QtWidgets.QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(5, 205)

        layout.addWidget(self.table, 1)
        selection_help = QtWidgets.QLabel(
            "Highlighted rows are selected for cleanup. Ctrl-click toggles rows; Shift-click selects a range; "
            "Ctrl+Shift-click adds a range; Ctrl+A selects all (with the table focused). "
            "The Keep choice determines which file survives; changing it does not select the row."
        )
        selection_help.setWordWrap(True)
        layout.addWidget(selection_help)
        selection_bar = QtWidgets.QHBoxLayout()
        self.select_all = QtWidgets.QPushButton("Select all")
        self.select_all.clicked.connect(self.table.selectAll)
        self.clear_selection = QtWidgets.QPushButton("Clear selection")
        self.clear_selection.clicked.connect(self.table.clearSelection)
        self.selection_count = QtWidgets.QLabel("0 pairs selected · 0 files targeted")
        selection_bar.addWidget(self.select_all)
        selection_bar.addWidget(self.clear_selection)
        selection_bar.addWidget(self.selection_count, 1)
        layout.addLayout(selection_bar)
        self.status = QtWidgets.QLabel("Choose the Playlists folder to scan. Saved path settings are used for playlist mapping.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(100)
        layout.addWidget(self.details)
        buttons = QtWidgets.QHBoxLayout()
        self.remove = QtWidgets.QPushButton("Move selected to recovery...")
        self.remove.setEnabled(False)
        self.remove.clicked.connect(self.remove_selected)
        self.delete = QtWidgets.QPushButton("Permanently delete selected...")
        self.delete.setEnabled(False)
        self.delete.setToolTip("Permanently delete the chosen duplicate files. Bypasses the Recycle Bin; cannot be undone.")
        self.delete.clicked.connect(lambda: self.remove_selected(permanent=True))
        self.recovery = QtWidgets.QPushButton("Open recovery folder")
        self.recovery.clicked.connect(self.open_recovery)
        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.remove)
        buttons.addWidget(self.delete)
        buttons.addWidget(self.recovery)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        recovery_help = QtWidgets.QLabel(
            f"Recovery moves audio into {QUARANTINE_NAME} under the scanned root, with original-path records. "
            "This is not the Recycle Bin and does not free disk space. Permanent deletion has no recovery copy."
        )
        recovery_help.setWordWrap(True)
        layout.addWidget(recovery_help)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self._selection_changed()

    def _apply_theme(self, dialog: QtWidgets.QDialog) -> None:
        """Apply the application's dark colors without changing the global theme."""
        dialog.setObjectName("tidalMaintenanceDialog")
        palette = dialog.palette()
        colors = {
            QtGui.QPalette.ColorRole.Window: "#222222",
            QtGui.QPalette.ColorRole.WindowText: "#f0f0f0",
            QtGui.QPalette.ColorRole.Base: "#242429",
            QtGui.QPalette.ColorRole.AlternateBase: "#2d2d31",
            QtGui.QPalette.ColorRole.Text: "#f0f0f0",
            QtGui.QPalette.ColorRole.Button: "#2d2d31",
            QtGui.QPalette.ColorRole.ButtonText: "#f0f0f0",
            QtGui.QPalette.ColorRole.Highlight: "#3a3a3f",
            QtGui.QPalette.ColorRole.HighlightedText: "#ffffff",
            QtGui.QPalette.ColorRole.PlaceholderText: "#a0a0a0",
        }
        for role, color in colors.items():
            palette.setColor(role, QtGui.QColor(color))
        for role in (
            QtGui.QPalette.ColorRole.WindowText,
            QtGui.QPalette.ColorRole.Text,
            QtGui.QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(
                QtGui.QPalette.ColorGroup.Disabled,
                role,
                QtGui.QColor("#a0a0a0"),
            )
        dialog.setPalette(palette)
        dialog.setAutoFillBackground(True)
        dialog.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_StyledBackground, True
        )

        font_size = max(10, int(getattr(SETTINGS, "fontSize", 11)))
        dialog.setStyleSheet("""
            QDialog#tidalMaintenanceDialog {
                background-color: #222222;
                color: #f0f0f0;
            }
            QDialog#tidalMaintenanceDialog QWidget {
                color: #f0f0f0;
                font-family: "Nationale";
                font-size: %dpt;
            }
            QDialog#tidalMaintenanceDialog QLabel {
                background: transparent;
                color: #f0f0f0;
            }
            QDialog#tidalMaintenanceDialog QLineEdit,
            QDialog#tidalMaintenanceDialog QPlainTextEdit,
            QDialog#tidalMaintenanceDialog QComboBox {
                background-color: #242429;
                color: #f0f0f0;
                border: 1px solid #48484d;
                border-radius: 4px;
                padding: 5px;
                selection-background-color: #3a3a3f;
                selection-color: #ffffff;
            }
            QDialog#tidalMaintenanceDialog QLineEdit:disabled,
            QDialog#tidalMaintenanceDialog QPlainTextEdit:disabled,
            QDialog#tidalMaintenanceDialog QComboBox:disabled {
                background-color: #29292d;
                color: #a0a0a0;
            }
            QDialog#tidalMaintenanceDialog QComboBox::drop-down {
                border-left: 1px solid #48484d;
                width: 20px;
            }
            QDialog#tidalMaintenanceDialog QComboBox QAbstractItemView {
                background-color: #2d2d31;
                color: #f0f0f0;
                selection-background-color: #3a3a3f;
                selection-color: #ffffff;
            }
            QDialog#tidalMaintenanceDialog QTableWidget {
                background-color: #242429;
                alternate-background-color: #2d2d31;
                color: #f0f0f0;
                gridline-color: #444449;
                border: 1px solid #48484d;
                selection-background-color: #3a3a3f;
                selection-color: #ffffff;
            }
            QDialog#tidalMaintenanceDialog QTableWidget QWidget#qt_scrollarea_viewport {
                background-color: #242429;
            }
            QDialog#tidalMaintenanceDialog QTableWidget:disabled {
                color: #a0a0a0;
            }
            QDialog#tidalMaintenanceDialog QTableWidget::item {
                padding: 4px;
            }
            QDialog#tidalMaintenanceDialog QTableWidget::item:selected {
                background-color: #006a80;
                color: #ffffff;
            }
            QDialog#tidalMaintenanceDialog QTableWidget::item:selected:!active {
                background-color: #006a80;
                color: #ffffff;
            }
            QDialog#tidalMaintenanceDialog QTableWidget::indicator {
                width: 16px;
                height: 16px;
            }
            QDialog#tidalMaintenanceDialog QHeaderView::section,
            QDialog#tidalMaintenanceDialog QTableCornerButton::section {
                background-color: #2d2d31;
                color: #f0f0f0;
                padding: 6px;
                border: none;
                border-right: 1px solid #48484d;
                border-bottom: 1px solid #48484d;
                font-weight: bold;
            }
            QDialog#tidalMaintenanceDialog QPushButton {
                background-color: #2d2d31;
                color: #f0f0f0;
                border: 1px solid #55555a;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QDialog#tidalMaintenanceDialog QPushButton:hover {
                background-color: #3a3a3f;
            }
            QDialog#tidalMaintenanceDialog QPushButton:pressed {
                background-color: #242429;
            }
            QDialog#tidalMaintenanceDialog QPushButton:focus {
                border: 1px solid #00c8c8;
            }
            QDialog#tidalMaintenanceDialog QPushButton:disabled {
                background-color: #29292d;
                color: #a0a0a0;
                border-color: #444449;
            }
            QDialog#tidalMaintenanceDialog QMenu {
                background-color: #2d2d31;
                color: #f0f0f0;
                border: 1px solid #55555a;
            }
            QDialog#tidalMaintenanceDialog QMenu::item:selected {
                background-color: #3a3a3f;
                color: #ffffff;
            }
            QToolTip {
                background-color: #2d2d31;
                color: #f0f0f0;
                border: 1px solid #55555a;
                padding: 4px;
            }
        """ % font_size)

    def _warning(self, title: str, message: str) -> None:
        """Display a warning using the same local maintenance-dialog theme."""
        box = QtWidgets.QMessageBox(self)
        self._apply_theme(box)
        box.setWindowTitle(title)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText(message)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        box.exec()

    def choose_root(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose local Playlists root", self.root_edit.text())
        if folder:
            self.root_edit.setText(folder)

    def _start(self, selections=None, *, permanent=False):
        self._busy = True
        self._permanent_operation = permanent
        self.scan.setEnabled(False)
        self.remove.setEnabled(False)
        self.delete.setEnabled(False)
        self.select_all.setEnabled(False)
        self.clear_selection.setEnabled(False)
        self.browse.setEnabled(False)
        self.root_edit.setEnabled(False)
        self.table.setEnabled(False)
        self.worker = DuplicateWorker(self.main_view, self.scan_root, selections, self, permanent=permanent)
        self.worker.progress.connect(self.status.setText)
        self.worker.failed.connect(self.failed)
        self.worker.result.connect(self.scanned if selections is None else self.removed)
        self.worker.finished.connect(self.idle)
        self.worker.start()

    def start_scan(self):
        root = os.path.abspath(self.root_edit.text().strip())
        if not os.path.isdir(root):
            self._warning(
                "Invalid folder",
                "Choose an existing local playlist root.",
            )
            return
        self.scan_root = root
        self.pairs = []
        self._selected_rows.clear()
        self.table.setRowCount(0)
        self._selection_changed()
        self.details.clear()
        self._start()

    def scanned(self, result):
        self.pairs, warnings = result
        self._selected_rows.clear()
        self.table.clearSelection()
        self.table.setRowCount(len(self.pairs))
        for row, pair in enumerate(self.pairs):
            check = QtWidgets.QTableWidgetItem("—")
            check.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            check.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, check)
            for column, text in enumerate((
                f"{pair.spotify_title}\n{pair.reason}", pair.first.path, pair.second.path,
                f"{pair.first.recording.duration:.2f}s / {pair.second.recording.duration:.2f}s",
            ), 1):
                item = QtWidgets.QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
            keep = QtWidgets.QComboBox()
            keep.addItems(["Keep A; remove B", "Keep B; remove A"])
            self.table.setItem(row, 5, QtWidgets.QTableWidgetItem())
            self.table.setCellWidget(row, 5, keep)
            keep.currentIndexChanged.connect(self._selection_changed)
        self.status.setText(f"Found {len(self.pairs)} duplicate candidate pair(s). Review evidence and choose which filename to keep. No files have changed.")
        self.details.setPlainText("\n".join(warnings))
        self._selection_changed()

    def _selection_changed(self, *_args):
        model = self.table.selectionModel()
        rows = {index.row() for index in model.selectedRows()} if model is not None else set()
        for row in self._selected_rows ^ rows:
            item = self.table.item(row, 0)
            if item is not None:
                item.setText("Selected" if row in rows else "—")
                font = item.font()
                font.setBold(row in rows)
                item.setFont(font)
        self._selected_rows = rows
        selections = self._cleanup_selections()
        count = len({duplicate.path for duplicate, _, _ in selections})
        self.selection_count.setText(f"{len(rows)} pairs selected · {count} files targeted")
        self.remove.setEnabled(bool(selections) and not self._busy)
        self.delete.setEnabled(bool(selections) and not self._busy)
        self.select_all.setEnabled(bool(self.pairs) and not self._busy)
        self.clear_selection.setEnabled(bool(rows) and not self._busy)

    def _cleanup_selections(self):
        selections = []
        for row in sorted(self._selected_rows):
            if row >= len(self.pairs):
                continue
            pair = self.pairs[row]
            keep_widget = self.table.cellWidget(row, 5)
            keep_a = isinstance(keep_widget, QtWidgets.QComboBox) and keep_widget.currentIndex() == 0
            selections.append((pair.second if keep_a else pair.first, pair.first if keep_a else pair.second, pair.reason))
        return selections

    def remove_selected(self, _checked=False, *, permanent=False):
        if self._busy:
            return
        selections = self._cleanup_selections()
        if not selections:
            return
        paths = {duplicate.path for duplicate, _, _ in selections}
        if any(keep.path in paths for _, keep, _ in selections):
            self._warning(
                "Conflicting choices",
                "A file chosen to keep is also marked for removal in another pair. "
                "Keep at least one consistent original.",
            )
            return
        confirm = QtWidgets.QMessageBox(self)
        self._apply_theme(confirm)
        confirm.setWindowTitle("Confirm PERMANENT deletion" if permanent else "Confirm recoverable duplicate removal")
        confirm.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        confirm.setText(
            f"Permanently DELETE {len(paths)} selected audio file(s)? This cannot be undone."
            if permanent else f"Move {len(paths)} explicitly selected audio file(s) out of their playlist folders?"
        )
        confirm.setInformativeText(
            "Metadata-only matches may be different recordings. Your selections are the final decision. "
            "VirtualDJ references to removed filenames will no longer resolve. Lyrics, covers and DJ sidecars are left unchanged.\n\n"
            + ("PERMANENT DELETION bypasses the Recycle Bin. No recovery copy or restoration record is created."
               if permanent else "Files are NOT permanently deleted. Recovery folder:\n"
               + os.path.join(self.scan_root, QUARANTINE_NAME))
        )
        confirm.setDetailedText("\n\n".join(
            f"{'DELETE' if permanent else 'MOVE'}: {duplicate.path}\nKEEP: {keeper.path}\nEvidence: {reason}"
            for duplicate, keeper, reason in selections
        ))
        acknowledgement = None
        if permanent:
            acknowledgement = QtWidgets.QCheckBox("I understand these audio files will be permanently deleted, not moved to recovery.")
            confirm.setCheckBox(acknowledgement)
        confirm.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        confirm.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        confirm.setEscapeButton(QtWidgets.QMessageBox.StandardButton.No)
        if acknowledgement is not None:
            yes_button = confirm.button(QtWidgets.QMessageBox.StandardButton.Yes)
            if yes_button is not None:
                yes_button.setText("Permanently delete")
                yes_button.setEnabled(False)
                acknowledgement.toggled.connect(yes_button.setEnabled)
        if confirm.exec() == QtWidgets.QMessageBox.StandardButton.Yes:
            if permanent and (acknowledgement is None or not acknowledgement.isChecked()):
                return
            self.status.setText("Verifying and permanently deleting selected files..." if permanent else "Verifying files and moving selected duplicates to recovery...")
            self._start(selections, permanent=permanent)

    def removed(self, result):
        moved, errors = result
        self.pairs = []
        self._selected_rows.clear()
        self.table.setRowCount(0)
        if self._permanent_operation:
            self.status.setText(f"Permanently deleted {len(moved)} file(s); {len(errors)} error(s)/notice(s). Scan again before another cleanup.")
            self.details.setPlainText("\n".join([*(f"Deleted: {path}" for path in moved), *errors]) or "No files were deleted.")
        else:
            self.status.setText(f"Moved {len(moved)} file(s) to recovery; {len(errors)} error(s). Scan again before another cleanup.")
            self.details.setPlainText("\n".join(errors) or "Original paths and checksums are recorded beside each quarantined file. Restore by moving the audio back to its original, unoccupied path. No sidecars were moved.")
        self._selection_changed()

    def failed(self, message):
        self.status.setText("Operation could not complete. See details.")
        self.details.setPlainText(message)

    def idle(self):
        self._busy = False
        self.scan.setEnabled(True)
        self.browse.setEnabled(True)
        self.root_edit.setEnabled(True)
        self.table.setEnabled(True)
        self._selection_changed()

    def open_recovery(self):
        path = os.path.join(self.scan_root or self.root_edit.text(), QUARANTINE_NAME)
        if os.path.isdir(path):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        else:
            self._warning("Recovery folder", f"The recovery folder is created when files are moved there:\n{path}\n\nIt stores audio and original-path records, not Recycle Bin entries.")

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("Waiting for the current operation to finish safely before closing. Press Close again when it finishes.")
            return
        super().reject()

    def closeEvent(self, a0):
        if self.worker and self.worker.isRunning():
            self.reject()
            if a0 is not None:
                a0.ignore()
        else:
            super().closeEvent(a0)


def open_duplicate_cleanup(settings_page):
    dialog = DuplicateCleanupDialog(settings_page.window(), settings_page)
    dialog.exec()
