"""Register existing files / repair identity tags: scan, review, confirm, apply.

Background QThread workers keep the UI responsive. Debug diagnostics for these
modules are quiet by default; set TIDAL_DL_IDENTITY_LOG_LEVEL=DEBUG to enable.
"""
from __future__ import annotations

import logging
import os
import time

from PyQt6 import QtCore, QtGui, QtWidgets

from .. import identity_index as index
from .. import identity_registration as registration
from ..paths import get_user_download_path
from ..settings import SETTINGS

logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, index.LOG_LEVEL, logging.INFO))


class RegistrationWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(
        self,
        main_view,
        root,
        mode,
        selections=None,
        write_tags=True,
        parent=None,
    ):
        super().__init__(parent)
        self.main_view = main_view
        self.root = root
        self.mode = mode  # "scan" or "apply"
        self.selections = selections or []
        self.write_tags = write_tags

        # QWidget/tree data stays on the GUI thread. Only this detached
        # playlist ID/name snapshot is handed to the worker.
        tree_handler = getattr(main_view, "tree_handler", None)
        self.playlist_names = {
            str(playlist["id"]): str(playlist.get("name") or "")
            for playlist in getattr(tree_handler, "_spotify_playlist_cache", [])
            if isinstance(playlist, dict) and playlist.get("id")
        }

    def run(self):
        started = time.monotonic()
        logger.info(
            "IDENTITY_WORKER_START mode=%s root=%r",
            self.mode,
            self.root,
        )
        try:
            if self.mode == "apply":
                results = registration.apply_selected(
                    self.selections,
                    self.root,
                    self.write_tags,
                    self.progress.emit,
                    self.isInterruptionRequested,
                )
                logger.info(
                    "IDENTITY_WORKER_APPLY_DONE root=%r results=%d elapsed=%.3fs",
                    self.root,
                    len(results),
                    time.monotonic() - started,
                )
                self.result.emit(results)
                return

            self.progress.emit(
                "Reading locally cached Spotify metadata (no Spotify API calls)..."
            )
            catalog, catalog_warnings = registration.playlist_catalog(
                self.main_view.link_persistence_manager,
                self.root,
                self.playlist_names,
                self.progress.emit,
                self.isInterruptionRequested,
            )

            self.progress.emit(
                "Scanning local audio files and verifying identity fingerprints..."
            )
            proposals, warnings = registration.scan(
                self.root,
                catalog,
                self.progress.emit,
                self.isInterruptionRequested,
            )
            warnings = catalog_warnings + warnings
            warnings.append(
                f"Scanned {len(catalog)} mapped playlist folder(s). "
                "Unmapped folders and manually added tracks were not assigned "
                "a Spotify association."
            )

            logger.info(
                "IDENTITY_WORKER_SCAN_DONE root=%r proposals=%d "
                "mapped_folders=%d warnings=%d elapsed=%.3fs",
                self.root,
                len(proposals),
                len(catalog),
                len(warnings),
                time.monotonic() - started,
            )
            self.result.emit((proposals, warnings))
        except index.IdentityCancelled:
            logger.info(
                "IDENTITY_WORKER_CANCELLED mode=%s root=%r elapsed=%.3fs",
                self.mode,
                self.root,
                time.monotonic() - started,
            )
            self.result.emit(
                ([], ["Scan cancelled; no files were changed."])
            )
        except Exception as exc:
            logger.error(
                "IDENTITY_WORKER_FAILED mode=%s root=%r error=%s",
                self.mode,
                self.root,
                exc,
                exc_info=True,
            )
            self.failed.emit(str(exc))


class IdentityRegistrationDialog(QtWidgets.QDialog):
    def __init__(self, main_view, parent=None):
        super().__init__(parent)
        self.main_view = main_view
        self.worker: RegistrationWorker | None = None
        self.proposals: list = []
        self.scan_root = ""
        self._busy = False
        self._selected_rows: set[int] = set()

        self.setWindowTitle("Register existing files / repair identity tags")
        self._apply_theme(self)
        self.resize(1150, 650)
        self.setMinimumSize(760, 480)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        explanation = QtWidgets.QLabel(
            "Give existing downloads a durable identity so downloads stay recognized after descriptive tags are edited or files are renamed in VirtualDJ.\n"
            "Only dedicated TIDAL-DL identity fields are written; title, artist, key, BPM, genre, artwork and comments are never modified. "
            "Nothing is selected automatically; conflicting or unreadable files are reported, not changed. A full backup is made before each tag write."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        row = QtWidgets.QHBoxLayout()
        default_root = os.path.join(
            get_user_download_path(SETTINGS.downloadPath),
            "flac",
            "Playlists",
        )
        self.root_edit = QtWidgets.QLineEdit(default_root)
        self.root_edit.setMinimumWidth(0)
        self.browse = QtWidgets.QPushButton("Choose playlist root...")
        self.browse.clicked.connect(self.choose_root)
        self.scan_button = QtWidgets.QPushButton("Scan existing files")
        self.scan_button.setToolTip(
            "Scan local audio and locally cached Spotify metadata only. "
            "No Spotify API calls or Spotify login are used."
        )
        self.scan_button.clicked.connect(self.start_scan)
        row.addWidget(self.root_edit, 1)
        row.addWidget(self.browse)
        row.addWidget(self.scan_button)
        layout.addLayout(row)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Selected", "Status", "Evidence", "File", "Association to register"]
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
                max(48, self.table.fontMetrics().lineSpacing() + 18)
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
            header.setSectionResizeMode(
                1, QtWidgets.QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(1, 155)
            header.setSectionResizeMode(
                2, QtWidgets.QHeaderView.ResizeMode.Stretch
            )
            header.setSectionResizeMode(
                3, QtWidgets.QHeaderView.ResizeMode.Stretch
            )
            header.setSectionResizeMode(
                4, QtWidgets.QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(4, 280)

        layout.addWidget(self.table, 1)

        selection_help = QtWidgets.QLabel(
            "Highlighted rows are selected for registration. Ctrl-click toggles rows; "
            "Shift-click selects a range; Ctrl+Shift-click adds a range; Ctrl+A selects "
            "all available rows when the table has focus. Only Identified and Review "
            "association rows are selectable. Changing an association does not select the row."
        )
        selection_help.setWordWrap(True)
        layout.addWidget(selection_help)

        selection_bar = QtWidgets.QHBoxLayout()
        self.select_all = QtWidgets.QPushButton("Select all")
        self.select_all.clicked.connect(self.table.selectAll)
        self.clear_selection = QtWidgets.QPushButton("Clear selection")
        self.clear_selection.clicked.connect(self.table.clearSelection)
        self.selection_count = QtWidgets.QLabel(
            "0 files selected · 0 available to register"
        )
        selection_bar.addWidget(self.select_all)
        selection_bar.addWidget(self.clear_selection)
        selection_bar.addWidget(self.selection_count, 1)
        layout.addLayout(selection_bar)

        self.status = QtWidgets.QLabel(
            "Choose the playlist folder to scan. Spotify metadata is read only "
            "from local persistence; no Spotify API calls or login are used. "
            "Cached playlist data may be incomplete or outdated."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(110)
        layout.addWidget(self.details)

        buttons = QtWidgets.QHBoxLayout()
        self.write_tags_box = QtWidgets.QCheckBox(
            "Write identity tags into audio files (index-only when unchecked)"
        )
        self.write_tags_box.setChecked(True)
        self.apply_button = QtWidgets.QPushButton("Register selected files...")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_selected)
        self.backups = QtWidgets.QPushButton("Open backups folder")
        self.backups.clicked.connect(self.open_backups)
        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.write_tags_box)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.backups)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

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
            QtCore.Qt.WidgetAttribute.WA_StyledBackground,
            True,
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
            QDialog#tidalMaintenanceDialog QLabel,
            QDialog#tidalMaintenanceDialog QCheckBox {
                background: transparent;
                color: #f0f0f0;
            }
            QDialog#tidalMaintenanceDialog QCheckBox {
                spacing: 6px;
            }
            QDialog#tidalMaintenanceDialog QCheckBox:disabled {
                color: #a0a0a0;
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
        """Display a warning using the local maintenance-dialog theme."""
        box = QtWidgets.QMessageBox(self)
        self._apply_theme(box)
        box.setWindowTitle(title)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText(message)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        box.exec()

    @staticmethod
    def _proposal_is_registerable(proposal) -> bool:
        return (
            proposal.status in ("Review association", "Identified")
            and bool(proposal.choices or proposal.identity is not None)
        )

    def _collect_selections(self):
        selections = []
        for row in sorted(self._selected_rows):
            if row >= len(self.proposals):
                continue

            proposal = self.proposals[row]
            if not self._proposal_is_registerable(proposal):
                continue

            combo = self.table.cellWidget(row, 4)
            if proposal.choices:
                if not isinstance(combo, QtWidgets.QComboBox):
                    continue
                identity, _ = proposal.choices[combo.currentIndex()]
            else:
                identity = proposal.identity

            if identity is not None:
                selections.append((proposal, identity))

        return selections

    def _selection_changed(self, *_args):
        model = self.table.selectionModel()
        selected_rows = (
            {index.row() for index in model.selectedRows()}
            if model is not None
            else set()
        )
        rows = {
            row
            for row in selected_rows
            if row < len(self.proposals)
            and self._proposal_is_registerable(self.proposals[row])
        }

        for row in self._selected_rows ^ rows:
            item = self.table.item(row, 0)
            if item is not None:
                item.setText("Selected" if row in rows else "—")
                font = item.font()
                font.setBold(row in rows)
                item.setFont(font)

        self._selected_rows = rows
        selections = self._collect_selections()
        available = sum(
            1
            for proposal in self.proposals
            if self._proposal_is_registerable(proposal)
        )

        self.selection_count.setText(
            f"{len(selections)} files selected · {available} available to register"
        )
        self.apply_button.setEnabled(bool(selections) and not self._busy)
        self.select_all.setEnabled(bool(available) and not self._busy)
        self.clear_selection.setEnabled(bool(rows) and not self._busy)

    def choose_root(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose playlist root", self.root_edit.text())
        if folder:
            self.root_edit.setText(folder)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.scan_button.setEnabled(not busy)
        self.browse.setEnabled(not busy)
        self.root_edit.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.write_tags_box.setEnabled(not busy)
        self._selection_changed()

    def _start(self, mode, selections=None, write_tags=True):
        self._set_busy(True)
        self.worker = RegistrationWorker(
            self.main_view, self.scan_root, mode, selections, write_tags, self
        )
        self.worker.progress.connect(self.status.setText)
        self.worker.failed.connect(self.failed)
        self.worker.result.connect(self.scanned if mode == "scan" else self.applied)
        self.worker.finished.connect(lambda: self._set_busy(False))
        self.worker.start()

    def start_scan(self):
        root = os.path.abspath(self.root_edit.text().strip())
        if not os.path.isdir(root):
            self._warning(
                "Invalid folder",
                "Choose an existing playlist folder to scan.",
            )
            return

        self.scan_root = root
        self.proposals = []
        self._selected_rows.clear()
        self.table.clearSelection()
        self.table.setRowCount(0)
        self.details.clear()
        self._selection_changed()
        self._start("scan")

    def scanned(self, result):
        self.proposals, warnings = result
        self._selected_rows.clear()
        self.table.clearSelection()
        self.table.setRowCount(len(self.proposals))

        for row, proposal in enumerate(self.proposals):
            selectable = self._proposal_is_registerable(proposal)
            flags = QtCore.Qt.ItemFlag.ItemIsEnabled
            if selectable:
                flags |= QtCore.Qt.ItemFlag.ItemIsSelectable

            marker = QtWidgets.QTableWidgetItem("—")
            marker.setFlags(flags)
            marker.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, marker)

            for column, text in enumerate(
                (proposal.status, proposal.evidence, proposal.path),
                1,
            ):
                item = QtWidgets.QTableWidgetItem(text)
                item.setFlags(flags)
                item.setToolTip(text)
                self.table.setItem(row, column, item)

            association_item = QtWidgets.QTableWidgetItem()
            association_item.setFlags(flags)
            self.table.setItem(row, 4, association_item)

            combo = QtWidgets.QComboBox()
            if proposal.choices:
                for _, description in proposal.choices:
                    combo.addItem(description)
                combo.setEnabled(proposal.status == "Review association")
            else:
                combo.addItem("Existing identity (verified)")
                combo.setEnabled(False)

            self.table.setCellWidget(row, 4, combo)
            combo.currentIndexChanged.connect(self._selection_changed)

        counts = registration.summary(self.proposals)
        self.status.setText(
            "Scan complete. Select only rows intended for registration; "
            "Review association rows are metadata-only and should be verified by listening. "
            "No files have changed. "
            + " | ".join(
                f"{name}: {count}"
                for name, count in counts.items()
            )
        )
        self.details.setPlainText("\n".join(warnings))
        self._selection_changed()
        self._set_busy(False)

    def apply_selected(self):
        if self._busy:
            return

        selections = self._collect_selections()
        if not selections:
            return

        write_tags = self.write_tags_box.isChecked()
        confirm = QtWidgets.QMessageBox(self)
        self._apply_theme(confirm)
        confirm.setWindowTitle("Confirm identity registration")
        confirm.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        confirm.setText(
            f"Register identity for {len(selections)} selected file(s)?"
        )

        if write_tags:
            mode_text = (
                "Dedicated identity fields (TIDAL_DL_ID, SPOTIFY_TRACK_ID, TIDAL_TRACK_ID) "
                "will be added where missing. A full byte-for-byte backup will be stored "
                "for every audio file that is modified. "
            )
        else:
            mode_text = (
                "Only the local identity index will be updated. Audio files will not be "
                "modified, so no audio-file backup will be created. "
            )

        confirm.setInformativeText(
            mode_text
            + "No other tag will be intentionally changed. Conflicting existing identity "
            "values will abort that file. Close VirtualDJ and any tag editors before continuing."
        )
        confirm.setDetailedText(
            "\n".join(
                sorted(proposal.path for proposal, _ in selections)
            )
        )
        confirm.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
        )
        confirm.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        confirm.setEscapeButton(QtWidgets.QMessageBox.StandardButton.No)

        if confirm.exec() == QtWidgets.QMessageBox.StandardButton.Yes:
            self.status.setText("Registering identities...")
            self._start("apply", selections, write_tags)

    def applied(self, results):
        succeeded = sum(1 for _, ok, _ in results if ok)
        failed_lines = [
            f"{path}: {message}"
            for path, ok, message in results
            if not ok
        ]
        self.status.setText(
            f"Registered {succeeded} of {len(results)} file(s). "
            "Scan again to verify or continue with the rest."
        )
        self.details.setPlainText(
            "\n".join(failed_lines)
            if failed_lines
            else "Identity registration completed. Full backups are created only for audio files whose tags are modified."
        )
        self.proposals = []
        self._selected_rows.clear()
        self.table.clearSelection()
        self.table.setRowCount(0)
        self._selection_changed()
        self._set_busy(False)

    def failed(self, message):
        self.status.setText("Operation could not complete. See details.")
        self.details.setPlainText(message)

    def open_backups(self):
        path = registration.backup_root()
        os.makedirs(path, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("Finishing the current file safely before closing; press Close again when it completes.")
            return
        super().reject()

    def closeEvent(self, a0):
        if self.worker and self.worker.isRunning():
            self.reject()
            if a0 is not None:
                a0.ignore()
        else:
            super().closeEvent(a0)


def open_identity_registration(settings_page):
    dialog = IdentityRegistrationDialog(settings_page.window(), settings_page)
    dialog.exec()
