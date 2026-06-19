# tidal_dl/linking.py

import concurrent.futures
import logging
import re
import threading
import unicodedata
import difflib  # Added for similarity checking
from typing import Any, Dict, List, Optional, Set, Tuple, Union, cast

import aigpy
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from .enums import Type
from .model import Artist, SearchResult, Track
from .tidal import TidalAPI
from tidal_dl.gui.gui_logging import setup_gui_logger

# Set up GUI logging with WARNING level for this module.
# Detailed per-candidate traces remain debug-only, but compact diagnostics must be visible.
setup_gui_logger(__name__, logging.WARNING)

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# Assuming aigpy is available
try:
    from aigpy.modelHelper import dictToModel as aigpy_dictToModel
except ImportError:
    logger.warning(
        "aigpy.modelHelper could not be imported. Model casting might fail."
    )

    def aigpy_dictToModel(indict: Any, model: Any) -> Any:
        return model  # Fallback for aigpy function

def normalize_title(title: Optional[str]) -> str:
    """
    Normalizes a title for SEARCH QUERY generation.
    Removes brackets and common suffixes to broaden search results.
    """
    if not title:
        return ""
    # Remove common tags, version info, brackets, hyphens used for separation, and extra whitespace
    normalized = title.lower()
    # Remove content within brackets/parentheses (e.g., (Remix), [Live])
    normalized = re.sub(r"[\(\[].*?[\)\]]", "", normalized)
    # Remove common suffixes like - In the Radio Mix, - 7" Radio Edit, - Edit, - Remix, etc.
    version_suffix_pattern = (
        r"\s*[-–—]\s*"
        r"(?:\d+\s*(?:\"|″|inch|inches|in)\s*)?"
        r"(?:(?:in|on|the|a|an)\s+){0,4}"
        r"(?:radio\s+edit|radio\s+version|radio\s+mix|single\s+edit|"
        r"extended\s+mix|instrumental|edit|version|mix|live)"
        r"\b.*$"
    )
    normalized = re.sub(version_suffix_pattern, "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"\s-\s(edit|remix|live|version|mix|radio edit|extended mix|instrumental)\b",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    # Remove leading/trailing whitespace and reduce multiple spaces to one
    normalized = " ".join(normalized.strip().split())
    return normalized

def fuzzy_normalize(text: Optional[str]) -> str:
    """
    Normalizes a string for COMPARISON/SCORING.
    Aggressively strips accents, punctuation, and whitespace to match content.
    Example: "Title (Remix)" == "Title - Remix" -> "titleremix"
    """
    if not text:
        return ""
    # Normalize unicode characters (e.g. accents) to ASCII equivalent
    text = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
    # Lowercase
    text = text.lower()
    # Remove all non-alphanumeric characters (a-z, 0-9)
    text = re.sub(r'[^a-z0-9]', '', text)
    return text


def _version_marker_categories(title: Optional[str]) -> set[str]:
    """
    Return broad mix/version categories present in a title.

    Used for matching logic only. Search normalization stays intentionally broad,
    but scoring must still distinguish an original track from a remix when the
    Spotify title itself is version-neutral.
    """
    if not title:
        return set()

    normalized = title.lower()
    categories: set[str] = set()

    if re.search(r"\boriginal\s+(mix|version|edit)\b|\boriginal\b", normalized):
        categories.add("original")
    if re.search(r"\bremix\b|\brework\b|\brmx\b", normalized):
        categories.add("remix")
    if re.search(r"\bradio\s+edit\b|\bedit\b", normalized):
        categories.add("edit")
    if re.search(r"\bextended\s+mix\b|\bclub\s+mix\b", normalized):
        categories.add("club_or_extended")
    if re.search(r"\bdub\b|\binstrumental\b|\bbootleg\b|\bvip\b", normalized):
        categories.add("alternate")
    if re.search(r"\bremaster(?:ed)?\b", normalized):
        categories.add("remaster")

    return categories


def _version_mismatch_penalty(
    spotify_title: Optional[str],
    tidal_title: Optional[str],
) -> Tuple[int, Optional[str]]:
    """
    Penalize candidate versions that conflict with the Spotify title.

    A Spotify title without a version marker should prefer plain/original mixes
    over remixes. This prevents a lone remix result from looking equally plausible
    when the original mix exists elsewhere in TIDAL search.
    """
    spotify_markers = _version_marker_categories(spotify_title)
    tidal_markers = _version_marker_categories(tidal_title)

    if not tidal_markers:
        return 0, None

    if not spotify_markers:
        if tidal_markers == {"original"} or "original" in tidal_markers:
            return 0, None
        return 3, "Version Mismatch"

    if spotify_markers.isdisjoint(tidal_markers):
        return 3, "Version Mismatch"

    return 0, None


def _track_diag_summary(track: Optional[Track]) -> Dict[str, Any]:
    """Return a compact, log-safe summary of a TIDAL track candidate."""
    if not track:
        return {}

    artist_names: List[str] = []
    raw_artists = getattr(track, "artists", None)
    if isinstance(raw_artists, list):
        for artist in raw_artists:
            artist_name = getattr(artist, "name", None)
            if artist_name:
                artist_names.append(str(artist_name))

    album_obj = getattr(track, "album", None)
    return {
        "id": getattr(track, "id", None),
        "title": getattr(track, "title", None),
        "artists": artist_names,
        "album": getattr(album_obj, "title", None),
        "duration": getattr(track, "duration", None),
        "isrc": getattr(track, "isrc", None),
    }


def _metadata_compare_key(value: Optional[Any]) -> str:
    if value is None:
        return ""

    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = normalized.replace("\u00a0", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold()


def _duration_second_options(
    value: Optional[Union[int, float, str]],
    preferred_unit: str,
) -> List[float]:
    if value is None:
        return []

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return []

    if numeric_value <= 0:
        return []

    options: List[float] = []

    def add_option(seconds: float) -> None:
        if seconds <= 0 or seconds > 86400:
            return
        if not any(abs(seconds - existing) < 0.001 for existing in options):
            options.append(seconds)

    if preferred_unit == "milliseconds":
        add_option(numeric_value / 1000.0)
        add_option(numeric_value)
    else:
        add_option(numeric_value)
        add_option(numeric_value / 1000.0)

    return options


def _best_duration_comparison(
    spotify_duration_ms: Optional[Union[int, float, str]],
    tidal_duration: Optional[Union[int, float, str]],
) -> Optional[Tuple[float, float, float]]:
    spotify_options = _duration_second_options(
        spotify_duration_ms,
        preferred_unit="milliseconds",
    )
    tidal_options = _duration_second_options(
        tidal_duration,
        preferred_unit="seconds",
    )

    if not spotify_options or not tidal_options:
        return None

    best_result: Optional[Tuple[float, float, float]] = None
    for spotify_seconds in spotify_options:
        for tidal_seconds in tidal_options:
            duration_diff = abs(tidal_seconds - spotify_seconds)
            if best_result is None or duration_diff < best_result[0]:
                best_result = (duration_diff, spotify_seconds, tidal_seconds)

    return best_result


TITLE_SIMILARITY_REVIEW_THRESHOLD = 0.72
MIN_TITLE_TOKEN_OVERLAP = 0.60
MAX_AUTO_LINK_CANDIDATE_SCORE = 4
MAX_RETURNED_REVIEW_CANDIDATE_SCORE = 12
MAX_REVIEW_CANDIDATE_SCORE = MAX_AUTO_LINK_CANDIDATE_SCORE
EMIT_LINKING_METADATA_DIAG = False

MAX_METADATA_EXTRA_QUERIES = 10
METADATA_EXTRA_QUERY_PAGE_OFFSETS = (0,)
MAX_COMPACT_DIAG_QUERY_SAMPLE = 6
MAX_COMPACT_DIAG_REJECTED_SAMPLE = 5

TEXT_HARD_REJECT_SIGNAL_THRESHOLD = 0.25
TITLE_EXCELLENT_SIGNAL_THRESHOLD = 0.90
TITLE_REVIEW_SIGNAL_THRESHOLD = 0.50
ARTIST_EXCELLENT_SIGNAL_THRESHOLD = 0.78
ARTIST_REVIEW_SIGNAL_THRESHOLD = 0.55
TOKEN_OVERLAP_BONUS_WEIGHT = 0.08

_TITLE_TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "or",
    "feat",
    "ft",
    "featuring",
    "with",
}


def _title_tokens_for_matching(text: Optional[str]) -> set[str]:
    normalized = normalize_title(text)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower())
    return {
        token
        for token in normalized.split()
        if token and token not in _TITLE_TOKEN_STOPWORDS
    }


def _title_token_overlap_ratio(
    spotify_title: Optional[str],
    tidal_title: Optional[str],
) -> float:
    spotify_tokens = _title_tokens_for_matching(spotify_title)
    tidal_tokens = _title_tokens_for_matching(tidal_title)

    if not spotify_tokens or not tidal_tokens:
        return 0.0

    return len(spotify_tokens & tidal_tokens) / len(spotify_tokens)


def _similarity_tokens(text: Optional[str]) -> set[str]:
    normalized = normalize_title(text)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower())
    return {
        token
        for token in normalized.split()
        if token and token not in _TITLE_TOKEN_STOPWORDS
    }


def _symmetric_token_overlap_ratio(left: Optional[str], right: Optional[str]) -> float:
    left_tokens = _similarity_tokens(left)
    right_tokens = _similarity_tokens(right)

    if not left_tokens or not right_tokens:
        return 0.0

    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def _similarity_signal(left: Optional[str], right: Optional[str]) -> float:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()

    if not left_text or not right_text:
        return 1.0

    left_norm = normalize_title(left_text)
    right_norm = normalize_title(right_text)
    left_fuzzy = fuzzy_normalize(left_text)
    right_fuzzy = fuzzy_normalize(right_text)

    if left_fuzzy and right_fuzzy:
        if left_fuzzy == right_fuzzy or left_fuzzy in right_fuzzy or right_fuzzy in left_fuzzy:
            return 1.0

    character_ratio = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
    fuzzy_ratio = difflib.SequenceMatcher(None, left_fuzzy, right_fuzzy).ratio()
    token_overlap = _symmetric_token_overlap_ratio(left_text, right_text)

    return min(
        1.0,
        max(character_ratio, fuzzy_ratio, token_overlap)
        + (token_overlap * TOKEN_OVERLAP_BONUS_WEIGHT),
    )


def _best_similarity_signal(
    source_values: List[str],
    target_values: List[str],
) -> float:
    source_clean = [str(value or "").strip() for value in source_values if str(value or "").strip()]
    target_clean = [str(value or "").strip() for value in target_values if str(value or "").strip()]

    if not source_clean or not target_clean:
        return 1.0

    return max(
        _similarity_signal(source_value, target_value)
        for source_value in source_clean
        for target_value in target_clean
    )


def _metadata_query_text(text: Optional[str]) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"[’`]", "'", value)
    value = re.sub(r"[\"“”″]", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _strip_trailing_version_descriptor(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""

    version_descriptor = (
        r"(?:\d+\s*(?:\"|″|inch|inches|in)\s*)?"
        r"(?:(?:in|on|the|a|an)\s+){0,4}"
        r"(?:radio\s+edit|radio\s+version|radio\s+mix|single\s+edit|"
        r"extended\s+mix|instrumental|edit|version|mix|live)"
        r"\b.*"
    )
    value = re.sub(
        rf"\s*[-–—]\s*{version_descriptor}\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        rf"\s*[\[(]\s*{version_descriptor}\s*[\])]\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def _remove_format_size_from_title(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""

    value = re.sub(
        r"\b\d+\s*(?:\"|″|inch|inches|in)\s+",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def _clean_title_query_variants(title: Optional[str]) -> List[str]:
    raw_title = str(title or "").strip()
    if not raw_title:
        return []

    candidates = [
        _strip_trailing_version_descriptor(raw_title),
        _remove_format_size_from_title(raw_title),
        raw_title,
        normalize_title(raw_title),
    ]

    variants: List[str] = []
    seen: Set[str] = set()
    for candidate in candidates:
        query_text = _metadata_query_text(candidate)
        if not query_text or query_text in seen:
            continue
        seen.add(query_text)
        variants.append(query_text)

    return variants


def _title_penalty_from_signal(signal: float) -> int:
    if signal >= TITLE_EXCELLENT_SIGNAL_THRESHOLD:
        return 0
    if signal >= TITLE_REVIEW_SIGNAL_THRESHOLD:
        return 1
    return 3


def _artist_penalty_from_signal(signal: float) -> int:
    if signal >= ARTIST_EXCELLENT_SIGNAL_THRESHOLD:
        return 0
    if signal >= ARTIST_REVIEW_SIGNAL_THRESHOLD:
        return 2
    return 4


def _should_hard_reject_candidate(title_signal: float, artist_signal: float) -> bool:
    return (
        title_signal < TEXT_HARD_REJECT_SIGNAL_THRESHOLD
        and artist_signal < TEXT_HARD_REJECT_SIGNAL_THRESHOLD
    )


def _titles_are_review_compatible(
    spotify_title: Optional[str],
    tidal_title: Optional[str],
) -> Tuple[bool, Optional[float], float]:
    spotify_norm = normalize_title(spotify_title)
    tidal_norm = normalize_title(tidal_title)

    if not spotify_norm or not tidal_norm:
        return True, None, 1.0

    if spotify_norm in tidal_norm or tidal_norm in spotify_norm:
        return True, 1.0, 1.0

    similarity_ratio = difflib.SequenceMatcher(
        None,
        spotify_norm,
        tidal_norm,
    ).ratio()
    token_overlap = _title_token_overlap_ratio(spotify_title, tidal_title)

    is_compatible = (
        similarity_ratio >= TITLE_SIMILARITY_REVIEW_THRESHOLD
        and token_overlap >= MIN_TITLE_TOKEN_OVERLAP
    )

    return is_compatible, similarity_ratio, token_overlap


def _search_result_diag_summary(
    result: Optional[SearchResult],
    source_query: str,
    limit: int = 8,
) -> Dict[str, Any]:
    """Return a compact summary of a TIDAL search result for matching diagnostics."""
    items: List[Track] = []
    if (
        result
        and result.tracks
        and isinstance(result.tracks.items, list)
    ):
        items = result.tracks.items

    return {
        "query": source_query,
        "count": len(items),
        "top": [_track_diag_summary(track) for track in items[:limit]],
    }


def searchLinkTrack(
    api: TidalAPI,
    title: str,
    artists: List[str],
    album: Optional[str],
    isrc: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> Tuple[
    Optional[Track], Optional[List[Dict[str, Any]]], Optional[int]
]:  # Add Optional[int] for score
    """
    Searches Tidal for a track based on Spotify metadata, prioritizing ISRC.
    Requires an initialized TidalAPI instance.
    Returns the best matching Tidal Track object or None.
    """
    # Buffer for ALL debug messages related to this specific track attempt
    debug_buffer: List[str] = []
    debug_buffer.append(
        f"Attempting to link Spotify track: Title='{title}', Artists='{artists}', Album='{album}', ISRC='{isrc}', DurationMs='{duration_ms}'"
    )

    best_match: Optional[Track] = None
    min_score = float("inf")  # Lower score is better
    is_isrc_match = False  # Flag to track if found via ISRC

    # 1. Try searching by ISRC (most reliable)
    if isrc:
        try:
            debug_buffer.append(f"Searching Tidal by ISRC: {isrc}")  # Add to buffer
            # Use the passed-in api instance to call search
            isrc_query = f'isrc:"{isrc}"'
            # Cast the result to help Pylance understand the type when return_raw=False
            search_result_isrc = cast(
                Optional[SearchResult], api.search(isrc_query, Type.Track, limit=5)
            )
            # search_result_isrc is SearchResult | None, not a tuple here
            if (
                search_result_isrc
                and search_result_isrc.tracks
                and search_result_isrc.tracks.items
            ):
                items_list_raw = search_result_isrc.tracks.items
                items_list: List[Track] = []

                if isinstance(items_list_raw, Track):
                    items_list = [items_list_raw]
                elif isinstance(items_list_raw, list):
                    items_list = items_list_raw

                # Ensure items_list is iterable (it should be List[Track])
                for item in items_list:
                    track: Track = item
                    # Direct ISRC match is usually sufficient
                    # Compare case-insensitively and handle potential None
                    tidal_isrc = getattr(track, "isrc", None)
                    if tidal_isrc and isrc.lower() == tidal_isrc.lower():
                        # Don't log INFO yet, just record the potential match
                        debug_buffer.append(
                            f"Found potential match via ISRC: Tidal ID {track.id} ('{track.title}')"
                        )
                        # Basic duration check if available
                        duration_comparison = _best_duration_comparison(
                            duration_ms,
                            getattr(track, "duration", None),
                        )
                        if duration_comparison is not None:
                            (
                                duration_diff,
                                spotify_duration_s,
                                tidal_duration_s,
                            ) = duration_comparison
                            if duration_diff <= 5:  # Allow 5s difference
                                debug_buffer.append(
                                    f"ISRC match confirmed by duration ({duration_diff:.2f}s diff)."
                                )
                                # Mark as ISRC match and break search; fetch full track later
                                best_match = track
                                min_score = 0  # ISRC match is score 0
                                is_isrc_match = True
                                break  # Found best possible match via ISRC+duration
                            else:
                                # Log duration mismatch to buffer, but don't necessarily skip yet
                                debug_buffer.append(
                                    f"ISRC match {track.id} duration mismatch ({duration_diff:.2f}s diff). "
                                    f"Spotify: {spotify_duration_s:.2f}s, Tidal: {tidal_duration_s:.2f}s. "
                                    f"Raw Spotify duration_ms: {duration_ms}, raw Tidal duration: {getattr(track, 'duration', None)}."
                                )
                        else:
                            debug_buffer.append(
                                f"ISRC match found, duration check not possible."
                            )
                            # Mark as ISRC match and break search; fetch full track later
                            best_match = track
                            min_score = 0  # ISRC match is score 0
                            is_isrc_match = True
                            break  # Found best possible match via ISRC

                # If an ISRC match was found in the loop, break out of the outer loop too
                if is_isrc_match:
                    # Fetch the full track data here since we confirmed the ISRC match
                    # Add check for best_match not being None
                    # Correct indentation and add None check for id
                    if best_match and best_match.id is not None:
                        logger.info(
                            f"Confident ISRC match found: Tidal ID {best_match.id} ('{best_match.title}'). Fetching full track data."
                        )
                        # Emit buffer before returning
                        logger.debug(f"--- Emitting buffered logs for ISRC match ---")
                        for msg in debug_buffer:
                            logger.debug(msg)
                        logger.debug(f"--- End buffered logs ---")
                        # Fetch full track data, ensuring ID is str
                        full_track = api.getTrack(
                            str(best_match.id), suppress_debug_prints=True
                        )
                        return full_track, None, 0  # ISRC match has score 0
                    else:
                        # Handle case where best_match or its id is None despite is_isrc_match flag
                        logger.error(
                            f"ISRC match flag set, but best_match ({best_match}) or best_match.id is None. Cannot fetch full track."
                        )
                        # Emit buffer before returning
                        logger.debug(
                            f"--- Emitting buffered logs for ISRC match failure ---"
                        )
                        for msg in debug_buffer:
                            logger.debug(msg)
                        logger.debug(f"--- End buffered logs ---")
                        return None, None, None

        except Exception as e:
            logger.error(
                f"Error during Tidal ISRC search for '{isrc}': {e}", exc_info=True
            )
            # Emit buffer on exception
            logger.debug(
                f"--- Emitting buffered logs due to exception during ISRC search ---"
            )
            for msg in debug_buffer:
                logger.debug(msg)
            logger.debug(f"--- End buffered logs ---")
            # Continue to metadata search even if ISRC search fails

    # 2. If ISRC search didn't find a match OR no ISRC provided, search by metadata
    # Reset buffer for metadata search phase if ISRC search was attempted but failed
    if not is_isrc_match:
        debug_buffer = []  # Clear buffer for metadata search details
        debug_buffer.append(
            f"Attempting metadata link for Spotify track: Title='{title}', Artists='{artists}', Album='{album}', ISRC='{isrc}', DurationMs='{duration_ms}'"
        )
        if isrc:
            debug_buffer.append(
                f" (ISRC search for '{isrc}' did not yield a confident match)"
            )
        else:
            debug_buffer.append(f" (No ISRC provided)")

        try:
            # Construct a reasonably specific query
            primary_artist = artists[0] if artists else ""
            # Use the NORMALIZED title for the search query to broaden matching
            query = f"{normalize_title(title)} {primary_artist}"
            # Optionally add album for more specificity if needed, but can sometimes be too restrictive
            # query = f"{normalize_title(title)} {primary_artist} {album}"
            debug_buffer.append(
                f"Searching Tidal by metadata query: '{query}' (using normalized title)"
            )  # Add to buffer
            # Use the passed-in api instance to call search, requesting raw response
            debug_buffer.append(
                f"Constructed metadata query: '{query}'"
            )  # ADDED FOR DEBUGGING
            search_tuple = api.search(query, Type.Track, limit=25, return_raw=True)
            search_queries_attempted: List[str] = [query]
            # When return_raw=True, search returns a tuple (SearchResult, str)
            # Correctly unpack the tuple returned when return_raw=True
            search_result: Optional[SearchResult] = None
            raw_api_response: Optional[str] = None
            if isinstance(search_tuple, tuple) and len(search_tuple) == 2:
                search_result = search_tuple[0]
                raw_api_response = search_tuple[1]
            elif search_tuple:  # Handle unexpected return type
                search_result = search_tuple
                logger.warning(
                    f"api.search with return_raw=True returned unexpected type: {type(search_tuple)}"
                )

            debug_buffer.append(
                f"Raw API response (metadata): {raw_api_response}"
            )  # ADDED FOR DEBUGGING
            # Determine number of items found (0 if None)
            # Ensure len() is called on a list
            items_for_len: List[Track] = []
            if (
                search_result
                and search_result.tracks
                and isinstance(search_result.tracks.items, list)
            ):
                items_for_len = search_result.tracks.items

            metadata_search_diagnostics: List[Dict[str, Any]] = [
                _search_result_diag_summary(search_result, f"{query} offset=0")
            ]

            # Broaden metadata candidate discovery even when the primary query returns results.
            # TIDAL search ranking can return a remix/alternate version first while the correct
            # original mix appears under a shorter or album-aware query.
            seen_track_ids = {
                str(getattr(track, "id", "") or "")
                for track in items_for_len
                if getattr(track, "id", None) is not None
            }

            def _append_unique_tracks_from_result(
                result: Optional[SearchResult],
                source_query: str,
            ) -> None:
                raw_items: List[Track] = []
                if (
                    result
                    and result.tracks
                    and isinstance(result.tracks.items, list)
                ):
                    raw_items = result.tracks.items

                added_summaries: List[Dict[str, Any]] = []
                duplicate_summaries: List[Dict[str, Any]] = []

                for extra_track in raw_items:
                    extra_id = str(getattr(extra_track, "id", "") or "")
                    if not extra_id:
                        continue
                    if extra_id in seen_track_ids:
                        if len(duplicate_summaries) < 5:
                            duplicate_summaries.append(_track_diag_summary(extra_track))
                        continue

                    seen_track_ids.add(extra_id)
                    items_for_len.append(extra_track)
                    if len(added_summaries) < 8:
                        added_summaries.append(_track_diag_summary(extra_track))

                metadata_search_diagnostics.append(
                    {
                        "query": source_query,
                        "count": len(raw_items),
                        "added_count": len(added_summaries),
                        "added": added_summaries,
                        "duplicates_sample": duplicate_summaries,
                        "top": [_track_diag_summary(track) for track in raw_items[:8]],
                    }
                )

                if added_summaries:
                    debug_buffer.append(
                        f"Extra metadata query '{source_query}' added {len(added_summaries)} unique candidate sample(s)."
                    )

            title_query_variants = _clean_title_query_variants(title)
            base_title_query = (
                title_query_variants[0]
                if title_query_variants
                else _metadata_query_text(normalize_title(title))
            )
            short_primary_artist = (
                primary_artist.split()[0].strip() if primary_artist else ""
            )
            spotify_title_has_version_marker = bool(_version_marker_categories(title))

            extra_queries: List[str] = []
            extra_query_seen: Set[str] = set()

            def _append_extra_query(candidate_query: str) -> None:
                if len(extra_queries) >= MAX_METADATA_EXTRA_QUERIES:
                    return

                cleaned_query = " ".join(str(candidate_query or "").strip().split())
                normalized_query = cleaned_query.lower()
                if cleaned_query and normalized_query not in extra_query_seen:
                    extra_query_seen.add(normalized_query)
                    extra_queries.append(cleaned_query)

            def _append_title_artist_query_variants(title_query: str) -> None:
                if not title_query:
                    return

                _append_extra_query(title_query)

                if primary_artist:
                    _append_extra_query(f"{title_query} {primary_artist}")
                    _append_extra_query(f"{primary_artist} {title_query}")

                if short_primary_artist and short_primary_artist.lower() != primary_artist.lower():
                    _append_extra_query(f"{title_query} {short_primary_artist}")
                    _append_extra_query(f"{short_primary_artist} {title_query}")

                if album:
                    _append_extra_query(f"{title_query} {album}")

                if primary_artist and album:
                    _append_extra_query(f"{title_query} {primary_artist} {album}")
                    _append_extra_query(f"{primary_artist} {title_query} {album}")

            _append_extra_query(f"{title} {primary_artist}")

            for title_query in title_query_variants:
                _append_title_artist_query_variants(title_query)

            if base_title_query and primary_artist and not spotify_title_has_version_marker:
                # Many electronic releases expose the desired source track as
                # "Original Mix" even when Spotify only names the base title.
                _append_extra_query(f"{base_title_query} original mix {primary_artist}")
                _append_extra_query(f"{base_title_query} original {primary_artist}")
                if short_primary_artist and short_primary_artist.lower() != primary_artist.lower():
                    _append_extra_query(f"{base_title_query} original mix {short_primary_artist}")

            normalized_seen_queries = {query.lower().strip()}
            for extra_query in extra_queries:
                normalized_extra_query = extra_query.lower().strip()
                if not normalized_extra_query or normalized_extra_query in normalized_seen_queries:
                    continue
                normalized_seen_queries.add(normalized_extra_query)

                for page_offset in METADATA_EXTRA_QUERY_PAGE_OFFSETS:
                    paged_query_label = f"{extra_query} offset={page_offset}"
                    search_queries_attempted.append(paged_query_label)

                    try:
                        debug_buffer.append(
                            f"Attempting extra metadata candidate query: '{extra_query}' offset={page_offset}"
                        )
                        extra_search_tuple = api.search(
                            extra_query,
                            Type.Track,
                            offset=page_offset,
                            limit=25,
                            return_raw=True,
                        )
                        extra_search_result: Optional[SearchResult] = None
                        if (
                            isinstance(extra_search_tuple, tuple)
                            and len(extra_search_tuple) == 2
                        ):
                            extra_search_result = extra_search_tuple[0]
                        elif extra_search_tuple:
                            extra_search_result = extra_search_tuple
                        before_count = len(items_for_len)
                        _append_unique_tracks_from_result(extra_search_result, paged_query_label)
                        if len(items_for_len) == before_count:
                            debug_buffer.append(
                                f"Extra metadata query '{paged_query_label}' added 0 unique candidates."
                            )
                    except Exception as extra_search_error:
                        debug_buffer.append(
                            f"Extra metadata query '{paged_query_label}' failed: {extra_search_error}"
                        )

            if (
                search_result
                and search_result.tracks
                and isinstance(getattr(search_result.tracks, "items", None), list)
            ):
                setattr(search_result.tracks, "items", items_for_len)

            num_items_found = len(items_for_len)

            # Add the initial finding message to the buffer *before* checking if 0
            debug_buffer.append(
                f"Found {num_items_found} potential matches for query '{query}'. Filtering..."
            )

            # Handle case where initial search yields no results
            if num_items_found == 0:
                logger.warning(
                    f"No Tidal tracks found for initial metadata query: '{query}'. Attempting fallback."
                )
                debug_buffer.append(
                    f"Initial query '{query}' yielded 0 results. Raw response: {raw_api_response}"
                )

                # --- Fallback Search Logic ---
                fallback_1_successful = False
                fallback_title_part = (
                    title  # Default to original title if ' - ' not found
                )
                if " - " in title:
                    fallback_title_part = title.split(" - ", 1)[0].strip()

                # --- Fallback 1 Attempt ---
                if (
                    fallback_title_part.lower() != normalize_title(title).lower()
                ):  # Only fallback if title part is different
                    fallback_query_1 = f"{fallback_title_part} {primary_artist}"
                    debug_buffer.append(
                        f"Attempting Fallback 1 search with query: '{fallback_query_1}'"
                    )
                    logger.info(
                        f"Attempting Fallback 1 search for '{title}' with query: '{fallback_query_1}'"
                    )
                    try:
                        debug_buffer.append(
                            f"Constructed Fallback 1 query: '{fallback_query_1}'"
                        )  # DEBUG
                        fallback_search_tuple_1 = api.search(
                            fallback_query_1, Type.Track, limit=10, return_raw=True
                        )
                        # Correctly unpack the tuple
                        fallback_search_result_1: Optional[SearchResult] = None
                        fallback_raw_response_1: Optional[str] = None
                        if (
                            isinstance(fallback_search_tuple_1, tuple)
                            and len(fallback_search_tuple_1) == 2
                        ):
                            fallback_search_result_1 = fallback_search_tuple_1[0]
                            fallback_raw_response_1 = fallback_search_tuple_1[1]
                        elif fallback_search_tuple_1:
                            fallback_search_result_1 = fallback_search_tuple_1
                            logger.warning(
                                f"Fallback 1 search returned unexpected type: {type(fallback_search_tuple_1)}"
                            )

                        debug_buffer.append(
                            f"Raw API response (Fallback 1): {fallback_raw_response_1}"
                        )  # DEBUG
                        # Ensure len() is called on a list
                        fallback_items_for_len_1: List[Track] = []
                        if (
                            fallback_search_result_1
                            and fallback_search_result_1.tracks
                            and isinstance(fallback_search_result_1.tracks.items, list)
                        ):
                            fallback_items_for_len_1 = (
                                fallback_search_result_1.tracks.items
                            )
                        fallback_num_items_1 = len(fallback_items_for_len_1)

                        debug_buffer.append(
                            f"Fallback 1 query '{fallback_query_1}' found {fallback_num_items_1} potential matches."
                        )
                        logger.debug(
                            f"Fallback 1 query '{fallback_query_1}' found {fallback_num_items_1} matches."
                        )

                        if fallback_num_items_1 > 0:
                            # Use fallback 1 results for candidate evaluation
                            search_result = fallback_search_result_1
                            raw_api_response = fallback_raw_response_1  # Update for consistency if needed later
                            num_items_found = fallback_num_items_1
                            fallback_1_successful = True
                            logger.info(
                                f"Fallback 1 search successful. Proceeding with {fallback_num_items_1} candidates."
                            )
                            # Let it flow into the candidate loop below
                        else:
                            # Fallback 1 also failed
                            logger.warning(
                                f"Fallback 1 search query '{fallback_query_1}' also found 0 results."
                            )
                            debug_buffer.append(
                                f"Fallback 1 query '{fallback_query_1}' also yielded 0 results. Raw response: {fallback_raw_response_1}"
                            )
                            # Continue to Fallback 2
                    except Exception as fallback_e_1:
                        logger.error(
                            f"Error during Fallback 1 search for '{fallback_query_1}': {fallback_e_1}",
                            exc_info=True,
                        )
                        debug_buffer.append(
                            f"Exception during Fallback 1 search: {fallback_e_1}"
                        )
                        # Continue to Fallback 2 despite exception in Fallback 1
                else:
                    # Fallback 1 not attempted (identical query or no ' - ')
                    logger.warning(
                        f"Initial query failed, Fallback 1 not attempted (query identical or no ' - ' in title)."
                    )
                    debug_buffer.append(
                        f"Fallback 1 not attempted: Fallback title part ('{fallback_title_part}') is identical to normalized title ('{normalize_title(title)}') or title has no ' - '."
                    )  # DEBUG

                # --- Fallback 2 Attempt (if Fallback 1 failed or wasn't attempted) ---
                if not fallback_1_successful:
                    fallback_query_2 = fallback_title_part  # Use title part before hyphen (or original title if no hyphen)
                    # Avoid duplicate search if Fallback 2 query is same as initial query
                    if fallback_query_2.lower() == query.lower().replace(
                        f" {primary_artist.lower()}", ""
                    ):
                        logger.warning(
                            f"Skipping Fallback 2: Query '{fallback_query_2}' would be identical to the initial title part."
                        )
                        debug_buffer.append(
                            f"Skipping Fallback 2: Query '{fallback_query_2}' would be identical to the initial title part."
                        )
                    else:
                        debug_buffer.append(
                            f"Attempting Fallback 2 search with query: '{fallback_query_2}' (title part only)"
                        )
                        logger.info(
                            f"Attempting Fallback 2 search for '{title}' with query: '{fallback_query_2}' (title part only)"
                        )
                        try:
                            debug_buffer.append(
                                f"Constructed Fallback 2 query: '{fallback_query_2}'"
                            )  # DEBUG
                            fallback_search_tuple_2 = api.search(
                                fallback_query_2, Type.Track, limit=10, return_raw=True
                            )
                            # Correctly unpack the tuple
                            fallback_search_result_2: Optional[SearchResult] = None
                            fallback_raw_response_2: Optional[str] = None
                            if (
                                isinstance(fallback_search_tuple_2, tuple)
                                and len(fallback_search_tuple_2) == 2
                            ):
                                fallback_search_result_2 = fallback_search_tuple_2[0]
                                fallback_raw_response_2 = fallback_search_tuple_2[1]
                            elif fallback_search_tuple_2:
                                fallback_search_result_2 = fallback_search_tuple_2
                                logger.warning(
                                    f"Fallback 2 search returned unexpected type: {type(fallback_search_tuple_2)}"
                                )

                            debug_buffer.append(
                                f"Raw API response (Fallback 2): {fallback_raw_response_2}"
                            )  # DEBUG
                            # Ensure len() is called on a list
                            fallback_items_for_len_2: List[Track] = []
                            if (
                                fallback_search_result_2
                                and fallback_search_result_2.tracks
                                and isinstance(
                                    fallback_search_result_2.tracks.items, list
                                )
                            ):
                                fallback_items_for_len_2 = (
                                    fallback_search_result_2.tracks.items
                                )
                            fallback_num_items_2 = len(fallback_items_for_len_2)

                            debug_buffer.append(
                                f"Fallback 2 query '{fallback_query_2}' found {fallback_num_items_2} potential matches."
                            )
                            logger.debug(
                                f"Fallback 2 query '{fallback_query_2}' found {fallback_num_items_2} matches."
                            )

                            if fallback_num_items_2 > 0:
                                # Use fallback 2 results for candidate evaluation
                                search_result = fallback_search_result_2
                                raw_api_response = (
                                    fallback_raw_response_2  # Update for consistency
                                )
                                num_items_found = fallback_num_items_2
                                logger.info(
                                    f"Fallback 2 search successful. Proceeding with {fallback_num_items_2} candidates."
                                )
                                # Let it flow into the candidate loop below
                            else:
                                # Fallback 2 also failed
                                logger.warning(
                                    f"Fallback 2 search query '{fallback_query_2}' also found 0 results."
                                )
                                debug_buffer.append(
                                    f"Fallback 2 query '{fallback_query_2}' also yielded 0 results. Raw response: {fallback_raw_response_2}"
                                )
                                # Both initial and fallbacks failed, emit buffer and return None
                                logger.debug(
                                    f"--- Emitting buffered logs for failed initial and fallback matches ---"
                                )
                                for msg in debug_buffer:
                                    logger.debug(msg)
                                logger.debug(f"--- End buffered logs ---")
                                return None, None, None  # All searches failed
                        except Exception as fallback_e_2:
                            logger.error(
                                f"Error during Fallback 2 search for '{fallback_query_2}': {fallback_e_2}",
                                exc_info=True,
                            )
                            debug_buffer.append(
                                f"Exception during Fallback 2 search: {fallback_e_2}"
                            )
                            # Emit buffer before returning None, None
                            logger.debug(
                                f"--- Emitting buffered logs due to Fallback 2 exception ---"
                            )
                            for msg in debug_buffer:
                                logger.debug(msg)
                            logger.debug(f"--- End buffered logs ---")
                            return None, None, None  # Exception during Fallback 2

                # If we are here and num_items_found is still 0, it means all fallbacks failed or were skipped.
                if num_items_found == 0:
                    logger.warning(
                        f"All search attempts (initial and fallbacks) failed for '{title}'."
                    )
                    # Ensure buffer is emitted if not already done by exceptions/failures above
                    logger.debug(
                        f"--- Emitting buffered logs for final failure after all fallbacks ---"
                    )
                    for msg in debug_buffer:
                        logger.debug(msg)
                    logger.debug(f"--- End buffered logs ---")
                    return None, None, None

                # --- End Fallback Search Logic ---

            # If we reach here, num_items_found > 0, continue processing

            min_score = float("inf")  # Reset min_score for metadata search
            best_match = None  # Reset best_match for metadata search
            candidates: List[Dict[str, Any]] = (
                []
            )  # Initialize list to store candidate details
            rejected_candidates: List[Dict[str, Any]] = []

            # Add checks for search_result and its attributes before iterating
            # Correct indentation and ensure iteration over a list
            if (
                search_result
                and search_result.tracks
                and isinstance(search_result.tracks.items, list)
            ):
                items_list: List[Track] = search_result.tracks.items
                for item in items_list:
                    track: Track = item

                    # --- Candidate Scoring Logic ---
                    spotify_artists_fuzzy = [fuzzy_normalize(a) for a in artists]
                    raw_tidal_artists = cast(
                        List[Artist],
                        getattr(track, "artists", []) or [],
                    )
                    tidal_artist_names = [
                        str(a.name)
                        for a in raw_tidal_artists
                        if hasattr(a, "name") and a.name
                    ]
                    tidal_artists_fuzzy = [
                        fuzzy_normalize(artist_name)
                        for artist_name in tidal_artist_names
                    ]

                    s_title_norm = normalize_title(title)
                    t_title_norm = normalize_title(track.title)
                    title_similarity_signal = _similarity_signal(title, track.title)
                    artist_similarity_signal = _best_similarity_signal(
                        artists,
                        tidal_artist_names,
                    )
                    title_token_overlap = _symmetric_token_overlap_ratio(
                        title,
                        getattr(track, "title", None),
                    )

                    if _should_hard_reject_candidate(
                        title_similarity_signal,
                        artist_similarity_signal,
                    ):
                        if len(rejected_candidates) < 30:
                            rejected_candidates.append(
                                {
                                    "reason": "very_low_title_and_artist_similarity",
                                    "track": _track_diag_summary(track),
                                    "spotify_title_norm": s_title_norm,
                                    "tidal_title_norm": t_title_norm,
                                    "spotify_artists_fuzzy": spotify_artists_fuzzy,
                                    "tidal_artists_fuzzy": tidal_artists_fuzzy,
                                    "title_similarity": round(title_similarity_signal, 3),
                                    "artist_similarity": round(artist_similarity_signal, 3),
                                    "title_token_overlap": round(title_token_overlap, 3),
                                }
                            )
                        continue

                    score = 0  # Lower is better, initialize score for this track
                    mismatch_reasons: List[str] = []  # Reasons for score penalties
 
                    # --- Title Scoring ---
                    spotify_title_fuzzy = fuzzy_normalize(title)
                    tidal_title_fuzzy = fuzzy_normalize(track.title)
 
                    title_score = _title_penalty_from_signal(title_similarity_signal)
                    if title_score:
                        score += title_score
                        mismatch_reasons.append("Title Differs")
                        debug_buffer.append(
                            f"    - Title similarity penalty: Spotify='{title}', Tidal='{getattr(track, 'title', None)}', Signal={title_similarity_signal:.3f}, TokenOverlap={title_token_overlap:.3f}, Penalty={title_score}"
                        )
                    # --- End Title Scoring ---
 
                    # --- Artist Scoring ---
                    artist_score = _artist_penalty_from_signal(artist_similarity_signal)
                    if artist_score:
                        score += artist_score
                        mismatch_reasons.append("Artist Mismatch")
                        debug_buffer.append(
                            f"    - Artist similarity penalty: SpotifyArtists={artists}, TidalArtists={tidal_artist_names}, Signal={artist_similarity_signal:.3f}, Penalty={artist_score}"
                        )
                    # --- End Artist Scoring ---

                    # Score based on duration difference
                    duration_score = 0
                    duration_diff = float("inf")
                    tidal_duration = getattr(track, "duration", None)
                    duration_comparison = _best_duration_comparison(
                        duration_ms,
                        tidal_duration,
                    )
                    if duration_comparison is not None:
                        (
                            duration_diff,
                            spotify_duration_s,
                            tidal_duration_s,
                        ) = duration_comparison
                        if duration_diff > 30:  # Penalize duration difference > 30s more
                            duration_score = 2
                            score += 2
                            mismatch_reasons.append("Duration > 30s")
                        elif duration_diff > 10:  # Penalize duration difference > 10s
                            duration_score = 1
                            score += 1
                            mismatch_reasons.append("Duration > 10s")
                        if duration_score > 0:
                            debug_buffer.append(
                                f"    - Duration mismatch penalty: Diff={duration_diff:.2f}s "
                                f"(Spotify={spotify_duration_s:.2f}s, Tidal={tidal_duration_s:.2f}s), "
                                f"Penalty={duration_score}. Raw Spotify duration_ms={duration_ms}, "
                                f"raw Tidal duration={tidal_duration}"
                            )
                    else:
                        duration_score = 1
                        score += 1  # Penalize if duration can't be compared
                        mismatch_reasons.append("Duration Incomparable")
                        debug_buffer.append(
                            f"    - Duration mismatch penalty: Cannot compare durations "
                            f"(Spotify raw duration_ms: {duration_ms}, Tidal raw duration: {tidal_duration})"
                        )

                    # Score based on mix/version mismatch.
                    version_score, version_reason = _version_mismatch_penalty(
                        title,
                        getattr(track, "title", None),
                    )
                    if version_score:
                        score += version_score
                        if version_reason:
                            mismatch_reasons.append(version_reason)
                        debug_buffer.append(
                            f"    - Version mismatch penalty: Spotify='{title}', Tidal='{getattr(track, 'title', None)}', Penalty={version_score}"
                        )

                    # --- Core Match Check ---
                    # If Title, Artist, Duration, and version category match, treat this as a strong match
                    # and ignore Album/ISRC penalties (likely same track on different release).
                    core_match = (
                        title_score == 0
                        and artist_score == 0
                        and duration_score == 0
                        and version_score == 0
                    )

                    # Score based on album match
                    tidal_album_title = getattr(
                        getattr(track, "album", None), "title", None
                    )
                    spotify_album_key = _metadata_compare_key(album)
                    tidal_album_key = _metadata_compare_key(tidal_album_title)
                    if (
                        tidal_album_key
                        and spotify_album_key
                        and tidal_album_key != spotify_album_key
                    ):
                        if not core_match:
                            score += 1  # Penalize album mismatch
                            mismatch_reasons.append("Album Mismatch")
                            debug_buffer.append(
                                f"    - Album mismatch: Spotify='{album}', Tidal='{tidal_album_title}'"
                            )
                        else:
                            debug_buffer.append(
                                f"    - Album mismatch ignored due to Core Match: Spotify='{album}', Tidal='{tidal_album_title}'"
                            )

                    # --- Calculate final score including ISRC penalty BEFORE comparing ---
                    isrc_penalty = 0  # Default penalty to 0
                    tidal_isrc = getattr(track, "isrc", None)
                    spotify_isrc_key = _metadata_compare_key(isrc)
                    tidal_isrc_key = _metadata_compare_key(tidal_isrc)
                    if spotify_isrc_key and tidal_isrc_key and spotify_isrc_key != tidal_isrc_key:
                        if not core_match:
                            isrc_penalty = 2  # Define penalty value
                            mismatch_reasons.append("ISRC Mismatch")
                            # Buffer the penalty application detail
                            debug_buffer.append(
                                f"    - ISRC mismatch penalty applied: Spotify='{isrc}', Tidal='{tidal_isrc}', Penalty={isrc_penalty}"
                            )
                        else:
                            debug_buffer.append(
                                f"    - ISRC mismatch ignored due to Core Match: Spotify='{isrc}', Tidal='{tidal_isrc}'"
                            )

                    # Calculate the final score for this specific track including all penalties
                    # 'score' here holds the score from title, artist, album, duration checks
                    final_score_for_this_track = score + isrc_penalty

                    candidate_data: Dict[str, Any] = {
                        "tidal_track": track,
                        "score": final_score_for_this_track,
                        "mismatch_reasons": mismatch_reasons,
                        "title_similarity": round(title_similarity_signal, 3),
                        "artist_similarity": round(artist_similarity_signal, 3),
                        "title_token_overlap": round(title_token_overlap, 3),
                        "duration_diff": (
                            duration_diff if duration_diff != float("inf") else None
                        ),
                    }

                    if final_score_for_this_track > MAX_RETURNED_REVIEW_CANDIDATE_SCORE:
                        if len(rejected_candidates) < 30:
                            rejected_candidates.append(
                                {
                                    "reason": "review_score_ceiling",
                                    "track": _track_diag_summary(track),
                                    "score": final_score_for_this_track,
                                    "mismatch_reasons": mismatch_reasons,
                                    "title_similarity": round(title_similarity_signal, 3),
                                    "artist_similarity": round(artist_similarity_signal, 3),
                                    "title_token_overlap": round(title_token_overlap, 3),
                                    "duration_diff": (
                                        duration_diff
                                        if duration_diff != float("inf")
                                        else None
                                    ),
                                }
                            )
                        debug_buffer.append(
                            f"  - Rejecting Tidal ID {track.id} ('{track.title}') because FinalScore={final_score_for_this_track} exceeds MAX_RETURNED_REVIEW_CANDIDATE_SCORE={MAX_RETURNED_REVIEW_CANDIDATE_SCORE}"
                        )
                        continue

                    candidates.append(candidate_data)
                    debug_buffer.append(
                        f"  - Checking Tidal ID {track.id} ('{track.title}'): BaseScore={score}, ISRCPenalty={isrc_penalty}, FinalScore={final_score_for_this_track}, TitleSignal={title_similarity_signal:.3f}, ArtistSignal={artist_similarity_signal:.3f}, DurationDiff={duration_diff:.2f}s"
                    )

                    if final_score_for_this_track > MAX_AUTO_LINK_CANDIDATE_SCORE:
                        debug_buffer.append(
                            f"    - Keeping Tidal ID {track.id} as review-only candidate because FinalScore={final_score_for_this_track} exceeds MAX_AUTO_LINK_CANDIDATE_SCORE={MAX_AUTO_LINK_CANDIDATE_SCORE}"
                        )
                        continue

                    # --- Update best match only if the FINAL score is lower ---
                    if final_score_for_this_track < min_score:
                        # Buffer the confirmation that this is the new best match
                        debug_buffer.append(
                            f"    - New best match found: Tidal ID {track.id} (Score: {final_score_for_this_track})"
                        )
                        # Update min_score with the final calculated score
                        min_score = final_score_for_this_track
                        # Update the best_match object
                        best_match = track
                # --- End of loop body ---

            # Sort candidates by score (ascending) and keep top 10
            candidates.sort(key=lambda x: x["score"])
            processed_candidates: List[Dict[str, Any]] = candidates[
                :10
            ]  # Keep top N candidates

            # --- Candidate return logic ---
            # For confident matches, the caller does not need candidate rows.
            # For uncertain matches, the review drawer must include the best match itself.
            # Otherwise the UI can look as if the best match was never found.
            candidates_to_return: Optional[List[Dict[str, Any]]] = None

            if best_match and processed_candidates:
                if min_score >= 2:
                    # Uncertain best match: return the full ranked candidate list,
                    # including the best match as the first/top review option.
                    candidates_to_return = processed_candidates
                else:
                    # Confident best match: keep only true alternatives for diagnostics.
                    # The confident return path below still returns Candidates=None.
                    alternative_candidates = [
                        cand
                        for cand in processed_candidates
                        if cand.get("tidal_track")
                        and getattr(cand["tidal_track"], "id", None)
                        != getattr(best_match, "id", None)
                    ]
                    if alternative_candidates:
                        candidates_to_return = alternative_candidates
            elif processed_candidates:
                # No best match selected, but scored candidates exist.
                # Let the UI show all available review candidates.
                candidates_to_return = processed_candidates
            # --- End candidate return logic ---

            best_score_for_diag: Optional[Union[int, float]] = None
            if min_score != float("inf"):
                best_score_for_diag = min_score

            no_match_with_candidates = (
                best_match is None
                and (num_items_found > 0 or len(rejected_candidates) > 0)
            )

            should_emit_matching_diag = EMIT_LINKING_METADATA_DIAG and (
                best_score_for_diag is None
                or best_score_for_diag >= 2
                or len(processed_candidates) <= 3
                or len(rejected_candidates) > 0
            )

            if no_match_with_candidates:
                compact_rejected = [
                    {
                        "reason": candidate.get("reason"),
                        "score": candidate.get("score"),
                        "track": candidate.get("track"),
                        "title_similarity": candidate.get("title_similarity"),
                        "artist_similarity": candidate.get("artist_similarity"),
                        "duration_diff": candidate.get("duration_diff"),
                    }
                    for candidate in rejected_candidates[:MAX_COMPACT_DIAG_REJECTED_SAMPLE]
                ]

                logger.warning(
                    "LINKING_NO_MATCH title=%r artists=%r album=%r raw_candidate_count=%r "
                    "scored_candidate_count=%r returned_candidate_count=%r queries_sample=%r "
                    "rejected_sample=%r",
                    title,
                    artists,
                    album,
                    num_items_found,
                    len(candidates),
                    len(candidates_to_return) if candidates_to_return else 0,
                    search_queries_attempted[:MAX_COMPACT_DIAG_QUERY_SAMPLE],
                    compact_rejected,
                )

            if should_emit_matching_diag:
                top_scored_candidates = [
                    {
                        "track": _track_diag_summary(candidate.get("tidal_track")),
                        "score": candidate.get("score"),
                        "mismatch_reasons": candidate.get("mismatch_reasons"),
                        "title_similarity": candidate.get("title_similarity"),
                        "artist_similarity": candidate.get("artist_similarity"),
                        "title_token_overlap": candidate.get("title_token_overlap"),
                        "duration_diff": candidate.get("duration_diff"),
                    }
                    for candidate in processed_candidates[:10]
                ]

                returned_candidates = [
                    {
                        "track": _track_diag_summary(candidate.get("tidal_track")),
                        "score": candidate.get("score"),
                        "mismatch_reasons": candidate.get("mismatch_reasons"),
                        "title_similarity": candidate.get("title_similarity"),
                        "artist_similarity": candidate.get("artist_similarity"),
                        "title_token_overlap": candidate.get("title_token_overlap"),
                        "duration_diff": candidate.get("duration_diff"),
                    }
                    for candidate in (candidates_to_return or [])[:10]
                ]

                logger.warning(
                    "LINKING_METADATA_DIAG_FULL title=%r artists=%r album=%r isrc=%r duration_ms=%r "
                    "best_id=%r best_title=%r best_score=%r raw_candidate_count=%r "
                    "scored_candidate_count=%r returned_candidate_count=%r "
                    "queries=%r search_results=%r rejected_candidates_sample=%r "
                    "top_scored_candidates=%r returned_candidates=%r",
                    title,
                    artists,
                    album,
                    isrc,
                    duration_ms,
                    getattr(best_match, "id", None),
                    getattr(best_match, "title", None),
                    best_score_for_diag,
                    num_items_found,
                    len(candidates),
                    len(candidates_to_return) if candidates_to_return else 0,
                    search_queries_attempted,
                    metadata_search_diagnostics,
                    rejected_candidates[:30],
                    top_scored_candidates,
                    returned_candidates,
                )
 
            # Log the best match found before confidence check
            if best_match:
                best_match_id = getattr(best_match, "id", "N/A")
                best_match_title = getattr(best_match, "title", "N/A")
                logger.debug(
                    f"    - Final Best Match (Pre-Confidence Check): Tidal ID {best_match_id} ('{best_match_title}'), Final Score: {min_score}"
                )
            else:
                logger.debug("    - No best match found during metadata search.")

            should_emit_buffer = (
                best_match is None or min_score >= 2
            )  # Emit if no match or uncertain

            if should_emit_buffer:  # Keep original logging for emitting buffer
                logger.debug(
                    f"--- Emitting buffered logs for uncertain/failed match (Score: {min_score}) ---"
                )
                for msg in debug_buffer:
                    logger.debug(msg)
                logger.debug(f"--- End buffered logs ---")
            # else: # Optional: Log that buffer is being discarded for confident match
            # logger.debug(f"--- Discarding buffered logs for confident match (Score: {min_score}) ---")

            if best_match and best_match.id is not None:
                is_confident = (min_score < 2) or (
                    min_score == 2
                    and (not candidates_to_return or len(candidates_to_return) == 0)
                )  # Confident if score 2 AND no *other* candidates

                if is_confident:
                    logger.info(
                        f"Confident metadata match found (Score: {min_score}): Tidal ID {best_match.id} ('{best_match.title}'). Fetching full track data."
                    )
                    full_track = api.getTrack(
                        str(best_match.id), suppress_debug_prints=True
                    )
                    logger.debug(
                        f"[searchLinkTrack] Returning CONFIDENT: BestMatchID='{getattr(full_track, 'id', 'N/A')}', Candidates=None, Score={min_score}"
                    )
                    return (
                        full_track,
                        None,
                        int(min_score),
                    )  # No alternative candidates to return for confident match
                else:
                    logger.info(
                        f"Uncertain metadata match found: Tidal ID {best_match.id} ('{best_match.title}') with score {min_score}. Fetching full track data."
                    )
                    full_track = api.getTrack(
                        str(best_match.id), suppress_debug_prints=False
                    )
                    # Return the filtered list of *alternative* candidates
                    logger.debug(
                        f"[searchLinkTrack] Returning UNCERTAIN: BestMatchID='{getattr(full_track, 'id', 'N/A')}', Candidates type: {type(candidates_to_return)}, len: {len(candidates_to_return) if candidates_to_return else 0}, Score={min_score}"
                    )
                    if candidates_to_return:
                        first_alt_track_obj = candidates_to_return[0].get("tidal_track")
                        first_alt_track_title = (
                            getattr(first_alt_track_obj, "title", "N/A_TITLE")
                            if first_alt_track_obj
                            else "N/A_OBJ_NONE"
                        )
                        logger.debug(
                            f"[searchLinkTrack] First candidate in UNCERTAIN return (alternatives): {first_alt_track_title}"
                        )
                    return full_track, candidates_to_return, int(min_score)
            else:
                # No best_match found at all (or best_match.id is None)
                if best_match is None:  # Explicitly check if best_match itself is None
                    logger.info(
                        f"No metadata match found for '{title}' by '{primary_artist}'."
                    )
                else:  # best_match exists but best_match.id is None
                    logger.error(
                        f"Best match found ({best_match}) but its ID is None. Cannot proceed."
                    )
                # Buffer should have already been emitted if should_emit_buffer was True
                # Return the processed_candidates if any were found, even if no single best_match was chosen
                # This allows UI to show candidates even if auto-linking failed completely.
                logger.debug(
                    f"searchLinkTrack: No confident match. Returning processed_candidates (length {len(processed_candidates) if processed_candidates else 0}): {processed_candidates}"
                )
                logger.debug(
                    f"[searchLinkTrack] Returning NO MATCH (after metadata block, processed_candidates): BestMatch=None, Candidates type: {type(processed_candidates)}, len: {len(processed_candidates) if processed_candidates else 0}, Score=None"
                )
                return (
                    None,
                    processed_candidates if processed_candidates else None,
                    None,
                )

        except Exception as e:
            logger.error(
                f"Error during Tidal metadata search for '{title}': {e}", exc_info=True
            )
            # Emit buffer on exception as well
            logger.debug(
                f"--- Emitting buffered logs due to exception during metadata search ---"
            )
            for msg in debug_buffer:
                logger.debug(msg)
            logger.debug(f"--- End buffered logs ---")
            logger.debug(
                f"searchLinkTrack: Returning None, None, None due to exception."
            )  # DEBUG ADDED
            logger.debug(
                f"[searchLinkTrack] Returning EXCEPTION (metadata search): BestMatch=None, Candidates=None, Score=None"
            )
            return None, None, None  # Exception during metadata search

    # Should not be reached if ISRC match was found and returned earlier
    logger.debug(
        f"searchLinkTrack: Reached final return statement. Returning None, None, None."
    )  # DEBUG ADDED
    logger.debug(
        f"[searchLinkTrack] Returning NO MATCH (end of function): BestMatch=None, Candidates=None, Score=None"
    )
    return None, None, None  # Explicitly return None if no path led to a match


# --- Linking Worker Class (Moved from gui.py) ---
class LinkingWorker(QObject):
    # MODIFIED: Signals now include spotify_data to allow robust row identification
    started = pyqtSignal(int, dict)  # row_index, spotify_data
    finished = pyqtSignal(
        int, object, object, object, dict
    )  # Args: row_index, best_match (Track/None), candidates (List[Dict]/None), score (int/None), spotify_data (Dict)
    error = pyqtSignal(int, str, dict)  # row_index, error_msg, spotify_data
    allTasksFinished = pyqtSignal()  # Emitted when the loop over all tracks completes

    def __init__(
        self,
        api: TidalAPI,
        tracks_to_link: List[Tuple[int, Dict[str, Any]]],
        stop_event: threading.Event,
        max_workers: int = 1,
    ):
        super().__init__()
        self.api = api
        self.tracks_to_link = tracks_to_link
        self._is_running = True
        self.stop_event = stop_event

        try:
            parsed_max_workers = int(max_workers)
        except (TypeError, ValueError):
            parsed_max_workers = 1
        self.max_workers = max(1, min(parsed_max_workers, 5))

    def _extract_linking_metadata(
        self,
        row_index: int,
        spotify_data: Dict[str, Any],
    ) -> Tuple[str, List[str], Optional[str], Optional[str], Optional[int]]:
        title_raw = spotify_data.get("name")
        title = str(title_raw).strip() if title_raw else None

        artists_data = spotify_data.get("artists", [])
        artists: List[str] = []
        if isinstance(artists_data, list) and artists_data:
            if isinstance(artists_data[0], str):
                artists = [
                    str(artist).strip()
                    for artist in artists_data
                    if str(artist).strip()
                ]
            elif isinstance(artists_data[0], dict):
                artists = [
                    str(artist.get("name", "")).strip()
                    for artist in artists_data
                    if str(artist.get("name", "")).strip()
                ]

        album_data: Optional[Union[str, Dict[str, str]]] = spotify_data.get("album")
        album: Optional[str] = None
        if isinstance(album_data, str):
            album = album_data
        elif isinstance(album_data, dict):
            album = album_data.get("name")

        isrc: Optional[str] = spotify_data.get("isrc")
        if not isrc and isinstance(spotify_data.get("external_ids"), dict):
            isrc = spotify_data.get("external_ids", {}).get("isrc")

        duration_ms: Optional[int] = None
        duration_raw = spotify_data.get("duration_ms")
        if duration_raw is not None:
            try:
                duration_ms = int(duration_raw)
            except (TypeError, ValueError):
                duration_ms = None

        if not title or not artists or not album:
            raise ValueError(
                f"Missing essential metadata after extraction: Title={title}, Artists={artists}, Album={album}. Original data: {spotify_data}"
            )

        return title, artists, album, isrc, duration_ms

    def _link_single_track(
        self,
        row_index: int,
        spotify_data: Dict[str, Any],
    ) -> Tuple[
        int,
        Optional[Track],
        Optional[List[Dict[str, Any]]],
        Optional[int],
        Dict[str, Any],
    ]:
        title, artists, album, isrc, duration_ms = self._extract_linking_metadata(
            row_index,
            spotify_data,
        )
        best_match, candidates, score = searchLinkTrack(
            self.api,
            title,
            artists,
            album,
            isrc,
            duration_ms,
        )
        return row_index, best_match, candidates, score, spotify_data

    def _emit_finished_result(
        self,
        row_index: int,
        best_match: Optional[Track],
        candidates: Optional[List[Dict[str, Any]]],
        score: Optional[int],
        spotify_data: Dict[str, Any],
    ) -> None:
        logger.debug(
            f"[LinkingWorker Row {row_index}] Emitting 'finished'. BestMatchID='{getattr(best_match, 'id', 'N/A')}', Candidates (alternatives) type: {type(candidates)}, len: {len(candidates) if candidates else 0}, Score={score}"
        )
        if candidates:
            logger.debug(
                f"[LinkingWorker Row {row_index}] First alternative candidate details before emit: TID='{getattr(candidates[0].get('tidal_track'), 'id', 'N/A')}', Title='{getattr(candidates[0].get('tidal_track'), 'title', 'N/A')}'"
            )
        self.finished.emit(row_index, best_match, candidates, score, spotify_data)

    def _run_single_track_sequentially(
        self,
        row_index: int,
        spotify_data: Dict[str, Any],
    ) -> None:
        self.started.emit(row_index, spotify_data)
        try:
            (
                finished_row_index,
                best_match,
                candidates,
                score,
                finished_spotify_data,
            ) = self._link_single_track(row_index, spotify_data)
            self._emit_finished_result(
                finished_row_index,
                best_match,
                candidates,
                score,
                finished_spotify_data,
            )
        except Exception as e:
            logger.error(
                f"Error linking track at row {row_index}: {e}",
                exc_info=True,
            )
            self.error.emit(row_index, str(e), spotify_data)

    def _run_parallel(self, worker_count: int) -> None:
        logger.info(
            "LinkingWorker: using %s simultaneous linking worker(s).",
            worker_count,
        )
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="tidal-link",
        )
        futures: Dict[Any, Tuple[int, Dict[str, Any]]] = {}

        try:
            for row_index, spotify_data in self.tracks_to_link:
                if self.stop_event.is_set() or not self._is_running:
                    logger.info("LinkingWorker: Stop detected before submitting remaining tracks.")
                    break
                self.started.emit(row_index, spotify_data)
                future = executor.submit(self._link_single_track, row_index, spotify_data)
                futures[future] = (row_index, spotify_data)

            for future in concurrent.futures.as_completed(list(futures.keys())):
                row_index, spotify_data = futures[future]
                if self.stop_event.is_set() or not self._is_running:
                    logger.info("LinkingWorker: Stop detected while collecting linked tracks.")
                    break

                try:
                    (
                        finished_row_index,
                        best_match,
                        candidates,
                        score,
                        finished_spotify_data,
                    ) = future.result()
                    self._emit_finished_result(
                        finished_row_index,
                        best_match,
                        candidates,
                        score,
                        finished_spotify_data,
                    )
                except Exception as e:
                    logger.error(
                        f"Error linking track at row {row_index}: {e}",
                        exc_info=True,
                    )
                    self.error.emit(row_index, str(e), spotify_data)
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

    @pyqtSlot()
    def run(self):
        logger.debug(f"LinkingWorker started for {len(self.tracks_to_link)} tracks.")

        try:
            worker_count = max(1, min(self.max_workers, len(self.tracks_to_link)))
            if worker_count <= 1:
                for row_index, spotify_data in self.tracks_to_link:
                    if self.stop_event.is_set():
                        logger.info("LinkingWorker: Stop event detected, breaking loop.")
                        break
                    if not self._is_running:
                        logger.info("LinkingWorker stopping early (_is_running is False).")
                        break
                    self._run_single_track_sequentially(row_index, spotify_data)
            else:
                self._run_parallel(worker_count)
        finally:
            logger.debug("LinkingWorker loop finished. Emitting allTasksFinished.")
            self.allTasksFinished.emit()

    # Corrected stop method signature and indentation
    def stop(self):
        self._is_running = False
