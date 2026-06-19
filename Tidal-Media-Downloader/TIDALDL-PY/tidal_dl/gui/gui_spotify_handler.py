import contextlib
import datetime
import difflib
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Optional, List, Dict, Sequence, Tuple, Union

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, Qt
from PyQt6.QtGui import QPixmap, QIcon, QFont
from PyQt6.QtWidgets import QTreeWidgetItem

from ..spotify import SpotifyAPI
from ..printf import Printf
from ..tidal import Type, Track, TIDAL_API
from .gui_utils import format_duration_ms
from .gui_custom_dialog import CustomQMessageBox
from ..persistence import LinkPersistenceManager
from .. import paths
from ..linking import normalize_title, fuzzy_normalize, _titles_are_review_compatible

if TYPE_CHECKING:
    from .gui_main import MainView

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # Set specific level for this module

# Set up GUI logging with INFO level for this module (Spotify operations need visibility)
from .gui_logging import setup_gui_logger
setup_gui_logger(__name__, logging.INFO)


class SpotifyGuiHandler(QObject):
    dropAddPreflightFinished = pyqtSignal(dict)
    dropAddPreflightError = pyqtSignal(str)

    def __init__(self, main_view: "MainView", spotify_api: SpotifyAPI):
        super().__init__()
        self.main_view = main_view
        self.spotify_api = spotify_api
        self._tracks_request_lock = threading.Lock()
        self._latest_tracks_request_token = 0
        self._latest_tracks_playlist_id: Optional[str] = None

        self._mutation_lock = threading.Lock()
        self._mutation_active = False
        self._drop_preflight_active = False
        self.dropAddPreflightFinished.connect(self._on_drop_add_preflight_finished)
        self.dropAddPreflightError.connect(self._on_drop_add_preflight_error)

    def _spotify_dialog_stylesheet(self) -> str:
        return """
            QDialog { background-color: #202024; color: #f2f2f2; }
            QLabel { color: #e8e8e8; background: transparent; }
            QLineEdit, QPlainTextEdit, QListWidget, QComboBox {
                background-color: #16161a; color: #f5f5f5;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 8px; padding: 8px;
                selection-background-color: #00a8a8;
            }
            QListWidget::item { padding: 10px; border-radius: 8px; }
            QListWidget::item:selected { background-color: rgba(0, 200, 200, 0.22); border: 1px solid rgba(0, 200, 200, 0.36); }
            QPushButton { background-color: rgba(255, 255, 255, 0.08); color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 8px; padding: 8px 14px; font-weight: 600; }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.14); }
            QPushButton:pressed { background-color: rgba(255, 255, 255, 0.18); }
            QPushButton:disabled { color: #777777; background-color: rgba(255, 255, 255, 0.04); }
        """

    def _show_spotify_status(self, message: str, tone: str = "info", timeout_ms: int = 4200) -> None:
        label = getattr(self.main_view, "spotifyActionStatusLabel", None)
        if label is None:
            return
        palette = {
            "info": ("rgba(0, 200, 200, 0.12)", "rgba(0, 200, 200, 0.28)", "#dffefe"),
            "success": ("rgba(37, 211, 102, 0.13)", "rgba(37, 211, 102, 0.32)", "#e8fff0"),
            "warning": ("rgba(255, 190, 80, 0.13)", "rgba(255, 190, 80, 0.32)", "#fff5df"),
            "error": ("rgba(255, 80, 80, 0.13)", "rgba(255, 80, 80, 0.36)", "#ffe8e8"),
        }
        background, border, color = palette.get(tone, palette["info"])
        label.setText(message)
        label.setStyleSheet(f"""QLabel {{ background-color: {background}; color: {color}; border: 1px solid {border}; border-radius: 10px; padding: 6px 12px; font-weight: 600; }}""")
        label.setVisible(True)
        label.raise_()
        if timeout_ms > 0:
            QtCore.QTimer.singleShot(timeout_ms, lambda: label.setVisible(False) if label.text() == message else None)

    def _prompt_text(self, title: str, label: str, default_text: str = "") -> Optional[str]:
        dialog = QtWidgets.QDialog(self.main_view)
        dialog.setWindowTitle(title)
        dialog.setStyleSheet(self._spotify_dialog_stylesheet())
        dialog.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        prompt = QtWidgets.QLabel(label)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        text_edit = QtWidgets.QLineEdit(default_text)
        text_edit.selectAll()
        layout.addWidget(text_edit)
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        value = text_edit.text().strip()
        return value or None

    def _prompt_choice(self, title: str, label: str, choices: Sequence[Tuple[str, str]]) -> Optional[str]:
        dialog = QtWidgets.QDialog(self.main_view)
        dialog.setWindowTitle(title)
        dialog.setStyleSheet(self._spotify_dialog_stylesheet())
        dialog.setMinimumWidth(500)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        prompt = QtWidgets.QLabel(label)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        combo = QtWidgets.QComboBox()
        for display_text, value in choices:
            combo.addItem(display_text, value)
        layout.addWidget(combo)
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return str(combo.currentData() or "")

    def _set_mutation_controls_enabled(self, enabled: bool) -> None:
        for attr_name in ("c_btnDownload", "c_btnLinkTracks"):
            widget = getattr(self.main_view, attr_name, None)
            if widget is not None:
                try:
                    widget.setEnabled(enabled)
                except Exception:
                    pass

    def _active_spotify_playlist_data(self) -> Optional[Dict[str, Any]]:
        playlist_obj = getattr(self.main_view, "s_playlist_obj", None)
        if isinstance(playlist_obj, dict) and playlist_obj.get("type") == "spotify":
            data = playlist_obj.get("data")
            if isinstance(data, dict):
                return data
        return None

    def _active_spotify_playlist_id(self) -> Optional[str]:
        data = self._active_spotify_playlist_data()
        playlist_id = data.get("id") if data else None
        return str(playlist_id) if playlist_id else None

    def _active_spotify_snapshot_id(self) -> Optional[str]:
        data = self._active_spotify_playlist_data()
        snapshot_id = data.get("snapshot_id") if data else None
        return str(snapshot_id) if snapshot_id else None

    def _get_selected_spotify_track_payloads(self, rows: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
        table_handler = getattr(self.main_view, "table_handler", None)
        if not table_handler or not hasattr(table_handler, "get_spotify_track_payloads_for_rows"):
            return []
        if rows is None or isinstance(rows, bool):
            table_widget = getattr(self.main_view, "tableWidget", None)
            rows = table_widget.getSelectedRows() if table_widget else []
        return table_handler.get_spotify_track_payloads_for_rows(list(rows))

    def _get_selected_table_track_payloads(self, rows: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
        table_handler = getattr(self.main_view, "table_handler", None)
        if not table_handler or not hasattr(table_handler, "get_table_track_payloads_for_rows"):
            return []
        if rows is None or isinstance(rows, bool):
            table_widget = getattr(self.main_view, "tableWidget", None)
            rows = table_widget.getSelectedRows() if table_widget else []
        return table_handler.get_table_track_payloads_for_rows(list(rows))

    def _get_spotify_playlist_choices(self) -> List[Dict[str, Any]]:
        tree_handler = getattr(self.main_view, "tree_handler", None)
        root_item = getattr(tree_handler, "spotify_root_item", None) if tree_handler else None
        choices: List[Dict[str, Any]] = []
        if not root_item:
            return choices

        def visit(item: QTreeWidgetItem) -> None:
            for index in range(item.childCount()):
                child = item.child(index)
                data = child.data(0, Qt.ItemDataRole.UserRole) if child else None
                if isinstance(data, dict):
                    if data.get("is_folder"):
                        visit(child)
                        continue
                    if data.get("type") == "spotify" and isinstance(data.get("data"), dict):
                        choices.append(data["data"])
                visit(child)

        visit(root_item)
        return choices

    def _choose_spotify_playlist(self, title: str = "Select Spotify Playlist") -> Optional[Dict[str, Any]]:
        playlists = self._get_spotify_playlist_choices()
        if not playlists:
            CustomQMessageBox.warning(
                self.main_view,
                "Spotify Playlists",
                "No Spotify playlists are loaded.",
                "Refresh Spotify playlists and retry the action.",
            )
            return None

        dialog = QtWidgets.QDialog(self.main_view)
        dialog.setWindowTitle(title)
        dialog.setStyleSheet(self._spotify_dialog_stylesheet())
        dialog.setMinimumSize(560, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        header = QtWidgets.QLabel(title)
        header.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(header)
        filter_edit = QtWidgets.QLineEdit()
        filter_edit.setPlaceholderText("Filter playlists...")
        layout.addWidget(filter_edit)
        list_widget = QtWidgets.QListWidget()
        layout.addWidget(list_widget, 1)

        def populate(filter_text: str = "") -> None:
            list_widget.clear()
            needle = filter_text.strip().lower()
            for playlist in playlists:
                name = str(playlist.get("name") or "Unknown Playlist")
                if needle and needle not in name.lower():
                    continue
                item = QtWidgets.QListWidgetItem(f"{name}\n{playlist.get('owner_name') or playlist.get('owner') or 'Unknown owner'} · {playlist.get('tracks_total', 0)} track(s)")
                item.setData(Qt.ItemDataRole.UserRole, playlist)
                list_widget.addItem(item)

        populate()
        filter_edit.textChanged.connect(populate)
        list_widget.itemDoubleClicked.connect(lambda _item: dialog.accept())
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        item = list_widget.currentItem()
        if not item:
            return None
        playlist = item.data(Qt.ItemDataRole.UserRole)
        return playlist if isinstance(playlist, dict) else None

    def _run_spotify_mutation(self, label: str, operation: Callable[[], Dict[str, Any]], refresh_playlist_id: Optional[str] = None) -> None:
        with self._mutation_lock:
            if self._mutation_active:
                CustomQMessageBox.warning(self.main_view, "Spotify Action Running", "Another Spotify playlist action is already running.", "Wait for the current Spotify action to finish before starting another one.")
                return
            self._mutation_active = True
        self.main_view.s_spotifyMutationStarted.emit(label)

        def _mutation_thread() -> None:
            try:
                result = operation()
                if not isinstance(result, dict):
                    result = {"success": False, "message": f"{label} returned an invalid result.", "playlist_id": refresh_playlist_id, "snapshot_id": None, "playlist": None}
                if refresh_playlist_id and not result.get("playlist_id"):
                    result["playlist_id"] = refresh_playlist_id
                if refresh_playlist_id:
                    result["refresh_playlist_id"] = refresh_playlist_id
                self.main_view.s_spotifyMutationFinished.emit(result)
            except Exception as exc:
                logger.error("Spotify mutation failed: %s", exc, exc_info=True)
                self.main_view.s_spotifyMutationError.emit(str(exc))
            finally:
                with self._mutation_lock:
                    self._mutation_active = False

        threading.Thread(target=_mutation_thread, daemon=True).start()

    @pyqtSlot(str)
    def onSpotifyMutationStarted(self, label: str) -> None:
        self.main_view.spotify_mutation_active = True
        self._set_mutation_controls_enabled(False)
        logger.info("Spotify mutation started: %s", label)
        self._show_spotify_status(f"Spotify: {label}...", "info", timeout_ms=0)

    @pyqtSlot(dict)
    def onSpotifyMutationFinished(self, result: Dict[str, Any]) -> None:
        self.main_view.spotify_mutation_active = False
        self._set_mutation_controls_enabled(True)
        message = str(result.get("message") or "Spotify action finished.")
        self._show_spotify_status(f"Spotify: {message}", "success" if result.get("success") else "error")
        if result.get("success"):
            logger.info(message)
            CustomQMessageBox.information(self.main_view, "Spotify Action Complete", message)

            refresh_playlist_id = result.get("refresh_playlist_id") or result.get("playlist_id")
            tree_handler = getattr(self.main_view, "tree_handler", None)

            tracks_delta: Optional[int] = None
            tracks_total: Optional[int] = None
            if result.get("tracks_delta") is not None:
                with contextlib.suppress(TypeError, ValueError):
                    tracks_delta = int(result.get("tracks_delta"))
            if result.get("tracks_total") is not None:
                with contextlib.suppress(TypeError, ValueError):
                    tracks_total = int(result.get("tracks_total"))

            if tree_handler and refresh_playlist_id and (tracks_delta is not None or tracks_total is not None):
                tree_handler.adjust_spotify_playlist_track_count(
                    str(refresh_playlist_id),
                    delta=tracks_delta,
                    total=tracks_total,
                )

            self.refreshSpotifyPlaylists()
            if refresh_playlist_id and self._active_spotify_playlist_id() == str(refresh_playlist_id):
                self.fetchSpotifyTracks(str(refresh_playlist_id))
        else:
            logger.warning(message)
            CustomQMessageBox.warning(self.main_view, "Spotify Action Failed", message)

    @pyqtSlot(str)
    def onSpotifyMutationError(self, error_message: str) -> None:
        self.main_view.spotify_mutation_active = False
        self._set_mutation_controls_enabled(True)
        logger.error("Spotify mutation error: %s", error_message)
        self._show_spotify_status(f"Spotify error: {error_message}", "error")
        CustomQMessageBox.critical(self.main_view, "Spotify Action Error", "Spotify action failed.", error_message)

    def createSpotifyPlaylistFromPrompt(self) -> None:
        name = self._prompt_text("Create Spotify Playlist", "Enter a name for the new Spotify playlist:")
        if not name:
            return
        self._run_spotify_mutation("Create Spotify playlist", lambda: self.spotify_api.create_playlist(name=name, public=False, collaborative=False, description="Created from TIDAL-DL."))

    def renameSpotifyPlaylist(self, playlist_data: Dict[str, Any]) -> None:
        playlist_id = str(playlist_data.get("id") or "")
        current_name = str(playlist_data.get("name") or "")
        if not playlist_id:
            return
        name = self._prompt_text("Rename Spotify Playlist", "Enter the new Spotify playlist name:", current_name)
        if not name or name == current_name:
            return
        self._run_spotify_mutation("Rename Spotify playlist", lambda: self.spotify_api.update_playlist_details(playlist_id, name=name), refresh_playlist_id=playlist_id)

    def editSpotifyPlaylistDetails(self, playlist_data: Dict[str, Any]) -> None:
        playlist_id = str(playlist_data.get("id") or "")
        if not playlist_id:
            return
        dialog = QtWidgets.QDialog(self.main_view)
        dialog.setWindowTitle("Edit Spotify Playlist Details")
        layout = QtWidgets.QVBoxLayout(dialog)
        name_edit = QtWidgets.QLineEdit(str(playlist_data.get("name") or ""))
        description_edit = QtWidgets.QPlainTextEdit(str(playlist_data.get("description") or ""))
        public_checkbox = QtWidgets.QCheckBox("Public")
        public_checkbox.setChecked(bool(playlist_data.get("public")))
        collaborative_checkbox = QtWidgets.QCheckBox("Collaborative")
        collaborative_checkbox.setChecked(bool(playlist_data.get("collaborative")))
        for label, widget in (("Name:", name_edit), ("Description:", description_edit)):
            layout.addWidget(QtWidgets.QLabel(label))
            layout.addWidget(widget)
        layout.addWidget(public_checkbox)
        layout.addWidget(collaborative_checkbox)
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        if public_checkbox.isChecked() and collaborative_checkbox.isChecked():
            CustomQMessageBox.warning(self.main_view, "Invalid Spotify Playlist Details", "A Spotify playlist cannot be both public and collaborative.", "Disable either Public or Collaborative and retry the action.")
            return
        self._run_spotify_mutation("Edit Spotify playlist details", lambda: self.spotify_api.update_playlist_details(playlist_id, name=name_edit.text().strip(), public=public_checkbox.isChecked(), collaborative=collaborative_checkbox.isChecked(), description=description_edit.toPlainText().strip()), refresh_playlist_id=playlist_id)

    def duplicateSpotifyPlaylist(self, playlist_data: Dict[str, Any]) -> None:
        playlist_id = str(playlist_data.get("id") or "")
        playlist_name = str(playlist_data.get("name") or "Spotify Playlist")
        if not playlist_id:
            return
        def _operation() -> Dict[str, Any]:
            tracks = self.spotify_api.get_playlist_tracks(playlist_id) or []
            uris = [track.get("uri") for track in tracks if isinstance(track, dict) and track.get("uri")]
            create_result = self.spotify_api.create_playlist(name=f"Copy of {playlist_name}", public=False, collaborative=False, description=f"Duplicated from {playlist_name} in TIDAL-DL.")
            if not create_result.get("success"):
                return create_result
            new_playlist_id = create_result.get("playlist_id")
            if new_playlist_id and uris:
                add_result = self.spotify_api.add_items_to_playlist(new_playlist_id, uris)
                if not add_result.get("success"):
                    return add_result
            create_result["message"] = f"Duplicated Spotify playlist: {playlist_name}"
            return create_result
        self._run_spotify_mutation("Duplicate Spotify playlist", _operation)

    def clearSpotifyPlaylist(self, playlist_data: Dict[str, Any]) -> None:
        playlist_id = str(playlist_data.get("id") or "")
        playlist_name = str(playlist_data.get("name") or "Spotify Playlist")
        if playlist_id and CustomQMessageBox.question(self.main_view, "Clear Spotify Playlist", f"Remove all tracks from '{playlist_name}'?", "This will keep the playlist but remove its current tracks."):
            self._run_spotify_mutation("Clear Spotify playlist", lambda: self.spotify_api.replace_playlist_items(playlist_id, []), refresh_playlist_id=playlist_id)

    def unfollowSpotifyPlaylist(self, playlist_data: Dict[str, Any]) -> None:
        playlist_id = str(playlist_data.get("id") or "")
        playlist_name = str(playlist_data.get("name") or "Spotify Playlist")
        if playlist_id and CustomQMessageBox.question(self.main_view, "Remove Spotify Playlist", f"Remove '{playlist_name}' from the Spotify library?", "Spotify does not expose a delete endpoint. This action will unfollow the playlist for the connected account."):
            self._run_spotify_mutation("Remove Spotify playlist from library", lambda: self.spotify_api.unfollow_playlist(playlist_id))

    def addSelectedRowsToSpotifyPlaylist(self, playlist_data: Dict[str, Any], rows: Optional[Sequence[int]] = None) -> None:
        self.addTableRowsToSpotifyPlaylist(playlist_data, rows)

    def addTableRowsToSpotifyPlaylist(self, playlist_data: Dict[str, Any], rows: Optional[Sequence[int]] = None) -> None:
        playlist_id = str(playlist_data.get("id") or "")
        if not playlist_id:
            return
        if not playlist_data.get("can_modify_items"):
            CustomQMessageBox.warning(
                self.main_view,
                "Add Tracks to Spotify Playlist",
                "This Spotify playlist cannot be modified.",
                "Playlist ownership or collaborator access is required.",
            )
            return

        row_payloads = self._get_selected_table_track_payloads(rows)
        if not row_payloads:
            CustomQMessageBox.warning(
                self.main_view,
                "Add Tracks to Spotify Playlist",
                "No draggable table tracks were available.",
                "Select one or more TIDAL/search/Spotify track rows and drop them on a modifiable Spotify playlist.",
            )
            return

        with self._mutation_lock:
            if self._mutation_active or self._drop_preflight_active:
                CustomQMessageBox.warning(
                    self.main_view,
                    "Spotify Action Running",
                    "Another Spotify playlist action is already running.",
                    "Wait for the current Spotify action to finish before starting another one.",
                )
                return
            self._drop_preflight_active = True

        self.main_view.spotify_mutation_active = True
        self._set_mutation_controls_enabled(False)
        self._show_spotify_status("Spotify: preparing dropped tracks...", "info", timeout_ms=0)

        def _preflight_thread() -> None:
            try:
                result = self._prepare_drop_add_preflight(playlist_data, row_payloads)
                self.dropAddPreflightFinished.emit(result)
            except Exception as exc:
                logger.error("Spotify drop preflight failed: %s", exc, exc_info=True)
                self.dropAddPreflightError.emit(str(exc))

        threading.Thread(target=_preflight_thread, daemon=True).start()

    def _reset_drop_preflight_state(self) -> None:
        with self._mutation_lock:
            self._drop_preflight_active = False
        self.main_view.spotify_mutation_active = False
        self._set_mutation_controls_enabled(True)

    def _tidal_track_drop_metadata(self, track: Track) -> Dict[str, Any]:
        artists_raw = getattr(track, "artists", None)
        if not isinstance(artists_raw, list):
            artists_raw = [getattr(track, "artist", None)]

        try:
            artists_text = TIDAL_API.getArtistsName([artist for artist in artists_raw if artist is not None])
        except Exception:
            artists_text = ""

        artists = [part.strip() for part in artists_text.split(",") if part.strip()]
        album_obj = getattr(track, "album", None)
        duration_seconds = getattr(track, "duration", None)
        duration_ms = int(float(duration_seconds) * 1000) if duration_seconds else None

        return {
            "title": str(getattr(track, "title", "") or ""),
            "artists": artists,
            "album": str(getattr(album_obj, "title", "") or ""),
            "duration_ms": duration_ms,
            "isrc": getattr(track, "isrc", None),
            "track": track,
        }

    def _score_spotify_candidate_for_tidal(self, tidal_meta: Dict[str, Any], candidate: Dict[str, Any]) -> Tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []

        tidal_isrc = str(tidal_meta.get("isrc") or "").strip().upper()
        candidate_isrc = str(candidate.get("isrc") or "").strip().upper()
        if tidal_isrc and candidate_isrc and tidal_isrc == candidate_isrc:
            return 0, []

        title_ok, similarity_ratio, token_overlap = _titles_are_review_compatible(
            tidal_meta.get("title"),
            candidate.get("name"),
        )
        if not title_ok:
            score += 4
            reasons.append("Title mismatch")
        elif similarity_ratio is not None and similarity_ratio < 0.92:
            score += 1
            reasons.append("Title differs")

        tidal_artists = [fuzzy_normalize(artist) for artist in tidal_meta.get("artists", []) if artist]
        candidate_artists = [fuzzy_normalize(artist) for artist in candidate.get("artists", []) if artist]
        if tidal_artists and candidate_artists:
            artist_match = any(
                left == right or left in right or right in left
                for left in tidal_artists
                for right in candidate_artists
            )
            if not artist_match:
                best_ratio = max(
                    (
                        difflib.SequenceMatcher(None, left, right).ratio()
                        for left in tidal_artists
                        for right in candidate_artists
                    ),
                    default=0.0,
                )
                if best_ratio < 0.78:
                    score += 3
                    reasons.append("Artist mismatch")
                else:
                    score += 1
                    reasons.append("Artist differs")

        tidal_duration = tidal_meta.get("duration_ms")
        candidate_duration = candidate.get("duration_ms")
        if isinstance(tidal_duration, int) and isinstance(candidate_duration, int):
            duration_diff_ms = abs(tidal_duration - candidate_duration)
            if duration_diff_ms > 60000:
                score += 3
                reasons.append("Duration > 60s")
            elif duration_diff_ms > 30000:
                score += 2
                reasons.append("Duration > 30s")
            elif duration_diff_ms > 8000:
                score += 1
                reasons.append("Duration differs")

        tidal_album = fuzzy_normalize(str(tidal_meta.get("album") or ""))
        candidate_album = fuzzy_normalize(str(candidate.get("album") or ""))
        if tidal_album and candidate_album and tidal_album != candidate_album:
            score += 1
            reasons.append("Album differs")

        if token_overlap < 0.60:
            score += 1
            reasons.append("Low title token overlap")

        return score, reasons

    def _resolve_tidal_track_to_spotify_candidates(self, track: Track) -> Dict[str, Any]:
        tidal_meta = self._tidal_track_drop_metadata(track)
        title = str(tidal_meta.get("title") or "").strip()
        artists = tidal_meta.get("artists", [])
        primary_artist = artists[0] if artists else ""
        album = str(tidal_meta.get("album") or "").strip()

        queries = []
        if title and primary_artist:
            queries.append(f"{title} {primary_artist}")
        if title and album:
            queries.append(f"{title} {album}")
        if title:
            queries.append(title)

        seen_queries: set[str] = set()
        seen_ids: set[str] = set()
        scored_candidates: List[Dict[str, Any]] = []
        for query in queries:
            normalized_query = normalize_title(query).lower()
            if normalized_query in seen_queries:
                continue
            seen_queries.add(normalized_query)
            for candidate in self.spotify_api.search_tracks(query, limit=12):
                candidate_id = str(candidate.get("id") or candidate.get("uri") or "")
                if not candidate_id or candidate_id in seen_ids:
                    continue
                seen_ids.add(candidate_id)
                score, reasons = self._score_spotify_candidate_for_tidal(tidal_meta, candidate)
                if score <= 4:
                    scored_candidates.append(
                        {
                            "track": candidate,
                            "score": score,
                            "mismatch_reasons": reasons,
                        }
                    )

        scored_candidates.sort(key=lambda item: (int(item.get("score", 99)), str(item.get("track", {}).get("name", ""))))
        auto_candidate = scored_candidates[0] if scored_candidates and int(scored_candidates[0].get("score", 99)) <= 1 else None

        return {
            "source": tidal_meta,
            "auto_candidate": auto_candidate,
            "candidates": scored_candidates[:6],
        }

    def _prepare_drop_add_preflight(self, playlist_data: Dict[str, Any], row_payloads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        playlist_id = str(playlist_data.get("id") or "")
        existing_tracks = self.spotify_api.get_playlist_tracks(playlist_id) or []
        existing_uris = {
            str(track.get("uri") or "").strip()
            for track in existing_tracks
            if isinstance(track, dict) and str(track.get("uri") or "").strip()
        }

        resolved: List[Dict[str, Any]] = []
        needs_review: List[Dict[str, Any]] = []
        no_matches: List[Dict[str, Any]] = []

        for payload in row_payloads:
            source_type = payload.get("source_type")
            if source_type == "spotify":
                uri = str(payload.get("uri") or "").strip()
                if uri:
                    resolved.append(
                        {
                            "uri": uri,
                            "spotify_track": dict(payload),
                            "source_title": str(payload.get("display_title") or payload.get("name") or "Spotify track"),
                            "source_type": "spotify",
                        }
                    )
                continue

            tidal_track = payload.get("tidal_track")
            if isinstance(tidal_track, Track):
                resolution = self._resolve_tidal_track_to_spotify_candidates(tidal_track)
                auto_candidate = resolution.get("auto_candidate")
                if isinstance(auto_candidate, dict) and isinstance(auto_candidate.get("track"), dict):
                    spotify_track = auto_candidate["track"]
                    resolved.append(
                        {
                            "uri": str(spotify_track.get("uri") or ""),
                            "spotify_track": spotify_track,
                            "source_title": str(resolution.get("source", {}).get("title") or payload.get("display_title") or "TIDAL track"),
                            "source_type": "tidal",
                            "score": auto_candidate.get("score"),
                            "mismatch_reasons": auto_candidate.get("mismatch_reasons", []),
                        }
                    )
                elif resolution.get("candidates"):
                    needs_review.append(resolution)
                else:
                    no_matches.append(
                        {
                            "source_title": str(payload.get("display_title") or getattr(tidal_track, "title", "") or "TIDAL track"),
                            "source_type": "tidal",
                        }
                    )

        return {
            "playlist_data": dict(playlist_data),
            "resolved": resolved,
            "needs_review": needs_review,
            "no_matches": no_matches,
            "existing_uris": list(existing_uris),
        }

    def _candidate_display_text(self, candidate: Dict[str, Any]) -> str:
        track = candidate.get("track", {})
        artists = ", ".join(track.get("artists", []) or [])
        duration = format_duration_ms(track.get("duration_ms"))
        reasons = ", ".join(candidate.get("mismatch_reasons", []) or ["Close match"])
        return f"{track.get('name', 'Unknown Track')} — {artists} · {track.get('album', 'Unknown Album')} · {duration} · score {candidate.get('score')} · {reasons}"

    def _choose_spotify_candidates_for_drop(self, needs_review: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not needs_review:
            return []

        dialog = QtWidgets.QDialog(self.main_view)
        dialog.setWindowTitle("Review Spotify Matches")
        dialog.setStyleSheet(self._spotify_dialog_stylesheet())
        dialog.setMinimumSize(760, 560)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        header = QtWidgets.QLabel("Review Spotify matches before adding tracks")
        header.setStyleSheet("font-size: 16px; font-weight: 800;")
        layout.addWidget(header)

        intro = QtWidgets.QLabel("Some TIDAL/search rows need manual Spotify match selection. Rows left on “Skip this track” will not be added.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        combo_rows: List[Tuple[Dict[str, Any], QtWidgets.QComboBox]] = []
        for review_item in needs_review:
            source = review_item.get("source", {})
            source_title = str(source.get("title") or "TIDAL track")
            source_artists = ", ".join(source.get("artists", []) or [])
            source_label = QtWidgets.QLabel(f"{source_title} — {source_artists}")
            source_label.setStyleSheet("font-weight: 700; color: #ffffff;")
            content_layout.addWidget(source_label)

            combo = QtWidgets.QComboBox()
            combo.addItem("Skip this track", None)
            for candidate in review_item.get("candidates", []):
                combo.addItem(self._candidate_display_text(candidate), candidate)
            content_layout.addWidget(combo)
            combo_rows.append((review_item, combo))

        content_layout.addStretch()
        scroll_area.setWidget(content)
        layout.addWidget(scroll_area, 1)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return []

        selected: List[Dict[str, Any]] = []
        for review_item, combo in combo_rows:
            candidate = combo.currentData()
            if not isinstance(candidate, dict) or not isinstance(candidate.get("track"), dict):
                continue
            spotify_track = candidate["track"]
            selected.append(
                {
                    "uri": str(spotify_track.get("uri") or ""),
                    "spotify_track": spotify_track,
                    "source_title": str(review_item.get("source", {}).get("title") or "TIDAL track"),
                    "source_type": "tidal",
                    "score": candidate.get("score"),
                    "mismatch_reasons": candidate.get("mismatch_reasons", []),
                }
            )
        return selected

    def _confirm_drop_add_mode(self, playlist_name: str, resolved: Sequence[Dict[str, Any]], duplicate_count: int) -> Optional[str]:
        total_count = len(resolved)
        if total_count <= 0:
            return None

        if duplicate_count <= 0:
            if total_count == 1:
                track_name = str(resolved[0].get("source_title") or "Selected track")
                accepted = CustomQMessageBox.question(
                    self.main_view,
                    "Add Track to Spotify Playlist",
                    f"Add “{track_name}” to “{playlist_name}”?",
                    "This action modifies the connected Spotify playlist.",
                )
            else:
                accepted = CustomQMessageBox.question(
                    self.main_view,
                    "Add Tracks to Spotify Playlist",
                    f"Add {total_count} selected tracks to “{playlist_name}”?",
                    "This action modifies the connected Spotify playlist.",
                )
            return "append_all" if accepted else None

        dialog = QtWidgets.QDialog(self.main_view)
        dialog.setWindowTitle("Duplicates Found")
        dialog.setStyleSheet(self._spotify_dialog_stylesheet())
        dialog.setMinimumWidth(560)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Spotify playlist duplicates found")
        title.setStyleSheet("font-size: 16px; font-weight: 800;")
        layout.addWidget(title)

        message = QtWidgets.QLabel(
            f"{duplicate_count} of {total_count} selected track(s) already exist in “{playlist_name}”."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        hint = QtWidgets.QLabel("Choose whether duplicate occurrences should still be added, or add only tracks that are not already present.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #c8c8c8;")
        layout.addWidget(hint)

        choice: Dict[str, Optional[str]] = {"value": None}
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()

        add_duplicates_button = QtWidgets.QPushButton("Add duplicates")
        only_new_button = QtWidgets.QPushButton("Only new tracks")
        cancel_button = QtWidgets.QPushButton("Cancel")

        add_duplicates_button.clicked.connect(lambda: (choice.update({"value": "append_all"}), dialog.accept()))
        only_new_button.clicked.connect(lambda: (choice.update({"value": "new_only"}), dialog.accept()))
        cancel_button.clicked.connect(dialog.reject)

        buttons.addWidget(add_duplicates_button)
        buttons.addWidget(only_new_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return choice.get("value")

    @pyqtSlot(dict)
    def _on_drop_add_preflight_finished(self, result: Dict[str, Any]) -> None:
        self._reset_drop_preflight_state()

        playlist_data = result.get("playlist_data", {})
        playlist_id = str(playlist_data.get("id") or "")
        playlist_name = str(playlist_data.get("name") or "Spotify playlist")
        resolved = list(result.get("resolved", []) or [])
        no_matches = list(result.get("no_matches", []) or [])
        existing_uris = {str(uri).strip() for uri in result.get("existing_uris", []) if str(uri).strip()}

        reviewed = self._choose_spotify_candidates_for_drop(result.get("needs_review", []) or [])
        resolved.extend(reviewed)

        clean_resolved = [
            item for item in resolved
            if str(item.get("uri") or "").strip()
        ]

        if not clean_resolved:
            detail = ""
            if no_matches:
                detail = f"{len(no_matches)} TIDAL/search track(s) had no Spotify match."
            CustomQMessageBox.warning(
                self.main_view,
                "Add Tracks to Spotify Playlist",
                "No Spotify tracks were available to add.",
                detail,
            )
            self._show_spotify_status("Spotify: no tracks were added.", "warning")
            return

        duplicate_count = sum(1 for item in clean_resolved if str(item.get("uri") or "").strip() in existing_uris)
        mode = self._confirm_drop_add_mode(playlist_name, clean_resolved, duplicate_count)
        if not mode:
            self._show_spotify_status("Spotify: playlist add cancelled.", "warning")
            return

        if mode == "new_only":
            clean_resolved = [
                item for item in clean_resolved
                if str(item.get("uri") or "").strip() not in existing_uris
            ]

        if not clean_resolved:
            CustomQMessageBox.information(
                self.main_view,
                "Add Tracks to Spotify Playlist",
                "No new tracks to add.",
                "All selected tracks already exist in the target Spotify playlist.",
            )
            self._show_spotify_status("Spotify: all selected tracks already exist in the playlist.", "warning")
            return

        uris = [str(item.get("uri")).strip() for item in clean_resolved if str(item.get("uri") or "").strip()]
        skipped_count = len(no_matches) + max(0, len(result.get("needs_review", []) or []) - len(reviewed))

        def _operation() -> Dict[str, Any]:
            add_result = self.spotify_api.add_items_to_playlist(playlist_id, uris)
            if add_result.get("success"):
                suffix = f" Skipped {skipped_count} unresolved track(s)." if skipped_count else ""
                add_result["message"] = f"Added {len(uris)} track(s) to “{playlist_name}”.{suffix}"
            return add_result

        self._run_spotify_mutation(
            "Add dropped tracks to Spotify playlist",
            _operation,
            refresh_playlist_id=playlist_id,
        )

    @pyqtSlot(str)
    def _on_drop_add_preflight_error(self, error_message: str) -> None:
        self._reset_drop_preflight_state()
        self._show_spotify_status(f"Spotify error: {error_message}", "error")
        CustomQMessageBox.critical(
            self.main_view,
            "Spotify Drop Error",
            "Could not prepare dropped tracks.",
            error_message,
        )

    def addSelectedRowsToChosenSpotifyPlaylist(self, rows: Sequence[int]) -> None:
        playlist_data = self._choose_spotify_playlist("Add Selected Tracks to Spotify Playlist")
        if playlist_data:
            self.addSelectedRowsToSpotifyPlaylist(playlist_data, rows)

    def createSpotifyPlaylistFromSelectedRows(self, rows: Sequence[int]) -> None:
        payloads = self._get_selected_table_track_payloads(rows)
        spotify_payloads = [payload for payload in payloads if payload.get("source_type") == "spotify" and payload.get("uri")]
        if len(spotify_payloads) != len(payloads):
            CustomQMessageBox.warning(
                self.main_view,
                "Create Spotify Playlist",
                "Only already-linked Spotify rows can create a playlist directly.",
                "Use drag/drop onto an existing Spotify playlist for TIDAL/search rows so match review can run first.",
            )
            return

        uris = [str(payload.get("uri")).strip() for payload in spotify_payloads if str(payload.get("uri") or "").strip()]
        if not uris:
            CustomQMessageBox.warning(self.main_view, "Create Spotify Playlist", "No selected Spotify track URIs were available.")
            return
        name = self._prompt_text("Create Spotify Playlist from Selection", "Enter a name for the playlist created from selected rows:")
        if not name:
            return
        def _operation() -> Dict[str, Any]:
            create_result = self.spotify_api.create_playlist(name=name, public=False, collaborative=False, description="Created from selected TIDAL-DL rows.")
            if not create_result.get("success"):
                return create_result
            playlist_id = create_result.get("playlist_id")
            if playlist_id:
                add_result = self.spotify_api.add_items_to_playlist(playlist_id, uris)
                if not add_result.get("success"):
                    return add_result
            create_result["message"] = f"Created Spotify playlist from {len(uris)} selected track(s)."
            return create_result
        self._run_spotify_mutation("Create Spotify playlist from selected rows", _operation)

    def removeSelectedRowsFromCurrentSpotifyPlaylist(self, rows: Sequence[int]) -> None:
        playlist_id = self._active_spotify_playlist_id()
        snapshot_id = self._active_spotify_snapshot_id()
        if not playlist_id:
            CustomQMessageBox.warning(self.main_view, "Remove Spotify Tracks", "No active Spotify playlist is selected.")
            return
        remove_items = []
        for payload in self._get_selected_spotify_track_payloads(rows):
            uri = payload.get("uri")
            position = payload.get("playlist_position")
            if uri:
                remove_items.append({"uri": uri, "positions": [position]} if isinstance(position, int) else uri)
        if not remove_items:
            CustomQMessageBox.warning(self.main_view, "Remove Spotify Tracks", "No selected Spotify track URIs were available.")
            return
        if CustomQMessageBox.question(self.main_view, "Remove Spotify Tracks", f"Remove {len(remove_items)} selected track(s) from the active Spotify playlist?", "This action modifies the connected Spotify playlist."):
            self._run_spotify_mutation("Remove selected Spotify tracks", lambda: self.spotify_api.remove_items_from_playlist(playlist_id, remove_items, snapshot_id=snapshot_id), refresh_playlist_id=playlist_id)

    def moveSelectedRowsToTop(self, rows: Sequence[int]) -> None:
        self._moveSelectedRowsToPosition(rows, insert_before=0, label="Move selected Spotify tracks to top")

    def moveSelectedRowsToBottom(self, rows: Sequence[int]) -> None:
        payloads = self._get_selected_spotify_track_payloads(rows)
        active_playlist = self._active_spotify_playlist_data() or {}
        tracks_total = int(active_playlist.get("tracks_total") or len(payloads))
        self._moveSelectedRowsToPosition(rows, insert_before=tracks_total, label="Move selected Spotify tracks to bottom")

    def moveSelectedRowsToPlaylistPosition(self, rows: Sequence[int], insert_before: int) -> None:
        self._moveSelectedRowsToPosition(rows, insert_before=max(0, int(insert_before)), label="Drag/drop reorder Spotify tracks")

    def _moveSelectedRowsToPosition(self, rows: Sequence[int], insert_before: int, label: str) -> None:
        playlist_id = self._active_spotify_playlist_id()
        snapshot_id = self._active_spotify_snapshot_id()
        if not playlist_id:
            CustomQMessageBox.warning(self.main_view, "Reorder Spotify Tracks", "No active Spotify playlist is selected.")
            return
        positions = sorted(payload.get("playlist_position") for payload in self._get_selected_spotify_track_payloads(rows) if isinstance(payload.get("playlist_position"), int))
        if not positions:
            CustomQMessageBox.warning(self.main_view, "Reorder Spotify Tracks", "No selected Spotify playlist positions were available.")
            return
        if positions != list(range(positions[0], positions[0] + len(positions))):
            CustomQMessageBox.warning(self.main_view, "Reorder Spotify Tracks", "Only contiguous Spotify playlist row selections can be moved in one action.")
            return
        first_position = positions[0]
        selected_count = len(positions)
        if first_position <= insert_before <= first_position + selected_count:
            self._show_spotify_status("Spotify: selected rows already occupy that position.", "warning")
            return
        self._run_spotify_mutation(label, lambda: self.spotify_api.reorder_playlist_items(playlist_id, range_start=positions[0], insert_before=insert_before, range_length=len(positions), snapshot_id=snapshot_id), refresh_playlist_id=playlist_id)

    def _spotify_uri_list_from_tracks(self, tracks: Sequence[Dict[str, Any]]) -> List[str]:
        return [str(track.get("uri")).strip() for track in tracks if isinstance(track, dict) and str(track.get("uri") or "").strip()]

    def backupSpotifyPlaylist(self, playlist_data: Dict[str, Any]) -> None:
        playlist_id = str(playlist_data.get("id") or "")
        playlist_name = str(playlist_data.get("name") or "Spotify Playlist")
        if not playlist_id:
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_name = f"Backup - {playlist_name} - {timestamp}"

        def _operation() -> Dict[str, Any]:
            tracks = self.spotify_api.get_playlist_tracks(playlist_id) or []
            uris = self._spotify_uri_list_from_tracks(tracks)
            create_result = self.spotify_api.create_playlist(name=backup_name, public=False, collaborative=False, description=f"Backup created from {playlist_name} in TIDAL-DL.")
            if not create_result.get("success"):
                return create_result
            new_playlist_id = create_result.get("playlist_id")
            if new_playlist_id and uris:
                add_result = self.spotify_api.add_items_to_playlist(new_playlist_id, uris)
                if not add_result.get("success"):
                    return add_result
                create_result["snapshot_id"] = add_result.get("snapshot_id")
            create_result["message"] = f"Created Spotify backup playlist with {len(uris)} track(s)."
            return create_result

        self._run_spotify_mutation("Create Spotify playlist backup", _operation)

    def removeDuplicateTracksFromSpotifyPlaylist(self, playlist_data: Dict[str, Any]) -> None:
        playlist_id = str(playlist_data.get("id") or "")
        playlist_name = str(playlist_data.get("name") or "Spotify Playlist")
        if not playlist_id:
            return
        if not CustomQMessageBox.question(self.main_view, "Remove Duplicate Spotify Tracks", f"Remove duplicate track occurrences from '{playlist_name}'?", "The first occurrence of each Spotify URI will be kept. Later duplicate occurrences will be removed."):
            return

        def _operation() -> Dict[str, Any]:
            tracks = self.spotify_api.get_playlist_tracks(playlist_id) or []
            seen_uris: set[str] = set()
            duplicate_items: List[Dict[str, Any]] = []
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                uri = str(track.get("uri") or "").strip()
                position = track.get("playlist_position")
                if not uri or not isinstance(position, int):
                    continue
                if uri in seen_uris:
                    duplicate_items.append({"uri": uri, "positions": [position]})
                else:
                    seen_uris.add(uri)
            if not duplicate_items:
                return {"success": True, "message": "No duplicate Spotify track occurrences were found.", "playlist_id": playlist_id, "snapshot_id": playlist_data.get("snapshot_id"), "playlist": None}
            result = self.spotify_api.remove_items_from_playlist(playlist_id, duplicate_items, snapshot_id=playlist_data.get("snapshot_id"))
            if result.get("success"):
                result["message"] = f"Removed {len(duplicate_items)} duplicate Spotify track occurrence(s)."
            return result

        self._run_spotify_mutation("Remove duplicate Spotify tracks", _operation, refresh_playlist_id=playlist_id)

    def createReviewPlaylistFromSelectedRows(self, rows: Sequence[int]) -> None:
        payloads = self._get_selected_spotify_track_payloads(rows)
        uris = [str(payload.get("uri")).strip() for payload in payloads if str(payload.get("uri") or "").strip()]
        if not uris:
            CustomQMessageBox.warning(self.main_view, "Create Review Playlist", "No selected Spotify track URIs were available.")
            return
        active_playlist = self._active_spotify_playlist_data() or {}
        default_name = f"Review - {active_playlist.get('name') or 'Selected Tracks'}"
        name = self._prompt_text("Create Review Playlist", "Enter a name for the Spotify review playlist:", default_name)
        if not name:
            return

        def _operation() -> Dict[str, Any]:
            create_result = self.spotify_api.create_playlist(name=name, public=False, collaborative=False, description="Review playlist created from selected TIDAL-DL rows.")
            if not create_result.get("success"):
                return create_result
            playlist_id = create_result.get("playlist_id")
            if playlist_id:
                add_result = self.spotify_api.add_items_to_playlist(playlist_id, uris)
                if not add_result.get("success"):
                    return add_result
                create_result["snapshot_id"] = add_result.get("snapshot_id")
            create_result["message"] = f"Created review playlist with {len(uris)} selected track(s)."
            return create_result

        self._run_spotify_mutation("Create Spotify review playlist", _operation)

    def syncSelectedRowsToSpotifyPlaylist(self, rows: Sequence[int]) -> None:
        payloads = self._get_selected_spotify_track_payloads(rows)
        source_uris = [str(payload.get("uri")).strip() for payload in payloads if str(payload.get("uri") or "").strip()]
        if not source_uris:
            CustomQMessageBox.warning(self.main_view, "Sync Selected Tracks", "No selected Spotify track URIs were available.")
            return
        target_playlist = self._choose_spotify_playlist("Sync Selected Tracks to Spotify Playlist")
        if not target_playlist:
            return
        target_playlist_id = str(target_playlist.get("id") or "")
        if not target_playlist_id:
            return
        mode = self._prompt_choice("Sync Selected Tracks", "Choose how selected rows should be applied to the target Spotify playlist:", [("Add missing selected tracks only", "add_missing"), ("Append all selected tracks", "append_all"), ("Replace target playlist with selected tracks", "replace")])
        if not mode:
            return

        def _operation() -> Dict[str, Any]:
            if mode == "replace":
                return self.spotify_api.replace_playlist_items(target_playlist_id, source_uris)
            if mode == "append_all":
                return self.spotify_api.add_items_to_playlist(target_playlist_id, source_uris)
            target_tracks = self.spotify_api.get_playlist_tracks(target_playlist_id) or []
            existing_uris = {str(track.get("uri")).strip() for track in target_tracks if isinstance(track, dict) and str(track.get("uri") or "").strip()}
            missing_uris = [uri for uri in source_uris if uri not in existing_uris]
            if not missing_uris:
                return {"success": True, "message": "Selected Spotify tracks already exist in the target playlist.", "playlist_id": target_playlist_id, "snapshot_id": target_playlist.get("snapshot_id"), "playlist": None}
            result = self.spotify_api.add_items_to_playlist(target_playlist_id, missing_uris)
            if result.get("success"):
                result["message"] = f"Synced {len(missing_uris)} missing selected track(s) to Spotify."
            return result

        self._run_spotify_mutation("Sync selected Spotify tracks", _operation, refresh_playlist_id=target_playlist_id)

    def loginSpotify(self, check_cache_only: bool = False):
        from tidal_dl.settings import SETTINGS

        if not SETTINGS.autoSpotifyLogin and check_cache_only:
            logger.info("Spotify auto-login is disabled. Skipping silent check.")
            self.main_view.s_spotifyLoginFinished.emit(False)
            return

        logger.info("Spotify login initiated...")

        def _spotify_auth_thread(initial_check_cache_only: bool):
            auth_result: Union[bool, str] = False
            try:
                logger.info(
                    "Attempting silent Spotify authentication (cache/refresh)..."
                )
                auth_result = self.spotify_api.authenticate(check_cache_only=True)

                if (
                    not auth_result
                    or auth_result == "SPOTIFY_SCOPE_UPGRADE_REQUIRED"
                ) and not initial_check_cache_only:
                    Printf.warning(
                        "Spotify authorization must be refreshed. Browser login will open..."
                    )
                    auth_result = self.spotify_api.authenticate(check_cache_only=False)
            except Exception as e:
                auth_result = f"Error: {e}"
            finally:
                self.main_view.s_spotifyLoginFinished.emit(auth_result)

        thread = threading.Thread(
            target=_spotify_auth_thread, args=(check_cache_only,), daemon=True
        )
        thread.start()

    def onSpotifyLoginFinished(self, auth_result: Union[bool, str]):
        logger.debug(
            f"SpotifyGuiHandler.onSpotifyLoginFinished called with auth_result: {auth_result}"
        )

        if isinstance(auth_result, str) and "CREDENTIALS_MISSING" in auth_result.upper():
            was_user_triggered = getattr(
                self.main_view.auth_handler,
                "_last_spotify_trigger_was_interactive",
                False,
            )
            if was_user_triggered:
                logger.info(
                    "Spotify Client ID and Secret are missing. Please configure them in Settings."
                )
                if self.main_view.navigation_handler:
                    self.main_view.navigation_handler.show_settings()

                QtWidgets.QApplication.processEvents()

                if self.main_view.settingsPage:
                    self.main_view.settingsPage.expandSpotifySection()

                try:
                    info_icon_path = paths.resource_path("assets/icons/info_icon.png")
                except Exception:
                    info_icon_path = None

                informative_text_for_dialog = (
                    "After entering them, click 'Save' at the bottom of the settings page, then try connecting to Spotify again. "
                    "For setup help, click 'How to get Spotify Client ID and Secret?' in the settings."
                )
                
                CustomQMessageBox.warning(
                    parent=self.main_view,
                    title="Spotify Credentials Missing",
                    main_message="Please enter Spotify Client ID and Secret in the 'Spotify Account Settings' section.",
                    informative_text=informative_text_for_dialog,
                    icon_path=info_icon_path
                )

                if hasattr(
                    self.main_view.auth_handler, "_last_spotify_trigger_was_interactive"
                ):
                    self.main_view.auth_handler._last_spotify_trigger_was_interactive = (
                        False
                    )
            else:
                Printf.warning(
                    "Spotify auto-login failed: Credentials missing. Configure in Settings to enable Spotify features."
                )

            self.main_view.tree_handler.update_spotify_root_item(logged_in=False)
            return

        self.main_view.tree_handler.update_spotify_root_item(
            logged_in=(auth_result is True)
        )

        if auth_result is True:
            logger.info("Spotify login successful!")
            self.fetchSpotifyPlaylists()
        elif auth_result == "SPOTIFY_SCOPE_UPGRADE_REQUIRED":
            logger.warning("Spotify login requires playlist write-scope reauthorization.")
            CustomQMessageBox.warning(
                self.main_view,
                "Spotify Reauthorization Required",
                "Spotify must be reconnected for playlist editing.",
                "The existing cached Spotify token does not include playlist write permissions.",
            )
        elif isinstance(auth_result, str):
            logger.warning(f"Spotify login failed. Reason: {auth_result}.")
        else:
            Printf.warning(
                "Spotify login failed. Please check credentials or network connection."
            )

    def refreshSpotifyPlaylists(self):
        logger.info("Refreshing Spotify playlists...")
        self.fetchSpotifyPlaylists()

    def fetchSpotifyPlaylists(self):
        logger.info("Fetching Spotify playlists...")

        def _spotify_playlist_thread():
            fetched_playlists: Optional[list] = None
            try:
                fetched_playlists = self.spotify_api.get_user_playlists()
            except Exception as e:
                logger.error(
                    f"Exception in Spotify playlist fetch thread: {e}", exc_info=True
                )
            finally:
                self.main_view.s_spotifyPlaylistsFetched.emit(fetched_playlists or [])

        thread = threading.Thread(target=_spotify_playlist_thread, daemon=True)
        thread.start()

    def fetchSpotifyTracks(self, playlist_id: str):
        logger.info(f"Fetching tracks for Spotify playlist ID: {playlist_id}")
        # Show loading row in table
        table_widget = self.main_view.tableWidget
        table_widget.clearRows()
        table_widget.addRow(["Loading Spotify tracks..."], None)

        normalized_playlist_id = str(playlist_id or "").strip()
        with self._tracks_request_lock:
            self._latest_tracks_request_token += 1
            request_token = self._latest_tracks_request_token
            self._latest_tracks_playlist_id = normalized_playlist_id

        def _spotify_track_thread():
            fetched_tracks: Optional[list] = None
            try:
                fetched_tracks = self.spotify_api.get_playlist_tracks(playlist_id)
            except Exception as e:
                logger.error(
                    f"Exception in Spotify track fetch thread for playlist {playlist_id}: {e}",
                    exc_info=True,
                )
            finally:
                with self._tracks_request_lock:
                    is_stale = (
                        request_token != self._latest_tracks_request_token
                        or normalized_playlist_id != self._latest_tracks_playlist_id
                    )

                if is_stale:
                    logger.info(
                        "Dropping stale Spotify track fetch result for playlist_id=%s (token=%s)",
                        normalized_playlist_id,
                        request_token,
                    )
                    return

                self.main_view.s_spotifyTracksFetched.emit(
                    playlist_id, fetched_tracks or []
                )

        thread = threading.Thread(target=_spotify_track_thread, daemon=True)
        thread.start()
