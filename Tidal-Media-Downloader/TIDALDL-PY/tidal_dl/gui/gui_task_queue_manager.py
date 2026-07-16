# tidal_dl/gui/gui_task_queue_manager.py

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, List, Dict, Any, Optional, Union, cast

from PyQt6 import QtCore
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, Qt

# Local imports
from tidal_dl.tidal import Playlist, AudioQuality, Track, TIDAL_API, Type
from tidal_dl.printf import Printf
from tidal_dl.download_item import DownloadItem
import aigpy

if TYPE_CHECKING:
    from tidal_dl.gui.gui_main import MainView

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (task operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

BULK_NON_COMPLETED_PREVIEW_LIMIT = 3
JOB_CHAIN_YIELD_MS = 25
SLOW_QUEUE_PHASE_MS = 300.0

class TaskQueueManager(QObject):
    """Manages a queue of linking and downloading jobs to run them sequentially."""
    # Signals
    jobStarted = pyqtSignal(str, str, int)  # playlist_id, action_type, total_items
    jobFinished = pyqtSignal(str)           # playlist_id

    def __init__(self, main_view: "MainView"):
        super().__init__(main_view)
        self.main_view = main_view
        self.task_queue = deque()
        self.is_running_task = False
        self.current_job: Optional[Dict[str, Any]] = None
        self._queued_preview_generation: Dict[str, int] = {}
        self._queued_preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="queued-preview")

    def _begin_main_busy(self, reason: str) -> None:
        QtCore.QMetaObject.invokeMethod(
            self.main_view,
            "beginBusyOperation",
            Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, reason),
        )

    def _end_main_busy(self, reason: str) -> None:
        QtCore.QMetaObject.invokeMethod(
            self.main_view,
            "endBusyOperation",
            Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, reason),
        )

    def add_spotify_link_job(self, spotify_playlists_data: List[Dict[str, Any]]):
        """Adds a job to link all tracks in the selected Spotify playlists."""
        if not spotify_playlists_data:
            return

        for p_data in spotify_playlists_data:
            job = {
                "type": "link_spotify",
                "playlist_data": p_data,
                "description": f"Link tracks for Spotify playlist: {p_data.get('data', {}).get('name', 'Unknown')}"
            }
            self.task_queue.append(job)
            logger.info(f"Queued job: {job['description']}")
        
        self.process_next_job()

    def add_spotify_download_job(
        self,
        spotify_playlists_data: List[Dict[str, Any]],
        quality: AudioQuality,
        non_completed_only: bool = False,
    ):
        """Adds a job to download all tracks in the selected Spotify playlists."""
        if not spotify_playlists_data:
            return

        queue_was_idle = (not self.is_running_task) and (len(self.task_queue) == 0)
        bulk_missing_queue = bool(
            non_completed_only
            and len(spotify_playlists_data) > BULK_NON_COMPLETED_PREVIEW_LIMIT
        )
        if bulk_missing_queue:
            logger.info(
                "Bulk Spotify missing-download queue detected; suppressing queued previews | playlist_count=%d",
                len(spotify_playlists_data),
            )

        for index, p_data in enumerate(spotify_playlists_data):
            job = {
                "type": "download_spotify",
                "playlist_data": p_data,
                "quality": quality,
                "non_completed_only": bool(non_completed_only),
                "description": f"Download Spotify playlist: {p_data.get('data', {}).get('name', 'Unknown')}"
            }
            self.task_queue.append(job)
            if bulk_missing_queue:
                logger.debug(f"Queued job: {job['description']}")
            else:
                logger.info(f"Queued job: {job['description']}")
            if non_completed_only:
                playlist_id = str(p_data.get("data", {}).get("id") or "").strip()
                first_job_starts_immediately = queue_was_idle and index == 0

                if playlist_id and not self._is_playlist_currently_processing(playlist_id):
                    if bulk_missing_queue and not first_job_starts_immediately:
                        self._emit_queued_missing_download_status(playlist_id)
                    else:
                        self._emit_calculating_missing_tracks_status(playlist_id)

                if not first_job_starts_immediately:
                    if bulk_missing_queue:
                        logger.info(
                            "Skipping queued Spotify missing preview | playlist_id=%s index=%d playlist_count=%d",
                            playlist_id,
                            index,
                            len(spotify_playlists_data),
                        )
                    else:
                        self._schedule_spotify_non_completed_preview(p_data)

        self.process_next_job()

    def add_tidal_download_job(
        self,
        tidal_playlists: List[Playlist],
        quality: AudioQuality,
        non_completed_only: bool = False,
    ):
        """Adds a job to download all tracks in the selected Tidal playlists."""
        if not tidal_playlists:
            return

        queue_was_idle = (not self.is_running_task) and (len(self.task_queue) == 0)
        bulk_missing_queue = bool(
            non_completed_only
            and len(tidal_playlists) > BULK_NON_COMPLETED_PREVIEW_LIMIT
        )
        if bulk_missing_queue:
            logger.info(
                "Bulk TIDAL missing-download queue detected; suppressing queued previews | playlist_count=%d",
                len(tidal_playlists),
            )

        for index, playlist in enumerate(tidal_playlists):
            job = {
                "type": "download_tidal",
                "playlist_obj": playlist,
                "quality": quality,
                "non_completed_only": bool(non_completed_only),
                "description": f"Download Tidal playlist: {getattr(playlist, 'title', 'Unknown')}"
            }
            self.task_queue.append(job)
            if bulk_missing_queue:
                logger.debug(f"Queued job: {job['description']}")
            else:
                logger.info(f"Queued job: {job['description']}")
            if non_completed_only:
                playlist_id = str(getattr(playlist, "uuid", "") or "").strip()
                first_job_starts_immediately = queue_was_idle and index == 0

                if playlist_id and not self._is_playlist_currently_processing(playlist_id):
                    if bulk_missing_queue and not first_job_starts_immediately:
                        self._emit_queued_missing_download_status(playlist_id)
                    else:
                        self._emit_calculating_missing_tracks_status(playlist_id)

                if not first_job_starts_immediately:
                    if bulk_missing_queue:
                        logger.info(
                            "Skipping queued TIDAL missing preview | playlist_id=%s index=%d playlist_count=%d",
                            playlist_id,
                            index,
                            len(tidal_playlists),
                        )
                    else:
                        self._schedule_tidal_non_completed_preview(playlist)

        self.process_next_job()

    def process_next_job(self):
        """Processes the next job in the queue if no other task is running."""
        if self.is_running_task or not self.task_queue:
            return

        self.is_running_task = True
        self.current_job = self.task_queue.popleft()
        
        if not self.current_job:
            self.is_running_task = False
            return

        job = self.current_job
        logger.info(
            "Starting job | description=%s remaining_queue=%d",
            job.get("description"),
            len(self.task_queue),
        )

        job_type = job.get("type")
        if job_type == "link_spotify":
            self._execute_spotify_link_job(job)
        elif job_type == "download_spotify":
            self._execute_spotify_download_job(job)
        elif job_type == "download_tidal":
            self._execute_tidal_download_job(job)
        else:
            logger.error(f"Unknown job type: {job_type}")
            self.job_finished()

    def job_finished(self):
        """Marks the current job as finished and processes the next one."""
        logger.info("Job finished | remaining_queue=%d", len(self.task_queue))
        if self.current_job:
            job_type = self.current_job.get("type")
            playlist_id = None
            if job_type in ["link_spotify", "download_spotify"]:
                playlist_data = self.current_job.get("playlist_data")
                if isinstance(playlist_data, dict):
                    playlist_id = playlist_data.get("data", {}).get("id")
            elif job_type == "download_tidal":
                playlist_id = getattr(self.current_job.get("playlist_obj"), 'uuid', None)
            
            if playlist_id:
                self.jobFinished.emit(str(playlist_id))
            self.current_job = None

        self.is_running_task = False
        if self.task_queue:
            QtCore.QTimer.singleShot(JOB_CHAIN_YIELD_MS, self.process_next_job)

    def stop_all_tasks(self):
        """Clears the queue and stops any active worker."""
        self.task_queue.clear()
        if self.main_view.linking_active:
            self.main_view.linking_gui_handler.onStopLinkingClicked()
        if self.main_view.download_active:
            self.main_view.download_handler.onStopClicked()
        logger.info("All queued tasks have been cleared.")

    @pyqtSlot(str, int)
    def _emit_download_queued_progress(self, playlist_id: str, total: int):
        """Show a queued download progress state with a known total."""
        safe_total = max(0, int(total))
        self.jobStarted.emit(str(playlist_id), "Queued for download", safe_total)

    @pyqtSlot(str)
    def _emit_all_tracks_completed_status(self, playlist_id: str):
        """Show a clear queued state when no missing tracks remain."""
        self.jobStarted.emit(str(playlist_id), "All tracks already completed", 0)

    @pyqtSlot(str)
    def _emit_calculating_missing_tracks_status(self, playlist_id: str):
        """Show an immediate non-generic state while missing-count preview is being computed."""
        self.jobStarted.emit(str(playlist_id), "Calculating missing tracks", 0)

    @pyqtSlot(str)
    def _emit_queued_missing_download_status(self, playlist_id: str):
        """Show a lightweight queued state without starting a filesystem preview."""
        self.jobStarted.emit(str(playlist_id), "Queued for missing download", 0)

    def _extract_playlist_id_from_job(self, job: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(job, dict):
            return None

        job_type = job.get("type")
        if job_type in ["link_spotify", "download_spotify"]:
            playlist_data = job.get("playlist_data")
            if isinstance(playlist_data, dict):
                return str(playlist_data.get("data", {}).get("id") or "").strip() or None

        if job_type == "download_tidal":
            playlist_obj = job.get("playlist_obj")
            playlist_uuid = getattr(playlist_obj, "uuid", None)
            if playlist_uuid:
                return str(playlist_uuid)

        return None

    def _is_playlist_currently_processing(self, playlist_id: str) -> bool:
        normalized_playlist_id = str(playlist_id or "").strip()
        if not normalized_playlist_id:
            return False

        current_job_playlist_id = self._extract_playlist_id_from_job(self.current_job)
        if self.is_running_task and current_job_playlist_id == normalized_playlist_id:
            return True

        download_handler = getattr(self.main_view, "download_handler", None)
        if (
            download_handler
            and str(getattr(download_handler, "_current_processing_playlist_id", "") or "").strip()
            == normalized_playlist_id
        ):
            return True

        linking_handler = getattr(self.main_view, "linking_gui_handler", None)
        if (
            linking_handler
            and str(getattr(linking_handler, "_current_processing_playlist_id", "") or "").strip()
            == normalized_playlist_id
        ):
            return True

        return False

    @pyqtSlot(str, int, int)
    def _apply_queued_preview_if_current(
        self,
        playlist_id: str,
        generation: int,
        missing_count: int,
    ):
        normalized_playlist_id = str(playlist_id or "").strip()
        if not normalized_playlist_id:
            return

        latest_generation = self._queued_preview_generation.get(normalized_playlist_id, 0)
        if int(generation) != int(latest_generation):
            return

        if self._is_playlist_currently_processing(normalized_playlist_id):
            return

        safe_missing_count = max(0, int(missing_count))
        if safe_missing_count == 0:
            self._emit_all_tracks_completed_status(normalized_playlist_id)
            return

        self._emit_download_queued_progress(normalized_playlist_id, safe_missing_count)

    def _schedule_spotify_non_completed_preview(self, playlist_data: Dict[str, Any]) -> None:
        playlist_info = playlist_data.get("data", {}) if isinstance(playlist_data, dict) else {}
        playlist_id = str(playlist_info.get("id") or "").strip()
        if not playlist_id:
            return

        generation = self._queued_preview_generation.get(playlist_id, 0) + 1
        self._queued_preview_generation[playlist_id] = generation

        def worker() -> None:
            missing_count = 0
            try:
                all_tracks_meta = self.main_view.spotify_api.get_playlist_tracks(playlist_id)
                if all_tracks_meta:
                    persisted_links = self.main_view.link_persistence_manager.get_links_for_playlist(playlist_id)
                    persisted_tracks = persisted_links.get("tracks", {})
                    linked_tracks_for_download: List[Any] = []

                    for meta in all_tracks_meta:
                        spotify_id = meta.get("id") if isinstance(meta, dict) else None
                        if not spotify_id or spotify_id not in persisted_tracks:
                            continue

                        link_info = persisted_tracks.get(spotify_id, {})
                        details = link_info.get("tidal_track_details")
                        score = link_info.get("score")
                        candidates = link_info.get("candidates") or []
                        link_status = link_info.get("link_status")

                        if not details:
                            continue

                        requires_manual_review = (
                            score is not None
                            and isinstance(score, (int, float))
                            and score > 1
                            and len(candidates) > 1
                            and link_status not in {"manual_linked", "candidate_confirmed"}
                        )
                        if requires_manual_review:
                            continue

                        track_obj = self._download_item_from_persisted_link(
                            playlist_id,
                            str(spotify_id),
                            link_info,
                        )
                        if track_obj:
                            linked_tracks_for_download.append(track_obj)

                    if linked_tracks_for_download:
                        filtered_tracks = self.main_view.table_handler.filter_non_completed_tracks(
                            linked_tracks_for_download,
                            playlist_data,
                        )
                        missing_count = len(filtered_tracks)
            except Exception as ex:
                logger.debug(
                    "Failed to compute queued non-completed preview for Spotify playlist %s: %s",
                    playlist_id,
                    ex,
                    exc_info=True,
                )

            QtCore.QMetaObject.invokeMethod(
                self,
                "_apply_queued_preview_if_current",
                Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, playlist_id),
                QtCore.Q_ARG(int, int(generation)),
                QtCore.Q_ARG(int, int(missing_count)),
            )

        try:
            self._queued_preview_executor.submit(worker)
        except Exception:
            threading.Thread(target=worker, daemon=True).start()

    def _schedule_tidal_non_completed_preview(self, playlist_obj: Playlist) -> None:
        playlist_id = str(getattr(playlist_obj, "uuid", "") or "").strip()
        if not playlist_id:
            return

        generation = self._queued_preview_generation.get(playlist_id, 0) + 1
        self._queued_preview_generation[playlist_id] = generation

        def worker() -> None:
            missing_count = 0
            try:
                tracks, _ = TIDAL_API.getItems(str(playlist_id), Type.Playlist)
                if tracks:
                    filtered_tracks = self.main_view.table_handler.filter_non_completed_tracks(
                        tracks,
                        cast(Optional[Playlist], playlist_obj),
                    )
                    missing_count = len(filtered_tracks)
            except Exception as ex:
                logger.debug(
                    "Failed to compute queued non-completed preview for Tidal playlist %s: %s",
                    playlist_id,
                    ex,
                    exc_info=True,
                )

            QtCore.QMetaObject.invokeMethod(
                self,
                "_apply_queued_preview_if_current",
                Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, playlist_id),
                QtCore.Q_ARG(int, int(generation)),
                QtCore.Q_ARG(int, int(missing_count)),
            )

        try:
            self._queued_preview_executor.submit(worker)
        except Exception:
            threading.Thread(target=worker, daemon=True).start()

    def _deserialize_persisted_track(
        self,
        playlist_id: str,
        spotify_id: str,
        link_info: Dict[str, Any],
    ) -> Optional[Track]:
        """Build a valid Track object from persisted link data, with ID recovery fallback."""
        details = link_info.get("tidal_track_details")
        track_obj: Optional[Track] = None

        if details:
            try:
                candidate_obj = aigpy.model.dictToModel(details, Track())
                if isinstance(candidate_obj, Track):
                    track_obj = candidate_obj
            except Exception as ex:
                logger.error(
                    f"Failed to deserialize linked track {spotify_id}: {ex}"
                )

        if isinstance(track_obj, Track) and getattr(track_obj, "id", None) is not None:
            return track_obj

        fallback_id_raw = link_info.get("tidal_track_id")
        if fallback_id_raw is None and isinstance(details, dict):
            fallback_id_raw = details.get("id")

        fallback_id = str(fallback_id_raw or "").strip()
        if not fallback_id:
            return None

        try:
            recovered_track = TIDAL_API.getTrack(fallback_id)
            if isinstance(recovered_track, Track) and getattr(recovered_track, "id", None) is not None:
                logger.debug(
                    "Recovered persisted link track via tidal_track_id fallback | playlist_id=%s spotify_id=%s tidal_track_id=%s",
                    playlist_id,
                    spotify_id,
                    fallback_id,
                )
                return recovered_track
        except Exception as ex:
            logger.debug(
                "Failed to recover track details via tidal_track_id fallback | playlist_id=%s spotify_id=%s tidal_track_id=%s error=%s",
                playlist_id,
                spotify_id,
                fallback_id,
                ex,
                exc_info=True,
            )

        if isinstance(track_obj, Track):
            setattr(track_obj, "id", fallback_id)
            logger.debug(
                "Using deserialized persisted track with injected fallback ID | playlist_id=%s spotify_id=%s tidal_track_id=%s",
                playlist_id,
                spotify_id,
                fallback_id,
            )
            return track_obj

        return None

    def _download_item_from_persisted_link(
        self,
        playlist_id: str,
        spotify_id: str,
        link_info: Dict[str, Any],
    ) -> Optional[DownloadItem]:
        track_obj = self._deserialize_persisted_track(playlist_id, spotify_id, link_info)
        if not isinstance(track_obj, Track):
            return None

        spotify_details = link_info.get("spotify_track_details")
        if not isinstance(spotify_details, dict):
            spotify_details = {}
        features = spotify_details.get("spotify_audio_features")
        if not isinstance(features, dict):
            features = {}

        return DownloadItem(
            tidal_track=track_obj,
            source_platform="spotify",
            source_track_id=str(spotify_id or spotify_details.get("id") or "") or None,
            spotify_key=features.get("key") if features else spotify_details.get("spotify_key"),
            spotify_mode=features.get("mode") if features else spotify_details.get("spotify_mode"),
            spotify_tempo=features.get("tempo") if features else spotify_details.get("spotify_tempo"),
            spotify_metadata=spotify_details,
        )

    @pyqtSlot(str, str, str)
    def _finish_job_no_download_needed(
        self,
        playlist_id: str,
        playlist_name: str,
        reason: str,
    ):
        """Finalize a queue job with a clear no-download outcome for the user."""
        logger.info(
            "No-download outcome | playlist_id=%s playlist_name=%s reason=%s",
            playlist_id,
            playlist_name,
            reason,
        )

        try:
            current_context = getattr(self.main_view, "s_playlist_obj", None)
            current_playlist_id = None

            if isinstance(current_context, dict):
                current_data = current_context.get("data")
                if isinstance(current_data, dict):
                    current_playlist_id = current_data.get("id")
            elif isinstance(current_context, Playlist):
                current_playlist_id = getattr(current_context, "uuid", None)

            if current_playlist_id and str(current_playlist_id) == str(playlist_id):
                self.main_view.table_handler.refresh_table_view()
        except Exception as ex:
            logger.debug(
                "Failed to refresh table for no-download outcome (playlist_id=%s): %s",
                playlist_id,
                ex,
                exc_info=True,
            )

        # Intentionally avoid modal dialogs here:
        # this path can happen for many queued playlists, and a blocking popup
        # would halt queue progress until manually dismissed.
        logger.info(
            "Skipping modal no-download dialog for queued flow | playlist_id=%s playlist_name=%s reason=%s",
            playlist_id,
            playlist_name,
            reason,
        )

        self.job_finished()


    def _execute_spotify_link_job(self, job: Dict[str, Any]):
        """Handles the logic for a Spotify linking job."""
        playlist_data = job.get("playlist_data", {})
        playlist_id = playlist_data.get("data", {}).get("id")

        if not playlist_id:
            logger.error("Cannot link playlist: Missing ID.")
            self.job_finished()
            return

        # Fetch tracks in a separate thread
        def fetch_tracks_thread():
            logger.debug(f"TASK_QUEUE: Fetching tracks for playlist {playlist_id}")
            tracks = self.main_view.spotify_api.get_playlist_tracks(playlist_id)
            
            # Emit jobStarted signal from the main thread
            QtCore.QMetaObject.invokeMethod(self, "jobStarted", Qt.ConnectionType.QueuedConnection,
                                            QtCore.Q_ARG(str, playlist_id),
                                            QtCore.Q_ARG(str, "Linking"),
                                            QtCore.Q_ARG(int, len(tracks) if tracks else 0))
            QtCore.QMetaObject.invokeMethod(self, "_start_linking_for_tracks", Qt.ConnectionType.QueuedConnection,
                                            QtCore.Q_ARG(str, playlist_id),
                                            QtCore.Q_ARG(list, tracks or []))

        threading.Thread(target=fetch_tracks_thread, daemon=True).start()

    @pyqtSlot(str, list)
    def _start_linking_for_tracks(self, playlist_id: str, tracks: List[Dict[str, Any]]):
        """Starts the LinkingWorker after tracks have been fetched."""
        if not tracks:
            logger.warning(f"No tracks found for Spotify playlist {playlist_id}. Skipping link job.")
            self.job_finished()
            return

        tracks_to_link_data = []
        for i, track_meta in enumerate(tracks):
            tracks_to_link_data.append((i, track_meta))
        
        self.main_view.startLinkingWorker(tracks_to_link_data, playlist_id, self.job_finished)


    def _execute_spotify_download_job(self, job: Dict[str, Any]):
        """Handles the logic for a Spotify download job, including pre-linking."""
        playlist_data = job.get("playlist_data", {})
        playlist_id = playlist_data.get("data", {}).get("id")
        playlist_name = str(playlist_data.get("data", {}).get("name", "Spotify playlist"))
        quality = job.get("quality")
        non_completed_only = bool(job.get("non_completed_only", False))

        if not playlist_id:
            logger.error("Cannot download playlist: Missing ID.")
            self.job_finished()
            return

        def pre_download_thread():
            busy_reason = f"Preparing missing downloads: {playlist_name}"
            if non_completed_only:
                self._begin_main_busy(busy_reason)

            pre_download_start = time.perf_counter()
            try:
                logger.info(
                    "Spotify queued download preparation started | playlist_id=%s non_completed_only=%s",
                    playlist_id,
                    non_completed_only,
                )
                spotify_fetch_start = time.perf_counter()
                all_tracks_meta = self.main_view.spotify_api.get_playlist_tracks(playlist_id)
                spotify_fetch_elapsed_ms = (time.perf_counter() - spotify_fetch_start) * 1000.0
                if spotify_fetch_elapsed_ms > SLOW_QUEUE_PHASE_MS:
                    logger.warning(
                        "Spotify playlist track fetch was slow | playlist_id=%s playlist_name=%s "
                        "track_count=%d elapsed_ms=%.1f",
                        playlist_id,
                        playlist_name,
                        len(all_tracks_meta or []),
                        spotify_fetch_elapsed_ms,
                    )
                # Emit jobStarted for the download action
                initial_action = "Checking missing tracks" if non_completed_only else "Downloading"
                QtCore.QMetaObject.invokeMethod(self, "jobStarted", Qt.ConnectionType.QueuedConnection,
                                                QtCore.Q_ARG(str, playlist_id),
                                                QtCore.Q_ARG(str, initial_action),
                                                QtCore.Q_ARG(int, len(all_tracks_meta) if all_tracks_meta else 0))
                
                if not all_tracks_meta:
                    logger.warning(f"No tracks found for Spotify playlist {playlist_id}. Skipping download.")
                    QtCore.QMetaObject.invokeMethod(self, "job_finished", Qt.ConnectionType.QueuedConnection)
                    return

                unlinked_tracks_for_worker = []
                linked_tracks_for_download: List[Any] = []
                persisted_links = self.main_view.link_persistence_manager.get_links_for_playlist(playlist_id)
                persisted_tracks = persisted_links.get("tracks", {})
                persisted_track_hits = 0
                skipped_manual_review = 0
                skipped_not_found_or_none_match = 0
                skipped_deserialize_error = 0
                skipped_missing_track_id = 0

                for i, meta in enumerate(all_tracks_meta):
                    spotify_id = meta.get("id")
                    
                    if spotify_id in persisted_tracks:
                        persisted_track_hits += 1
                        # Track exists in persistence
                        link_info = persisted_tracks[spotify_id]
                        details = link_info.get("tidal_track_details")
                        score = link_info.get("score")
                        candidates = link_info.get("candidates") or []
                        link_status = link_info.get("link_status")

                        if details:
                            # Only unresolved multi-candidate rows block queued download.
                            # Single-candidate rows are already auto-selected for review and remain downloadable.
                            requires_manual_review = (
                                score is not None
                                and isinstance(score, (int, float))
                                and score > 1
                                and len(candidates) > 1
                                and link_status not in {"manual_linked", "candidate_confirmed"}
                            )
                            if requires_manual_review:
                                skipped_manual_review += 1
                                logger.info(
                                    f"Skipping track {spotify_id}: Manual review required (Score: {score}, Candidates: {len(candidates)})."
                                )
                                continue

                            # It has valid link details and is confirmed/confident -> Add to download
                            track_obj = self._download_item_from_persisted_link(
                                str(playlist_id),
                                str(spotify_id),
                                link_info,
                            )
                            if track_obj:
                                linked_tracks_for_download.append(track_obj)
                            else:
                                if details:
                                    skipped_deserialize_error += 1
                                skipped_missing_track_id += 1
                                logger.warning(
                                    "Skipping track %s: persisted link did not contain a valid Tidal track ID/object.",
                                    spotify_id,
                                )
                        else:
                            # Retry stale false negatives. Improved matching logic can repair
                            # previous Not Found rows during explicit playlist download actions.
                            if link_status == "not_found":
                                unlinked_tracks_for_worker.append((i, meta))
                                logger.info(
                                    "Retrying previously not_found Spotify track during queued download | playlist_id=%s spotify_id=%s",
                                    playlist_id,
                                    spotify_id,
                                )
                            else:
                                # It exists but has NO details and is not a retryable Not Found row.
                                skipped_not_found_or_none_match += 1
                                logger.debug(
                                    f"Skipping track {spotify_id} (Marked as unresolved/None Match in persistence)."
                                )
                    else:
                        # Not in persistence at all -> Needs linking
                        unlinked_tracks_for_worker.append((i, meta))

                logger.info(
                    "Spotify pre-download summary | playlist_id=%s total=%d persisted=%d linked_ready=%d unlinked=%d skipped_manual_review=%d skipped_not_found=%d skipped_deserialize_error=%d skipped_missing_track_id=%d",
                    playlist_id,
                    len(all_tracks_meta),
                    persisted_track_hits,
                    len(linked_tracks_for_download),
                    len(unlinked_tracks_for_worker),
                    skipped_manual_review,
                    skipped_not_found_or_none_match,
                    skipped_deserialize_error,
                    skipped_missing_track_id,
                )
                logger.info(
                    "Spotify queued download preparation checkpoint | playlist_id=%s elapsed_ms=%.1f",
                    playlist_id,
                    (time.perf_counter() - pre_download_start) * 1000,
                )
                
                if unlinked_tracks_for_worker:
                    logger.info(f"Found {len(unlinked_tracks_for_worker)} unlinked tracks in playlist. Linking them first...")
                    # Emit a separate jobStarted for the linking sub-task
                    QtCore.QMetaObject.invokeMethod(self, "jobStarted", Qt.ConnectionType.QueuedConnection,
                                                    QtCore.Q_ARG(str, playlist_id),
                                                    QtCore.Q_ARG(str, "Linking"),
                                                    QtCore.Q_ARG(int, len(unlinked_tracks_for_worker)))
                    
                    def post_link_download_thread():
                        busy_reason = f"Preparing linked missing downloads: {playlist_name}"
                        if non_completed_only:
                            self._begin_main_busy(busy_reason)

                        post_link_start = time.perf_counter()
                        try:
                            logger.info("Pre-download linking finished. Gathering all linked tracks for download.")
                            all_linked_tracks: List[Any] = []
                            newly_linked_links = self.main_view.link_persistence_manager.get_links_for_playlist(
                                playlist_id
                            )
                            newly_linked_tracks = newly_linked_links.get("tracks", {})

                            for _, meta in unlinked_tracks_for_worker:
                                spotify_track_id = meta.get("id")
                                if not spotify_track_id:
                                    continue

                                saved_link = newly_linked_tracks.get(spotify_track_id)
                                if not saved_link:
                                    continue

                                tidal_details = saved_link.get("tidal_track_details")
                                if not tidal_details:
                                    continue

                                try:
                                    track_obj = self._download_item_from_persisted_link(
                                        str(playlist_id),
                                        str(spotify_track_id),
                                        saved_link,
                                    )
                                    if isinstance(track_obj, DownloadItem):
                                        all_linked_tracks.append(cast(Any, track_obj))
                                except Exception as exc:
                                    logger.warning(
                                        "Failed to deserialize newly linked track for download | "
                                        "playlist_id=%s spotify_track_id=%s error=%s",
                                        playlist_id,
                                        spotify_track_id,
                                        exc,
                                    )

                            combined_download_list = linked_tracks_for_download + all_linked_tracks

                            filter_start = time.perf_counter()
                            final_download_list = combined_download_list
                            if non_completed_only:
                                final_download_list = self.main_view.table_handler.filter_non_completed_tracks(
                                    combined_download_list,
                                    playlist_data,
                                    allow_slow_fallback=False,
                                    reason="queued_spotify_post_link_non_completed",
                                )
                            filter_elapsed_ms = (time.perf_counter() - filter_start) * 1000.0

                            logger.info(
                                "Spotify post-link non-completed filter applied | playlist_id=%s before=%d after=%d "
                                "removed=%d elapsed_ms=%.1f",
                                playlist_id,
                                len(combined_download_list),
                                len(final_download_list),
                                len(combined_download_list) - len(final_download_list),
                                filter_elapsed_ms,
                            )

                            if not final_download_list:
                                no_download_reason = (
                                    "All linked tracks are already downloaded on disk."
                                    if combined_download_list
                                    else "No valid linked tracks were available to download."
                                )
                                QtCore.QMetaObject.invokeMethod(
                                    self,
                                    "_finish_job_no_download_needed",
                                    Qt.ConnectionType.QueuedConnection,
                                    QtCore.Q_ARG(str, str(playlist_id)),
                                    QtCore.Q_ARG(str, playlist_name),
                                    QtCore.Q_ARG(str, no_download_reason),
                                )
                                return

                            if non_completed_only:
                                QtCore.QMetaObject.invokeMethod(
                                    self,
                                    "_emit_download_queued_progress",
                                    Qt.ConnectionType.QueuedConnection,
                                    QtCore.Q_ARG(str, str(playlist_id)),
                                    QtCore.Q_ARG(int, len(final_download_list)),
                                )
                            self._start_download(final_download_list, playlist_data, quality)

                            elapsed_ms = (time.perf_counter() - post_link_start) * 1000.0
                            if elapsed_ms > SLOW_QUEUE_PHASE_MS:
                                logger.warning(
                                    "Spotify post-link download preparation was slow | playlist_id=%s "
                                    "tracks=%d elapsed_ms=%.1f",
                                    playlist_id,
                                    len(final_download_list),
                                    elapsed_ms,
                                )

                        except Exception:
                            logger.error(
                                "Spotify post-link download preparation failed | playlist_id=%s",
                                playlist_id,
                                exc_info=True,
                            )
                            QtCore.QMetaObject.invokeMethod(
                                self,
                                "job_finished",
                                Qt.ConnectionType.QueuedConnection,
                            )
                        finally:
                            if non_completed_only:
                                self._end_main_busy(busy_reason)

                    @pyqtSlot()
                    def on_linking_done():
                        if self.main_view.linking_worker:
                            try:
                                self.main_view.linking_worker.allTasksFinished.disconnect(on_linking_done)
                            except TypeError:
                                pass

                        self.jobFinished.emit(str(playlist_id))
                        threading.Thread(target=post_link_download_thread, daemon=True).start()

                    # Use invokeMethod to call startLinkingWorker on the main thread
                    QtCore.QMetaObject.invokeMethod(
                        self.main_view, 
                        "startLinkingWorker", 
                        Qt.ConnectionType.QueuedConnection,
                        QtCore.Q_ARG(list, unlinked_tracks_for_worker),
                        QtCore.Q_ARG(str, playlist_id),
                        QtCore.Q_ARG(object, on_linking_done)
                    )

                else:
                    logger.info("All tracks are already linked (or skipped). Starting download.")
                    before_non_completed_filter = len(linked_tracks_for_download)
                    if non_completed_only:
                        filter_start = time.perf_counter()
                        linked_tracks_for_download = self.main_view.table_handler.filter_non_completed_tracks(
                            linked_tracks_for_download,
                            playlist_data,
                            allow_slow_fallback=False,
                            reason="queued_spotify_pre_download_non_completed",
                        )
                        filter_elapsed_ms = (time.perf_counter() - filter_start) * 1000.0
                        logger.info(
                            "Spotify non-completed filter applied | playlist_id=%s before=%d after=%d "
                            "removed=%d elapsed_ms=%.1f",
                            playlist_id,
                            before_non_completed_filter,
                            len(linked_tracks_for_download),
                            before_non_completed_filter - len(linked_tracks_for_download),
                            filter_elapsed_ms,
                        )

                    if not linked_tracks_for_download:
                        no_download_reason = (
                            "All linked tracks are already downloaded on disk."
                            if before_non_completed_filter > 0
                            else "No valid linked tracks were available to download."
                        )
                        QtCore.QMetaObject.invokeMethod(
                            self,
                            "_finish_job_no_download_needed",
                            Qt.ConnectionType.QueuedConnection,
                            QtCore.Q_ARG(str, str(playlist_id)),
                            QtCore.Q_ARG(str, playlist_name),
                            QtCore.Q_ARG(str, no_download_reason),
                        )
                        return

                    if non_completed_only:
                        QtCore.QMetaObject.invokeMethod(
                            self,
                            "_emit_download_queued_progress",
                            Qt.ConnectionType.QueuedConnection,
                            QtCore.Q_ARG(str, str(playlist_id)),
                            QtCore.Q_ARG(int, len(linked_tracks_for_download)),
                        )

                    self._start_download(linked_tracks_for_download, playlist_data, quality)
            finally:
                if non_completed_only:
                    self._end_main_busy(busy_reason)

        threading.Thread(target=pre_download_thread, daemon=True).start()


    def _execute_tidal_download_job(self, job: Dict[str, Any]):
        """Handles the logic for a Tidal download job."""
        playlist_obj = job.get("playlist_obj")
        quality = job.get("quality")
        non_completed_only = bool(job.get("non_completed_only", False))
        playlist_id = getattr(playlist_obj, 'uuid', None)
        playlist_name = str(getattr(playlist_obj, "title", "Tidal playlist"))

        if not playlist_obj or not playlist_id:
            logger.error("Cannot download Tidal playlist: Invalid playlist object.")
            self.job_finished()
            return

        def fetch_tracks_thread():
            busy_reason = f"Preparing missing downloads: {playlist_name}"
            if non_completed_only:
                self._begin_main_busy(busy_reason)

            fetch_start = time.perf_counter()
            try:
                tracks, _ = TIDAL_API.getItems(str(playlist_id), Type.Playlist)
                fetch_elapsed_ms = (time.perf_counter() - fetch_start) * 1000.0
                if fetch_elapsed_ms > SLOW_QUEUE_PHASE_MS:
                    logger.warning(
                        "TIDAL playlist item fetch was slow | playlist_id=%s track_count=%d elapsed_ms=%.1f",
                        playlist_id,
                        len(tracks or []),
                        fetch_elapsed_ms,
                    )
                if non_completed_only and tracks:
                    before_count = len(tracks)
                    filter_start = time.perf_counter()
                    tracks = self.main_view.table_handler.filter_non_completed_tracks(
                        tracks,
                        cast(Optional[Playlist], playlist_obj),
                        allow_slow_fallback=False,
                        reason="queued_tidal_pre_download_non_completed",
                    )
                    filter_elapsed_ms = (time.perf_counter() - filter_start) * 1000.0
                    logger.info(
                        "TIDAL non-completed filter applied | playlist_id=%s before=%d after=%d "
                        "removed=%d elapsed_ms=%.1f",
                        playlist_id,
                        before_count,
                        len(tracks),
                        before_count - len(tracks),
                        filter_elapsed_ms,
                    )

                if non_completed_only and not tracks:
                    QtCore.QMetaObject.invokeMethod(
                        self,
                        "_finish_job_no_download_needed",
                        Qt.ConnectionType.QueuedConnection,
                        QtCore.Q_ARG(str, str(playlist_id)),
                        QtCore.Q_ARG(str, playlist_name),
                        QtCore.Q_ARG(str, "All tracks are already downloaded on disk."),
                    )
                    return

                # Emit jobStarted signal
                QtCore.QMetaObject.invokeMethod(self, "jobStarted", Qt.ConnectionType.QueuedConnection,
                                                QtCore.Q_ARG(str, str(playlist_id)),
                                                QtCore.Q_ARG(str, "Downloading"),
                                                QtCore.Q_ARG(int, len(tracks) if tracks else 0))

                if non_completed_only:
                    QtCore.QMetaObject.invokeMethod(
                        self,
                        "_emit_download_queued_progress",
                        Qt.ConnectionType.QueuedConnection,
                        QtCore.Q_ARG(str, str(playlist_id)),
                        QtCore.Q_ARG(int, len(tracks) if tracks else 0),
                    )

                self._start_download(tracks or [], playlist_obj, quality)
            finally:
                if non_completed_only:
                    self._end_main_busy(busy_reason)

        threading.Thread(target=fetch_tracks_thread, daemon=True).start()

    def _start_download(self, tracks: List[Any], playlist_context: Union[Playlist, Dict[str, Any]], quality: Optional[AudioQuality]):
        """Starts the DownloadWorker with the prepared list of tracks."""
        if not tracks:
            logger.warning("No tracks to download.")
            self.job_finished()
            return

        quality_str = Printf.map_quality(quality) if quality else None

        # The downloadEnd slot now calls job_finished, so we don't connect here.
        # Use invokeMethod to ensure thread safety when calling GUI methods from background thread
        QtCore.QMetaObject.invokeMethod(
            self.main_view.download_handler,
            "_start_download_thread",
            Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(object, tracks),
            QtCore.Q_ARG(object, playlist_context),
            QtCore.Q_ARG(object, quality_str)
        )
