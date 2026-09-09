"""Register existing files / repair identity tags: scan, review, confirm, apply.

Background QThread workers keep the UI responsive. Debug diagnostics for these
modules are quiet by default; set TIDAL_DL_IDENTITY_LOG_LEVEL=DEBUG to enable.
"""
from __future__ import annotations

import os

from PyQt6 import QtCore, QtGui, QtWidgets

from .. import identity_index as index
from .. import identity_registration as registration
from ..paths import get_user_download_path
from ..settings import SETTINGS


class RegistrationWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, main_view, root, mode, selections=None, write_tags=True, parent=None):
        super().__init__(parent)
        self.main_view = main_view
        self.root = root
        self.mode = mode  # "scan" or "apply"
        self.selections = selections or []
        self.write_tags = write_tags

    def run(self):
        try:
            if self.mode == "apply":
                results = registration.apply_selected(
                    self.selections, self.root, self.write_tags,
                    self.progress.emit, self.isInterruptionRequested,
                )
                self.result.emit(results)
                return
            if getattr(self.main_view.spotify_api, "sp", None) is None:
                catalog = {}
                catalog_warnings = [
                    "Spotify is not logged in; legacy Spotify associations were not proposed. "
                    "Existing identity tags and audio fingerprints were still verified."
                ]
            else:
                catalog, catalog_warnings = registration.playlist_catalog(
                    self.main_view.spotify_api,
                    self.main_view.link_persistence_manager,
                    self.root,
                    self.progress.emit,
                    self.isInterruptionRequested,
                )
            proposals, warnings = registration.scan(
                self.root, catalog, self.progress.emit, self.isInterruptionRequested
            )
            warnings = catalog_warnings + warnings
            warnings.append(
                f"Scanned {len(catalog)} mapped playlist folder(s). Unmapped folders and manually added tracks are untouched."
            )
            self.result.emit((proposals, warnings))
        except index.IdentityCancelled:
            self.result.emit(([], ["Scan cancelled; no files were changed."]))
        except Exception as exc:
            self.failed.emit(str(exc))


class IdentityRegistrationDialog(QtWidgets.QDialog):
    def __init__(self, main_view, parent=None):
        super().__init__(parent)
        self.main_view = main_view
        self.worker: RegistrationWorker | None = None
        self.proposals: list = []
        self.scan_root = ""
        self.setWindowTitle("Register existing files / repair identity tags")
        self.resize(1150, 650)
        layout = QtWidgets.QVBoxLayout(self)
        explanation = QtWidgets.QLabel(
            "Give existing downloads a durable identity so downloads stay recognized after you edit tags or rename files in VirtualDJ.\n"
            "Only dedicated TIDAL-DL identity fields are ever written; your title, artist, key, BPM, genre, artwork and comments are never modified. "
            "Nothing is selected automatically; conflicting or unreadable files are reported, not changed. A full backup is made before each write."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        row = QtWidgets.QHBoxLayout()
        default_root = os.path.join(get_user_download_path(SETTINGS.downloadPath), "flac", "Playlists")
        self.root_edit = QtWidgets.QLineEdit(default_root)
        self.browse = QtWidgets.QPushButton("Choose playlist root...")
        self.browse.clicked.connect(self.choose_root)
        self.scan_button = QtWidgets.QPushButton("Scan existing files")
        self.scan_button.clicked.connect(self.start_scan)
        row.addWidget(self.root_edit, 1)
        row.addWidget(self.browse)
        row.addWidget(self.scan_button)
        layout.addLayout(row)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Register?", "Status", "Evidence", "File", "Association to register"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            header.setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.status = QtWidgets.QLabel(
            "Choose the playlist folder to scan. Spotify login is needed only to propose legacy associations; "
            "without Spotify, existing identity tags and audio fingerprints can still be verified."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(110)
        layout.addWidget(self.details)
        buttons = QtWidgets.QHBoxLayout()
        self.write_tags_box = QtWidgets.QCheckBox("Write identity tags into audio files (index-only when unchecked)")
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

    def choose_root(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose playlist root", self.root_edit.text())
        if folder:
            self.root_edit.setText(folder)

    def _set_busy(self, busy: bool):
        self.scan_button.setEnabled(not busy)
        self.browse.setEnabled(not busy)
        self.root_edit.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.apply_button.setEnabled(not busy and any(p.status in ("Review association", "Identified") for p in self.proposals))
        self.write_tags_box.setEnabled(not busy)

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
            QtWidgets.QMessageBox.warning(self, "Invalid folder", "Choose an existing playlist folder to scan.")
            return
        self.scan_root = root
        self.proposals = []
        self.table.setRowCount(0)
        self.details.clear()
        self._start("scan")

    def scanned(self, result):
        self.proposals, warnings = result
        registerable = {"Review association", "Identified"}
        self.table.setRowCount(len(self.proposals))
        for row, proposal in enumerate(self.proposals):
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.CheckState.Unchecked)
            check.setFlags(check.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            if proposal.status not in registerable:
                check.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, check)
            for column, text in enumerate((proposal.status, proposal.evidence, proposal.path), 1):
                item = QtWidgets.QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
            combo = QtWidgets.QComboBox()
            if proposal.choices:
                for _, description in proposal.choices:
                    combo.addItem(description)
                combo.setEnabled(proposal.status == "Review association")
            else:
                combo.addItem("Existing identity (verified)")
                combo.setEnabled(False)
            self.table.setCellWidget(row, 4, combo)
        counts = registration.summary(self.proposals)
        self.status.setText(
            "Scan complete. Review each row: verify with your ears where the evidence is metadata-only, then register. No files have changed yet. "
            + " | ".join(f"{name}: {count}" for name, count in counts.items())
        )
        self.details.setPlainText("\n".join(warnings))
        self._set_busy(False)

    def apply_selected(self):
        selections = []
        for row, proposal in enumerate(self.proposals):
            item = self.table.item(row, 0)
            if item is None or item.checkState() != QtCore.Qt.CheckState.Checked:
                continue
            combo = self.table.cellWidget(row, 4)
            if proposal.choices:
                if not isinstance(combo, QtWidgets.QComboBox):
                    continue
                identity, _ = proposal.choices[combo.currentIndex()]
            else:
                identity = proposal.identity
            if identity is None:
                continue
            selections.append((proposal, identity))
        if not selections:
            return
        write_tags = self.write_tags_box.isChecked()
        confirm = QtWidgets.QMessageBox(self)
        confirm.setWindowTitle("Confirm identity registration")
        confirm.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        confirm.setText(f"Register identity for {len(selections)} selected file(s)?")
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
        confirm.setDetailedText("\n".join(sorted(proposal.path for proposal, _ in selections)))
        confirm.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        confirm.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        if confirm.exec() == QtWidgets.QMessageBox.StandardButton.Yes:
            self.status.setText("Registering identities...")
            self._start("apply", selections, write_tags)

    def applied(self, results):
        succeeded = sum(1 for _, ok, _ in results if ok)
        failed_lines = [f"{path}: {message}" for path, ok, message in results if not ok]
        self.status.setText(f"Registered {succeeded} of {len(results)} file(s). Scan again to verify or continue with the rest.")
        self.details.setPlainText(
            "\n".join(failed_lines) if failed_lines
            else "Identity registration completed. Full backups are created only for audio files whose tags are modified."
        )
        self.proposals = []
        self.table.setRowCount(0)
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
