"""Conservative, playlist-aware duplicate review and reversible quarantine."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .local_identity import (
    AUDIO_EXTENSIONS, LocalAudio, file_signature, folder_lock,
    match_recording, read_local_audio, spotify_recording,
)

QUARANTINE_NAME = ".tidal-dl-duplicates"


@dataclass(frozen=True)
class DuplicatePair:
    first: LocalAudio
    second: LocalAudio
    reason: str
    spotify_title: str


def inside(path, root):
    try:
        path = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def scan_duplicates(root, playlist_folders, progress=lambda text: None, cancelled=lambda: False):
    """playlist_folders maps exact local directories to fetched Spotify tracks.

    Only compare within a directory, and only pairs associated with the same
    online recording. Extra local tracks are not orphans to be deleted.
    """
    pairs, warnings = [], []
    for folder, tracks in playlist_folders.items():
        if cancelled():
            break
        if not inside(folder, root) or not os.path.isdir(folder) or os.path.islink(folder) or QUARANTINE_NAME in os.path.normpath(folder).split(os.sep):
            continue
        progress(f"Reading audio tags: {folder}")
        local_files = []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if cancelled():
                        return pairs, warnings
                    if not entry.is_file(follow_symlinks=False) or os.path.splitext(entry.name)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    audio = read_local_audio(entry.path)
                    if audio:
                        local_files.append(audio)
                    else:
                        warnings.append(f"Unreadable or unsupported audio (left untouched): {entry.path}")
        except OSError as exc:
            warnings.append(f"Cannot scan {folder}: {exc}")
            continue
        seen = set()
        for meta in tracks:
            if cancelled():
                return pairs, warnings
            target = spotify_recording(meta)
            if not target.spotify_id:
                continue
            candidates = [audio for audio in local_files if match_recording(audio.recording, target, allow_legacy=True)]
            # Matching PCM can connect a legacy file with no useful identifying tags.
            pcm_ids = {a.pcm_identity for a in candidates if a.pcm_identity}
            candidates = [a for a in local_files if a in candidates or (a.pcm_identity and a.pcm_identity in pcm_ids)]
            for i, first in enumerate(candidates):
                for second in candidates[i + 1:]:
                    key = tuple(sorted((first.path, second.path)))
                    if key in seen:
                        continue
                    reason = (
                        "Identical FLAC PCM checksum and stream parameters"
                        if first.pcm_identity and first.pcm_identity == second.pcm_identity
                        else match_recording(first.recording, second.recording, allow_legacy=True)
                    )
                    if reason:
                        seen.add(key)
                        pairs.append(DuplicatePair(first, second, reason, str(meta.get("name") or target.spotify_id)))
    return pairs, warnings


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def quarantine_selected(root, selections):
    """selections contains explicitly chosen (duplicate, keeper, reason) triples.

    Never permanently delete; never overwrite; leave all lyrics/covers/DJ sidecars
    alone. Each move gets a durable recovery record before it is attempted.
    """
    root = os.path.realpath(root)
    removal_paths = {a.path for a, _, _ in selections}
    if any(keep.path in removal_paths for _, keep, _ in selections):
        raise ValueError("A file chosen to keep is also selected for removal. Resolve the conflicting selections first.")
    moved, errors = [], []
    seen = set()
    for duplicate, keeper, reason in selections:
        if duplicate.path in seen:
            continue
        seen.add(duplicate.path)
        try:
            if duplicate.path == keeper.path or os.path.dirname(duplicate.path) != os.path.dirname(keeper.path):
                raise ValueError("A duplicate must have a different retained file in the same folder.")
            if not all(inside(a.path, root) and not os.path.islink(a.path) for a in (duplicate, keeper)):
                raise ValueError("File is outside the scan root or is a symbolic link.")
            with folder_lock(os.path.dirname(duplicate.path)):
                if any(file_signature(a.path) != a.signature for a in (duplicate, keeper)):
                    raise ValueError("A file changed since the scan. Scan again before removing it.")
                if os.path.samefile(duplicate.path, keeper.path):
                    raise ValueError("These paths refer to the same file.")
                checksum = _sha256(duplicate.path)
                recovery = os.path.join(root, QUARANTINE_NAME)
                if os.path.islink(recovery) or not inside(recovery, root):
                    raise ValueError("Unsafe recovery directory.")
                os.makedirs(recovery, exist_ok=True)
                if not inside(recovery, root):
                    raise ValueError("Recovery directory was redirected outside the scan root.")
                session = os.path.join(recovery, uuid.uuid4().hex)
                os.mkdir(session)
                destination = os.path.join(session, os.path.basename(duplicate.path))
                record = {
                    "original_path": duplicate.path, "quarantine_path": destination,
                    "kept_path": keeper.path, "reason": reason, "sha256": checksum,
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                    "restore_instructions": "Close audio applications. Move quarantine_path back to original_path only if that path is unoccupied. Never overwrite an existing file.",
                }
                with open(os.path.join(session, "recovery.json"), "x", encoding="utf-8") as file:
                    json.dump(record, file, indent=2, ensure_ascii=False)
                    file.flush()
                    os.fsync(file.fileno())
                if any(file_signature(a.path) != a.signature for a in (duplicate, keeper)):
                    raise ValueError("A file changed during verification; nothing was moved.")
                os.rename(duplicate.path, destination)
                moved.append(record)
        except Exception as exc:
            errors.append(f"{duplicate.path}: {exc}")
    return moved, errors
