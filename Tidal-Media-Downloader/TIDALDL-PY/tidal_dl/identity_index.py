 li"""Durable, audio-backed identities with a persistent unchanged-file scan cache.

Descriptive metadata is never written by this module. A minimal read-only snapshot
of matching fields may be stored locally so unchanged files need not be reopened on
every maintenance scan. Workers may reuse read connections; writes remain
independent, fully synchronous transactions.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

from mutagen import File as MutagenFile
from mutagen.flac import FLAC

from .paths import getProfilePath

LOG_LEVEL = os.environ.get(
    "TIDAL_DL_IDENTITY_LOG_LEVEL",
    "INFO",
).upper()
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))


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


def _create_schema_v2(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS recordings (
            uid TEXT PRIMARY KEY,
            spotify_id TEXT NOT NULL,
            tidal_id TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS fingerprints (
            fingerprint TEXT PRIMARY KEY,
            uid TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            signature TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_cache (
            path TEXT PRIMARY KEY,
            signature TEXT NOT NULL,
            uid TEXT NOT NULL,
            spotify_id TEXT NOT NULL,
            tidal_id TEXT NOT NULL,
            isrc TEXT NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            duration REAL NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS fingerprints_uid "
        "ON fingerprints(uid)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS files_fingerprint "
        "ON files(fingerprint)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS recordings_spotify "
        "ON recordings(spotify_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS recordings_tidal "
        "ON recordings(tidal_id)"
    )
    db.execute("PRAGMA user_version=2")


def _migrate_v1_to_v2(db):
    db.execute("PRAGMA synchronous=FULL")
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "ALTER TABLE recordings RENAME TO recordings_v1"
        )
        _create_schema_v2(db)
        db.execute(
            """
            INSERT INTO recordings(uid, spotify_id, tidal_id)
            SELECT uid, spotify_id, tidal_id
            FROM recordings_v1
            """
        )
        db.execute(
            """
            INSERT INTO fingerprints(fingerprint, uid)
            SELECT fingerprint, uid
            FROM recordings_v1
            """
        )
        db.execute("DROP TABLE recordings_v1")
        db.commit()
        logger.info(
            "IDENTITY_INDEX_MIGRATED schema=1->2"
        )
    except Exception:
        db.rollback()
        raise


_read_session = threading.local()


@contextmanager
def read_session():
    """Reuse a worker's read connection, without holding a read transaction.

    Writes still use independent, fully synchronous transactions. Closing the
    scope releases the connection even when a scan fails or is cancelled.
    """
    if getattr(_read_session, "db", None) is not None:
        yield
        return
    with connection() as db:
        _read_session.db = db
        try:
            yield
        finally:
            _read_session.db = None


@contextmanager
def connection(create=False):
    shared = getattr(_read_session, "db", None)
    if not create and shared is not None:
        yield shared
        return
    path = index_path()
    if not create and not os.path.isfile(path):
        yield None
        return

    db = sqlite3.connect(path, timeout=15)
    try:
        db.execute("PRAGMA busy_timeout=15000")
        version = db.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        if version not in (0, 1, 2):
            raise RuntimeError(
                f"Unsupported identity index version {version}; "
                "preserve the database and update the application."
            )

        if version == 1:
            _migrate_v1_to_v2(db)
            version = 2

        if version == 0:
            if not create:
                yield None
                return
            db.execute("PRAGMA synchronous=FULL")
            _create_schema_v2(db)
            db.commit()
        elif create:
            db.execute("PRAGMA synchronous=FULL")
            _create_schema_v2(db)
            db.commit()

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


def cache_fingerprint(path, fingerprint, expected_signature):
    """Persist verified path/fingerprint state without assigning identity."""
    if signature(path) != expected_signature:
        raise IdentityConflict(
            "File changed before fingerprint-cache commit; scan again."
        )

    with connection(create=True) as db:
        if db is None:
            raise RuntimeError(
                "Identity index could not be opened for fingerprint caching."
            )

        try:
            db.execute(
                "INSERT OR REPLACE INTO files(path, signature, fingerprint) "
                "VALUES(?,?,?)",
                (
                    path_key(path),
                    json.dumps(expected_signature),
                    fingerprint,
                ),
            )

            if signature(path) != expected_signature:
                raise IdentityConflict(
                    "File changed during fingerprint-cache commit; scan again."
                )

            db.commit()
            logger.debug(
                "IDENTITY_HASH_CACHE_WRITE path=%r fingerprint=%s",
                path,
                fingerprint,
            )
        except Exception:
            db.rollback()
            raise


def cached_scan(path, expected_signature):
    """Return cached read-only scan facts when the file is unchanged."""
    expected = json.dumps(expected_signature)
    with connection() as db:
        if db is None:
            return None
        try:
            row = db.execute(
                "SELECT signature, uid, spotify_id, tidal_id, "
                "isrc, title, artist, duration "
                "FROM scan_cache WHERE path=?",
                (path_key(path),),
            ).fetchone()
        except sqlite3.OperationalError:
            # Existing schema-v2 databases gain this additive cache lazily.
            return None

        if row and row[0] == expected:
            return (
                Identity(row[1], row[2], row[3]),
                str(row[4] or ""),
                str(row[5] or ""),
                str(row[6] or ""),
                float(row[7] or 0),
            )
    return None


def cache_scans(entries):
    """Persist read-only scan facts in one transaction for future scans."""
    rows = []
    for path, expected_signature, embedded, recording in entries:
        rows.append(
            (
                path_key(path),
                json.dumps(expected_signature),
                embedded.uid,
                embedded.spotify_id,
                embedded.tidal_id,
                str(getattr(recording, "isrc", "") or ""),
                str(getattr(recording, "title", "") or ""),
                str(getattr(recording, "artist", "") or ""),
                float(getattr(recording, "duration", 0) or 0),
            )
        )

    if not rows:
        return

    with connection(create=True) as db:
        if db is None:
            raise RuntimeError(
                "Identity index could not be opened for scan caching."
            )
        db.execute("BEGIN IMMEDIATE")
        try:
            db.executemany(
                "INSERT OR REPLACE INTO scan_cache("
                "path, signature, uid, spotify_id, tidal_id, "
                "isrc, title, artist, duration"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                rows,
            )
            db.commit()
            logger.debug(
                "IDENTITY_SCAN_CACHE_WRITE files=%d",
                len(rows),
            )
        except Exception:
            db.rollback()
            raise


def _identity_for_fingerprint_db(db, fingerprint):
    if not fingerprint:
        return None
    row = db.execute(
        """
        SELECT r.uid, r.spotify_id, r.tidal_id
        FROM fingerprints AS f
        JOIN recordings AS r ON r.uid = f.uid
        WHERE f.fingerprint=?
        """,
        (fingerprint,),
    ).fetchone()
    return Identity(*row) if row else None


def _identity_for_uid_db(db, uid):
    if not uid:
        return None
    row = db.execute(
        "SELECT uid, spotify_id, tidal_id "
        "FROM recordings WHERE uid=?",
        (uid,),
    ).fetchone()
    return Identity(*row) if row else None


def _identity_for_provider_db(
    db,
    spotify_id="",
    tidal_id="",
):
    clauses = []
    params = []

    if spotify_id:
        clauses.append("spotify_id=?")
        params.append(spotify_id)
    if tidal_id:
        clauses.append("tidal_id=?")
        params.append(tidal_id)

    if not clauses:
        return None

    rows = db.execute(
        "SELECT uid, spotify_id, tidal_id "
        "FROM recordings WHERE " + " OR ".join(clauses),
        tuple(params),
    ).fetchall()

    candidates = {
        row[0]: Identity(*row)
        for row in rows
    }
    merged = None
    for candidate in candidates.values():
        merged = (
            candidate
            if merged is None
            else merge_identity(merged, candidate)
        )
    return merged


def identity_for_fingerprint(fingerprint):
    with connection() as db:
        if db is None:
            return None
        return _identity_for_fingerprint_db(
            db,
            fingerprint,
        )


def identity_for_uid(uid):
    with connection() as db:
        if db is None:
            return None
        return _identity_for_uid_db(db, uid)


def identity_for_provider(
    spotify_id="",
    tidal_id="",
):
    with connection() as db:
        if db is None:
            return None
        return _identity_for_provider_db(
            db,
            spotify_id,
            tidal_id,
        )


def cached_identity(path):
    fingerprint = cached_fingerprint(path)
    return (
        identity_for_fingerprint(fingerprint)
        if fingerprint
        else None
    )


def check_identity(identity):
    if identity.uid:
        uuid.UUID(identity.uid)
    if (
        identity.spotify_id
        and not re.fullmatch(
            r"[A-Za-z0-9]{22}",
            identity.spotify_id,
        )
    ):
        raise IdentityConflict(
            "Invalid Spotify ID; an existing value will not be overwritten."
        )
    if (
        identity.tidal_id
        and not re.fullmatch(
            r"[0-9]+",
            identity.tidal_id,
        )
    ):
        raise IdentityConflict(
            "Invalid TIDAL ID; an existing value will not be overwritten."
        )


def merge_identity(existing, proposed):
    check_identity(proposed)
    if existing is None:
        return Identity(
            proposed.uid or str(uuid.uuid4()),
            proposed.spotify_id,
            proposed.tidal_id,
        )

    check_identity(existing)
    values = []
    for field in ("uid", "spotify_id", "tidal_id"):
        old = getattr(existing, field)
        new = getattr(proposed, field)
        if old and new and old != new:
            raise IdentityConflict(
                f"Conflicting {field}: existing={old}, "
                f"proposed={new}. Manual investigation required."
            )
        values.append(old or new)

    return Identity(
        values[0] or str(uuid.uuid4()),
        values[1],
        values[2],
    )


def _resolve_declared_identity_db(
    db,
    proposed,
    fingerprint="",
):
    known = None

    candidates = (
        _identity_for_fingerprint_db(
            db,
            fingerprint,
        ),
        _identity_for_uid_db(
            db,
            proposed.uid,
        ),
        _identity_for_provider_db(
            db,
            proposed.spotify_id,
            proposed.tidal_id,
        ),
    )

    for candidate in candidates:
        if candidate is None:
            continue
        known = (
            candidate
            if known is None
            else merge_identity(known, candidate)
        )

    return (
        merge_identity(known, proposed)
        if known is not None
        else proposed
    )


def resolve_declared_identity(
    proposed,
    fingerprint="",
):
    check_identity(proposed)
    with connection() as db:
        if db is None:
            return proposed
        return _resolve_declared_identity_db(
            db,
            proposed,
            fingerprint,
        )


def ensure_identity(proposed):
    """Create or reuse a recording UID without requiring a fingerprint."""
    check_identity(proposed)

    with connection(create=True) as db:
        if db is None:
            raise RuntimeError(
                "Identity index could not be opened."
            )

        db.execute("BEGIN IMMEDIATE")
        try:
            resolved = _resolve_declared_identity_db(
                db,
                proposed,
            )
            identity = merge_identity(
                resolved if resolved.uid else None,
                proposed,
            )
            db.execute(
                """
                INSERT INTO recordings(uid, spotify_id, tidal_id)
                VALUES(?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    spotify_id=excluded.spotify_id,
                    tidal_id=excluded.tidal_id
                """,
                (
                    identity.uid,
                    identity.spotify_id,
                    identity.tidal_id,
                ),
            )
            db.commit()
            return identity
        except Exception:
            db.rollback()
            raise


def register(
    path,
    fingerprint,
    identity,
    expected_signature,
):
    """Associate one exact content fingerprint with a durable recording UID."""
    if signature(path) != expected_signature:
        raise IdentityConflict(
            "File changed before identity-index commit; scan again."
        )

    with connection(create=True) as db:
        if db is None:
            raise RuntimeError(
                "Identity index could not be opened."
            )

        db.execute("BEGIN IMMEDIATE")
        try:
            resolved = _resolve_declared_identity_db(
                db,
                identity,
                fingerprint,
            )
            merged = merge_identity(
                resolved if resolved.uid else None,
                identity,
            )

            db.execute(
                """
                INSERT INTO recordings(uid, spotify_id, tidal_id)
                VALUES(?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    spotify_id=excluded.spotify_id,
                    tidal_id=excluded.tidal_id
                """,
                (
                    merged.uid,
                    merged.spotify_id,
                    merged.tidal_id,
                ),
            )

            mapped = db.execute(
                "SELECT uid FROM fingerprints "
                "WHERE fingerprint=?",
                (fingerprint,),
            ).fetchone()
            if mapped and mapped[0] != merged.uid:
                raise IdentityConflict(
                    "Content fingerprint is already associated "
                    "with a different recording UID."
                )

            db.execute(
                "INSERT OR IGNORE INTO fingerprints"
                "(fingerprint, uid) VALUES(?,?)",
                (fingerprint, merged.uid),
            )
            db.execute(
                "INSERT OR REPLACE INTO files"
                "(path, signature, fingerprint) VALUES(?,?,?)",
                (
                    path_key(path),
                    json.dumps(expected_signature),
                    fingerprint,
                ),
            )

            if signature(path) != expected_signature:
                raise IdentityConflict(
                    "File changed during identity-index commit; scan again."
                )

            db.commit()
            logger.debug(
                "IDENTITY_INDEX_REGISTER "
                "path=%r fingerprint=%s uid=%s",
                path,
                fingerprint,
                merged.uid,
            )
            return merged
        except Exception:
            db.rollback()
            raise


CURRENT_FINGERPRINT_PREFIXES = (
    "flac-streaminfo-md5-v1:",
    "encoded-audio-sha256-v1:",
)


def fingerprint_is_current(fingerprint):
    return bool(
        fingerprint
        and fingerprint.startswith(
            CURRENT_FINGERPRINT_PREFIXES
        )
    )


def fingerprint_kind(fingerprint):
    if fingerprint.startswith(
        "flac-streaminfo-md5-v1:"
    ):
        return "flac-streaminfo-md5"
    if fingerprint.startswith(
        "encoded-audio-sha256-v1:"
    ):
        return "encoded-audio-sha256"
    if fingerprint.startswith(
        "pcm-s32le-v1:"
    ):
        return "legacy-decoded-pcm-sha256"
    return "unknown"


def audio_fingerprint(
    path,
    cancelled=lambda: False,
    use_cache=True,
):
    """Return an exact audio-content identity without decoding by default.

    FLAC uses the STREAMINFO decoded-PCM MD5. Other supported containers use
    FFmpeg stream-copy hashing over demuxed encoded audio packets so mutable
    tags, artwork and filenames do not participate in the fingerprint.
    """
    if cancelled():
        raise IdentityCancelled()

    if use_cache:
        cached = cached_fingerprint(path)
        if cached and fingerprint_is_current(cached):
            logger.debug(
                "IDENTITY_FINGERPRINT_CACHE_HIT "
                "path=%r kind=%s",
                path,
                fingerprint_kind(cached),
            )
            return cached
        if cached:
            logger.debug(
                "IDENTITY_FINGERPRINT_LEGACY_CACHE "
                "path=%r kind=%s",
                path,
                fingerprint_kind(cached),
            )

    started = time.monotonic()
    before = signature(path)
    audio = MutagenFile(path)

    if audio is None or not getattr(
        audio,
        "info",
        None,
    ):
        raise ValueError(
            "Unsupported or unreadable audio file."
        )

    info = audio.info
    rate = int(
        getattr(info, "sample_rate", 0) or 0
    )
    channels = int(
        getattr(info, "channels", 0) or 0
    )

    if rate <= 0 or channels <= 0:
        raise ValueError(
            "Audio stream has no valid "
            "sample-rate/channel information."
        )

    fingerprint = ""

    if isinstance(audio, FLAC):
        md5_value = int(
            getattr(info, "md5_signature", 0) or 0
        )
        bits = int(
            getattr(info, "bits_per_sample", 0) or 0
        )
        total_samples = int(
            getattr(info, "total_samples", 0) or 0
        )

        if (
            md5_value
            and bits > 0
            and total_samples > 0
        ):
            fingerprint = (
                "flac-streaminfo-md5-v1:"
                f"{rate}:{channels}:{bits}:"
                f"{total_samples}:"
                f"{md5_value:032x}"
            )

    if not fingerprint:
        extension = os.path.splitext(
            path
        )[1].lower()
        if extension == ".mp3":
            family = "mp3"
        elif extension in {".m4a", ".mp4"}:
            family = "mp4-audio"
        elif extension == ".flac":
            family = "flac-encoded"
        else:
            family = (
                extension.lstrip(".")
                or type(audio).__name__.lower()
            )

        command = [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-c:a",
            "copy",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ]

        logger.debug(
            "IDENTITY_FINGERPRINT_STREAMCOPY_START "
            "path=%r family=%s",
            path,
            family,
        )

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        while process.poll() is None:
            if cancelled():
                process.kill()
                process.wait()
                raise IdentityCancelled()
            time.sleep(0.05)

        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                "FFmpeg encoded-audio fingerprint failed: "
                + stderr.strip()
            )

        match = re.search(
            r"SHA256=([0-9a-fA-F]{64})",
            stdout or "",
        )
        if match is None:
            raise RuntimeError(
                "FFmpeg did not return a valid "
                "SHA-256 audio hash."
            )

        fingerprint = (
            "encoded-audio-sha256-v1:"
            f"{family}:{rate}:{channels}:"
            f"{match.group(1).lower()}"
        )

    if signature(path) != before:
        raise IdentityConflict(
            "File changed while its audio "
            "fingerprint was being computed."
        )

    if use_cache:
        try:
            cache_fingerprint(
                path,
                fingerprint,
                before,
            )
        except IdentityConflict:
            raise
        except Exception as exc:
            logger.warning(
                "IDENTITY_FINGERPRINT_CACHE_WRITE_FAILED "
                "path=%r error=%s",
                path,
                exc,
                exc_info=logger.isEnabledFor(
                    logging.DEBUG
                ),
            )

    logger.debug(
        "IDENTITY_FINGERPRINT_DONE "
        "path=%r kind=%s elapsed=%.3fs",
        path,
        fingerprint_kind(fingerprint),
        time.monotonic() - started,
    )
    return fingerprint


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
