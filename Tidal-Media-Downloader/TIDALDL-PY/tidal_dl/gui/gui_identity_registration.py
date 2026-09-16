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
        self._last_progress = 0.0

        # QWidget/tree data stays on the GUI thread. Only this detached
        # playlist ID/name snapshot is handed to the worker.
        tree_handler = getattr(main_view, "tree_handler", None)
        self.playlist_names = {
            str(playlist["id"]): str(playlist.get("name") or "")
            for playlist in getattr(tree_handler, "_spotify_playlist_cache", [])
            if isinstance(playlist, dict) and playlist.get("id")
        }

    def _report_progress(self, text):
        now = time.monotonic()
        if now - self._last_progress >= 0.1:
            self._last_progress = now
            self.progress.emit(text)

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
                    self._report_progress,
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
                self._report_progress,
                self.isInterruptionRequested,
            )

            self.progress.emit(
                "Scanning local audio files and verifying identity fingerprints..."
            )
            proposals, warnings = registration.scan(
                self.root,
                catalog,
                self._report_progress,
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
        self._ready_count = 0
        self._review_count = 0
        self._rendering = False
        self._render_row = 0
        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render_batch)

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
            "Nothing is selected automatically; conflicting or unreadable files are reported, not changed. "
            "Tag writes use one temporary verified staging copy at a time; no persistent audio backup is created, and crash leftovers are cleaned automatically on a later launch."
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
            "Highlighted rows are selected for registration. Ready rows have a confirmed "
            "identity and are not registered yet. Review rows are metadata-only matches and "
            "require manual verification. Registered rows are already complete and cannot be "
            "selected. Ctrl-click toggles rows; Shift-click selects a range; Ctrl+A selects "
            "all actionable rows when the table has focus."
        )
        selection_help.setWordWrap(True)
        layout.addWidget(selection_help)

        selection_bar = QtWidgets.QHBoxLayout()
        self.select_identified = QtWidgets.QPushButton(
            "Select ready to register"
        )
        self.select_identified.setToolTip(
            "Select only confirmed, unregistered files. Manual-review rows stay unselected."
        )
        self.select_identified.clicked.connect(
            self._select_identified_only
        )
        self.select_all = QtWidgets.QPushButton(
            "Select ready + review"
        )
        self.select_all.setToolTip(
            "Select confirmed files plus metadata-only review rows. Disabled when no review rows exist."
        )
        self.select_all.clicked.connect(self.table.selectAll)
        self.clear_selection = QtWidgets.QPushButton("Clear selection")
        self.clear_selection.clicked.connect(self.table.clearSelection)
        self.selection_count = QtWidgets.QLabel(
            "0 selected · 0 ready · 0 review"
        )
        selection_bar.addWidget(self.select_identified)
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
        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.write_tags_box)
        buttons.addWidget(self.apply_button)
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
        if proposal.status == "Identified":
            return proposal.identity is not None
        if proposal.status == "Review association":
            return bool(proposal.choices)
        return False

    def _collect_selections(self):
        selections = []
        for row in sorted(self._selected_rows):
            if row >= len(self.proposals):
                continue

            proposal = self.proposals[row]
            if not self._proposal_is_registerable(proposal):
                continue

            if proposal.status == "Review association":
                combo = self.table.cellWidget(row, 4)
                if (
                    not proposal.choices
                    or not isinstance(combo, QtWidgets.QComboBox)
                ):
                    continue
                identity, _ = proposal.choices[combo.currentIndex()]
            else:
                identity = proposal.identity

            if identity is not None:
                selections.append((proposal, identity))

        return selections

    def _select_identified_only(self):
        if self._busy:
            return

        model = self.table.model()
        selection_model = self.table.selectionModel()
        if model is None or selection_model is None:
            return

        selection = QtCore.QItemSelection()
        last_column = self.table.columnCount() - 1
        first = None
        for row, proposal in enumerate(self.proposals):
            ready = (
                proposal.status == "Identified"
                and self._proposal_is_registerable(proposal)
            )
            if ready and first is None:
                first = row
            elif not ready and first is not None:
                selection.select(
                    model.index(first, 0), model.index(row - 1, last_column),
                )
                first = None
        if first is not None:
            selection.select(
                model.index(first, 0),
                model.index(len(self.proposals) - 1, last_column),
            )

        selection_model.select(
            selection,
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QtCore.QItemSelectionModel.SelectionFlag.Rows,
        )

    def _selection_changed(self, *_args):
        model = self.table.selectionModel()
        # selectedRows() performs expensive per-index selection checks for
        # fragmented selections. Row selection lets us expand ranges directly.
        selected_rows = set()
        if model is not None:
            for selected_range in model.selection():
                selected_rows.update(range(selected_range.top(), selected_range.bottom() + 1))
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
        self._update_selection_controls()

    def _update_selection_controls(self):
        count = len(self._selected_rows)
        ready = self._ready_count
        review = self._review_count
        self.selection_count.setText(
            f"{count} selected · {ready} ready · {review} review"
        )
        self.apply_button.setEnabled(bool(count) and not self._busy)
        self.select_identified.setEnabled(bool(ready) and not self._busy)
        self.select_all.setEnabled(bool(review) and not self._busy)
        self.clear_selection.setEnabled(bool(count) and not self._busy)

    def choose_root(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose playlist root", self.root_edit.text())
        if folder:
            self.root_edit.setText(folder)

    def _set_busy(self, busy: bool):
        busy = busy or self._rendering
        self._busy = busy
        self.scan_button.setEnabled(not busy)
        self.browse.setEnabled(not busy)
        self.root_edit.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.write_tags_box.setEnabled(not busy)
        self._update_selection_controls()

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
        self._ready_count = self._review_count = 0
        self._selected_rows.clear()
        self.table.clearSelection()
        self.table.setRowCount(0)
        self.details.clear()
        self._selection_changed()
        self._start("scan")

    def scanned(self, result):
        self.proposals, warnings = result
        self._render_started = time.monotonic()
        self._rendering = True
        self._set_busy(True)
        self._render_row = 0
        self._scan_warnings = warnings
        self._ready_count = self._review_count = 0
        self._selected_rows.clear()
        self.table.clearSelection()
        self.table.setRowCount(len(self.proposals))
        self._render_timer.start(0)

    def _render_batch(self):
        # Yield between bounded batches instead of blocking Qt for the entire
        # library. The timer belongs to the dialog and cannot outlive it.
        self.table.setUpdatesEnabled(False)
        try:
            self._render_rows()
        except Exception as exc:
            logger.exception("IDENTITY_REVIEW_RENDER_FAILED")
            self._rendering = False
            self.proposals = []
            self._selected_rows.clear()
            self._ready_count = self._review_count = 0
            self.table.setRowCount(0)
            self.failed(str(exc))
            self._set_busy(False)
            return
        finally:
            self.table.setUpdatesEnabled(True)
        if self._render_row < len(self.proposals):
            self.status.setText(f"Preparing review: {self._render_row}/{len(self.proposals)} files")
            self._render_timer.start(1)
        else:
            self._finish_render()

    def _render_rows(self):
        deadline = time.monotonic() + 0.012
        end = min(self._render_row + 100, len(self.proposals))
        while self._render_row < end:
            row = self._render_row
            proposal = self.proposals[row]
            selectable = self._proposal_is_registerable(proposal)
            if selectable:
                self._ready_count += proposal.status == "Identified"
                self._review_count += proposal.status == "Review association"
            flags = QtCore.Qt.ItemFlag.ItemIsEnabled
            if selectable:
                flags |= QtCore.Qt.ItemFlag.ItemIsSelectable

            marker = QtWidgets.QTableWidgetItem("—")
            marker.setFlags(flags)
            marker.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, marker)

            status_text = {
                "Identified": "Ready to register",
                "Review association": "Needs review",
            }.get(proposal.status, proposal.status)
            for column, text in enumerate(
                (status_text, proposal.evidence, proposal.path),
                1,
            ):
                item = QtWidgets.QTableWidgetItem(text)
                item.setFlags(flags)
                item.setToolTip(text)
                self.table.setItem(row, column, item)

            association_text = (
                "Existing identity (verified)"
                if (
                    proposal.status in ("Registered", "Identified")
                    and proposal.identity is not None
                )
                else "—"
            )
            association_item = QtWidgets.QTableWidgetItem(
                association_text
            )
            association_item.setFlags(flags)
            self.table.setItem(row, 4, association_item)

            if (
                proposal.status == "Review association"
                and proposal.choices
            ):
                combo = QtWidgets.QComboBox()
                for _, description in proposal.choices:
                    combo.addItem(description)
                self.table.setCellWidget(row, 4, combo)
            self._render_row += 1
            if time.monotonic() >= deadline:
                break

    def _finish_render(self):
        self._rendering = False
        logger.info(
            "IDENTITY_REVIEW_RENDER_DONE rows=%d elapsed_ms=%.1f",
            len(self.proposals),
            (time.monotonic() - self._render_started) * 1000,
        )
        counts = registration.summary(self.proposals)
        self.status.setText(
            "Scan complete. Ready rows are confirmed but not registered yet. "
            "Needs review rows are metadata-only and should be verified by listening; "
            "Registered rows need no action. No files have changed. "
            f"Registered: {counts.get('Registered', 0)} | "
            f"Ready to register: {counts.get('Identified', 0)} | "
            f"Needs review: {counts.get('Review association', 0)} | "
            f"Unmatched: {counts.get('Unmatched', 0)} | "
            f"Conflicts/errors: {counts.get('Conflict / error', 0)}"
        )
        self.details.setPlainText("\n".join(self._scan_warnings))
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
                "will be added where missing. Each modified file will temporarily use one "
                "same-folder staging copy, which will be verified before atomic replacement "
                "and deleted as soon as that file finishes. A small machine-local transaction "
                "record will allow automatic cleanup on a later launch if the application "
                "terminates unexpectedly. No persistent audio-file backup will be created. "
            )
        else:
            mode_text = (
                "Only the local identity index will be updated. Audio files will not be "
                "modified, so no staging copy will be created. "
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
            else (
                "Identity registration completed. No persistent audio-file backups "
                "were created; temporary staging files were removed after processing."
            )
        )
        self.proposals = []
        self._ready_count = self._review_count = 0
        self._selected_rows.clear()
        self.table.clearSelection()
        self.table.setRowCount(0)
        self._selection_changed()
        self._set_busy(False)

    def failed(self, message):
        self.status.setText("Operation could not complete. See details.")
        self.details.setPlainText(message)

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("Finishing the current file safely before closing; press Close again when it completes.")
            return
        self._render_timer.stop()
        super().reject()

    def closeEvent(self, a0):
        if self.worker and self.worker.isRunning():
            self.reject()
            if a0 is not None:
                a0.ignore()
        else:
            self._render_timer.stop()
            super().closeEvent(a0)


def open_identity_registration(settings_page):
    dialog = IdentityRegistrationDialog(settings_page.window(), settings_page)
    dialog.exec()
