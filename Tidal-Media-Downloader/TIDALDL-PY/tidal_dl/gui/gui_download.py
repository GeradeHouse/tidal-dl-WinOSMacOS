# tidal_dl/gui/gui_download.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   gui_download.py
@Time    :   2025/04/14
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Handles download logic and state for the Tidal Media Downloader GUI.
"""

import platform
import logging
import time
import threading
import traceback
import os
from functools import partial
from typing import cast, List, Optional, Union, Any, Dict, Tuple, TYPE_CHECKING

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal, QThread, pyqtSlot
from PyQt6.QtWidgets import QMenu, QTableWidgetItem, QMessageBox
from PyQt6.QtGui import QAction

from .gui_table import SplitterTable
from ..printf import Printf
from ..tidal import TIDAL_API, AudioQuality, Track, Album, Playlist, Artist
from ..download import downloadTrack as core_downloadTrack
from ..format import getAlbumPath, getPlaylistPath, getTrackPath
from ..model import StreamUrl
from ..settings import SETTINGS
from .gui_utils import show_info_message, show_in_folder
from .gui_custom_dialog import ModernDarkDialog
from ..paths import resource_path

if TYPE_CHECKING:
    from .gui import MainView


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Set up GUI logging with INFO level for this module (download operations need visibility)
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class DownloadWorker(QObject):
    """Worker to handle downloads in a separate QThread."""

    # Signals
    setupProgressBar = pyqtSignal(str)           # track_id (kept for compatibility; not emitted)
    progress = pyqtSignal(str, int)              # track_id, percentage
    trackStarted = pyqtSignal(str)               # track_id
    trackFinished = pyqtSignal(str, bool, str)   # track_id, ok, error_msg
    allFinished = pyqtSignal(str, bool, str, str)  # title, result, msg, path
    actuallyPaused = pyqtSignal()

    def __init__(
        self,
        main_view_instance: "MainView",
        items_to_download: List[Track],
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
        download_quality: Optional[str],
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.main_view = main_view_instance
        self.items_to_download = items_to_download
        self.playlist_context = playlist_context
        self.download_quality = download_quality

    def run(self):
        logger.debug(
            f"[DownloadWorker] Started for {len(self.items_to_download)} tracks."
        )
        final_path = None
        try:
            # Set working directory on Windows if needed
            if platform.system() == "Windows":
                download_path = os.path.abspath(SETTINGS.downloadPath)
                os.makedirs(download_path, exist_ok=True)
                # os.chdir(download_path) #removed this line, this was causing the issue

            for track_item in self.items_to_download:
                # Pause/Stop handling
                if self.main_view.stop_event.is_set():
                    logger.info("Stop request detected. Aborting download queue.")
                    break
                if self.main_view.download_paused:
                    logger.info("Download queue paused. Waiting for resume signal...")
                    self.actuallyPaused.emit()
                    self.main_view.pause_event.wait()
                    logger.info("Download queue resumed.")
                    if self.main_view.stop_event.is_set():
                        logger.info(
                            "Stop request detected after pause. Aborting download queue."
                        )
                        break

                if not isinstance(track_item, Track) or track_item.id is None:
                    continue

                track_id_str = str(track_item.id)

                # Bridge object for aigpy callbacks
                class AigpyProgressBridge:
                    def __init__(self, worker: "DownloadWorker", track_id: str):
                        self.worker = worker
                        self.track_id = track_id
                        self._last_percent = -1

                    def update(self, currentSize: int, totalSize: int):
                        if totalSize > 0:
                            percent = int((currentSize / totalSize) * 100)
                            if percent != self._last_percent:
                                self.worker.progress.emit(self.track_id, percent)
                                self._last_percent = percent

                    def finish(self):
                        if self._last_percent != 100:
                            self.worker.progress.emit(self.track_id, 100)

                    def updateStream(self, stream):
                        pass

                    def setMaxNum(self, maxNum: int):
                        pass

                progress_handler = AigpyProgressBridge(self, track_id_str)

                # Signal "real start" so UI can switch this row to a progress bar
                self.trackStarted.emit(track_id_str)
                time.sleep(0.05)  # allow UI to process

                try:
                    album_param = (
                        self.playlist_context
                        if isinstance(self.playlist_context, Album)
                        else None
                    )
                    ok, err = core_downloadTrack(
                        track=track_item,
                        main_view_instance=self.main_view,
                        album=album_param,
                        playlist_context=self.playlist_context,
                        userProgress=progress_handler,
                        downloadQuality=self.download_quality,
                    )
                    progress_handler.finish()
                    self.trackFinished.emit(track_id_str, bool(ok), err or "")
                except Exception as e:
                    logger.exception("Download failed for track %s", track_id_str)
                    self.trackFinished.emit(track_id_str, False, str(e))
                    # IMPORTANT: do not raise; continue with next track

            # Determine final path for "Show in Folder" by computing the same path as downloadTrack
            if self.items_to_download:
                first_track_item = self.items_to_download[0]
                final_path = None

                # Get artist info for path construction
                artists = TIDAL_API.getArtistsName(
                    cast(List[Artist], getattr(first_track_item, "artists", []))
                )
                artist = getattr(getattr(first_track_item, "artist", None), "name", "") or artists

                # Dummy stream for getTrackPath (only used for extension, which we ignore for folder)
                dummy_stream = StreamUrl()
                dummy_stream.codec = "flac"  # Assume FLAC for path calculation

                # Compute the full path using the same logic as downloadTrack
                computed_path = getTrackPath(first_track_item, dummy_stream, artist, artists, album=None, playlist_context=self.playlist_context)
                final_path = os.path.dirname(computed_path)
                
                # Ensure the path is absolute by joining it with the base download path
                final_path = os.path.join(os.path.abspath(SETTINGS.downloadPath), final_path)
                final_path = os.path.normpath(final_path)
                
                logger.debug(f"Computed track path: '{computed_path}', folder: '{final_path}'")
                logger.debug(f"SETTINGS.downloadPath: '{SETTINGS.downloadPath}'")

                # Fallback to base download path if path construction failed
                if not final_path:
                    final_path = SETTINGS.downloadPath
                    if not os.path.isabs(final_path):
                        final_path = os.path.abspath(final_path)

                logger.debug(f"Determined final path for 'Show in Folder': '{final_path}'")

            # Emit final signal
            if self.main_view.stop_requested or self.main_view.cancel_requested:
                status_msg = (
                    "Download cancelled by user."
                    if self.main_view.cancel_requested
                    else "Download stopped by user."
                )
                status_title = (
                    "Download Cancelled"
                    if self.main_view.cancel_requested
                    else "Download Stopped"
                )
                self.allFinished.emit(status_title, False, status_msg, "")
            else:
                self.allFinished.emit("Download Success!", True, "", final_path or "")

        except Exception as e:
            logger.error("[DownloadWorker] Exception caught in run()", exc_info=True)
            logger.error(f"Error in download thread: {traceback.format_exc()}")
            self.allFinished.emit("Download Error", False, str(e), "")


class DownloadHandler(QObject):
    """
    Manages download operations, state (active, paused, stopped),
    and interactions between the GUI (MainView) and the core download functions.
    """
    # MODIFIED: Add progress signal
    downloadProgress = pyqtSignal(str, int) # playlist_id, count

    def __init__(
        self,
        main_view: "MainView",
        button_stack: QtWidgets.QStackedWidget,
        btn_download: QtWidgets.QPushButton,
        btn_pause_resume: QtWidgets.QPushButton,
        btn_stop: QtWidgets.QPushButton,
    ):
        super().__init__(main_view)
        self.main_view = main_view
        self.button_stack = button_stack
        self.btn_download = btn_download
        self.btn_pause_resume = btn_pause_resume
        self.btn_stop = btn_stop

        # State Management
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self.download_thread: Optional[QThread] = None
        self.download_worker: Optional[DownloadWorker] = None
        self._processed_count = 0 # MODIFIED: Add counter

        table_widget = getattr(self.main_view, "tableWidget", None)
        if table_widget:
            table_widget.itemSelectionChanged.connect(self._update_download_button_text)
        else:
            logger.error(
                "DownloadHandler init: tableWidget not found on main_view. Button text update on selection change will not work."
            )
        self._update_download_button_text()

    def _update_download_button_text(self):
        table = getattr(self.main_view, "tableWidget", None)
        if not table:
            self.btn_download.setText("Download Selected")
            return

        selected_indices = table.getSelectedRows()
        if not selected_indices:
            self.btn_download.setText("Download All")
        else:
            self.btn_download.setText("Download Selected")
        logger.debug(f"Download button text updated to: '{self.btn_download.text()}'")

    def _update_ui_for_download_start(self):
        self.button_stack.setCurrentIndex(1)
        self.btn_pause_resume.setText("Pause")
        self.btn_pause_resume.setEnabled(True)
        self.btn_stop.setText("Stop")
        self.btn_stop.setEnabled(True)
        logger.debug("[GUI] Set download UI: Showing Pause/Stop")

    @pyqtSlot()
    def onActuallyPaused(self):
        logger.debug(
            f"[GUI] Slot onActuallyPaused called. Current state: download_paused={self.main_view.download_paused}"
        )
        if self.main_view.download_paused:
            self.btn_pause_resume.setText("Resume")
            self.btn_pause_resume.setEnabled(True)
            logger.debug("[GUI] Set button text to 'Resume' and enabled")
        else:
            logger.warning("[GUI] onActuallyPaused called but download_paused is False")

    def startContextMenuDownload(
        self, tracks_with_rows: List[Tuple[int, Track]], quality_enum: AudioQuality
    ):
        if self.main_view.download_active:
            show_info_message(
                self.main_view,
                "Download In Progress",
                "Another download is already in progress.",
                "",
                icon_path=resource_path("assets/icons/info_icon.png")
            )
            return

        if not tracks_with_rows:
            show_info_message(
                self.main_view,
                "Download Error",
                "Could not retrieve track information for download.",
                "Please select valid tracks.",
                icon_path=resource_path("assets/icons/error_icon.png")
            )
            return

        tracks_only = [track for _, track in tracks_with_rows]
        current_playlist_context = getattr(self.main_view, "s_playlist_obj", None)

        quality_str = Printf.map_quality(quality_enum)

        self._start_download_thread(tracks_only, current_playlist_context, quality_str)


    def downloadTableContextMenu(self, menu: QMenu, selected_rows_indices: List[int]):
        logger.debug("[GUI ContextMenu] downloadTableContextMenu executing.")
        tracks_with_rows: List[Tuple[int, Track]] = []
        menu_title_base = "Download"

        if not selected_rows_indices:
            no_tracks_action = menu.addAction("No tracks selected for download")
            if no_tracks_action:
                no_tracks_action.setEnabled(False)
            return

        for r_idx in selected_rows_indices:
            tidal_track: Optional[Track] = self._get_tidal_track_from_row(r_idx)
            if tidal_track:
                tracks_with_rows.append((r_idx, tidal_track))

        if not tracks_with_rows:
            no_valid_tracks_action = menu.addAction(
                "No downloadable tracks in selection"
            )
            if no_valid_tracks_action:
                no_valid_tracks_action.setEnabled(False)
            return

        num_downloadable = len(tracks_with_rows)
        menu_title = f"{menu_title_base} {num_downloadable} Track"
        if num_downloadable > 1:
            menu_title += "s"

        downloadMenu = menu.addMenu(menu_title)
        if not downloadMenu:
            return

        dlQualities = [
            ("M4a (High Efficiency)", AudioQuality.LOW),
            ("M4a (Full Bandwidth)", AudioQuality.HIGH),
            ("MP3 (Constant Bitrate)", AudioQuality.MP3),
            ("FLAC (CD Standard)", AudioQuality.LOSSLESS),
            ("FLAC (High Resolution)", AudioQuality.HI_RES_LOSSLESS),
            ("Highest Available", AudioQuality.HIGHEST),
        ]

        for text, qual_enum in dlQualities:
            action: Optional[QAction] = downloadMenu.addAction(text)
            if action:
                action.triggered.connect(
                    partial(self.startContextMenuDownload, tracks_with_rows, qual_enum)
                )

    def _get_tidal_track_from_row(self, row_index: int) -> Optional[Track]:
        table = getattr(self.main_view, "tableWidget", None)
        if not table or row_index >= table.rowCount():
            return None

        title_item: Optional[QTableWidgetItem] = table.item(row_index, 1)
        if not title_item:
            return None

        title_data: Optional[Any] = title_item.data(Qt.ItemDataRole.UserRole)
        tidal_track: Optional[Track] = None

        if isinstance(title_data, dict) and title_data.get("type") == "spotify_track":
            tidal_track_candidate = title_data.get("tidal_track")
            if isinstance(tidal_track_candidate, Track):
                tidal_track = tidal_track_candidate
        elif isinstance(title_data, Track):
            tidal_track = title_data

        if tidal_track and isinstance(tidal_track, Track):
            return tidal_track
        return None

    @pyqtSlot()
    def download(self):
        QTimer.singleShot(0, self._start_download_from_selection)

    def _start_download_from_selection(self):
        table = cast(SplitterTable, self.main_view.tableWidget)
        if not table:
            return

        button_text = self.btn_download.text()
        tracks_to_download: List[Track] = []
        current_playlist_obj = self.main_view.s_playlist_obj

        if button_text == "Download Selected":
            selected_rows_indices = table.getSelectedRows()
            if not selected_rows_indices:
                show_info_message(
                    self.main_view, "Selection Error", "Please select rows first.", "",
                    icon_path=resource_path("assets/icons/info_icon.png")
                )
                return
            for row_index in selected_rows_indices:
                track = self._get_tidal_track_from_row(row_index)
                if track:
                    tracks_to_download.append(track)
        elif button_text == "Download All":
            is_spotify_playlist = (
                isinstance(current_playlist_obj, dict)
                and current_playlist_obj.get("type") == "spotify"
            )
            unlinked_tracks_exist = False
            for row_index in range(table.rowCount()):
                track = self._get_tidal_track_from_row(row_index)
                if track:
                    tracks_to_download.append(track)
                elif is_spotify_playlist:
                    unlinked_tracks_exist = True

            if is_spotify_playlist and unlinked_tracks_exist:
                msgBox = QMessageBox(self.main_view)
                msgBox.setWindowTitle("Unlinked Tracks Found")
                msgBox.setText("Some tracks aren’t available for download.")
                msgBox.setInformativeText(
                    "Would you like to download only the linked tracks?"
                )
                continueButton = msgBox.addButton(
                    "Continue", QMessageBox.ButtonRole.YesRole
                )
                cancelButton = msgBox.addButton(
                    "Cancel", QMessageBox.ButtonRole.RejectRole
                )
                msgBox.exec()
                if msgBox.clickedButton() == cancelButton:
                    return

        if not tracks_to_download:
            show_info_message(
                self.main_view,
                "Download Error",
                "No downloadable tracks found in selection.",
                "",
                icon_path=resource_path("assets/icons/error_icon.png")
            )
            return

        if self.main_view.download_active:
            show_info_message(
                self.main_view,
                "Download In Progress",
                "A download is already in progress.",
                "",
                icon_path=resource_path("assets/icons/info_icon.png")
            )
            return
        
        selected_quality_enum = self.main_view.c_combTQuality.currentData()
        quality_arg_str = Printf.map_quality(selected_quality_enum)

        self._start_download_thread(tracks_to_download, current_playlist_obj, quality_arg_str)

    def _start_download_thread(
        self,
        tracks_to_start: List[Track],
        playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
        quality_arg_str: Optional[str]
    ):
        self.main_view.download_active = True
        self._processed_count = 0 # MODIFIED: Reset counter
        self.main_view.stop_event.clear()
        self.main_view.pause_event.set()
        self.main_view.download_paused = False
        self.main_view.stop_requested = False
        self.main_view.cancel_requested = False

        self.active_downloads.clear()
        for i, track in enumerate(tracks_to_start):
            self.active_downloads[str(track.id)] = {
                "status": "pending",
                "progress": 0,
                "playlist_context": playlist_context,
                "tooltip": f"Position {i+1} of {len(tracks_to_start)} in queue.",
            }
        logger.debug(
            f"[DownloadHandler] Populating active_downloads with {len(tracks_to_start)} pending tracks."
        )

        self._update_ui_for_download_start()
        self.main_view.table_handler.refresh_view_for_pending_downloads(
            tracks_to_start
        )

        self.download_thread = QThread(self.main_view)
        self.download_worker = DownloadWorker(
            self.main_view, tracks_to_start, playlist_context, quality_arg_str
        )
        self.download_worker.moveToThread(self.download_thread)

        self.download_thread.started.connect(self.download_worker.run)

        # Progress + lifecycle
        self.download_worker.progress.connect(
            self.main_view.table_handler.update_track_progress
        )
        self.download_worker.progress.connect(self.onProgressUpdate)
        self.download_worker.trackStarted.connect(self.onTrackStarted)
        self.download_worker.trackFinished.connect(self.onTrackFinished)
        self.download_worker.allFinished.connect(self.downloadEnd)
        self.download_worker.actuallyPaused.connect(self.onActuallyPaused)

        self.download_thread.finished.connect(self.download_worker.deleteLater)
        self.download_thread.finished.connect(self.download_thread.deleteLater)

        self.download_thread.start()

    @pyqtSlot(str)
    def onTrackStarted(self, track_id: str):
        state = self.active_downloads.get(track_id)
        if state:
            state["status"] = "downloading"
            state["progress"] = 0
        # Ensure the UI switches to a progress bar on the correct row
        self.main_view.table_handler.setup_progress_bar_for_download(track_id)

    @pyqtSlot(str, int)
    def onProgressUpdate(self, track_id: str, percent: int):
        state = self.active_downloads.get(track_id)
        if state:
            state["progress"] = percent

    @pyqtSlot(str, bool, str)
    def onTrackFinished(self, track_id: str, ok: bool, error_msg: str):
        state = self.active_downloads.get(track_id)
        if state:
            state["status"] = "completed" if ok else "failed"
            state["progress"] = 100 if ok else state.get("progress", 0)
            if not ok:
                state["error"] = error_msg
        self.main_view.table_handler.mark_track_completed(track_id, ok, error_msg)

        # MODIFIED: Increment counter and emit progress
        self._processed_count += 1
        playlist_context = state.get("playlist_context") if state else None
        playlist_id = None
        if isinstance(playlist_context, Playlist):
            playlist_id = playlist_context.uuid
        elif isinstance(playlist_context, dict): # Spotify playlist
            playlist_id = playlist_context.get("data", {}).get("id")
        
        if playlist_id:
            self.downloadProgress.emit(str(playlist_id), self._processed_count)

    def onPauseResumeClicked(self):
        if not self.main_view.download_active:
            return

        if self.main_view.download_paused:
            self.main_view.download_paused = False
            self.btn_pause_resume.setText("Pause")
            self.main_view.pause_event.set()
            logger.info("Resume requested.")
        else:
            self.main_view.download_paused = True
            self.btn_pause_resume.setText("Pausing...")
            self.btn_pause_resume.setEnabled(False)
            self.main_view.pause_event.clear()
            logger.info("Pause requested. Download will pause after current track.")

    def onStopClicked(self):
        if not self.main_view.download_active:
            return

        if self.main_view.stop_requested:
            self.main_view.cancel_requested = True
            self.main_view.stop_event.set()
            self.btn_stop.setText("Cancelling...")
            self.btn_stop.setEnabled(False)
            self.btn_pause_resume.setEnabled(False)
            logger.info("Immediate cancellation requested.")
        else:
            self.main_view.stop_requested = True
            self.main_view.stop_event.set()
            self.btn_stop.setText("Stopping...")
            self.btn_pause_resume.setEnabled(False)
            logger.info("Stop requested. Finishing current track...")

    @pyqtSlot(str, bool, str, str)
    def downloadEnd(self, title: str, result: bool, msg: str, path: Optional[str] = None):
        logger.debug(
            f"[GUI] Entering downloadEnd: title='{title}', result={result}, msg='{msg}', path='{path}'"
        )

        self.main_view.download_active = False
        self.main_view.download_paused = False
        self.main_view.stop_requested = False
        self.main_view.cancel_requested = False
        self.download_thread = None
        self.download_worker = None
        self.active_downloads.clear()

        self.button_stack.setCurrentIndex(0)
        self.btn_download.setEnabled(True)
        self.btn_pause_resume.setText("Pause")
        self.btn_pause_resume.setEnabled(True)
        self.btn_stop.setText("Stop")
        self.btn_stop.setEnabled(True)

        self._update_download_button_text()
        self.main_view.table_handler.refresh_table_view()

        # The TaskQueueManager will now handle the final dialog
        if self.main_view.task_queue_manager and self.main_view.task_queue_manager.is_running_task:
             # Let the queue manager know this job is done
            self.main_view.task_queue_manager.job_finished()
        else:
            # If not part of a queue, show the dialog as before
            if result:
                info_icon_path = resource_path("assets/icons/success_icon.png")
                custom_dialog = ModernDarkDialog(
                    title="Info",
                    main_message="Download finished successfully.",
                    informative_text=f"Saved to: {path if path else 'N/A'}",
                    icon_path=info_icon_path,
                    parent=self.main_view,
                    show_folder_path=path,
                    show_in_folder_func=show_in_folder,
                )
                custom_dialog.exec()
            elif title in ["Download Stopped", "Download Cancelled", "Download Error"]:
                show_info_message(self.main_view, title, msg or f"Download failed: {msg}", "", icon_path=resource_path("assets/icons/error_icon.png"))
            else:
                show_info_message(self.main_view, "Download Failed", f"Download failed: {msg}", "", icon_path=resource_path("assets/icons/error_icon.png"))