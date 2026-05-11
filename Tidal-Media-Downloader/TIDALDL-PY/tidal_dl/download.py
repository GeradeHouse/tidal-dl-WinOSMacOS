# tidal_dl/download.py

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   download.py
@Time    :   2020/11/08
@Author  :   Yaronzz
@Modified by: GeradeHouse
@Version :   1.0
@Contact :   yaronhuang@foxmail.com
@Desc    :   Core download module for Tidal Media Downloader.
"""

import asyncio
import json
import logging
import os
import re
import stat
import subprocess
import tempfile
import time
import traceback
import unicodedata
from contextlib import suppress
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union, cast

import aigpy
from moviepy.audio.io.AudioFileClip import AudioFileClip
from mutagen import File as MutagenFile

from .decryption import *
from .format import getAlbumPath, getAudioTypeFolder, getTrackPath
from .metadata.album import AlbumMetadata
from .metadata.track import TrackMetadata
from .metadata.tagger import tag_file
from .model import Album, Artist, Playlist, StreamUrl, Track
from .paths import get_user_download_path
from .printf import *
from .tidal import TIDAL_API, SETTINGS, AudioQuality, Type

if TYPE_CHECKING:
    from tidal_dl.gui.gui_main import MainView  # type: ignore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Download diagnostics need INFO-level phase markers

# Set up GUI logging with INFO level for this modul- LAZY LOADED
def _setup_gui_logging():
    """Lazy-load GUI logging setup to avoid circular imports."""
    try:
        from .gui.gui_logging import setup_gui_logger
        setup_gui_logger(__name__, logging.INFO)
    except ImportError:
        # GUI logging not available during non-GUI operations (e.g., headless downloads)
        pass

# Initialize GUI logging lazily
_setup_gui_logging()


def log_ffprobe_info(filepath: str, stage_name: str):
    """Runs ffprobe on the file and logs format/stream info."""
    try:
        command: List[str] = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            filepath,
        ]
        logger.debug(f"[{stage_name}] Running ffprobe: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        probe_data = json.loads(result.stdout)
        logger.debug(
            f"[{stage_name}] ffprobe result for '{filepath}': {json.dumps(probe_data, indent=2)}"
        )
        # Log specific useful info
        if "format" in probe_data:
            logger.debug(
                f"[{stage_name}] Format: {probe_data['format'].get('format_name', 'N/A')}, Duration: {probe_data['format'].get('duration', 'N/A')}"
            )
        if "streams" in probe_data:
            for i, stream in enumerate(probe_data["streams"]):
                logger.debug(
                    f"[{stage_name}] Stream #{i}: Codec: {stream.get('codec_name', 'N/A')}, Type: {stream.get('codec_type', 'N/A')}, Profile: {stream.get('profile', 'N/A')}, Bitrate: {stream.get('bit_rate', 'N/A')}, SampleFmt: {stream.get('sample_fmt', 'N/A')}, SampleRate: {stream.get('sample_rate', 'N/A')}"
                )
    # except FileNotFoundError: # Removed ffprobe dependency
    #     logger.warning(f"[{stage_name}] ffprobe command not found. Skipping ffprobe analysis for '{filepath}'. Make sure ffmpeg (which includes ffprobe) is installed and in your system's PATH.")
    # except subprocess.CalledProcessError as e: # Removed ffprobe dependency
    #     logger.error(f"[{stage_name}] ffprobe failed for '{filepath}': {e}")
    #     logger.error(f"[{stage_name}] ffprobe stderr: {e.stderr}")
    # except json.JSONDecodeError as e: # Removed ffprobe dependency
    #     logger.error(f"[{stage_name}] Failed to parse ffprobe JSON output for '{filepath}': {e}")
    except (
        Exception
    ) as e:  # Catch general exceptions during media analysis (ffprobe replacement/removal)
        logger.error(
            f"[{stage_name}] Error during media analysis (ffprobe replacement) on '{filepath}': {e}"
        )


def _track_diag_identity(track: Optional[Track]) -> str:
    if track is None:
        return "track_id=None title=None"
    track_id = getattr(track, "id", None)
    title = getattr(track, "title", None)
    return f"track_id={track_id} title={title!r}"


def _log_phase_start(phase: str, track: Optional[Track] = None, extra: str = "") -> float:
    started_at = time.monotonic()
    logger.info(
        "DL_PHASE_START phase=%s %s extra=%s",
        phase,
        _track_diag_identity(track),
        extra or "-",
    )
    return started_at


def _log_phase_end(
    phase: str,
    started_at: float,
    track: Optional[Track] = None,
    extra: str = "",
) -> None:
    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "DL_PHASE_END phase=%s elapsed=%.3fs %s extra=%s",
        phase,
        elapsed_seconds,
        _track_diag_identity(track),
        extra or "-",
    )


def _log_phase_error(
    phase: str,
    started_at: float,
    track: Optional[Track] = None,
    extra: str = "",
    exc: Optional[BaseException] = None,
) -> None:
    elapsed_seconds = time.monotonic() - started_at
    logger.error(
        "DL_PHASE_ERROR phase=%s elapsed=%.3fs %s extra=%s error=%s",
        phase,
        elapsed_seconds,
        _track_diag_identity(track),
        extra or "-",
        str(exc) if exc else "-",
        exc_info=exc is not None,
    )


def __isSkip__(finalpath: str, url: str) -> bool:
    if not SETTINGS.checkExist:
        return False
    curSize = aigpy.file.getSize(finalpath)
    if curSize <= 0:
        return False
    netSize = aigpy.net.getSize(url)
    return curSize >= netSize


def _extract_playlist_identity(
    playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
) -> tuple[Optional[str], Optional[str]]:
    playlist_id: Optional[str] = None
    playlist_name: Optional[str] = None

    if isinstance(playlist_context, Playlist):
        playlist_id = str(getattr(playlist_context, "uuid", "") or "").strip() or None
        playlist_name = str(getattr(playlist_context, "title", "") or "").strip() or None
        return playlist_id, playlist_name

    if isinstance(playlist_context, dict):
        p_type = playlist_context.get("type")
        p_data = playlist_context.get("data")

        if p_type == "spotify" and isinstance(p_data, dict):
            playlist_id = str(p_data.get("id", "") or "").strip() or None
            playlist_name = str(p_data.get("name", "") or "").strip() or None
            return playlist_id, playlist_name

        if p_type == "tidal" and isinstance(p_data, Playlist):
            playlist_id = str(getattr(p_data, "uuid", "") or "").strip() or None
            playlist_name = str(getattr(p_data, "title", "") or "").strip() or None
            return playlist_id, playlist_name

        if isinstance(p_data, dict):
            playlist_id = str(
                p_data.get("id", p_data.get("uuid", "")) or ""
            ).strip() or None
            playlist_name = str(
                p_data.get("name", p_data.get("title", "")) or ""
            ).strip() or None
            return playlist_id, playlist_name

    return None, None


def _extract_download_root_and_relative_playlist_dir(
    candidate_path: str,
) -> tuple[Optional[str], Optional[str]]:
    normalized_path = os.path.normpath(os.path.abspath(candidate_path))
    configured_download_root = os.path.normpath(
        os.path.abspath(get_user_download_path(SETTINGS.downloadPath))
    )
    candidate_dir = os.path.dirname(normalized_path)
    audio_type_folders = {"flac", "mp3", "m4a", "mp4", "aac", "unknown"}
    relative_to_configured_root: Optional[str] = None
    try:
        maybe_relative = os.path.relpath(candidate_dir, configured_download_root)
        if maybe_relative not in {"", "."} and not maybe_relative.startswith(".."):
            relative_to_configured_root = os.path.normpath(maybe_relative)
    except ValueError:
        # Different drive letters (Windows): continue with playlist-segment fallback.
        relative_to_configured_root = None

    if relative_to_configured_root:
        relative_parts = relative_to_configured_root.split(os.sep)
        if relative_parts and relative_parts[0].lower() in audio_type_folders:
            return configured_download_root, relative_to_configured_root

    path_parts = normalized_path.split(os.sep)

    if "Playlists" in path_parts:
        playlists_index = path_parts.index("Playlists")
        if playlists_index >= len(path_parts) - 1:
            return None, None

        if playlists_index == 0:
            return None, None

        download_root = os.sep.join(path_parts[:playlists_index])
        if not download_root:
            return None, None

        relative_playlist_dir = os.path.join(*path_parts[playlists_index:-1])
        return download_root, relative_playlist_dir

    # Fallback for custom playlist folder formats that do not include a "Playlists" segment.
    if not relative_to_configured_root:
        return None, None

    return configured_download_root, relative_to_configured_root


def _extract_track_id_from_audio_tags(file_path: str) -> Optional[str]:
    with suppress(Exception):
        audio_file = MutagenFile(file_path)
        if not audio_file or not getattr(audio_file, "tags", None):
            return None

        tags = audio_file.tags
        candidate_values: List[Any] = []

        if isinstance(tags, dict):
            for tag_key in (
                "TIDAL_TRACK",
                "TXXX:TIDAL_TRACK",
                "----:com.apple.iTunes:TIDAL_TRACK",
                "TIDAL_TRACK_ID",
                "TXXX:TIDAL_TRACK_ID",
                "----:com.apple.iTunes:TIDAL_TRACK_ID",
            ):
                if tag_key in tags:
                    candidate_values.append(tags.get(tag_key))

            for tag_key, tag_value in tags.items():
                key_upper = str(tag_key).upper()
                if "TIDAL_TRACK" in key_upper:
                    candidate_values.append(tag_value)

        for raw_value in candidate_values:
            if isinstance(raw_value, list) and raw_value:
                raw_value = raw_value[0]

            if isinstance(raw_value, bytes):
                raw_text = raw_value.decode("utf-8", errors="ignore").strip()
            else:
                raw_text = str(raw_value).strip()

            if raw_text and raw_text.isdigit():
                return raw_text

    return None


def _normalize_stem_for_match(stem: str) -> str:
    """Normalizes file stems to improve matching across punctuation/unicode variants."""
    raw_stem = str(stem or "")
    if not raw_stem:
        return ""

    normalized = unicodedata.normalize("NFKD", raw_stem)
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    normalized = normalized.replace("’", "'").replace("`", "'")
    normalized = re.sub(
        r"\((feat\.?|ft\.?)\s+[^)]*\)",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", normalized).strip().lower()
    return " ".join(normalized.split())


def _scan_single_playlist_folder_for_track(
    folder_path: str,
    expected_stems: set[str],
    expected_extensions: set[str],
    track_id_str: str,
    allow_stem_match: bool,
    read_audio_tags: bool = False,
) -> Optional[str]:
    normalized_expected_stems = {
        _normalize_stem_for_match(expected_stem)
        for expected_stem in expected_stems
        if expected_stem
    }

    scan_started_at = time.monotonic()
    scanned_files = 0
    tag_reads = 0

    try:
        for entry in os.scandir(folder_path):
            if not entry.is_file():
                continue

            _, extension = os.path.splitext(entry.name)
            extension_lower = extension.lower()
            if extension_lower not in expected_extensions:
                continue

            scanned_files += 1
            file_size = aigpy.file.getSize(entry.path)
            if file_size <= 0:
                continue

            if allow_stem_match:
                file_stem = os.path.splitext(entry.name)[0]
                normalized_file_stem = _normalize_stem_for_match(file_stem)
                if (
                    file_stem in expected_stems
                    or (
                        normalized_file_stem
                        and normalized_file_stem in normalized_expected_stems
                    )
                ):
                    return entry.path

            if not read_audio_tags:
                continue

            tag_reads += 1
            tags_track_id = _extract_track_id_from_audio_tags(entry.path)
            if tags_track_id and tags_track_id == track_id_str:
                return entry.path
    except OSError as ex:
        logger.debug(
            "DL_LOCAL_SCAN_ERROR folder=%r error=%s",
            folder_path,
            ex,
        )
        return None
    finally:
        elapsed = time.monotonic() - scan_started_at
        if elapsed >= 1.0:
            logger.warning(
                "DL_LOCAL_SCAN_SLOW folder=%r elapsed=%.3fs scanned_files=%d "
                "tag_reads=%d allow_stem_match=%s read_audio_tags=%s",
                folder_path,
                elapsed,
                scanned_files,
                tag_reads,
                allow_stem_match,
                read_audio_tags,
            )

    return None


def _find_existing_playlist_track_path(
    track: Track,
    stream: StreamUrl,
    artist: str,
    artists: str,
    album: Optional[Album],
    playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]],
    main_view_instance: "MainView",
    audio_type_folder: Optional[str] = None,
    candidate_extensions: Optional[List[str]] = None,
    allow_deep_playlist_scan: bool = False,
    read_audio_tags: bool = False,
) -> Optional[str]:
    if not playlist_context:
        return None

    scan_started_at = time.monotonic()
    extensions = candidate_extensions or [".flac", ".mp3", ".m4a", ".mp4"]
    candidate_paths = []
    for extension in extensions:
        candidate_audio_type_folder = audio_type_folder or getAudioTypeFolder(
            stream,
            extension=extension,
        )
        candidate_base_path = getTrackPath(
            track,
            stream,
            artist,
            artists,
            album,
            playlist_context,
            audio_type_folder=candidate_audio_type_folder,
        )
        base_stem, _ = os.path.splitext(os.path.normpath(candidate_base_path))
        candidate_paths.append(f"{base_stem}{extension}")

    expected_stems = {
        os.path.splitext(os.path.basename(candidate_path))[0]
        for candidate_path in candidate_paths
    }
    expected_extensions = {
        os.path.splitext(candidate_path)[1].lower() for candidate_path in candidate_paths
    }

    def _finish(result_path: Optional[str], reason: str) -> Optional[str]:
        elapsed = time.monotonic() - scan_started_at
        log_fn = logger.warning if elapsed >= 1.0 else logger.info
        log_fn(
            "DL_LOCAL_EXISTING_CHECK_DONE track_id=%s result=%s reason=%s "
            "elapsed=%.3fs candidate_count=%d deep_scan=%s read_audio_tags=%s",
            str(getattr(track, "id", "") or ""),
            bool(result_path),
            reason,
            elapsed,
            len(candidate_paths),
            allow_deep_playlist_scan,
            read_audio_tags,
        )
        return result_path

    for candidate_path in candidate_paths:
        if os.path.exists(candidate_path) and aigpy.file.getSize(candidate_path) > 0:
            return _finish(candidate_path, "exact_candidate_path")

    download_root, computed_relative_playlist_dir = _extract_download_root_and_relative_playlist_dir(
        candidate_paths[0]
    )
    if not download_root or not computed_relative_playlist_dir:
        return _finish(None, "no_playlist_directory_context")

    track_id_str = str(getattr(track, "id", "") or "").strip()
    if not track_id_str:
        return _finish(None, "missing_track_id")

    playlist_id, playlist_name = _extract_playlist_identity(playlist_context)

    persistence_manager = getattr(main_view_instance, "link_persistence_manager", None)
    persisted_relative_playlist_dir: Optional[str] = None
    if persistence_manager and playlist_id and hasattr(persistence_manager, "get_playlist_folder_hint"):
        with suppress(Exception):
            persisted_relative_playlist_dir = persistence_manager.get_playlist_folder_hint(playlist_id)

    preferred_relative_dirs = [
        computed_relative_playlist_dir,
        persisted_relative_playlist_dir,
    ]

    searched_relative_dirs: set[str] = set()
    for relative_dir in preferred_relative_dirs:
        if not relative_dir:
            continue
        normalized_relative_dir = os.path.normpath(relative_dir)
        if normalized_relative_dir in searched_relative_dirs:
            continue
        searched_relative_dirs.add(normalized_relative_dir)
        candidate_dir = os.path.join(download_root, normalized_relative_dir)
        if not os.path.isdir(candidate_dir):
            continue

        matched = _scan_single_playlist_folder_for_track(
            candidate_dir,
            expected_stems,
            expected_extensions,
            track_id_str,
            allow_stem_match=True,
            read_audio_tags=read_audio_tags,
        )
        if matched:
            return _finish(matched, "preferred_playlist_dir")

    if not allow_deep_playlist_scan:
        return _finish(None, "preferred_dirs_only_no_match")

    candidate_playlist_roots = [os.path.join(download_root, "Playlists")]
    for audio_folder in ("flac", "mp3", "m4a", "mp4", "aac", "unknown"):
        candidate_playlist_roots.append(os.path.join(download_root, audio_folder, "Playlists"))

    playlist_roots: List[str] = []
    seen_playlist_roots: set[str] = set()
    for playlists_root in candidate_playlist_roots:
        normalized_playlists_root = os.path.normpath(playlists_root)
        if normalized_playlists_root in seen_playlist_roots:
            continue
        seen_playlist_roots.add(normalized_playlists_root)
        if os.path.isdir(playlists_root):
            playlist_roots.append(playlists_root)

    if not playlist_roots:
        return _finish(None, "no_playlist_roots")

    prioritized_dirs: List[str] = []
    remaining_dirs: List[str] = []
    playlist_name_key = (playlist_name or "").strip().lower()

    for playlists_root in playlist_roots:
        with suppress(OSError):
            for entry in os.scandir(playlists_root):
                if not entry.is_dir():
                    continue

                entry_name_key = entry.name.strip().lower()
                if (
                    playlist_name_key
                    and (
                        playlist_name_key == entry_name_key
                        or playlist_name_key in entry_name_key
                        or entry_name_key in playlist_name_key
                    )
                ):
                    prioritized_dirs.append(entry.path)
                else:
                    remaining_dirs.append(entry.path)

    for folder_path in prioritized_dirs:
        matched = _scan_single_playlist_folder_for_track(
            folder_path,
            expected_stems,
            expected_extensions,
            track_id_str,
            allow_stem_match=True,
            read_audio_tags=read_audio_tags,
        )
        if matched:
            if (
                persistence_manager
                and playlist_id
                and hasattr(persistence_manager, "set_playlist_folder_hint")
            ):
                with suppress(Exception):
                    relative_dir = os.path.relpath(folder_path, download_root)
                    persistence_manager.set_playlist_folder_hint(
                        playlist_id,
                        relative_dir,
                        playlist_name,
                    )
            return _finish(matched, "deep_prioritized_playlist_dir")

    for folder_path in remaining_dirs:
        matched = _scan_single_playlist_folder_for_track(
            folder_path,
            expected_stems,
            expected_extensions,
            track_id_str,
            allow_stem_match=False,
            read_audio_tags=read_audio_tags,
        )
        if matched:
            if (
                persistence_manager
                and playlist_id
                and hasattr(persistence_manager, "set_playlist_folder_hint")
            ):
                with suppress(Exception):
                    relative_dir = os.path.relpath(folder_path, download_root)
                    persistence_manager.set_playlist_folder_hint(
                        playlist_id,
                        relative_dir,
                        playlist_name,
                    )
            return _finish(matched, "deep_remaining_playlist_dir")

    return _finish(None, "not_found")


def __encrypted__(stream: StreamUrl, srcPath: str, descPath: str):
    if aigpy.string.isNull(stream.encryptionKey):
        os.replace(srcPath, descPath)
    else:
        assert stream.encryptionKey is not None
        key, nonce = decrypt_security_token(stream.encryptionKey)
        decrypt_file(srcPath, descPath, key, nonce)
        os.remove(srcPath)


def __parseContributors__(
    roleType: str, Contributors: Optional[Dict[str, Any]]
) -> Optional[List[str]]:
    if Contributors is None:
        return None
    try:
        ret: List[str] = []
        for item in Contributors["items"]:
            if item["role"] == roleType:
                ret.append(item["name"])
        return ret
    except:
        return None


def create_streamrip_metadata(track: Track, album: Album) -> TrackMetadata:
    """
    Converts tidal-dl's Track and Album models to streamrip's Metadata models.
    This acts as a bridge between the two systems.
    """
    # ADDED: Debug log to inspect the album object being processed
    logger.debug(f"[create_streamrip_metadata] Processing Album object: {album}")

    # Step 1: Create the streamrip AlbumMetadata object from the tidal-dl Album
    # We adapt the logic from streamrip's `from_tidal` classmethod
    album_meta = AlbumMetadata.from_tidal(album)

    # Step 2: Create the streamrip TrackMetadata object
    # We adapt the logic from streamrip's `from_tidal` classmethod for tracks
    track_meta = TrackMetadata.from_tidal(album_meta, track)

    # Step 3: Manually add any extra information if needed
    # For example, streamrip's model can hold composer, which we get from contributors
    phase_started = _log_phase_start(
        "metadata_contributors_fetch",
        track,
        "source=create_streamrip_metadata optional=true",
    )
    try:
        contributors = TIDAL_API.getTrackContributors(str(track.id))
        _log_phase_end(
            "metadata_contributors_fetch",
            phase_started,
            track,
            "source=create_streamrip_metadata optional=true",
        )
        composers = __parseContributors__("Composer", contributors)
        if composers:
            track_meta.composer = ", ".join(composers)
    except Exception as e:
        _log_phase_error(
            "metadata_contributors_fetch",
            phase_started,
            track,
            "source=create_streamrip_metadata optional=true",
            e,
        )
        logger.warning(f"Could not fetch contributors for track {track.id}: {e}")

    return track_meta


def __setMetaData__(
    track: "Track",
    album: Optional["Album"],
    filepath: str,
    contributors: Optional[Dict[str, Any]], # Kept for potential future use, but logic is now in bridge
    lyrics: Optional[str], # Kept for potential future use
):
    """
    This function now uses the advanced streamrip tagging engine.
    """
    logger.info(f"Starting metadata tagging for '{os.path.basename(filepath)}' using streamrip engine.")

    def _is_permission_denied_error(exc: Exception) -> bool:
        """Best-effort detection for transient file lock / ACL write errors."""
        current: Optional[BaseException] = exc
        visited: set[int] = set()
        while current and id(current) not in visited:
            visited.add(id(current))
            err_no = getattr(current, "errno", None)
            if err_no == 13:
                return True

            message = str(current).lower()
            if (
                "permission denied" in message
                or "winerror 32" in message
                or "used by another process" in message
            ):
                return True

            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

        return False
    
    # Determine the correct album object
    album_obj = album if album is not None else track.album
    if not isinstance(album_obj, Album):
        logger.error("Could not determine a valid album object for metadata. Aborting tagging.")
        return

    cover_path = None  # Initialize cover_path to None
    try:
        # Step 1: Create the rich streamrip metadata object
        streamrip_meta = create_streamrip_metadata(track, album_obj)
        
        # Add lyrics if available
        if lyrics:
            streamrip_meta.lyrics = lyrics

        # --- ADVANCED: RYM Enrichment (Optional but Recommended) ---
        # To enable this, you need to initialize the service.
        # This is a simplified example.
        try:
            # These imports are here to prevent crashing if the files are not yet integrated
            from .metadata.rym_service import RymMetadataService
            # from streamrip.scraper import RYMMetadataScraper # You would need to integrate this file too
            # from streamrip.config import RymConfig # You would need to integrate this file too
            
            # This setup should ideally be done once, not per track
            # rym_config = RymConfig(enabled=True, genre_mode="replace") 
            # scraper = RYMMetadataScraper()
            # rym_service = RymMetadataService(scraper, rym_config)
            # asyncio.run(streamrip_meta.album.enrich_with_rym(rym_service))
            # logger.info("Enriched metadata with RYM data.")
            logger.debug("RYM enrichment is a placeholder. Full integration of scraper/config needed.")
        except ImportError:
            logger.warning("Could not import RYM service components. Skipping metadata enrichment.")
        # ---------------------------------------------------------

        # Step 2: Download cover art to a temporary file
        phase_started = _log_phase_start(
            "metadata_cover_fetch",
            track,
            f"cover={getattr(album_obj, 'cover', None)} size=1280x1280 optional=true",
        )
        try:
            cover_data = TIDAL_API.getCoverData(album_obj.cover, "1280", "1280")
            _log_phase_end(
                "metadata_cover_fetch",
                phase_started,
                track,
                f"bytes={len(cover_data) if cover_data else 0}",
            )
        except Exception as cover_error:
            _log_phase_error(
                "metadata_cover_fetch",
                phase_started,
                track,
                "optional=true",
                cover_error,
            )
            raise
        if cover_data:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_f:
                temp_f.write(cover_data)
                cover_path = temp_f.name
        
        # Step 3: Run tagging with retries for transient file locks
        # (OneDrive sync / AV scanners can briefly lock newly written files)
        max_retries = 6
        base_delay_seconds = 0.25
        file_name = os.path.basename(filepath)

        for attempt in range(1, max_retries + 1):
            try:
                # Ensure write-bit is present before mutagen tries to save
                try:
                    os.chmod(filepath, stat.S_IREAD | stat.S_IWRITE)
                except Exception:
                    pass

                # We use asyncio.run() to call the async tag_file from sync code
                phase_started = _log_phase_start(
                    "metadata_tag_file",
                    track,
                    f"file={file_name!r} attempt={attempt}/{max_retries}",
                )
                asyncio.run(tag_file(filepath, streamrip_meta, cover_path))
                _log_phase_end(
                    "metadata_tag_file",
                    phase_started,
                    track,
                    f"file={file_name!r} attempt={attempt}/{max_retries}",
                )
                logger.info(f"Successfully tagged '{file_name}' with extensive metadata.")
                break
            except Exception as tag_error:
                _log_phase_error(
                    "metadata_tag_file",
                    phase_started,
                    track,
                    f"file={file_name!r} attempt={attempt}/{max_retries}",
                    tag_error,
                )
                is_permission = _is_permission_denied_error(tag_error)
                if is_permission and attempt < max_retries:
                    wait_seconds = base_delay_seconds * attempt
                    logger.warning(
                        f"Permission denied while tagging '{file_name}' "
                        f"(attempt {attempt}/{max_retries}). Retrying in {wait_seconds:.2f}s..."
                    )
                    time.sleep(wait_seconds)
                    continue

                logger.error(
                    f"Failed to tag file '{file_name}' using streamrip engine: {tag_error}",
                    exc_info=True,
                )
                logger.error(f"Failed to write metadata for '{track.title}': {tag_error}")
                if is_permission:
                    logger.error(
                        "Tagging failed due to persistent file access denial. "
                        "If this path is inside OneDrive, consider pausing sync or "
                        "using a non-synced download folder."
                    )
                break

    except Exception as e:
        logger.error(f"Failed to tag file '{os.path.basename(filepath)}' using streamrip engine: {e}", exc_info=True)
        logger.error(f"Failed to write metadata for '{track.title}': {e}")
    finally:
        # Clean up the temporary cover file
        if cover_path and os.path.exists(cover_path):
            os.remove(cover_path)


def downloadCover(album: Album):
    if album is None:
        return

    artistName = TIDAL_API.getArtistsName(cast(List[Artist], album.artists))
    albumArtistName = getattr(getattr(album, "artist", None), "name", "")
    flag = TIDAL_API.getFlag(album, Type.Album, True, "")

    path = getAlbumPath(album, artistName, albumArtistName, flag)
    if path is None:
        return
    path += "/cover.jpg"
    
    # --- START OF MODIFICATION ---
    # Use the authenticated getCoverData method instead of a direct, unauthenticated download.
    cover_data = TIDAL_API.getCoverData(album.cover, "1280", "1280")
    if cover_data:
        with open(path, "wb") as f:
            f.write(cover_data)
    # --- END OF MODIFICATION ---


def downloadAlbumInfo(album: Album, tracks: List[Track]):
    if album is None:
        return

    artistName = TIDAL_API.getArtistsName(cast(List[Artist], album.artists))
    albumArtistName = getattr(getattr(album, "artist", None), "name", "")
    flag = TIDAL_API.getFlag(album, Type.Album, True, "")

    path = getAlbumPath(album, artistName, albumArtistName, flag)
    if path is None:
        return
    aigpy.path.mkdirs(path)

    # Use f-string formatting to avoid concatenation issues if any value is None.
    infos = f"[ID]          {album.id}\n"
    infos += f"[Title]       {album.title}\n"
    infos += (
        f"[Artists]     {TIDAL_API.getArtistsName(cast(List[Artist], album.artists))}\n"
    )
    infos += f"[ReleaseDate] {album.releaseDate}\n"
    infos += f"[SongNum]     {album.numberOfTracks}\n"
    infos += f"[Duration]    {album.duration}\n\n"

    if album.numberOfVolumes is None:
        return
    for index in range(0, album.numberOfVolumes):
        volumeNumber = index + 1
        infos += f"===========CD {volumeNumber}=============\n"
        for item in tracks:
            if item.volumeNumber != volumeNumber:
                continue
            infos += "{:<8}".format(f"[{item.trackNumber}]")
            infos += f"{item.title}\n"
    aigpy.file.write(path + "/AlbumInfo.txt", infos, "w+")  # Added filename


def __validateTrackObject__(track: Any) -> Track:
    """Ensure track is a proper Track object with required attributes"""
    import logging

    # Re-import Track locally for Pylance
    from .model import Track

    # If track is a string, attempt to convert using TIDAL_API.getTypeData
    if isinstance(track, str):
        logger.debug(f"Converting track from string: {track}")
        from .tidal import TIDAL_API, Type

        track_obj = TIDAL_API.getTypeData(track, Type.Track)
        if track_obj is None:
            raise ValueError(f"Failed conversion from string '{track}' to Track object")
        track = track_obj
    # If track is a dict, convert to Track object using aigpy
    if isinstance(track, dict):
        logger.debug(f"Converting track from dict: {track}")
        # Use aigpy.model.dictToModel for conversion
        track_obj = aigpy.model.dictToModel(track, Track())
        if track_obj is None:
            raise ValueError(f"Failed conversion from dict to Track object: {track}")
        track = track_obj  # Assign the converted object back
    # Extra check: if track.title is callable, this indicates invalid data
    if hasattr(track, "title") and callable(track.title):
        logger.error(f"Track title is callable, invalid track data: {track}")
        if "title" in track.__dict__ and not callable(track.__dict__["title"]):
            track.title = track.__dict__["title"]
            logger.debug(f"Fixed track title from __dict__: {track.title}")
        else:
            raise ValueError(
                "Track title remains callable after conversion: " + str(track)
            )
    if not hasattr(track, "audioQuality"):
        # Attempt to fetch full track info if audioQuality is missing
        track_id = getattr(track, "id", None)  # Get ID safely
        if track_id is None:
            raise ValueError(
                "Invalid track object - missing ID and audioQuality attribute."
            )
        logger.warning(
            f"Track object missing audioQuality, attempting to fetch full info for ID: {track_id}"
        )
        try:
            from .tidal import TIDAL_API, Type

            full_track = TIDAL_API.getTrack(str(track_id))  # Ensure ID is string
            if full_track and hasattr(full_track, "audioQuality"):
                logger.info(
                    f"Successfully fetched full track info with audioQuality for ID: {track_id}"
                )
                return full_track  # Return the complete object
            else:
                raise ValueError(
                    "Failed to fetch full track info or audioQuality still missing."
                )
        except Exception as e:
            logger.error(f"Error fetching full track info for ID {track_id}: {e}")
            raise ValueError(
                "Invalid track object - missing audioQuality attribute and failed to fetch full info; got type "
                + str(type(track))
            )

    # Additional check: if track is still a string, attempt conversion using getTrack
    if isinstance(track, str):
        logger.error(
            "Track remains a string after conversion; attempting getTrack conversion again"
        )
        from .tidal import TIDAL_API, Type

        try:
            new_track = TIDAL_API.getTrack(track)
            if not isinstance(new_track, Track):
                raise TypeError(f"Expected Track object but got {type(new_track)}")
            track = new_track
            logger.debug(
                f"Successfully converted track string to Track object: {track.id}"
            )
        except Exception as e:
            logger.error(f"Critical error converting track {track}: {str(e)}")
            raise ValueError(
                f"Failed to convert track ID to valid object: {track}"
            ) from e
    # Final check to ensure we have a Track object
    if not isinstance(track, Track):
        raise TypeError(
            f"Validation failed: Expected Track object, but got {type(track)}"
        )
    return track


def __getTrackQuality__(track: Track) -> AudioQuality:
    """Safely get audio quality from track object with extra debug logging"""
    import logging

    logger.debug(
        f"Entering __getTrackQuality__ with track: {track} (type: {type(track)}) and mediaMetadata: {getattr(track, 'mediaMetadata', None)}"
    )
    try:
        if hasattr(track, "audioQuality") and track.audioQuality is not None:
            quality_str = str(track.audioQuality)
            logger.debug(f"Track has audioQuality attribute: {quality_str}")
            return AudioQuality(quality_str)
        if hasattr(track, "mediaMetadata"):
            media_meta = track.mediaMetadata
            if isinstance(media_meta, dict):
                quality_str = media_meta.get("audioQuality")
                if quality_str and isinstance(quality_str, str):
                    logger.debug(f"Derived quality from mediaMetadata: {quality_str}")
                    return AudioQuality(quality_str)
        # Fallback if neither attribute exists
        logger.warning(
            f"Track object missing both audioQuality and mediaMetadata. Defaulting to LOSSLESS."
        )
        return AudioQuality.LOSSLESS
    except Exception as e:
        logger.error(f"Exception in __getTrackQuality__: {e}. Defaulting to LOSSLESS.")
        return AudioQuality.LOSSLESS


# Modify signature to accept playlist_context (including Album)
def downloadTrack(
    track: "Track",
    main_view_instance: "MainView",
    album: Optional["Album"] = None,
    playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    userProgress: Optional[Any] = None,
    partSize: int = 1048576,
    downloadQuality: Optional[str] = None,
):
    check = False
    actual_download_part_path = None
    try:
        track = __validateTrackObject__(track)
        assert isinstance(
            track, Track
        ), f"Validation failed, expected Track, got {type(track)}"

        # --- Fix missing album artist ---
        # The track.album object from search/track info might lack artist data.
        # We check and fetch full album info if needed to ensure correct paths and tagging.
        target_album = album if album else track.album
        if target_album and getattr(target_album, 'id', None):
            # Check if artist info is missing
            # Note: model.py initializes artist=Artist(), so we check for name/id or if it's None
            has_artist = False
            
            # Check 'artists' list
            artists_list = getattr(target_album, 'artists', None)
            if artists_list:
                has_artist = True
                
            # Check 'artist' object
            if not has_artist:
                artist_obj = getattr(target_album, 'artist', None)
                if artist_obj and getattr(artist_obj, 'name', None):
                    has_artist = True
            
            if not has_artist:
                logger.debug(f"Album metadata for '{getattr(target_album, 'title', 'Unknown')}' missing artist. Fetching full details...")
                try:
                    full_album = TIDAL_API.getAlbum(str(target_album.id))
                    if full_album:
                        if album:
                            # We can't reassign the local 'album' variable to affect the caller, 
                            # but we can use 'full_album' for local operations.
                            album = full_album
                        else:
                            track.album = full_album
                except Exception as e:
                    logger.warning(f"Failed to fetch full album info: {e}")
        # --- End Fix ---

        logger.debug(f"Starting downloadTrack for '{track.title}'")

        requested_mp3 = False
        intended_quality = SETTINGS.audioQuality

        if downloadQuality:
            normalized_quality = str(downloadQuality).lower()
            normalized_quality = (
                normalized_quality
                .replace("–", "-")
                .replace("—", "-")
                .replace(" ", "_")
                .replace("-", "_")
            )
            normalized_quality = re.sub(r"[^a-z0-9_]+", "_", normalized_quality)
            normalized_quality = re.sub(r"_+", "_", normalized_quality).strip("_")

            # Order matters: check the most specific FLAC/HiRes labels before generic "high".
            if (
                "highest_available" in normalized_quality
                or normalized_quality == "highest"
            ):
                intended_quality = AudioQuality.HIGHEST
            elif "mp3" in normalized_quality:
                intended_quality = AudioQuality.MP3
            elif (
                "hi_res_lossless" in normalized_quality
                or "hires" in normalized_quality
                or "high_resolution" in normalized_quality
                or "max_flac" in normalized_quality
                or "flac_max" in normalized_quality
                or normalized_quality == "max"
            ):
                intended_quality = AudioQuality.HI_RES_LOSSLESS
            elif (
                "cd_standard" in normalized_quality
                or "cd_flac" in normalized_quality
                or "flac_cd" in normalized_quality
                or "lossless" in normalized_quality
            ):
                intended_quality = AudioQuality.LOSSLESS
            elif (
                "flac_high" in normalized_quality
                or "high_flac" in normalized_quality
            ):
                intended_quality = AudioQuality.HIGH
            elif (
                "aac" in normalized_quality
                or "m4a" in normalized_quality
                or normalized_quality == "low"
                or normalized_quality.startswith("low_")
                or normalized_quality.endswith("_low")
            ):
                intended_quality = AudioQuality.LOW
            elif normalized_quality == "high":
                intended_quality = AudioQuality.HIGH
            else:
                logger.warning(
                    "Unknown download quality label %r normalized=%r; falling back to SETTINGS.audioQuality=%s",
                    downloadQuality,
                    normalized_quality,
                    SETTINGS.audioQuality,
                )
        
        if intended_quality == AudioQuality.MP3:
            requested_mp3 = True
            intended_quality = AudioQuality.HIGH
            logger.debug("MP3 requested. Setting download quality to HIGH for conversion.")

        q = intended_quality
        if intended_quality == AudioQuality.HIGHEST:
            is_hires = False
            media_metadata = getattr(track, "mediaMetadata", {})
            if isinstance(media_metadata, dict):
                tags = media_metadata.get("tags", [])
                if "HIRES_LOSSLESS" in [str(t).upper() for t in tags]:
                    is_hires = True
            q = AudioQuality.HI_RES_LOSSLESS if is_hires else AudioQuality.LOSSLESS

        if track.id is None:
            raise ValueError(f"Track '{track.title}' has no ID.")
        phase_started = _log_phase_start(
            "stream_url_fetch",
            track,
            f"requested_quality={q.value}",
        )
        stream = TIDAL_API.getStreamUrl(str(track.id), q)
        _log_phase_end(
            "stream_url_fetch",
            phase_started,
            track,
            f"retrieved_quality={getattr(stream, 'soundQuality', None)} codec={getattr(stream, 'codec', None)} segment_count={len(getattr(stream, 'urls', []) or [])}",
        )

        requested_lossless_stream = q in {
            AudioQuality.LOSSLESS,
            AudioQuality.HI_RES_LOSSLESS,
        }
        resolved_codec = str(getattr(stream, "codec", "") or "").lower()
        resolved_sound_quality = str(getattr(stream, "soundQuality", "") or "").upper()

        if requested_lossless_stream and "mp4a" in resolved_codec:
            raise ValueError(
                "Requested FLAC/lossless quality, but TIDAL returned an AAC stream "
                f"(requested={q.value}, retrieved={resolved_sound_quality}, codec={resolved_codec}). "
                "Aborting instead of silently saving an .m4a file."
            )

        artists = TIDAL_API.getArtistsName(cast(List[Artist], getattr(track, "artists", [])))
        artist = getattr(getattr(track, "artist", None), "name", "") or artists
        audio_type_folder = getAudioTypeFolder(
            stream,
            AudioQuality.MP3 if requested_mp3 else intended_quality,
        )
        path = getTrackPath(
            track,
            stream,
            artist,
            artists,
            album,
            playlist_context,
            audio_type_folder=audio_type_folder,
        )
        if not os.path.isabs(path):
            path = os.path.join(get_user_download_path(SETTINGS.downloadPath), path)
        path = os.path.normpath(path)
        aigpy.path.mkdirs(os.path.dirname(path))

        if SETTINGS.showTrackInfo and not SETTINGS.multiThread:
            # Convert to logger.info() with structured track information
            logger.info(f"Track: {track.title} (ID: {track.id}, Quality: {Printf.map_track_quality(track)})")
        if userProgress:
            userProgress.updateStream(stream)

        ### START DASH INTEGRATION ###
        # The key change is here. We now use `stream.urls` which can be a list of segments.
        url_list = stream.urls if stream.urls and len(stream.urls) > 1 else [stream.url]
        if not url_list or url_list[0] is None:
            raise Exception("No valid URL or URL list available for download.")
        
        logger.info(
            "[DL Track] name='%s'. Stream resolved with %d segment(s); running local skip checks before network download.",
            os.path.basename(path),
            len(url_list),
        )
        ### END DASH INTEGRATION ###

        if playlist_context:
            phase_started = _log_phase_start(
                "playlist_existing_check",
                track,
                f"path={path!r} audio_type_folder={audio_type_folder!r} deep_scan=false read_audio_tags=false",
            )
            try:
                existing_playlist_track_path = _find_existing_playlist_track_path(
                    track,
                    stream,
                    artist,
                    artists,
                    album,
                    playlist_context,
                    main_view_instance,
                    audio_type_folder=audio_type_folder,
                    candidate_extensions=[os.path.splitext(path)[1].lower()],
                    allow_deep_playlist_scan=False,
                    read_audio_tags=False,
                )
                _log_phase_end(
                    "playlist_existing_check",
                    phase_started,
                    track,
                    f"found={bool(existing_playlist_track_path)} path={existing_playlist_track_path!r}",
                )
            except Exception as existing_check_error:
                _log_phase_error(
                    "playlist_existing_check",
                    phase_started,
                    track,
                    f"path={path!r}",
                    existing_check_error,
                )
                raise

            if existing_playlist_track_path:
                logger.info(
                    f"{os.path.basename(existing_playlist_track_path)} (skip:already exists in playlist folder!)"
                )
                return True, ""

        phase_started = _log_phase_start(
            "final_file_skip_check",
            track,
            f"path={path!r} url_host={str(url_list[0]).split('/')[2] if '://' in str(url_list[0]) else '-'}",
        )
        try:
            should_skip_existing_file = __isSkip__(path, url_list[0])
            _log_phase_end(
                "final_file_skip_check",
                phase_started,
                track,
                f"skip={should_skip_existing_file}",
            )
        except Exception as skip_check_error:
            _log_phase_error(
                "final_file_skip_check",
                phase_started,
                track,
                f"path={path!r}",
                skip_check_error,
            )
            raise

        if should_skip_existing_file:
            logger.info(f"{os.path.basename(path)} (skip:already exists!)")
            return True, ""

        actual_download_part_path = path + ".part"
        if main_view_instance.cancel_requested:
            return False, "Download cancelled by user before start."

        logger.info(
            "[DL Track] name='%s'. Starting network download with %d segment(s).",
            os.path.basename(path),
            len(url_list),
        )
        tool = aigpy.download.DownloadTool(actual_download_part_path, url_list)
        tool.setUserProgress(userProgress)
        tool.setPartSize(partSize)
        phase_started = _log_phase_start(
            "download_tool_start",
            track,
            f"part_path={actual_download_part_path!r} segment_count={len(url_list)} part_size={partSize}",
        )
        try:
            check, err = tool.start(False)
            _log_phase_end(
                "download_tool_start",
                phase_started,
                track,
                f"success={check} err={err or ''!r}",
            )
        except Exception as download_error:
            _log_phase_error(
                "download_tool_start",
                phase_started,
                track,
                f"part_path={actual_download_part_path!r} segment_count={len(url_list)}",
                download_error,
            )
            raise

        if not check:
            logger.error(f"DL Track '{track.title}' failed: {err or ''}")
            return False, str(err or "")

        __encrypted__(stream, actual_download_part_path, path)

        if requested_mp3 and path.lower().endswith((".m4a", ".mp4", ".mov", ".flac")):
            logger.info(f"Converting '{track.title}' to MP3 (320kbps)...")
            logger.info(f"Converting '{track.title}' to MP3...")
            mp3_path = path.rsplit('.', 1)[0] + '.mp3'
            try:
                with AudioFileClip(path) as audio_clip:
                    audio_clip.write_audiofile(mp3_path, codec='mp3', bitrate='320k', logger=None)
                os.remove(path)
                path = mp3_path
            except Exception as e:
                logger.error(f"Failed to convert '{track.title}' to MP3: {e}")

        if path.lower().endswith((".mp4", ".mov")) and stream.codec and 'flac' in stream.codec.lower():
            logger.info(f"Detected FLAC in MP4 container for '{track.title}'. Extracting...")
            demuxed_path = path.rsplit('.', 1)[0] + '.flac'
            try:
                with AudioFileClip(path) as audio_clip:
                    audio_clip.write_audiofile(demuxed_path, codec="flac", logger="bar")
                os.remove(path)
                path = demuxed_path
            except Exception as e:
                logger.error(f"Demuxing of FLAC stream failed: {e}")
                return False, str(e)

        contributors = None
        if track.id:
            phase_started = _log_phase_start(
                "contributors_prefetch",
                track,
                "source=downloadTrack optional=true",
            )
            try:
                contributors = TIDAL_API.getTrackContributors(str(track.id))
                _log_phase_end(
                    "contributors_prefetch",
                    phase_started,
                    track,
                    "source=downloadTrack optional=true",
                )
            except Exception as ex:
                _log_phase_error(
                    "contributors_prefetch",
                    phase_started,
                    track,
                    "source=downloadTrack optional=true",
                    ex,
                )
                logger.debug(f"Failed to get contributors: {ex}")

        lyrics = ""
        if track.id:
            phase_started = _log_phase_start(
                "lyrics_fetch",
                track,
                "optional=true",
            )
            try:
                lyrics_data = TIDAL_API.getLyrics(str(track.id))
                lyrics = (
                    getattr(lyrics_data, "subtitles", None)
                    or getattr(lyrics_data, "lyrics", None)
                    or ""
                )
                _log_phase_end(
                    "lyrics_fetch",
                    phase_started,
                    track,
                    f"has_lyrics={bool(lyrics)} length={len(lyrics) if lyrics else 0}",
                )
                if SETTINGS.lyricFile and lyrics:
                    lrcPath = path.rsplit(".", 1)[0] + ".lrc"
                    aigpy.file.write(lrcPath, lyrics, "w")
            except Exception as ex:
                _log_phase_error(
                    "lyrics_fetch",
                    phase_started,
                    track,
                    "optional=true",
                    ex,
                )
                logger.debug(f"No lyrics available: {ex}")

        phase_started = _log_phase_start(
            "metadata_set",
            track,
            f"path={path!r}",
        )
        __setMetaData__(cast(Track, track), album, path, contributors, lyrics)
        _log_phase_end(
            "metadata_set",
            phase_started,
            track,
            f"path={path!r}",
        )
        logger.info(track.title or f"Track {track.id}")
        return True, ""

    except Exception as e:
        logger.error(f"DL Track '{getattr(track, 'title', 'Unknown')}' failed: {e}")
        logger.error(f"Exception in downloadTrack for '{getattr(track, 'title', 'Unknown')}': {e}", exc_info=True)
        return False, str(e)
    finally:
        interrupted = main_view_instance.stop_requested or main_view_instance.cancel_requested
        if (not check or interrupted) and actual_download_part_path and os.path.exists(actual_download_part_path):
            os.remove(actual_download_part_path)


# Modify signature to accept playlist_context (including Album)
def downloadTracks(
    tracks: List[Track],
    main_view_instance: "MainView",
    album: Optional["Album"] = None,
    playlist_context: Optional[Union[Playlist, Album, Dict[str, Any]]] = None,
    downloadQuality: Optional[str] = None,
):
    # When downloading a playlist with a selected quality (other than "highest"),
    # only download tracks whose maximum available quality (normalized) exactly matches the requested quality.
    if downloadQuality is not None and downloadQuality.lower() != "highest":
        filtered_tracks = []
        # Determine a quality filter using the same mapping.
        quality_map_enum = {
            "aac – low (up to 320 kbps)": AudioQuality.LOW,
            "flac – high (16-bit, 44.1 khz)": AudioQuality.HIGH,
            "flac – cd standard (16-bit, 44.1 khz)": AudioQuality.LOSSLESS,
            "flac – max / hires (up to 24-bit, 192 khz)": AudioQuality.HI_RES_LOSSLESS,
            # Backward-compatible aliases for persisted/older GUI labels.
            "m4a - aac – high efficiency (96 kbps, 44.1 khz)": AudioQuality.LOW,
            "m4a - aac – full bandwidth (320 kbps, 44.1 khz)": AudioQuality.HIGH,
            "flac – high resolution (24-bit, 96 khz)": AudioQuality.HI_RES_LOSSLESS,
            "highest available": AudioQuality.HIGHEST,
            "low": AudioQuality.LOW,
            "high": AudioQuality.HIGH,
            "max": AudioQuality.HI_RES_LOSSLESS,
            "lossless": AudioQuality.LOSSLESS,
            "hi_res_lossless": AudioQuality.HI_RES_LOSSLESS,
            "highest": AudioQuality.HIGHEST,
        }
        filter_quality = quality_map_enum.get(downloadQuality.lower(), None)
        for track in tracks:
            try:
                # Determine maximum available quality from track metadata
                track = __validateTrackObject__(track)  # Ensure track object is valid
                max_available_enum = AudioQuality.LOW  # Default lowest
                is_hires = False
                media_metadata = getattr(track, "mediaMetadata", {})
                if isinstance(media_metadata, dict):
                    tags = media_metadata.get("tags", [])
                    if isinstance(tags, list):
                        for tag in tags:
                            if isinstance(tag, str) and tag.upper() == "HIRES_LOSSLESS":
                                is_hires = True
                                break
                if is_hires:
                    max_available_enum = AudioQuality.HI_RES_LOSSLESS
                elif hasattr(track, "audioQuality") and track.audioQuality is not None:
                    # Compare with enum *value* as track.audioQuality is likely a string from API
                    if track.audioQuality == AudioQuality.LOSSLESS.value:
                        max_available_enum = AudioQuality.LOSSLESS
                    elif track.audioQuality == AudioQuality.HIGH.value:
                        max_available_enum = AudioQuality.HIGH
                    # LOW is default if neither matches

                # Define quality ranking (higher value is better)
                quality_ranking = {
                    AudioQuality.LOW: 1,
                    AudioQuality.HIGH: 2,
                    AudioQuality.LOSSLESS: 3,
                    AudioQuality.HI_RES_LOSSLESS: 4,
                    # HIGHEST is handled separately, but assign a rank for completeness
                    AudioQuality.HIGHEST: 5,
                }

                # Compare the track's max available quality with the filter quality using ranking
                # Handle None case for filter_quality before calling .get()
                filter_rank = (
                    quality_ranking.get(filter_quality, 0)
                    if filter_quality is not None
                    else 0
                )
                available_rank = quality_ranking.get(max_available_enum, 0)
                logger.debug(
                    f"Filtering track {track.id}: Max Available Quality Enum={max_available_enum.name} (Rank {available_rank}), Filter Quality Enum={filter_quality.name if filter_quality else 'None'} (Rank {filter_rank})"
                )

                # Allow download if available quality rank is >= filter quality rank
                if filter_quality is not None and available_rank >= filter_rank:
                    logger.debug(
                        f"Track {track.id} passed quality filter (Available >= Requested)."
                    )
                    filtered_tracks.append(track)
                else:
                    # Provide more context in the log message when a track fails the filter
                    logger.debug(
                        f"Track {track.id} did NOT pass quality filter (Available Rank {available_rank} < Requested Rank {filter_rank})."
                    )
            except Exception as e:
                logger.warning(
                    f"Error during quality filtering for track {track.id}: {e}"
                )
                logger.error(
                    traceback.format_exc()
                )  # Add traceback for better debugging
                continue
        tracks = filtered_tracks
        logger.debug(
            f"Finished filtering tracks. Number of tracks remaining: {len(tracks)}"
        )
        if not tracks:
            logger.warning(
                "No tracks matched the specified quality filter. Nothing to download."
            )
            return  # Exit if no tracks are left

    # If downloadQuality was HIGHEST, the filtering above might be too strict or unnecessary
    # as downloadTrack already handles selecting the best available.
    # However, keeping the filter as corrected ensures consistency if user specifically requests e.g., LOSSLESS
    # and wants ONLY lossless tracks, even if HIRES is available.

    # Now, for each track that passes the quality filter, call downloadTrack.
    logger.debug(
        f"Proceeding to download {len(tracks)} tracks. MultiThread: {SETTINGS.multiThread}"
    )
    if not SETTINGS.multiThread:
        logger.debug("Using single-thread download.")
        for index, item in enumerate(tracks):
            # === Pause/Stop Check (Inside Loop) ===
            logger.debug(
                f"[Thread] Loop {index+1}/{len(tracks)}: Checking stop_event (is_set={main_view_instance.stop_event.is_set()})"
            )
            if main_view_instance.stop_event.is_set():
                logger.info("Stop request detected. Aborting download queue.")
                break  # Exit the loop

            logger.debug(
                f"[Thread] Loop {index+1}/{len(tracks)}: Checking download_paused (is {main_view_instance.download_paused})"
            )
            if main_view_instance.download_paused:
                logger.info(
                    "Download queue paused. Waiting for resume signal..."
                )  # Updated log
                logger.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Emitting pause confirmation signal."
                )
                main_view_instance.signal_actually_paused.emit()
                logger.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Calling pause_event.wait() (event is_set={main_view_instance.pause_event.is_set()})"
                )
                main_view_instance.pause_event.wait()  # Wait for GUI to set() the event
                logger.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Returned from pause_event.wait() (event is_set={main_view_instance.pause_event.is_set()})"
                )
                logger.info("Download queue resumed.")  # Updated log
                # Re-check stop after pause
                logger.debug(
                    f"[Thread] Loop {index+1}/{len(tracks)}: Re-checking stop_event after pause (is_set={main_view_instance.stop_event.is_set()})"
                )
                if main_view_instance.stop_event.is_set():
                    logger.info(
                        "Stop request detected after pause. Aborting download queue."
                    )
                    break
            # === End Pause/Stop Check ===

            logger.debug(
                f"Initiating single-thread download for track {index+1}/{len(tracks)}: {item.title}"
            )
            # Pass main_view_instance and playlist_context to downloadTrack
            downloadTrack(
                item,
                main_view_instance,
                album=album,
                playlist_context=playlist_context,
                downloadQuality=downloadQuality,
            )
    else:
        # Note: Pause/Stop logic is NOT implemented for multi-thread mode here.
        thread_pool = ThreadPoolExecutor(max_workers=5)
        for index, item in enumerate(tracks):
            # Need to pass main_view_instance if multi-thread pause/stop were implemented
            # Pass playlist_context here as well
            thread_pool.submit(
                downloadTrack,
                item,
                main_view_instance,
                album,
                playlist_context,
                None,
                1048576,
                downloadQuality,
            )  # Pass main_view_instance and playlist_context
        thread_pool.shutdown(wait=True)
