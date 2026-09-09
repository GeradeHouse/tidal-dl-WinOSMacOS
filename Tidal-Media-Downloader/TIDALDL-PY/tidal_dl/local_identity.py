"""Read-only recording identity shared by downloads and duplicate review.

Names and durations are supporting evidence, never proof that audio is identical.
No file is removed merely because it is absent from an online playlist.
"""
from __future__ import annotations

import logging
import os
from contextlib import suppress
import re
import threading
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from mutagen import File as MutagenFile

from . import identity_index

logger = logging.getLogger(__name__)
AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".wav", ".aiff", ".ogg", ".opus"}
# Serialize check-through-finalization within a destination folder. Different
# playlists can still download concurrently. Cleanup uses the same lock.
_locks_guard = threading.Lock()
_folder_locks: dict[str, threading.RLock] = {}


def folder_lock(folder):
    key = os.path.normcase(os.path.realpath(folder))
    with _locks_guard:
        return _folder_locks.setdefault(key, threading.RLock())


def normalized(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def recording_title(value):
    # Only this conventional redundant label is ignored. Preserve radio edits,
    # mixed/live/extended versions and named remixes (important for DJ libraries).
    value = re.sub(r"\(\s*original mix\s*\)|\[\s*original mix\s*\]|\s+-\s+original mix\s*$", "", str(value or ""), flags=re.I)
    return normalized(value)


def _values(value):
    if hasattr(value, "text"):
        value = value.text
    if not isinstance(value, (list, tuple)):
        value = [value]
    return tuple(str(v.decode("utf-8", "replace") if isinstance(v, bytes) else v).strip() for v in value if v is not None)


def _tags(audio):
    result = {}
    for key, value in (getattr(audio, "tags", None) or {}).items():
        key = str(key)
        if key.startswith("----:") or key.upper().startswith("TXXX:"):
            key = key.rsplit(":", 1)[-1]
        result[key.upper()] = _values(value)
    return result


def _first(tags, *keys):
    for key in keys:
        values = tags.get(key, ())
        if values and values[0]:
            return values[0]
    return ""


@dataclass(frozen=True)
class Recording:
    uid: str = ""
    spotify_id: str = ""
    tidal_id: str = ""
    isrc: str = ""
    title: str = ""
    artist: str = ""
    duration: float = 0


@dataclass(frozen=True)
class LocalAudio:
    path: str
    recording: Recording
    signature: tuple
    pcm_identity: tuple | None


def file_signature(path):
    stat = os.stat(path, follow_symlinks=False)
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino


@lru_cache(maxsize=20000)
def _read_cached(path, signature):
    audio = MutagenFile(path)
    info = getattr(audio, "info", None)
    duration = float(getattr(info, "length", 0) or 0)
    if audio is None or duration <= 0 or signature[0] <= 0:
        return None
    tags = _tags(audio)
    title = _first(tags, "TITLE", "TIT2", "©NAM")
    artist = _first(tags, "ARTIST", "TPE1", "©ART")
    stem = os.path.splitext(os.path.basename(path))[0]
    if title and artist and " - " in stem:
        file_artist, file_title = stem.split(" - ", 1)
        # Old tags sometimes omit '(Mixed)' or a remix suffix that the filename
        # still preserves. Do not erase that evidence of a different version.
        if normalized(file_artist) == normalized(artist) and recording_title(file_title).startswith(recording_title(title) + " "):
            title = file_title
    if not title or not artist:
        if " - " in stem:
            file_artist, file_title = stem.split(" - ", 1)
            artist = artist or file_artist
            title = title or file_title
    recording = Recording(
        uid=_first(tags, "TIDAL_DL_ID"),
        spotify_id=_first(tags, "SPOTIFY_TRACK_ID", "SPOTIFY_TRACK"),
        tidal_id=_first(tags, "TIDAL_TRACK_ID", "TIDAL_TRACK"),
        isrc=normalized(_first(tags, "ISRC", "TSRC")).replace(" ", ""),
        title=recording_title(title), artist=normalized(artist), duration=duration,
    )
    # FLAC STREAMINFO MD5 describes decoded PCM, not filenames or mutable tags.
    md5 = getattr(info, "md5_signature", 0)
    pcm = None
    if md5 and info is not None:
        pcm = (md5, info.sample_rate, info.channels, info.bits_per_sample, info.total_samples)
    return LocalAudio(path, recording, signature, pcm)


def read_local_audio(path):
    path = os.path.abspath(path)
    if os.path.islink(path):
        return None
    try:
        return _read_cached(path, file_signature(path))
    except Exception as exc:
        logger.debug("Cannot read local audio identity %r: %s", path, exc)
        return None


def spotify_recording(meta):
    artists = meta.get("artists") or []
    artist = artists[0] if artists else ""
    if isinstance(artist, dict):
        artist = artist.get("name", "")
    return Recording(
        spotify_id=str(meta.get("id") or ""),
        isrc=normalized(meta.get("isrc") or (meta.get("external_ids") or {}).get("isrc")).replace(" ", ""),
        title=recording_title(meta.get("name")), artist=normalized(artist),
        duration=float(meta.get("duration_ms") or 0) / 1000,
    )


def download_recordings(track, download_item=None):
    title = str(getattr(track, "title", "") or "")
    if getattr(track, "version", None):
        title += f" ({track.version})"
    result = [Recording(
        tidal_id=str(getattr(track, "id", "") or ""),
        isrc=normalized(getattr(track, "isrc", "")).replace(" ", ""),
        title=recording_title(title),
        artist=normalized(getattr(getattr(track, "artist", None), "name", "")),
        duration=float(getattr(track, "duration", 0) or 0),
    )]
    if download_item is not None and getattr(download_item, "source_platform", None) == "spotify":
        meta = dict(getattr(download_item, "spotify_metadata", None) or {})
        meta["id"] = download_item.source_track_id
        result.insert(0, spotify_recording(meta))
    return result


def match_recording(left, right, allow_legacy=False):
    close_duration = left.duration > 0 and right.duration > 0 and abs(left.duration - right.duration) <= 2.0
    # The source Spotify ID remains stable even when the linked TIDAL release changes.
    if left.spotify_id and left.spotify_id == right.spotify_id:
        return "Spotify track ID" if not (left.duration and right.duration) or close_duration else ""
    if left.tidal_id and left.tidal_id == right.tidal_id and close_duration:
        return "TIDAL track ID"
    same_metadata = bool(left.title and left.artist and left.title == right.title and left.artist == right.artist and close_duration)
    if same_metadata and left.isrc and left.isrc == right.isrc:
        return "ISRC, artist, version and duration"
    if same_metadata and allow_legacy:
        # Contradictory recording codes must not be silently treated as equivalent.
        if left.isrc and right.isrc and left.isrc != right.isrc:
            return ""
        return "Possible duplicate: artist, title and duration only"
    return ""


def identity_match(identity, targets):
    """Registered audio identities survive renamed files and edited tags."""
    if identity is None:
        return ""
    for target in targets:
        if identity.spotify_id and target.spotify_id and identity.spotify_id == target.spotify_id:
            return "Registered Spotify audio identity"
        if identity.tidal_id and target.tidal_id and identity.tidal_id == target.tidal_id:
            return "Registered TIDAL audio identity"
    return ""


def find_existing_recording(folder, targets, extensions):
    """Only inspect this destination, never suppress intentional copies elsewhere."""
    if not os.path.isdir(folder):
        return None
    possible_matches = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False) or os.path.splitext(entry.name)[1].lower() not in extensions:
                continue
            with suppress(Exception):
                reason = identity_match(identity_index.resolve_identity(entry.path), targets)
                if reason:
                    logger.info("Skip existing recording via identity index: %s | %s", entry.path, reason)
                    return entry.path
            local = read_local_audio(entry.path)
            if not local:
                continue
            if local.recording.uid:
                with suppress(Exception):
                    reason = identity_match(
                        identity_index.identity_for_uid(local.recording.uid),
                        targets,
                    )
                    if reason:
                        logger.info(
                            "Skip existing recording via embedded identity UID: %s | %s",
                            entry.path,
                            reason,
                        )
                        return entry.path
            for target in targets:
                reason = match_recording(local.recording, target)
                if reason:
                    logger.info("Skip existing recording: %s | %s", entry.path, reason)
                    return entry.path
                if match_recording(local.recording, target, allow_legacy=True):
                    possible_matches.append(entry.path)
    if possible_matches:
        raise ValueError(
            "Possible existing recording with matching artist, title/version and duration but no reliable shared ID. "
            "No additional copy was downloaded. Review the existing audio before retrying: "
            + "; ".join(sorted(set(possible_matches)))
        )
    return None
