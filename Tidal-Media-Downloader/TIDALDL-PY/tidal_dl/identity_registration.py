"""Review plans and identity-only writes with crash-recoverable staged replacement."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import TXXX
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

from . import identity_index as index
from .duplicate_cleanup import inside
from .format import getPlaylistPath
from .local_identity import (
    Recording,
    _tags,
    _first,
    local_audio_from_metadata,
    match_recording,
    spotify_recording,
    folder_lock,
)
from .paths import get_user_download_path
from .settings import SETTINGS

logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, index.LOG_LEVEL, logging.WARNING))
SUPPORTED = {".flac", ".mp3", ".m4a", ".mp4"}
PRIVATE_TAG = "TIDAL_DL_ID"
STAGE_PREFIX = ".tidal-identity-"
STAGE_JOURNAL_DIR = "identity-staging"
STAGE_PENDING_PREFIX = ".pending-"
EXCLUDED = {".tidal-dl-duplicates", ".tidal-dl-identity-backups", "identity-backups"}


@dataclass(frozen=True)
class Proposal:
    path: str
    signature: tuple
    fingerprint: str
    identity: index.Identity | None
    status: str
    evidence: str
    choices: tuple = ()


def embedded_identity(path):
    audio = MutagenFile(path)
    return _embedded_identity_from_audio(audio)


def _embedded_identity_from_audio(audio):
    if not isinstance(audio, (FLAC, MP3, MP4)):
        raise ValueError("Identity-only writing supports FLAC, MP3 and M4A/MP4 audio.")
    tags = _tags(audio)
    # Check all aliases and multivalues, not just the first value.
    fields = []
    for keys in ((PRIVATE_TAG,), ("SPOTIFY_TRACK_ID", "SPOTIFY_TRACK"), ("TIDAL_TRACK_ID", "TIDAL_TRACK")):
        values = {v for key in keys for v in tags.get(key, ()) if v}
        if len(values) > 1:
            raise index.IdentityConflict(f"Conflicting identity tags: {', '.join(keys)}")
        fields.append(next(iter(values), ""))
    identity = index.Identity(*fields)
    index.check_identity(identity)
    return identity


def playlist_catalog(persistence, root, playlist_names, progress, cancelled):
    """Build exact playlist-folder mappings from local persistence only."""
    folders = defaultdict(list)
    warnings = [
        "Cache-only identity scan: no Spotify API calls were made. "
        "Cached metadata may be incomplete or outdated; current Spotify "
        "playlist membership is not verified."
    ]
    download_root = get_user_download_path(SETTINGS.downloadPath)
    playlists = persistence.get_cached_spotify_playlists_for_cleanup()

    for playlist_id, name in (playlist_names or {}).items():
        normalized_id = str(playlist_id)
        playlist = playlists.setdefault(
            normalized_id,
            {
                "id": normalized_id,
                "name": str(name or ""),
                "folder_hint": "",
                "tracks": [],
            },
        )
        if name:
            playlist["name"] = str(name)

    if not playlists:
        warnings.append(
            "No locally cached Spotify playlists or track metadata are "
            "available. Audio files can still be checked against existing "
            "identity tags and the audio index."
        )
        logger.info(
            "IDENTITY_CATALOG_CACHE_ONLY playlists=0 "
            "mapped_playlists=0 mapped_folders=0 tracks=0"
        )
        return folders, warnings

    mapped_playlists = 0
    cached_tracks = 0

    for playlist in playlists.values():
        if cancelled():
            raise index.IdentityCancelled()
        if not isinstance(playlist, dict):
            continue

        playlist_id = str(playlist.get("id") or "").strip()
        if not playlist_id:
            continue

        name = str(playlist.get("name") or playlist_id).strip()
        tracks = playlist.get("tracks")
        if not isinstance(tracks, list) or not tracks:
            warnings.append(
                f"No cached Spotify track metadata; skipped playlist: {name}"
            )
            continue

        context = {"type": "spotify", "data": playlist}
        candidates = set()

        if playlist.get("name"):
            for audio_type in (
                "",
                "flac",
                "mp3",
                "m4a",
                "mp4",
                "aac",
                "unknown",
            ):
                path = getPlaylistPath(
                    context,
                    os.path.join(download_root, audio_type),
                )
                if path:
                    candidates.add(os.path.abspath(path))

        hint = str(playlist.get("folder_hint") or "").strip()
        if hint:
            candidates.add(
                os.path.abspath(os.path.join(download_root, hint))
            )

        if playlist.get("name"):
            relative_candidate = getPlaylistPath(
                context,
                "__identity_root__",
                use_folder_link=False,
            )
            if relative_candidate:
                relative = os.path.relpath(
                    relative_candidate,
                    "__identity_root__",
                )
                parts = relative.split(os.sep)
                if parts and parts[0].casefold() == "playlists":
                    parts = parts[1:]
                if parts:
                    candidates.add(
                        os.path.abspath(os.path.join(root, *parts))
                    )

        candidates = {
            path
            for path in candidates
            if inside(path, root) and os.path.isdir(path)
        }
        if not candidates:
            warnings.append(
                "No local folder could be mapped from cached metadata; "
                f"skipped playlist: {name}"
            )
            continue

        links = persistence.get_links_for_playlist(
            playlist_id
        ).get("tracks", {})
        if not isinstance(links, dict):
            links = {}

        progress(
            f"Using {len(tracks)} cached Spotify track(s): {name}"
        )
        mapped_playlists += 1
        cached_tracks += len(tracks)

        for meta in tracks:
            if not isinstance(meta, dict):
                continue

            spotify_id = str(meta.get("id") or "").strip()
            if not spotify_id or meta.get("is_local"):
                continue

            link = links.get(spotify_id, {})
            if not isinstance(link, dict):
                link = {}

            details = link.get("tidal_track_details") or {}
            tidal_id = str(
                link.get("tidal_track_id")
                or (
                    details.get("id")
                    if isinstance(details, dict)
                    else ""
                )
                or ""
            )

            target = replace(
                spotify_recording(meta),
                tidal_id=tidal_id,
            )
            title = str(meta.get("name") or spotify_id)

            for folder in candidates:
                folders[index.path_key(folder)].append(
                    (target, title)
                )

    logger.info(
        "IDENTITY_CATALOG_CACHE_ONLY playlists=%d "
        "mapped_playlists=%d mapped_folders=%d tracks=%d",
        len(playlists),
        mapped_playlists,
        len(folders),
        cached_tracks,
    )
    return folders, warnings


def scan(
    root,
    catalog,
    progress=lambda text: None,
    cancelled=lambda: False,
):
    with index.read_session():
        return _scan(root, catalog, progress, cancelled)


def _scan(root, catalog, progress, cancelled):
    proposals = []
    warnings = []
    root = os.path.realpath(root)
    cache_hits = 0
    scan_cache_hits = 0
    scan_cache_updates = []
    scan_cache_writable = True
    fingerprints_computed = 0
    fingerprints_deferred = 0
    legacy_migrations = 0

    logger.info(
        "IDENTITY_SCAN_START root=%r mapped_folders=%d",
        root,
        len(catalog),
    )

    def walk_error(exc):
        warnings.append(
            f"Could not inspect folder: {exc}"
        )

    def flush_scan_cache():
        nonlocal scan_cache_writable
        if not scan_cache_updates:
            return
        if not scan_cache_writable:
            scan_cache_updates.clear()
            return
        try:
            index.cache_scans(tuple(scan_cache_updates))
        except Exception as exc:
            scan_cache_writable = False
            warnings.append(
                f"Could not update the unchanged-file scan cache: {exc}"
            )
            logger.warning(
                "IDENTITY_SCAN_CACHE_WRITE_FAILED error=%s",
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
        finally:
            scan_cache_updates.clear()

    for directory, dirnames, filenames in os.walk(
        root,
        topdown=True,
        onerror=walk_error,
        followlinks=False,
    ):
        dirnames[:] = [
            name
            for name in dirnames
            if name.casefold() not in EXCLUDED
        ]

        if cancelled():
            flush_scan_cache()
            raise index.IdentityCancelled()

        for filename in filenames:
            if cancelled():
                flush_scan_cache()
                raise index.IdentityCancelled()

            path = os.path.join(
                directory,
                filename,
            )
            if (
                os.path.splitext(filename)[1].lower()
                not in SUPPORTED
            ):
                continue

            file_number = len(proposals) + 1
            progress(
                f"Checking file state "
                f"({file_number}): {path}"
            )
            before = ()
            audio = None

            try:
                before = index.signature(path)
                scan_cached = index.cached_scan(path, before)
                if scan_cached is not None:
                    (
                        embedded,
                        cached_isrc,
                        cached_title,
                        cached_artist,
                        cached_duration,
                    ) = scan_cached
                    cached_recording = Recording(
                        uid=embedded.uid,
                        spotify_id=embedded.spotify_id,
                        tidal_id=embedded.tidal_id,
                        isrc=cached_isrc,
                        title=cached_title,
                        artist=cached_artist,
                        duration=cached_duration,
                    )
                    scan_cache_hits += 1
                    progress(
                        "Using cached file metadata "
                        f"({file_number}): {path}"
                    )
                else:
                    progress(
                        "Reading local audio metadata "
                        f"({file_number}): {path}"
                    )
                    audio = MutagenFile(path)
                    embedded = _embedded_identity_from_audio(audio)
                    cached_recording = None

                has_embedded_identity = bool(
                    embedded.uid
                    or embedded.spotify_id
                    or embedded.tidal_id
                )

                cached = index.cached_fingerprint(
                    path
                )
                current_cached = bool(
                    cached
                    and index.fingerprint_is_current(
                        cached
                    )
                )
                fingerprint = (
                    (cached or "")
                    if current_cached
                    else ""
                )

                identity = (
                    index.resolve_declared_identity(
                        embedded,
                        cached or "",
                    )
                )

                catalog_entries = catalog.get(
                    index.path_key(directory),
                    (),
                )
                provider_match_reasons = []

                for target, _title in catalog_entries:
                    same_spotify = bool(
                        identity.spotify_id
                        and target.spotify_id
                        and identity.spotify_id
                        == target.spotify_id
                    )
                    same_tidal = bool(
                        identity.tidal_id
                        and target.tidal_id
                        and identity.tidal_id
                        == target.tidal_id
                    )
                    if not (same_spotify or same_tidal):
                        continue

                    target_identity = index.Identity(
                        uid=identity.uid or target.uid,
                        spotify_id=(
                            target.spotify_id
                            or identity.spotify_id
                        ),
                        tidal_id=(
                            target.tidal_id
                            or identity.tidal_id
                        ),
                    )
                    identity = index.merge_identity(
                        identity
                        if (
                            identity.uid
                            or identity.spotify_id
                            or identity.tidal_id
                        )
                        else None,
                        target_identity,
                    )

                    reason = (
                        "Spotify track ID"
                        if same_spotify
                        else "TIDAL track ID"
                    )
                    if reason not in provider_match_reasons:
                        provider_match_reasons.append(reason)

                legacy_identity = bool(
                    cached
                    and not current_cached
                    and (
                        identity.uid
                        or identity.spotify_id
                        or identity.tidal_id
                    )
                )

                content_known = bool(
                    cached
                    and index.identity_for_fingerprint(
                        cached
                    )
                )

                if current_cached:
                    cache_hits += 1

                if (
                    not fingerprint
                    and not has_embedded_identity
                ):
                    progress(
                        "Computing fast audio identity "
                        f"({file_number}): {path}"
                    )
                    fingerprint = (
                        index.audio_fingerprint(
                            path,
                            cancelled,
                        )
                    )
                    fingerprints_computed += 1

                    fingerprint_identity = (
                        index.identity_for_fingerprint(
                            fingerprint
                        )
                    )
                    if fingerprint_identity:
                        identity = (
                            index.merge_identity(
                                identity,
                                fingerprint_identity,
                            )
                            if (
                                identity.uid
                                or identity.spotify_id
                                or identity.tidal_id
                            )
                            else fingerprint_identity
                        )
                        content_known = True
                    elif legacy_identity:
                        identity = index.register(
                            path,
                            fingerprint,
                            identity,
                            before,
                        )
                        content_known = True
                        legacy_migrations += 1
                elif not fingerprint:
                    fingerprints_deferred += 1

                fully_registered = bool(
                    current_cached
                    and content_known
                    and has_embedded_identity
                    and embedded.uid == identity.uid
                    and embedded.spotify_id == identity.spotify_id
                    and embedded.tidal_id == identity.tidal_id
                )
                if fully_registered:
                    if index.signature(path) != before:
                        raise index.IdentityConflict("File changed while reading metadata; scan again.")
                    proposals.append(
                        Proposal(
                            path=path,
                            signature=before,
                            fingerprint=fingerprint,
                            identity=identity,
                            status="Registered",
                            evidence=(
                                "Dedicated identity tags and "
                                "the content index agree."
                            ),
                        )
                    )
                    if scan_cached is None and scan_cache_writable:
                        scan_cache_updates.append(
                            (path, before, embedded, None)
                        )
                        if len(scan_cache_updates) >= 250:
                            flush_scan_cache()
                    logger.debug(
                        "IDENTITY_SCAN_ROW path=%r status=Registered "
                        "fingerprint_kind=%s choices=0",
                        path,
                        index.fingerprint_kind(fingerprint),
                    )
                    continue

                if (
                    cached_recording is not None
                    and cached_recording.duration > 0
                ):
                    local_recording = cached_recording
                else:
                    if os.path.islink(path):
                        raise ValueError("Symbolic links cannot be registered.")
                    if audio is None:
                        audio = MutagenFile(path)
                    local = local_audio_from_metadata(path, before, audio)
                    if local is None:
                        raise ValueError(
                            "Audio tags could not be read."
                        )
                    local_recording = local.recording
                    if index.signature(path) != before:
                        raise index.IdentityConflict("File changed while reading metadata; scan again.")
                    if scan_cache_writable:
                        scan_cache_updates.append(
                            (path, before, embedded, local_recording)
                        )
                        if len(scan_cache_updates) >= 250:
                            flush_scan_cache()

                choices = []
                evidence = ""

                for target, title in catalog_entries:
                    reason = match_recording(
                        local_recording,
                        target,
                        allow_legacy=True,
                    )
                    if not reason:
                        continue

                    if (
                        reason
                        == "Possible duplicate: artist, title and duration only"
                    ):
                        if provider_match_reasons:
                            continue

                        conflicting_provider = bool(
                            (
                                identity.spotify_id
                                and target.spotify_id
                                and identity.spotify_id
                                != target.spotify_id
                            )
                            or (
                                identity.tidal_id
                                and target.tidal_id
                                and identity.tidal_id
                                != target.tidal_id
                            )
                        )
                        if not conflicting_provider:
                            choices.append(
                                (
                                    target,
                                    title,
                                    reason,
                                )
                            )
                        continue

                    target_identity = index.Identity(
                        uid=identity.uid or target.uid,
                        spotify_id=(
                            target.spotify_id
                            or identity.spotify_id
                        ),
                        tidal_id=(
                            target.tidal_id
                            or identity.tidal_id
                        ),
                    )
                    identity = index.merge_identity(
                        identity
                        if (
                            identity.uid
                            or identity.spotify_id
                            or identity.tidal_id
                        )
                        else None,
                        target_identity,
                    )
                    if reason not in provider_match_reasons:
                        provider_match_reasons.append(reason)

                if provider_match_reasons:
                    choices.clear()

                has_identity = bool(
                    identity.uid
                    or identity.spotify_id
                    or identity.tidal_id
                )

                if has_identity:
                    if (
                        content_known
                        and has_embedded_identity
                        and embedded.uid
                        == identity.uid
                        and embedded.spotify_id
                        == identity.spotify_id
                        and embedded.tidal_id
                        == identity.tidal_id
                    ):
                        status = "Registered"
                        evidence = (
                            "Dedicated identity tags and "
                            "the content index agree."
                        )
                    elif provider_match_reasons:
                        status = "Identified"
                        evidence = (
                            "Verified provider/ISRC association found; "
                            "missing dedicated identity fields are ready "
                            "to register."
                        )
                    elif content_known:
                        status = "Identified"
                        evidence = (
                            "Verified through the fast "
                            "audio-content index."
                        )
                    elif has_embedded_identity:
                        status = "Identified"
                        evidence = (
                            "Consistent dedicated identity "
                            "tags; content fingerprint "
                            "deferred until registration."
                        )
                    else:
                        status = "Identified"
                        evidence = (
                            "Existing indexed identity found."
                        )
                elif choices:
                    status = "Review association"
                    evidence = (
                        "Cached playlist metadata match "
                        "only; manual review required."
                    )
                else:
                    status = "Unmatched"
                    evidence = (
                        "No embedded identity, indexed "
                        "audio identity, or cached "
                        "playlist association."
                    )

                proposals.append(
                    Proposal(
                        path=path,
                        signature=before,
                        fingerprint=fingerprint,
                        identity=identity,
                        status=status,
                        evidence=evidence,
                        choices=tuple(
                            (
                                replace(
                                    target,
                                    uid=(
                                        identity.uid
                                        or target.uid
                                    ),
                                ),
                                f"{title} — {reason}",
                            )
                            for (
                                target,
                                title,
                                reason,
                            ) in choices
                        ),
                    )
                )

                logger.debug(
                    "IDENTITY_SCAN_ROW "
                    "path=%r status=%s "
                    "fingerprint_kind=%s choices=%d",
                    path,
                    status,
                    (
                        index.fingerprint_kind(
                            fingerprint
                        )
                        if fingerprint
                        else "deferred"
                    ),
                    len(choices),
                )
            except index.IdentityCancelled:
                flush_scan_cache()
                raise
            except Exception as exc:
                warnings.append(
                    f"{path}: {exc}"
                )
                proposals.append(
                    Proposal(
                        path=path,
                        signature=before,
                        fingerprint="",
                        identity=None,
                        status="Conflict / error",
                        evidence=str(exc),
                    )
                )
                logger.warning(
                    "IDENTITY_SCAN_FAILED "
                    "path=%r error=%s",
                    path,
                    exc,
                    exc_info=logger.isEnabledFor(
                        logging.DEBUG
                    ),
                )

    flush_scan_cache()
    logger.info(
        "IDENTITY_SCAN_DONE root=%r files=%d "
        "warnings=%d fingerprint_cache_hits=%d "
        "scan_cache_hits=%d fingerprints_computed=%d "
        "fingerprints_deferred=%d "
        "legacy_migrations=%d",
        root,
        len(proposals),
        len(warnings),
        cache_hits,
        scan_cache_hits,
        fingerprints_computed,
        fingerprints_deferred,
        legacy_migrations,
    )
    return proposals, warnings


def _file_hash(path, cancelled=lambda: False):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            if cancelled():
                raise index.IdentityCancelled()
            digest.update(block)
    return digest.hexdigest()


def _snapshot(audio):
    """Compare all nonidentity tag values and FLAC pictures after staged save."""
    excluded = {PRIVATE_TAG, "SPOTIFY_TRACK_ID", "TIDAL_TRACK_ID"}
    tags = {str(k): repr(v) for k, v in (audio.tags or {}).items()
            if str(k).rsplit(":", 1)[-1].upper() not in excluded}
    pictures = tuple(p.write() for p in getattr(audio, "pictures", ()))
    return tags, pictures


def _add_missing(audio, identity):
    tags = audio.tags
    if tags is None:
        audio.add_tags()
        tags = audio.tags
    if tags is None:
        raise ValueError("Identity tags could not be created for this container.")
    for key, value in ((PRIVATE_TAG, identity.uid), ("SPOTIFY_TRACK_ID", identity.spotify_id), ("TIDAL_TRACK_ID", identity.tidal_id)):
        if not value:
            continue
        if _first(_tags(audio), key):
            continue
        if isinstance(audio, MP3):
            tags.add(TXXX(encoding=3, desc=key, text=[value]))
        elif isinstance(audio, MP4):
            tags[f"----:com.apple.iTunes:{key}"] = [value.encode("utf-8")]
        elif isinstance(audio, FLAC):
            audio[key] = [value]
        else:
            raise ValueError("Unsupported identity-tag container.")


def _staging_journal_root():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"),
            "AppData",
            "Local",
        )
    elif sys.platform == "darwin":
        base = os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Application Support",
        )
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(
            os.path.expanduser("~"),
            ".local",
            "state",
        )
    return os.path.join(base, "Tidal-DL", STAGE_JOURNAL_DIR)


def _fsync_directory(path):
    if os.name == "nt":
        return

    try:
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
    except OSError:
        return

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _process_is_running(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False

    if pid <= 0:
        return False

    if pid == os.getpid():
        return True

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL(
                "kernel32",
                use_last_error=True,
            )
            kernel32.OpenProcess.argtypes = (
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            )
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL

            handle = kernel32.OpenProcess(
                0x1000,
                False,
                pid,
            )
            if not handle:
                return False

            kernel32.CloseHandle(handle)
            return True
        except Exception:
            # Failure to determine process state must be conservative:
            # never delete a possibly active staging transaction.
            return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False

    return True


def _remove_journal_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning(
            "IDENTITY_STAGE_JOURNAL_REMOVE_FAILED "
            "path=%r error=%s",
            path,
            exc,
        )
        return False

    _fsync_directory(os.path.dirname(path))
    return True


def _validated_staged_path(record):
    staged = record.get("staged_path")
    source = record.get("source_path")

    if not isinstance(staged, str) or not isinstance(source, str):
        return None

    staged = os.path.abspath(staged)
    source = os.path.abspath(source)

    if os.path.normcase(staged) == os.path.normcase(source):
        return None

    if os.path.normcase(os.path.dirname(staged)) != os.path.normcase(
        os.path.dirname(source)
    ):
        return None

    if not os.path.basename(staged).startswith(STAGE_PREFIX):
        return None

    staged_ext = os.path.splitext(staged)[1].lower()
    source_ext = os.path.splitext(source)[1].lower()
    if staged_ext != source_ext or staged_ext not in SUPPORTED:
        return None

    return staged


def _begin_stage_transaction(path):
    token = uuid.uuid4().hex
    source = os.path.abspath(path)
    staged = os.path.join(
        os.path.dirname(source),
        f"{STAGE_PREFIX}{token}{os.path.splitext(source)[1]}",
    )

    journal_root = _staging_journal_root()
    os.makedirs(journal_root, exist_ok=True)

    manifest = os.path.join(
        journal_root,
        f"{token}.json",
    )
    pending = os.path.join(
        journal_root,
        f"{STAGE_PENDING_PREFIX}{os.getpid()}-{token}.tmp",
    )

    record = {
        "version": 1,
        "pid": os.getpid(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": source,
        "staged_path": staged,
    }

    try:
        with open(
            pending,
            "x",
            encoding="utf-8",
        ) as handle:
            json.dump(
                record,
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(pending, manifest)
        _fsync_directory(journal_root)
    except Exception:
        for candidate in (pending, manifest):
            try:
                if os.path.lexists(candidate):
                    os.remove(candidate)
            except OSError:
                pass
        _fsync_directory(journal_root)
        raise

    return staged, manifest


def _finish_stage_transaction(staged, manifest):
    try:
        if os.path.lexists(staged):
            os.remove(staged)
            _fsync_directory(os.path.dirname(staged))
    except OSError as exc:
        logger.warning(
            "IDENTITY_STAGE_CLEANUP_DEFERRED "
            "path=%r manifest=%r error=%s",
            staged,
            manifest,
            exc,
        )
        return

    _remove_journal_file(manifest)


def cleanup_stale_identity_staging():
    journal_root = _staging_journal_root()

    if not os.path.isdir(journal_root):
        return 0

    try:
        names = os.listdir(journal_root)
    except OSError as exc:
        logger.warning(
            "IDENTITY_STAGE_STARTUP_CLEANUP_FAILED "
            "journal_root=%r error=%s",
            journal_root,
            exc,
        )
        return 0

    removed_stages = 0
    deferred = 0

    for name in names:
        candidate = os.path.join(journal_root, name)

        if (
            name.startswith(STAGE_PENDING_PREFIX)
            and name.endswith(".tmp")
        ):
            payload = name[
                len(STAGE_PENDING_PREFIX):-4
            ]
            pid_text, separator, _token = payload.partition("-")

            owner_pid = None
            if separator:
                try:
                    owner_pid = int(pid_text)
                except ValueError:
                    owner_pid = None

            if (
                owner_pid is not None
                and _process_is_running(owner_pid)
            ):
                deferred += 1
                continue

            if not _remove_journal_file(candidate):
                deferred += 1
            continue

        if not name.endswith(".json"):
            continue

        try:
            with open(
                candidate,
                "r",
                encoding="utf-8",
            ) as handle:
                record = json.load(handle)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "IDENTITY_STAGE_MANIFEST_INVALID "
                "manifest=%r error=%s",
                candidate,
                exc,
            )
            if not _remove_journal_file(candidate):
                deferred += 1
            continue
        except OSError as exc:
            logger.warning(
                "IDENTITY_STAGE_MANIFEST_READ_FAILED "
                "manifest=%r error=%s",
                candidate,
                exc,
            )
            deferred += 1
            continue

        if _process_is_running(record.get("pid")):
            deferred += 1
            continue

        staged = _validated_staged_path(record)
        if staged is None:
            logger.warning(
                "IDENTITY_STAGE_MANIFEST_UNSAFE "
                "manifest=%r",
                candidate,
            )
            if not _remove_journal_file(candidate):
                deferred += 1
            continue

        try:
            if os.path.lexists(staged):
                os.remove(staged)
                _fsync_directory(os.path.dirname(staged))
                removed_stages += 1
        except OSError as exc:
            logger.warning(
                "IDENTITY_STAGE_STALE_REMOVE_FAILED "
                "path=%r manifest=%r error=%s",
                staged,
                candidate,
                exc,
            )
            deferred += 1
            continue

        if not _remove_journal_file(candidate):
            deferred += 1

    try:
        os.rmdir(journal_root)
    except OSError:
        pass

    logger.info(
        "IDENTITY_STAGE_STARTUP_CLEANUP "
        "removed_stages=%d deferred=%d",
        removed_stages,
        deferred,
    )
    return removed_stages


def apply_one(
    proposal,
    identity,
    root,
    write_tags,
    cancelled=lambda: False,
):
    path = proposal.path
    if not inside(path, root) or os.path.islink(path):
        raise ValueError(
            "Path is outside the selected root "
            "or is a symbolic link."
        )

    with folder_lock(os.path.dirname(path)):
        if cancelled():
            raise index.IdentityCancelled()

        if index.signature(path) != proposal.signature:
            raise index.IdentityConflict(
                "File changed since review; scan again."
            )

        current = embedded_identity(path)

        fingerprint = (
            proposal.fingerprint
            if index.fingerprint_is_current(
                proposal.fingerprint
            )
            else index.audio_fingerprint(
                path,
                cancelled,
            )
        )

        resolved = index.resolve_declared_identity(
            identity,
            fingerprint,
        )
        identity = index.merge_identity(
            resolved if resolved.uid else None,
            identity,
        )
        identity = index.merge_identity(
            identity,
            current,
        )

        if not write_tags or current == identity:
            index.register(
                path,
                fingerprint,
                identity,
                proposal.signature,
            )
            return "Indexed without changing audio file"
        original_hash = _file_hash(path, cancelled)
        staged, manifest = _begin_stage_transaction(path)

        try:
            shutil.copy2(path, staged)

            with open(staged, "rb+") as handle:
                os.fsync(handle.fileno())

            if (
                _file_hash(staged, cancelled) != original_hash
                or index.signature(path) != proposal.signature
            ):
                raise index.IdentityConflict(
                    "Staging verification failed or source changed; "
                    "source was not modified."
                )

            audio = MutagenFile(staged)
            if audio is None:
                raise ValueError(
                    "Staged audio could not be opened for identity writing."
                )

            previous = _snapshot(audio)
            _add_missing(audio, identity)

            if isinstance(audio, MP3):
                version = getattr(
                    audio.tags,
                    "version",
                    (2, 4, 0),
                )[1]
                audio.save(
                    v2_version=version
                    if version in (3, 4)
                    else 4
                )
            else:
                audio.save()

            if _snapshot(MutagenFile(staged)) != previous:
                raise index.IdentityConflict(
                    "Tag writer changed nonidentity metadata. "
                    "Staged changes rejected; original untouched."
                )

            if embedded_identity(staged) != identity:
                raise index.IdentityConflict(
                    "Identity-tag verification failed; "
                    "original untouched."
                )

            if (
                index.audio_fingerprint(
                    staged,
                    cancelled,
                    use_cache=False,
                )
                != fingerprint
            ):
                raise index.IdentityConflict(
                    "Staged audio verification failed; "
                    "original untouched."
                )

            with open(staged, "rb+") as handle:
                os.fsync(handle.fileno())

            if cancelled():
                raise index.IdentityCancelled()

            if (
                index.signature(path) != proposal.signature
                or _file_hash(path, cancelled) != original_hash
            ):
                raise index.IdentityConflict(
                    "Source changed during registration; "
                    "staged changes rejected."
                )

            os.replace(staged, path)
            _fsync_directory(os.path.dirname(path))

            try:
                index.register(
                    path,
                    fingerprint,
                    identity,
                    index.signature(path),
                )
            except Exception as exc:
                raise RuntimeError(
                    "Identity tags saved, but index update failed: "
                    f"{exc}. Scan again to repair the index."
                ) from exc

            logger.info(
                "IDENTITY_REGISTERED path=%r uid=%s",
                path,
                identity.uid,
            )
            return "Identity tags added"
        finally:
            _finish_stage_transaction(
                staged,
                manifest,
            )


def repair_verified_download_identity(path, targets):
    """Make a strong download-time existing-recording match durable."""
    path = os.path.abspath(path)
    current = embedded_identity(path)

    identity = (
        current
        if (
            current.uid
            or current.spotify_id
            or current.tidal_id
        )
        else None
    )

    for target in targets:
        spotify_id = str(
            getattr(target, "spotify_id", "") or ""
        )
        tidal_id = str(
            getattr(target, "tidal_id", "") or ""
        )
        if not spotify_id and not tidal_id:
            continue

        candidate = index.Identity(
            uid=identity.uid if identity is not None else "",
            spotify_id=spotify_id,
            tidal_id=tidal_id,
        )
        identity = index.merge_identity(
            identity,
            candidate,
        )

    if identity is None:
        return "No provider identity available for repair"

    if current.uid and current == identity:
        indexed = index.identity_for_uid(current.uid)
        if indexed == identity:
            return "Identity already complete"

    proposal = Proposal(
        path=path,
        signature=index.signature(path),
        fingerprint=index.cached_fingerprint(path) or "",
        identity=current,
        status="Identified",
        evidence=(
            "Strong existing-recording match verified during download."
        ),
    )

    return apply_one(
        proposal,
        identity,
        os.path.dirname(path),
        True,
    )


def summary(proposals):
    counts = {"Registered": 0, "Identified": 0, "Review association": 0, "Unmatched": 0, "Conflict / error": 0}
    for proposal in proposals:
        counts[proposal.status] = counts.get(proposal.status, 0) + 1
    return counts


def apply_selected(selections, root, write_tags, progress=lambda text: None, cancelled=lambda: False):
    cleanup_stale_identity_staging()
    results = []
    for proposal, identity in selections:
        if cancelled():
            break
        progress(f"Registering ({len(results) + 1}/{len(selections)}): {proposal.path}")
        try:
            message = apply_one(proposal, identity, root, write_tags, cancelled)
            results.append((proposal.path, True, message))
        except index.IdentityCancelled:
            break
        except Exception as exc:
            logger.error("IDENTITY_REGISTER_FAILED path=%r error=%s", proposal.path, exc, exc_info=logger.isEnabledFor(logging.DEBUG))
            results.append((proposal.path, False, str(exc)))
    return results
