"""Browse, confirm and manage explicit playlist destinations."""

import logging
import os
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets

from tidal_dl.paths import get_user_download_path
from tidal_dl.playlist_folders import PLAYLIST_FOLDERS, canonical_folder, playlist_key
from tidal_dl.settings import SETTINGS

if TYPE_CHECKING:
    from .gui_playlist_tree_handler import PlaylistTreeHandler

logger = logging.getLogger(__name__)


class SiblingFolderScan(QtCore.QThread):
    """Shallow, cancellable discovery; never reads audio or follows symlinks."""

    def __init__(self, parent_folder: str, parent: QtCore.QObject):
        super().__init__(parent)
        self.parent_folder = parent_folder
        self.folders: dict[str, str] = {}
        self.error = ""

    def run(self) -> None:
        try:
            with os.scandir(self.parent_folder) as entries:
                for entry in entries:
                    if self.isInterruptionRequested():
                        return
                    if entry.is_dir(follow_symlinks=False):
                        self.folders[entry.name] = entry.path
        except OSError as exc:
            self.error = str(exc)


class PlaylistFolderController(QtCore.QObject):
    def __init__(self, handler: "PlaylistTreeHandler") -> None:
        super().__init__(handler)
        self.handler = handler
        self.parent_widget = handler.tree_widget
        self.scans: set[SiblingFolderScan] = set()
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.shutdown)

    def shutdown(self) -> None:
        for scan in tuple(self.scans):
            scan.requestInterruption()
        for scan in tuple(self.scans):
            scan.wait()

    def _descriptor(self, playlist_id: str):
        item = self.handler.id_to_item.get(playlist_id)
        return self.handler._get_playlist_download_count_descriptor(item) if item is not None else None

    def _can_change(self) -> bool:
        manager = self.handler.task_queue_manager
        download_handler = self.handler.download_handler
        thread = download_handler.download_thread if download_handler else None
        busy = bool(manager and (manager.is_running_task or manager.task_queue))
        if thread is not None:
            try:
                busy = busy or thread.isRunning()
            except RuntimeError:
                pass
        if busy:
            QtWidgets.QMessageBox.information(
                self.parent_widget, "Playlist folder is in use",
                "Finish or stop the current jobs and clear the queue before changing folder links. "
                "This prevents tracks from one download job being written to different folders.",
            )
        return not busy

    def _save(self, changes: dict[str, str | None]) -> bool:
        if not self._can_change():
            return False
        try:
            PLAYLIST_FOLDERS.update(changes)
        except Exception as exc:
            logger.exception("Could not save playlist folder links")
            QtWidgets.QMessageBox.warning(self.parent_widget, "Folder link not saved", str(exc))
            return False
        for playlist_id, item in list(self.handler.id_to_item.items()):
            descriptor = self.handler._get_playlist_download_count_descriptor(item)
            if descriptor and playlist_key(descriptor[4]) in changes:
                self.handler._on_playlist_download_count_resolved(
                    playlist_id, self.handler._download_count_generation.get(playlist_id, 0), -1
                )
                self.handler._invalidate_playlist_download_count(playlist_id)
                self.update_tooltip(playlist_id)
        self.handler._schedule_visible_download_count_scan()
        table = self.handler.table_handler
        active = getattr(self.handler.main_view, "s_playlist_obj", None)
        if table and playlist_key(active) in changes:
            table.refresh_table_view()
        return True

    def update_tooltip(self, playlist_id: str) -> None:
        descriptor = self._descriptor(playlist_id)
        widget = self.handler.item_widgets.get(playlist_id)
        if not descriptor or widget is None:
            return
        try:
            path = PLAYLIST_FOLDERS.get(descriptor[4])
            text = (
                f"Open linked playlist folder:\n{path}\nRight-click for folder options."
                if path else "Open local playlist folder. Right-click to link a different folder."
            )
        except Exception:
            text = "Folder links could not be read. Click for details."
        widget.folder_button.setToolTip(text)

    def offer_missing(self, playlist_id: str, linked_folder: str | None = None) -> None:
        descriptor = self._descriptor(playlist_id)
        if not descriptor:
            return
        dialog = QtWidgets.QMessageBox(self.parent_widget)
        dialog.setWindowTitle("Playlist folder not found")
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Information)
        dialog.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        dialog.setText(
            f'The linked folder for "{descriptor[2]}" is unavailable:\n{linked_folder}'
            if linked_folder else f'No local folder was found for "{descriptor[2]}".'
        )
        dialog.setInformativeText(
            "If your tracks are elsewhere, select their existing folder. Future downloads for "
            "this playlist will use that exact folder. No files will be moved or deleted."
        )
        browse = dialog.addButton("Browse and link folder…", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is browse:
            self.browse(playlist_id)

    def browse(self, playlist_id: str) -> None:
        if not self._can_change():
            return
        descriptor = self._descriptor(playlist_id)
        if not descriptor:
            return
        key = playlist_key(descriptor[4])
        if key is None:
            return
        try:
            old_folder = PLAYLIST_FOLDERS.get(descriptor[4])
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.parent_widget, "Cannot read folder links", str(exc))
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self.parent_widget, f'Link local folder — {descriptor[2]}',
            old_folder or get_user_download_path(SETTINGS.downloadPath),
        )
        if not folder:
            return
        folder = os.path.abspath(folder)
        if folder == os.path.dirname(folder):
            QtWidgets.QMessageBox.warning(
                self.parent_widget, "Choose a playlist folder",
                "Select the folder containing this playlist's tracks, not an entire drive.",
            )
            return
        dialog = QtWidgets.QMessageBox(self.parent_widget)
        dialog.setWindowTitle("Confirm playlist folder link")
        dialog.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        dialog.setText(f'{descriptor[1].upper()} — {descriptor[2]}\nID: {descriptor[0]}\n\nDestination:\n{folder}')
        dialog.setInformativeText(
            "New downloads will go directly into this folder, for every audio format. "
            "The global download location and playlist-folder template will not apply to this playlist. "
            "Existing-file checks will use this destination. No files will be moved, renamed, retagged or deleted."
            + (f"\n\nPrevious link:\n{old_folder}" if old_folder else "")
        )
        scan = QtWidgets.QCheckBox("Look for exact playlist-name matches beside this folder")
        scan.setChecked(True)
        scan.setToolTip("Only immediate sibling folders are scanned. You review every proposed link before saving.")
        dialog.setCheckBox(scan)
        confirm = dialog.addButton("Link folder", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is not confirm or not self._save({key: folder}):
            return
        self.handler._open_local_folder(folder)
        if scan.isChecked():
            self.scan_siblings(folder)

    def remove(self, playlist_id: str) -> None:
        if not self._can_change():
            return
        descriptor = self._descriptor(playlist_id)
        key = playlist_key(descriptor[4]) if descriptor else None
        if not key:
            return
        answer = QtWidgets.QMessageBox.question(
            self.parent_widget, "Remove local folder link",
            "Return this playlist to the normal download location and folder template?\n\n"
            "Files in the linked folder will stay where they are. They will not be moved or deleted.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._save({key: None})

    def add_menu(self, menu: QtWidgets.QMenu, playlist_id: str) -> None:
        submenu = menu.addMenu("Local folder")
        if submenu is None:
            return
        submenu.addAction("Open folder", lambda: self.handler._open_playlist_folder(playlist_id))
        submenu.addAction("Browse and link folder…", lambda: self.browse(playlist_id))
        remove_action = submenu.addAction("Remove folder link…", lambda: self.remove(playlist_id))
        descriptor = self._descriptor(playlist_id)
        try:
            folder = PLAYLIST_FOLDERS.get(descriptor[4]) if descriptor else None
        except Exception:
            folder = None
        if remove_action is not None:
            remove_action.setEnabled(bool(folder))
        if folder:
            submenu.addAction("Find matching sibling folders…", lambda: self.scan_siblings(folder))

    def scan_siblings(self, folder: str) -> None:
        progress = QtWidgets.QProgressDialog(
            "Looking for sibling playlist folders…", "Cancel", 0, 0, self.parent_widget
        )
        progress.setWindowTitle("Find other playlist folders")
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        worker = SiblingFolderScan(os.path.dirname(folder), self)
        self.scans.add(worker)
        progress.canceled.connect(worker.requestInterruption)

        def finished():
            cancelled = worker.isInterruptionRequested()
            progress.close()
            self.scans.discard(worker)
            if not cancelled:
                if worker.error:
                    QtWidgets.QMessageBox.warning(
                        self.parent_widget, "Sibling scan unavailable",
                        "The selected playlist link was saved, but sibling folders could not be read:\n" + worker.error,
                    )
                else:
                    self._review_siblings(worker.parent_folder, worker.folders)
            worker.deleteLater()
            progress.deleteLater()

        worker.finished.connect(finished)
        progress.show()
        worker.start()

    def _review_siblings(self, parent_folder: str, folders: dict[str, str]) -> None:
        try:
            links = PLAYLIST_FOLDERS.snapshot()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.parent_widget, "Cannot read folder links", str(exc))
            return
        owners = {canonical_folder(path): key for key, path in links.items()}
        matches = []
        for item in list(self.handler.id_to_item.values()):
            descriptor = self.handler._get_playlist_download_count_descriptor(item)
            if not descriptor:
                continue
            key = playlist_key(descriptor[4])
            # Literal full-name equality: no fuzzy matching, prefix stripping,
            # case folding or filename sanitization that could merge playlists.
            folder = folders.get(descriptor[2])
            if not key or not folder or key in links:
                continue
            if canonical_folder(folder) in owners:
                continue
            matches.append((key, descriptor[2], folder))
        if not matches:
            QtWidgets.QMessageBox.information(
                self.parent_widget, "Sibling scan complete",
                "No unlinked, exact-name matches were found among the currently loaded TIDAL/Spotify playlists. "
                "Existing links and folders already assigned to another playlist were left unchanged.",
            )
            return
        counts: dict[str, int] = {}
        for _, _, folder in matches:
            counts[folder] = counts.get(folder, 0) + 1
        dialog = QtWidgets.QDialog(self.parent_widget)
        dialog.setWindowTitle("Link other playlist folders")
        dialog.resize(850, 440)
        layout = QtWidgets.QVBoxLayout(dialog)
        explanation = QtWidgets.QLabel(
            "Review exact-name matches below. Only checked rows will be linked.\n"
            "Duplicate playlist names are unchecked: choose at most one playlist for each folder.\n"
            "Existing links are never replaced. Only currently loaded playlists are included.\n"
            f"Scanned parent: {parent_folder}"
        )
        explanation.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        table = QtWidgets.QTableWidget(len(matches), 4)
        table.setHorizontalHeaderLabels(["Link / playlist", "Service", "Playlist ID", "Destination"])
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, (key, name, folder) in enumerate(matches):
            title = QtWidgets.QTableWidgetItem(name)
            title.setFlags(title.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            title.setCheckState(QtCore.Qt.CheckState.Checked if counts[folder] == 1 else QtCore.Qt.CheckState.Unchecked)
            table.setItem(row, 0, title)
            service, _, identifier = key.partition(":")
            for column, value in enumerate((service.upper(), identifier, folder), 1):
                cell = QtWidgets.QTableWidgetItem(value)
                cell.setToolTip(value)
                table.setItem(row, column, cell)
        header = table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            header.setStretchLastSection(True)
        layout.addWidget(table)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        save_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save)
        if save_button is not None:
            save_button.setText("Link selected folders")
        buttons.rejected.connect(dialog.reject)

        def save_selected():
            changes = {}
            for row, (key, _, folder) in enumerate(matches):
                cell = table.item(row, 0)
                if cell is not None and cell.checkState() == QtCore.Qt.CheckState.Checked:
                    changes[key] = folder
            if not changes:
                QtWidgets.QMessageBox.information(dialog, "No folders selected", "Check at least one playlist to link, or choose Cancel.")
                return
            try:
                current_links = PLAYLIST_FOLDERS.snapshot()
            except Exception as exc:
                QtWidgets.QMessageBox.warning(dialog, "Cannot read folder links", str(exc))
                return
            if any(key in current_links for key in changes):
                QtWidgets.QMessageBox.warning(dialog, "Links changed", "A playlist was linked while this review was open. Cancel and scan again; existing links will not be replaced.")
                return
            if changes and self._save(changes):
                dialog.accept()

        buttons.accepted.connect(save_selected)
        layout.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()
