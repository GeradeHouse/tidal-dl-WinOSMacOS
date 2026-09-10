"""Review plans and identity-only writes with full-file recovery backups."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
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
from .local_identity import _tags, _first, read_local_audio, match_recording, spotify_recording, folder_lock
from .paths import getProfilePath, get_user_download_path
from .settings import SETTINGS

logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, index.LOG_LEVEL, logging.WARNING))
SUPPORTED = {".flac", ".mp3", ".m4a", ".mp4"}
PRIVATE_TAG = "TIDAL_DL_ID"
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
    proposals = []
    warnings = []
    root = os.path.realpath(root)
    cache_hits = 0
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
            raise index.IdentityCancelled()

        for filename in filenames:
            if cancelled():
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
                f"Checking local audio "
                f"({file_number}): {path}"
            )
            before = ()

            try:
                before = index.signature(path)
                embedded = embedded_identity(path)
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

                local = read_local_audio(path)
                if local is None:
                    raise ValueError(
                        "Audio tags could not be read."
                    )

                choices = []
                evidence = ""

                if not identity.spotify_id:
                    for target, title in catalog.get(
                        index.path_key(directory),
                        (),
                    ):
                        reason = match_recording(
                            local.recording,
                            target,
                            allow_legacy=True,
                        )
                        if reason:
                            choices.append(
                                (
                                    target,
                                    title,
                                    reason,
                                )
                            )

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

    logger.info(
        "IDENTITY_SCAN_DONE root=%r files=%d "
        "warnings=%d fingerprint_cache_hits=%d "
        "fingerprints_computed=%d "
        "fingerprints_deferred=%d "
        "legacy_migrations=%d",
        root,
        len(proposals),
        len(warnings),
        cache_hits,
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


def backup_root():
    return os.path.join(getProfilePath(), "identity-backups")


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
        # Full byte-for-byte backup permits recovery of unknown tags and padding,
        # not just the fields this application understands.
        recovery = os.path.join(backup_root(), uuid.uuid4().hex)
        os.makedirs(recovery, exist_ok=False)
        backup = os.path.join(recovery, os.path.basename(path))
        original_hash = _file_hash(path, cancelled)
        shutil.copy2(path, backup)
        with open(backup, "rb") as handle:
            os.fsync(handle.fileno())
        if _file_hash(backup, cancelled) != original_hash or index.signature(path) != proposal.signature:
            raise index.IdentityConflict("Backup verification failed or source changed; source was not modified.")
        record = {
            "version": 1, "original_path": path, "backup_path": backup,
            "original_sha256": original_hash, "fingerprint": fingerprint,
            "identity": identity.__dict__, "utc": datetime.now(timezone.utc).isoformat(),
            "recovery": "Close audio editors. Preserve the current file separately, then copy this backup to original_path. This restores ALL tags to their pre-registration values. Do not discard later DJ edits inadvertently.",
        }
        fd, staged = tempfile.mkstemp(prefix=".tidal-identity-", suffix=os.path.splitext(path)[1], dir=os.path.dirname(path))
        os.close(fd)
        try:
            shutil.copy2(backup, staged)
            audio = MutagenFile(staged)
            if audio is None:
                raise ValueError("Staged audio could not be opened for identity writing.")
            previous = _snapshot(audio)
            _add_missing(audio, identity)
            if isinstance(audio, MP3):
                version = getattr(audio.tags, "version", (2, 4, 0))[1]
                audio.save(v2_version=version if version in (3, 4) else 4)
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
            with open(staged, "rb") as handle:
                os.fsync(handle.fileno())
            record["registered_sha256"] = _file_hash(staged, cancelled)
            with open(os.path.join(recovery, "recovery.json"), "x", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            if cancelled():
                raise index.IdentityCancelled()
            if index.signature(path) != proposal.signature or _file_hash(path, cancelled) != original_hash:
                raise index.IdentityConflict("Source changed during registration; staged changes rejected.")
            os.replace(staged, path)
            # If DB commit fails, keep the valid tags and full backup; report the
            # partial result instead of rolling back over possible external edits.
            try:
                index.register(path, fingerprint, identity, index.signature(path))
            except Exception as exc:
                raise RuntimeError(f"Identity tags saved, but index update failed: {exc}. Backup: {backup}. Scan again to repair the index.") from exc
            logger.info("IDENTITY_REGISTERED path=%r uid=%s backup=%r", path, identity.uid, backup)
            return f"Identity tags added; backup: {backup}"
        finally:
            if os.path.exists(staged):
                os.remove(staged)


def summary(proposals):
    counts = {"Registered": 0, "Identified": 0, "Review association": 0, "Unmatched": 0, "Conflict / error": 0}
    for proposal in proposals:
        counts[proposal.status] = counts.get(proposal.status, 0) + 1
    return counts


def apply_selected(selections, root, write_tags, progress=lambda text: None, cancelled=lambda: False):
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
