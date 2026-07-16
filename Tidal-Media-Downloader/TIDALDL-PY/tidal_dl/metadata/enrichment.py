from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Iterable, Optional

from .album import extract_label_from_copyright

logger = logging.getLogger(__name__)

MISSING_METADATA_TEXT = "-"

SPOTIFY_PITCH_CLASSES = (
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
)

_CAMELOT_KEYS: Dict[tuple[str, str], str] = {
    ("B", "major"): "1B",
    ("F#", "major"): "2B",
    ("Gb", "major"): "2B",
    ("Db", "major"): "3B",
    ("C#", "major"): "3B",
    ("Ab", "major"): "4B",
    ("G#", "major"): "4B",
    ("Eb", "major"): "5B",
    ("D#", "major"): "5B",
    ("Bb", "major"): "6B",
    ("A#", "major"): "6B",
    ("F", "major"): "7B",
    ("C", "major"): "8B",
    ("G", "major"): "9B",
    ("D", "major"): "10B",
    ("A", "major"): "11B",
    ("E", "major"): "12B",
    ("Ab", "minor"): "1A",
    ("G#", "minor"): "1A",
    ("Eb", "minor"): "2A",
    ("D#", "minor"): "2A",
    ("Bb", "minor"): "3A",
    ("A#", "minor"): "3A",
    ("F", "minor"): "4A",
    ("C", "minor"): "5A",
    ("G", "minor"): "6A",
    ("D", "minor"): "7A",
    ("A", "minor"): "8A",
    ("E", "minor"): "9A",
    ("B", "minor"): "10A",
    ("F#", "minor"): "11A",
    ("Gb", "minor"): "11A",
    ("C#", "minor"): "12A",
    ("Db", "minor"): "12A",
}

_NOTE_ALIASES: Dict[str, str] = {
    "C": "C",
    "C SHARP": "C#",
    "C#": "C#",
    "D FLAT": "Db",
    "DB": "Db",
    "D": "D",
    "D SHARP": "D#",
    "D#": "D#",
    "E FLAT": "Eb",
    "EB": "Eb",
    "E": "E",
    "F": "F",
    "F SHARP": "F#",
    "F#": "F#",
    "G FLAT": "Gb",
    "GB": "Gb",
    "G": "G",
    "G SHARP": "G#",
    "G#": "G#",
    "A FLAT": "Ab",
    "AB": "Ab",
    "A": "A",
    "A SHARP": "A#",
    "A#": "A#",
    "B FLAT": "Bb",
    "BB": "Bb",
    "B": "B",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "null", "unknown", "unknown year"}:
        return ""
    return text


def _get_attr(obj: Any, attr: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(attr)
    return getattr(obj, attr, None)


def _iter_name_values(value: Any) -> Iterable[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = _clean_text(value)
        return [cleaned] if cleaned else []
    if isinstance(value, dict):
        for key in ("name", "title", "value"):
            cleaned = _clean_text(value.get(key))
            if cleaned:
                return [cleaned]
        return []
    if isinstance(value, (list, tuple, set)):
        names: list[str] = []
        for item in value:
            names.extend(_iter_name_values(item))
        return names

    cleaned = _clean_text(getattr(value, "name", None)) or _clean_text(
        getattr(value, "title", None)
    )
    return [cleaned] if cleaned else []


def _first_clean_value(*values: Any) -> str:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return ""


def _extract_year(value: Any) -> str:
    text = _clean_text(value)
    if len(text) >= 4 and text[:4].isdigit():
        return text[:4]
    return ""


def normalize_key_name(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""

    normalized = (
        text.replace("♯", "#")
        .replace("♭", "b")
        .replace("_", " ")
        .replace("-", " ")
    )
    normalized = " ".join(normalized.split()).upper().strip()
    normalized = re.sub(r"\b(MAJOR|MINOR|MAJ|MIN)\b", " ", normalized)
    normalized = re.sub(r"(MAJOR|MINOR|MAJ|MIN)$", "", normalized)
    normalized = " ".join(normalized.split()).strip()
    normalized = re.sub(r"^([A-G](?:#|B)?)M$", r"\1", normalized)

    match = re.match(r"^([A-G])(?:\s*|_|-)?(SHARP|FLAT)$", normalized)
    if match:
        letter = match.group(1)
        modifier = match.group(2)
        normalized = f"{letter} {'SHARP' if modifier == 'SHARP' else 'FLAT'}"

    return _NOTE_ALIASES.get(normalized, text.strip())


def normalize_key_scale(value: Any) -> str:
    text = _clean_text(value).lower()
    if not text:
        return ""

    normalized_text = (
        text.replace("♯", "#")
        .replace("♭", "b")
        .replace("_", " ")
        .replace("-", " ")
    )

    if "minor" in normalized_text or re.search(r"\bmin\b", normalized_text):
        return "minor"
    if "major" in normalized_text or re.search(r"\bmaj\b", normalized_text):
        return "major"

    compact = "".join(normalized_text.split())
    if compact in {"m", "min"} or re.match(r"^[a-g](?:#|b)?m$", compact):
        return "minor"
    if compact in {"maj", "major"} or re.match(r"^[a-g](?:#|b)?maj$", compact):
        return "major"

    parts = normalized_text.split()
    if parts:
        tail = parts[-1]
        if tail in {"min", "m"}:
            return "minor"
        if tail == "maj":
            return "major"
    return normalized_text.strip()


def format_track_key(track: Any, *, use_camelot_key: bool = False) -> str:
    raw_key_text = _clean_text(_get_attr(track, "key"))
    key = normalize_key_name(_get_attr(track, "key"))
    scale = normalize_key_scale(_get_attr(track, "keyScale"))
    if not scale:
        scale = normalize_key_scale(raw_key_text)
    if not key:
        return MISSING_METADATA_TEXT

    if use_camelot_key and scale:
        camelot_key = _CAMELOT_KEYS.get((key, scale))
        if camelot_key:
            return camelot_key

    if scale:
        return f"{key} {scale}"
    return key


def format_spotify_key(
    key_number: int | None,
    mode: int | None,
    *,
    use_camelot_key: bool = False,
) -> str:
    """Format Spotify Audio Features key/mode values for display or tagging."""
    try:
        key_int = int(key_number) if key_number is not None else -1
    except (TypeError, ValueError):
        return MISSING_METADATA_TEXT

    if key_int < 0 or key_int >= len(SPOTIFY_PITCH_CLASSES):
        return MISSING_METADATA_TEXT

    try:
        mode_int = int(mode) if mode is not None else None
    except (TypeError, ValueError):
        mode_int = None

    if mode_int not in (0, 1):
        return MISSING_METADATA_TEXT

    key = SPOTIFY_PITCH_CLASSES[key_int]
    scale = "major" if mode_int == 1 else "minor"

    if use_camelot_key:
        camelot_key = _CAMELOT_KEYS.get((key, scale))
        if camelot_key:
            return camelot_key

    return f"{key} {scale}"


def format_bpm(track: Any) -> str:
    bpm = _get_attr(track, "bpm")
    if bpm in (None, "", 0, "0"):
        return MISSING_METADATA_TEXT

    try:
        bpm_number = float(bpm)
    except (TypeError, ValueError):
        return _clean_text(bpm) or MISSING_METADATA_TEXT

    if bpm_number <= 0:
        return MISSING_METADATA_TEXT
    if bpm_number.is_integer():
        return str(int(bpm_number))
    return f"{bpm_number:.1f}".rstrip("0").rstrip(".")


def extract_release_year(track: Any) -> str:
    album = _get_attr(track, "album")
    for candidate in (
        _get_attr(album, "releaseDate"),
        _get_attr(album, "streamStartDate"),
        _get_attr(album, "tidalReleaseDate"),
        _get_attr(track, "releaseDate"),
        _get_attr(track, "streamStartDate"),
        _get_attr(track, "tidalReleaseDate"),
    ):
        year = _extract_year(candidate)
        if year:
            return year
    return MISSING_METADATA_TEXT


def extract_label(track: Any) -> str:
    album = _get_attr(track, "album")
    for candidate in (
        _get_attr(album, "copyright"),
        _get_attr(album, "copyRight"),
        _get_attr(track, "copyright"),
        _get_attr(track, "copyRight"),
    ):
        label = extract_label_from_copyright(_clean_text(candidate))
        if label:
            return label
    return MISSING_METADATA_TEXT


def extract_genre(track: Any) -> str:
    album = _get_attr(track, "album")
    media_metadata = _get_attr(track, "mediaMetadata")

    candidates = [
        _get_attr(track, "genres"),
        _get_attr(track, "genre"),
        _get_attr(album, "genres"),
        _get_attr(album, "genre"),
    ]
    if isinstance(media_metadata, dict):
        candidates.extend([media_metadata.get("genres"), media_metadata.get("genre")])

    for candidate in candidates:
        names = [name for name in _iter_name_values(candidate) if name]
        if names:
            return ", ".join(dict.fromkeys(names))
    return MISSING_METADATA_TEXT


def get_track_display_metadata(
    track: Any,
    *,
    use_camelot_key: bool = False,
) -> Dict[str, str]:
    if track is None:
        return {
            "release_year": MISSING_METADATA_TEXT,
            "bpm": MISSING_METADATA_TEXT,
            "key": MISSING_METADATA_TEXT,
            "genre": MISSING_METADATA_TEXT,
            "label": MISSING_METADATA_TEXT,
        }

    return {
        "release_year": extract_release_year(track),
        "bpm": format_bpm(track),
        "key": format_track_key(track, use_camelot_key=use_camelot_key),
        "genre": extract_genre(track),
        "label": extract_label(track),
    }


def enrich_track_album_metadata(
    track: Any,
    get_album_by_id: Callable[[str], Any],
) -> Any:
    """Fetch full album metadata only when release year or copyright-derived label need it."""
    if track is None:
        return track

    album = _get_attr(track, "album")
    album_id = _get_attr(album, "id")
    if not album_id:
        return track

    has_release_date = bool(_clean_text(_get_attr(album, "releaseDate")))
    has_copyright = bool(
        _first_clean_value(
            _get_attr(album, "copyright"),
            _get_attr(album, "copyRight"),
            _get_attr(track, "copyright"),
            _get_attr(track, "copyRight"),
        )
    )

    if has_release_date and has_copyright:
        return track

    try:
        full_album = get_album_by_id(str(album_id))
    except Exception as exc:
        logger.debug(
            "Failed to enrich TIDAL album metadata for album %s: %s",
            album_id,
            exc,
            exc_info=True,
        )
        return track

    if full_album is not None:
        try:
            setattr(track, "album", full_album)
        except Exception:
            logger.debug(
                "Failed to attach enriched album metadata to track %s",
                _get_attr(track, "id"),
                exc_info=True,
            )
    return track
