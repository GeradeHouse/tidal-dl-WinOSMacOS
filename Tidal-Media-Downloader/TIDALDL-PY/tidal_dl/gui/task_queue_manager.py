# tidal_dl/gui/task_queue_manager.py

import logging
import threading
from collections import deque
from typing import TYPE_CHECKING, List, Dict, Any, Optional, Union, cast

from PyQt6 import QtCore
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, Qt

# Local imports
from tidal_dl.tidal import Playlist, AudioQuality, Track, TIDAL_API, Type
from tidal_dl.printf import Printf
import aigpy

if TYPE_CHECKING:
    from tidal_dl.gui.gui import MainView

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (task operations need visibility)
from tidal_dl.gui.gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)

class TaskQueueManager(QObject):
    """Manages a queue of linking and downloading jobs to run them sequentially."""
    # MODIFIED: Add signals
    jobStarted = pyqtSignal(str, str, int)  # playlist_id, action_type, total_items
    jobFinished = pyqtSignal(str)           # playlist_id

    def __init__(self, main_view: "MainView"):
        super().__init__(main_view)
        self.main_view = main_view
        self.task_queue = deque()
        self.is_running_task = False
        self.current_job: Optional[Dict[str, Any]] = None # MODIFIED: Add current_job tracking

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

    def add_spotify_download_job(self, spotify_playlists_data: List[Dict[str, Any]], quality: AudioQuality):
        """Adds a job to download all tracks in the selected Spotify playlists."""
        if not spotify_playlists_data:
            return

        for p_data in spotify_playlists_data:
            job = {
                "type": "download_spotify",
                "playlist_data": p_data,
                "quality": quality,
                "description": f"Download Spotify playlist: {p_data.get('data', {}).get('name', 'Unknown')}"
            }
            self.task_queue.append(job)
            logger.info(f"Queued job: {job['description']}")

        self.process_next_job()

    def add_tidal_download_job(self, tidal_playlists: List[Playlist], quality: AudioQuality):
        """Adds a job to download all tracks in the selected Tidal playlists."""
        if not tidal_playlists:
            return

        for playlist in tidal_playlists:
            job = {
                "type": "download_tidal",
                "playlist_obj": playlist,
                "quality": quality,
                "description": f"Download Tidal playlist: {getattr(playlist, 'title', 'Unknown')}"
            }
            self.task_queue.append(job)
            logger.info(f"Queued job: {job['description']}")

        self.process_next_job()

    def process_next_job(self):
        """Processes the next job in the queue if no other task is running."""
        if self.is_running_task or not self.task_queue:
            return

        self.is_running_task = True
        self.current_job = self.task_queue.popleft() # MODIFIED: Store current job
        
        # MODIFIED: Add check to satisfy Pylance
        if not self.current_job:
            self.is_running_task = False
            return

        job = self.current_job
        logger.info(f"Starting job: {job['description']}")

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
        logger.info("Job finished.")
        # MODIFIED: Emit jobFinished signal with playlist ID
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
        self.process_next_job()

    def stop_all_tasks(self):
        """Clears the queue and stops any active worker."""
        self.task_queue.clear()
        if self.main_view.linking_active:
            self.main_view.linking_gui_handler.onStopLinkingClicked()
        if self.main_view.download_active:
            self.main_view.download_handler.onStopClicked()
        logger.info("All queued tasks have been cleared.")


    def _execute_spotify_link_job(self, job: Dict[str, Any]):
        """Handles the logic for a Spotify linking job."""
        playlist_data = job.get("playlist_data", {})
        playlist_id = playlist_data.get("data", {}).get("id")

        if not playlist_id:
            logger.error("Cannot link playlist: Missing ID.")
            self.job_finished()
            return

        # DEBUG: Track playlist data
        logger.debug(f"🔴🔴🔴 TASK_QUEUE: Starting link job for playlist {playlist_id}")
        logger.debug(f"🔴🔴🔴 TASK_QUEUE: Playlist data: {playlist_data}")

        # Fetch tracks in a separate thread
        def fetch_tracks_thread():
            logger.debug(f"🔴🔴🔴 TASK_QUEUE: Fetching tracks for playlist {playlist_id}")
            tracks = self.main_view.spotify_api.get_playlist_tracks(playlist_id)
            logger.debug(f"🔴🔴🔴 TASK_QUEUE: Fetched tracks: {len(tracks) if tracks else 0} tracks")
            if tracks:
                logger.debug(f"🔴🔴🔴 TASK_QUEUE: First track sample: {tracks[0] if tracks else 'None'}")
            else:
                logger.warning(f"🔴🔴🔴 TASK_QUEUE: No tracks found for playlist {playlist_id}")
            
            # MODIFIED: Emit jobStarted signal from the main thread
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
        quality = job.get("quality")

        if not playlist_id:
            logger.error("Cannot download playlist: Missing ID.")
            self.job_finished()
            return

        def pre_download_thread():
            all_tracks_meta = self.main_view.spotify_api.get_playlist_tracks(playlist_id)
            # MODIFIED: Emit jobStarted for the download action
            QtCore.QMetaObject.invokeMethod(self, "jobStarted", Qt.ConnectionType.QueuedConnection,
                                            QtCore.Q_ARG(str, playlist_id),
                                            QtCore.Q_ARG(str, "Downloading"),
                                            QtCore.Q_ARG(int, len(all_tracks_meta) if all_tracks_meta else 0))
            
            if not all_tracks_meta:
                logger.warning(f"No tracks found for Spotify playlist {playlist_id}. Skipping download.")
                QtCore.QMetaObject.invokeMethod(self, "job_finished", Qt.ConnectionType.QueuedConnection)
                return

            unlinked_tracks_for_worker = []
            linked_tracks_for_download = []
            persisted_links = self.main_view.link_persistence_manager.get_links_for_playlist(playlist_id)
            persisted_tracks = persisted_links.get("tracks", {})

            for i, meta in enumerate(all_tracks_meta):
                spotify_id = meta.get("id")
                if spotify_id in persisted_tracks and persisted_tracks[spotify_id].get("tidal_track_details"):
                    track_dict = persisted_tracks[spotify_id]["tidal_track_details"]
                    try:
                        track_obj = aigpy.model.dictToModel(track_dict, Track())
                        if track_obj:
                            linked_tracks_for_download.append(track_obj)
                    except Exception as e:
                        logger.error(f"Failed to deserialize linked track {spotify_id}: {e}")
                else:
                    unlinked_tracks_for_worker.append((i, meta))
            
            if unlinked_tracks_for_worker:
                logger.info(f"Found {len(unlinked_tracks_for_worker)} unlinked tracks in playlist. Linking them first...")
                # MODIFIED: Emit a separate jobStarted for the linking sub-task
                QtCore.QMetaObject.invokeMethod(self, "jobStarted", Qt.ConnectionType.QueuedConnection,
                                                QtCore.Q_ARG(str, playlist_id),
                                                QtCore.Q_ARG(str, "Linking"),
                                                QtCore.Q_ARG(int, len(unlinked_tracks_for_worker)))
                
                @pyqtSlot()
                def on_linking_done():
                    if self.main_view.linking_worker:
                        try:
                            self.main_view.linking_worker.allTasksFinished.disconnect(on_linking_done)
                        except TypeError:
                            pass
                    
                    # MODIFIED: Emit jobFinished for the linking sub-task
                    self.jobFinished.emit(str(playlist_id))
                    
                    logger.info("Pre-download linking finished. Gathering all linked tracks for download.")
                    final_download_list = list(linked_tracks_for_download)
                    newly_linked_links = self.main_view.link_persistence_manager.get_links_for_playlist(playlist_id)
                    newly_linked_tracks = newly_linked_links.get("tracks", {})

                    for _, meta in unlinked_tracks_for_worker:
                        spotify_id = meta.get("id")
                        if spotify_id in newly_linked_tracks and newly_linked_tracks[spotify_id].get("tidal_track_details"):
                            track_dict = newly_linked_tracks[spotify_id]["tidal_track_details"]
                            try:
                                track_obj = aigpy.model.dictToModel(track_dict, Track())
                                if track_obj:
                                    final_download_list.append(track_obj)
                            except Exception as e:
                                logger.error(f"Failed to deserialize newly linked track {spotify_id}: {e}")
                    
                    self._start_download(final_download_list, playlist_data, quality)

                self.main_view.startLinkingWorker(unlinked_tracks_for_worker, playlist_id, on_linking_done)

            else:
                logger.info("All tracks are already linked. Starting download.")
                self._start_download(linked_tracks_for_download, playlist_data, quality)

        threading.Thread(target=pre_download_thread, daemon=True).start()


    def _execute_tidal_download_job(self, job: Dict[str, Any]):
        """Handles the logic for a Tidal download job."""
        playlist_obj = job.get("playlist_obj")
        quality = job.get("quality")
        playlist_id = getattr(playlist_obj, 'uuid', None)

        if not playlist_obj or not playlist_id:
            logger.error("Cannot download Tidal playlist: Invalid playlist object.")
            self.job_finished()
            return

        def fetch_tracks_thread():
            tracks, _ = TIDAL_API.getItems(str(playlist_id), Type.Playlist)
            # MODIFIED: Emit jobStarted signal
            QtCore.QMetaObject.invokeMethod(self, "jobStarted", Qt.ConnectionType.QueuedConnection,
                                            QtCore.Q_ARG(str, str(playlist_id)),
                                            QtCore.Q_ARG(str, "Downloading"),
                                            QtCore.Q_ARG(int, len(tracks) if tracks else 0))
            self._start_download(tracks or [], playlist_obj, quality)

        threading.Thread(target=fetch_tracks_thread, daemon=True).start()

    def _start_download(self, tracks: List[Track], playlist_context: Union[Playlist, Dict[str, Any]], quality: Optional[AudioQuality]):
        """Starts the DownloadWorker with the prepared list of tracks."""
        if not tracks:
            logger.warning("No tracks to download.")
            self.job_finished()
            return

        quality_str = Printf.map_quality(quality) if quality else None

        # The downloadEnd slot now calls job_finished, so we don't connect here.
        self.main_view.download_handler._start_download_thread(tracks, playlist_context, quality_str)