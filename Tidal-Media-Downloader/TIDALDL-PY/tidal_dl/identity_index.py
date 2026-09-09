"""Durable, audio-backed identities. No descriptive tags are stored or written.

Set TIDAL_DL_IDENTITY_LOG_LEVEL=DEBUG before launch for detailed diagnostics.
SQLite connections are short-lived and thread-local; all writes are transactional.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

from mutagen import File as MutagenFile

from .paths import getProfilePath

LOG_LEVEL = os.environ.get("TIDAL_DL_IDENTITY_LOG_LEVEL", "WARNING").upper()
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL, logging.WARNING))


class IdentityConflict(ValueError):
    """Existing identity evidence disagrees; never silently overwrite it."""


class IdentityCancelled(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    uid: str
    spotify_id: str = ""
    tidal_id: str = ""


def index_path():
    return os.path.join(getProfilePath(), "local-audio-identities.sqlite3")


def signature(path):
    st = os.stat(path, follow_symlinks=False)
    return st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino


def path_key(path):
    return os.path.normcase(os.path.abspath(path))


@contextmanager
def connection(create=False):
    path = index_path()
    if not create and not os.path.isfile(path):
        yield None
        return
    # Do not use WAL: the configured profile can reside on OneDrive/network storage.
    db = sqlite3.connect(path, timeout=15)
    try:
        db.execute("PRAGMA busy_timeout=15000")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError(f"Unsupported identity index version {version}; preserve the database and update the application.")
        if create:
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recordings (
                    fingerprint TEXT PRIMARY KEY, uid TEXT NOT NULL UNIQUE,
                    spotify_id TEXT NOT NULL, tidal_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, signature TEXT NOT NULL, fingerprint TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS files_fingerprint ON files(fingerprint);
                PRAGMA user_version=1;
            """)
        yield db
    finally:
        db.close()


def cached_fingerprint(path):
    with connection() as db:
        if db is None:
            return None
        row = db.execute("SELECT signature, fingerprint FROM files WHERE path=?", (path_key(path),)).fetchone()
        if row and row[0] == json.dumps(signature(path)):
            return row[1]
    return None


def identity_for_fingerprint(fingerprint):
    with connection() as db:
        if db is None:
            return None
        row = db.execute("SELECT uid, spotify_id, tidal_id FROM recordings WHERE fingerprint=?", (fingerprint,)).fetchone()
        return Identity(*row) if row else None


def identity_for_uid(uid):
    if not uid:
        return None
    with connection() as db:
        if db is None:
            return None
        row = db.execute(
            "SELECT uid, spotify_id, tidal_id FROM recordings WHERE uid=?",
            (uid,),
        ).fetchone()
        return Identity(*row) if row else None


def cached_identity(path):
    fingerprint = cached_fingerprint(path)
    return identity_for_fingerprint(fingerprint) if fingerprint else None


def check_identity(identity):
    if identity.uid:
        uuid.UUID(identity.uid)
    if identity.spotify_id and not re.fullmatch(r"[A-Za-z0-9]{22}", identity.spotify_id):
        raise IdentityConflict("Invalid Spotify ID; an existing value will not be overwritten.")
    if identity.tidal_id and not re.fullmatch(r"[0-9]+", identity.tidal_id):
        raise IdentityConflict("Invalid TIDAL ID; an existing value will not be overwritten.")


def merge_identity(existing, proposed):
    check_identity(proposed)
    if existing is None:
        return Identity(proposed.uid or str(uuid.uuid4()), proposed.spotify_id, proposed.tidal_id)
    check_identity(existing)
    values = []
    for field in ("uid", "spotify_id", "tidal_id"):
        old, new = getattr(existing, field), getattr(proposed, field)
        if old and new and old != new:
            raise IdentityConflict(f"Conflicting {field}: existing={old}, proposed={new}. Manual investigation required.")
        values.append(old or new)
    return Identity(values[0] or str(uuid.uuid4()), values[1], values[2])


def register(path, fingerprint, identity, expected_signature):
    """Associate verified audio with IDs; no path or tag-only identity guessing."""
    if signature(path) != expected_signature:
        raise IdentityConflict("File changed before identity-index commit; scan again.")
    with connection(create=True) as maybe_db:
        db = maybe_db if maybe_db is not None else sqlite3.connect(index_path(), timeout=15)
        db.execute("PRAGMA busy_timeout=15000")
        try:
            row = db.execute("SELECT uid, spotify_id, tidal_id FROM recordings WHERE fingerprint=?", (fingerprint,)).fetchone()
            merged = merge_identity(Identity(*row) if row else None, identity)
            db.execute(
                "INSERT INTO recordings VALUES(?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET spotify_id=excluded.spotify_id, tidal_id=excluded.tidal_id",
                (fingerprint, merged.uid, merged.spotify_id, merged.tidal_id),
            )
            db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (path_key(path), json.dumps(expected_signature), fingerprint))
            if signature(path) != expected_signature:
                raise IdentityConflict("File changed during identity-index commit; scan again.")
            db.commit()
            logger.debug("IDENTITY_INDEX_REGISTER path=%r fingerprint=%s uid=%s", path, fingerprint, merged.uid)
            return merged
        except Exception:
            db.rollback()
            raise


def audio_fingerprint(path, cancelled=lambda: False, use_cache=True):
    """SHA-256 of fully decoded, unresampled PCM plus rate/channel count.

    FFmpeg's hash muxer consumes audio incrementally: no whole-track RAM buffer.
    No filename, tags, artwork or FLAC stored MD5 is trusted as audio evidence.
    """
    if cancelled():
        raise IdentityCancelled()
    if use_cache:
        cached = cached_fingerprint(path)
        if cached:
            return cached
    started = time.monotonic()
    before = signature(path)
    audio = MutagenFile(path)
    info = getattr(audio, "info", None)
    rate = int(getattr(info, "sample_rate", 0) or 0)
    channels = int(getattr(info, "channels", 0) or 0)
    duration = float(getattr(info, "length", 0) or 0)
    if rate <= 0 or channels <= 0 or duration <= 0:
        raise ValueError("Audio stream properties are missing or invalid.")
    from imageio_ffmpeg import get_ffmpeg_exe

    command = [get_ffmpeg_exe(), "-nostdin", "-v", "error", "-xerror", "-i", os.path.abspath(path),
               "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
               "-c:a", "pcm_s32le", "-f", "hash", "-hash", "sha256", "-"]
    logger.debug("IDENTITY_HASH_START path=%r rate=%s channels=%s", path, rate, channels)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        while True:
            if cancelled():
                raise IdentityCancelled()
            if time.monotonic() - started > max(120, min(duration * 2, 1800)):
                raise TimeoutError("Audio fingerprint decoding timed out; the file was not changed.")
            try:
                output, error = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError("Audio decoding failed: " + error.decode("utf-8", "replace")[-2000:])
        match = re.fullmatch(rb"SHA256=([a-fA-F0-9]{64})\s*", output)
        if not match:
            raise RuntimeError("Audio decoder returned an invalid fingerprint.")
        if signature(path) != before:
            raise IdentityConflict("Audio file changed while calculating its fingerprint.")
        fingerprint = f"pcm-s32le-v1:{rate}:{channels}:{match.group(1).decode().lower()}"
        logger.debug("IDENTITY_HASH_DONE path=%r elapsed=%.3fs fingerprint=%s", path, time.monotonic() - started, fingerprint)
        return fingerprint
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def resolve_identity(path, verify_audio=False, cancelled=lambda: False):
    """Fast lookups never decode; worker/download lookups may verify edited files."""
    cached = cached_identity(path)
    if cached or not verify_audio or not os.path.isfile(index_path()):
        return cached
    fingerprint = audio_fingerprint(path, cancelled)
    identity = identity_for_fingerprint(fingerprint)
    if identity:
        register(path, fingerprint, identity, signature(path))
    return identity
