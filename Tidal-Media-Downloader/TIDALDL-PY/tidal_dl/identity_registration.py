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


def playlist_catalog(api, persistence, root, progress, cancelled):
    """Exact folder mappings only. Never infer identity from folder names alone."""
    if api.sp is None:
        raise ValueError("Log in to Spotify to match legacy files, or disable Spotify matching for an offline scan.")
    playlists = api.get_user_playlists()
    if playlists is None:
        raise RuntimeError("Spotify playlists could not be read. Retry or use an offline scan.")
    folders = defaultdict(list)
    warnings = []
    download_root = get_user_download_path(SETTINGS.downloadPath)
    for playlist in playlists:
        if cancelled():
            raise index.IdentityCancelled()
        context = {"type": "spotify", "data": playlist}
        candidates = set()
        for audio_type in ("", "flac", "mp3", "m4a", "mp4", "aac", "unknown"):
            path = getPlaylistPath(context, os.path.join(download_root, audio_type))
            if path:
                candidates.add(os.path.abspath(path))
        hint = persistence.get_playlist_folder_hint(str(playlist["id"]))
        if hint:
            candidates.add(os.path.abspath(os.path.join(download_root, hint)))
        relative = getPlaylistPath(context, "__identity_root__")
        if relative:
            parts = os.path.relpath(relative, "__identity_root__").split(os.sep)
            if parts and parts[0].casefold() == "playlists":
                parts = parts[1:]
            if parts:
                candidates.add(os.path.abspath(os.path.join(root, *parts)))
        candidates = {p for p in candidates if inside(p, root) and os.path.isdir(p)}
        if not candidates:
            continue
        progress(f"Reading Spotify playlist: {playlist['name']}")
        tracks = api.get_playlist_tracks(playlist["id"])
        if tracks is None:
            warnings.append(f"Spotify fetch failed; no legacy matches proposed for {playlist['name']}")
            continue
        links = persistence.get_links_for_playlist(str(playlist["id"])).get("tracks", {})
        for meta in tracks:
            if not meta.get("id") or meta.get("is_local"):
                continue
            # A persisted link helps identify candidates, but is NOT proof that
            # the local audio was downloaded from that TIDAL release.
            link = links.get(meta["id"], {})
            details = link.get("tidal_track_details") or {}
            tidal_id = str(link.get("tidal_track_id") or details.get("id") or "")
            target = replace(spotify_recording(meta), tidal_id=tidal_id)
            for folder in candidates:
                folders[index.path_key(folder)].append((target, str(meta.get("name") or meta["id"])))
    return folders, warnings


def scan(root, catalog, progress=lambda text: None, cancelled=lambda: False):
    proposals, warnings = [], []
    root = os.path.realpath(root)
    def walk_error(error):
        warnings.append(str(error))
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = [name for name in dirs if name not in EXCLUDED and not name.startswith(".") and
                   not os.path.islink(os.path.join(directory, name)) and inside(os.path.join(directory, name), root)]
        for name in sorted(files):
            if cancelled():
                raise index.IdentityCancelled()
            path = os.path.join(directory, name)
            if name.startswith(".") or os.path.splitext(name)[1].lower() not in SUPPORTED:
                continue
            if not inside(path, root) or os.path.islink(path):
                warnings.append(f"Skipped redirected file: {path}")
                continue
            progress(f"Verifying audio ({len(proposals) + 1}): {path}")
            before = ()
            try:
                before = index.signature(path)
                embedded = embedded_identity(path)
                fingerprint = index.audio_fingerprint(path, cancelled)
                known = index.identity_for_fingerprint(fingerprint)
                # Reuse an indexed UID when available; otherwise defer UID creation
                # until registration commit so identical audio copies converge.
                combined = index.merge_identity(known, embedded) if known is not None else embedded
                local = read_local_audio(path)
                if local is None:
                    raise ValueError("Audio tags could not be read.")
                choices = {}
                for target, title in catalog.get(index.path_key(directory), ()):
                    reason = match_recording(local.recording, target, allow_legacy=True)
                    if reason:
                        # Never invent a TIDAL provider ID from the current link.
                        proposed = index.Identity(combined.uid, target.spotify_id, embedded.tidal_id or combined.tidal_id)
                        choices[target.spotify_id] = (proposed, f"{title} — {target.spotify_id} — {reason}")
                if combined.spotify_id or combined.tidal_id:
                    status = "Registered" if known and embedded.uid and combined == known else "Identified"
                    evidence = "Verified audio index" if known else "Existing provider identity tags"
                    # Even a TIDAL-ID match to Spotify must be explicitly chosen.
                    if not combined.spotify_id and choices:
                        status = "Review association"
                        choices = {"keep": (combined, "Keep existing identity; do not add Spotify association"), **choices}
                    else:
                        choices = {}
                    proposal = Proposal(path, before, fingerprint, combined, status, evidence, tuple(choices.values()))
                elif choices:
                    proposal = Proposal(path, before, fingerprint, None, "Review association", "Legacy matching is not audio proof; choose a source explicitly.", tuple(choices.values()))
                else:
                    proposal = Proposal(path, before, fingerprint, None, "Unmatched", "No reliable provider association. Left untouched.")
                if index.signature(path) != before:
                    raise index.IdentityConflict("File changed during scan; scan again.")
                proposals.append(proposal)
                logger.debug("IDENTITY_SCAN_ROW path=%r status=%s choices=%d", path, proposal.status, len(proposal.choices))
            except index.IdentityCancelled:
                raise
            except Exception as exc:
                logger.warning("IDENTITY_SCAN_FAILED path=%r error=%s", path, exc, exc_info=logger.isEnabledFor(logging.DEBUG))
                proposals.append(Proposal(path, before, "", None, "Conflict / error", str(exc)))
    logger.info("IDENTITY_SCAN_DONE root=%r files=%d warnings=%d", root, len(proposals), len(warnings))
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


def apply_one(proposal, identity, root, write_tags, cancelled=lambda: False):
    path = proposal.path
    if not inside(path, root) or os.path.islink(path):
        raise ValueError("Path is outside the selected root or is a symbolic link.")
    with folder_lock(os.path.dirname(path)):
        if cancelled():
            raise index.IdentityCancelled()
        if index.signature(path) != proposal.signature:
            raise index.IdentityConflict("File changed since review; scan again.")
        current = embedded_identity(path)
        # Check every association before writing anything.
        identity = index.merge_identity(index.identity_for_fingerprint(proposal.fingerprint), identity)
        identity = index.merge_identity(identity, current)
        fingerprint = index.audio_fingerprint(path, cancelled, use_cache=False)
        if fingerprint != proposal.fingerprint:
            raise index.IdentityConflict("Audio changed since review; scan again.")
        if not write_tags or current == identity:
            index.register(path, fingerprint, identity, proposal.signature)
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
                raise index.IdentityConflict("Tag writer changed nonidentity metadata. Staged changes rejected; original untouched.")
            if embedded_identity(staged) != identity:
                raise index.IdentityConflict("Identity-tag verification failed; original untouched.")
            if index.audio_fingerprint(staged, cancelled, use_cache=False) != fingerprint:
                raise index.IdentityConflict("Staged audio verification failed; original untouched.")
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
