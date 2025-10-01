#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   linking.py
@Time    :   2025/04/10
@Author  :   GeradeHouse
@Version :   1.0
@Desc    :   Logic for linking Spotify tracks to Tidal tracks
"""

import logging
from typing import List, Optional, cast, Tuple, Dict, Any
import threading
import re

# Import Qt components needed for the handler

logger = logging.getLogger(__name__)
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot  # Added for LinkingWorker

# Import project components
from .model import Track, SearchResult, Artist
from .enums import Type
from .tidal import TidalAPI

# Assuming aigpy is available
try:
    from aigpy.modelHelper import dictToModel as aigpy_dictToModel
except ImportError:
    logging.warning(
        "aigpy.modelHelper could not be imported. Model casting might fail."
    )

    def aigpy_dictToModel(indict: Any, model_type: Any) -> Any:
        return model_type  # Fallback for aigpy function, match expected param name 'indict'


# Forward declaration for type hinting MainView without circular import


# Helper function to normalize titles (Removed duplicate definition)
def normalize_title(title: Optional[str]) -> str:
    if not title:
        return ""
    # Remove common tags, version info, brackets, hyphens used for separation, and extra whitespace
    normalized = title.lower()
    # Remove content within brackets/parentheses (e.g., (Remix), [Live])
    normalized = re.sub(r"[\(\[].*?[\)\]]", "", normalized)
    # Remove common suffixes like - Edit, - Remix, - Live, - Version, etc.
    normalized = re.sub(
        r"\s-\s(edit|remix|live|version|mix|radio edit|extended mix|instrumental)\b",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    # Remove leading/trailing whitespace and reduce multiple spaces to one
    normalized = " ".join(normalized.strip().split())
    return normalized


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
                        if duration_ms is not None and track.duration:
                            duration_diff = abs(track.duration - (duration_ms / 1000))
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
                                    f"ISRC match {track.id} duration mismatch ({duration_diff:.2f}s diff). Spotify: {duration_ms/1000}s, Tidal: {track.duration}s."
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
                        logging.info(
                            f"Confident ISRC match found: Tidal ID {best_match.id} ('{best_match.title}'). Fetching full track data."
                        )
                        # Emit buffer before returning
                        logging.debug(f"--- Emitting buffered logs for ISRC match ---")
                        for msg in debug_buffer:
                            logging.debug(msg)
                        logging.debug(f"--- End buffered logs ---")
                        # Fetch full track data, ensuring ID is str
                        full_track = api.getTrack(
                            str(best_match.id), suppress_debug_prints=True
                        )
                        return full_track, None, 0  # ISRC match has score 0
                    else:
                        # Handle case where best_match or its id is None despite is_isrc_match flag
                        logging.error(
                            f"ISRC match flag set, but best_match ({best_match}) or best_match.id is None. Cannot fetch full track."
                        )
                        # Emit buffer before returning
                        logging.debug(
                            f"--- Emitting buffered logs for ISRC match failure ---"
                        )
                        for msg in debug_buffer:
                            logging.debug(msg)
                        logging.debug(f"--- End buffered logs ---")
                        return None, None, None

        except Exception as e:
            logging.error(
                f"Error during Tidal ISRC search for '{isrc}': {e}", exc_info=True
            )
            # Emit buffer on exception
            logging.debug(
                f"--- Emitting buffered logs due to exception during ISRC search ---"
            )
            for msg in debug_buffer:
                logging.debug(msg)
            logging.debug(f"--- End buffered logs ---")
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
            search_tuple = api.search(query, Type.Track, limit=10, return_raw=True)
            # When return_raw=True, search returns a tuple (SearchResult, str)
            # Correctly unpack the tuple returned when return_raw=True
            search_result: Optional[SearchResult] = None
            raw_api_response: Optional[str] = None
            if isinstance(search_tuple, tuple) and len(search_tuple) == 2:
                search_result = search_tuple[0]
                raw_api_response = search_tuple[1]
            elif search_tuple:  # Handle unexpected return type
                search_result = search_tuple
                logging.warning(
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
            num_items_found = len(items_for_len)

            # Add the initial finding message to the buffer *before* checking if 0
            debug_buffer.append(
                f"Found {num_items_found} potential matches for query '{query}'. Filtering..."
            )

            # Handle case where initial search yields no results
            if num_items_found == 0:
                logging.warning(
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
                    logging.info(
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
                            logging.warning(
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
                        logging.debug(
                            f"Fallback 1 query '{fallback_query_1}' found {fallback_num_items_1} matches."
                        )

                        if fallback_num_items_1 > 0:
                            # Use fallback 1 results for candidate evaluation
                            search_result = fallback_search_result_1
                            raw_api_response = fallback_raw_response_1  # Update for consistency if needed later
                            num_items_found = fallback_num_items_1
                            fallback_1_successful = True
                            logging.info(
                                f"Fallback 1 search successful. Proceeding with {fallback_num_items_1} candidates."
                            )
                            # Let it flow into the candidate loop below
                        else:
                            # Fallback 1 also failed
                            logging.warning(
                                f"Fallback 1 search query '{fallback_query_1}' also found 0 results."
                            )
                            debug_buffer.append(
                                f"Fallback 1 query '{fallback_query_1}' also yielded 0 results. Raw response: {fallback_raw_response_1}"
                            )
                            # Continue to Fallback 2
                    except Exception as fallback_e_1:
                        logging.error(
                            f"Error during Fallback 1 search for '{fallback_query_1}': {fallback_e_1}",
                            exc_info=True,
                        )
                        debug_buffer.append(
                            f"Exception during Fallback 1 search: {fallback_e_1}"
                        )
                        # Continue to Fallback 2 despite exception in Fallback 1
                else:
                    # Fallback 1 not attempted (identical query or no ' - ')
                    logging.warning(
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
                        logging.warning(
                            f"Skipping Fallback 2: Query '{fallback_query_2}' would be identical to the initial title part."
                        )
                        debug_buffer.append(
                            f"Skipping Fallback 2: Query '{fallback_query_2}' would be identical to the initial title part."
                        )
                    else:
                        debug_buffer.append(
                            f"Attempting Fallback 2 search with query: '{fallback_query_2}' (title part only)"
                        )
                        logging.info(
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
                                logging.warning(
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
                            logging.debug(
                                f"Fallback 2 query '{fallback_query_2}' found {fallback_num_items_2} matches."
                            )

                            if fallback_num_items_2 > 0:
                                # Use fallback 2 results for candidate evaluation
                                search_result = fallback_search_result_2
                                raw_api_response = (
                                    fallback_raw_response_2  # Update for consistency
                                )
                                num_items_found = fallback_num_items_2
                                logging.info(
                                    f"Fallback 2 search successful. Proceeding with {fallback_num_items_2} candidates."
                                )
                                # Let it flow into the candidate loop below
                            else:
                                # Fallback 2 also failed
                                logging.warning(
                                    f"Fallback 2 search query '{fallback_query_2}' also found 0 results."
                                )
                                debug_buffer.append(
                                    f"Fallback 2 query '{fallback_query_2}' also yielded 0 results. Raw response: {fallback_raw_response_2}"
                                )
                                # Both initial and fallbacks failed, emit buffer and return None
                                logging.debug(
                                    f"--- Emitting buffered logs for failed initial and fallback matches ---"
                                )
                                for msg in debug_buffer:
                                    logging.debug(msg)
                                logging.debug(f"--- End buffered logs ---")
                                return None, None, None  # All searches failed
                        except Exception as fallback_e_2:
                            logging.error(
                                f"Error during Fallback 2 search for '{fallback_query_2}': {fallback_e_2}",
                                exc_info=True,
                            )
                            debug_buffer.append(
                                f"Exception during Fallback 2 search: {fallback_e_2}"
                            )
                            # Emit buffer before returning None, None
                            logging.debug(
                                f"--- Emitting buffered logs due to Fallback 2 exception ---"
                            )
                            for msg in debug_buffer:
                                logging.debug(msg)
                            logging.debug(f"--- End buffered logs ---")
                            return None, None, None  # Exception during Fallback 2

                # If we are here and num_items_found is still 0, it means all fallbacks failed or were skipped.
                if num_items_found == 0:
                    logging.warning(
                        f"All search attempts (initial and fallbacks) failed for '{title}'."
                    )
                    # Ensure buffer is emitted if not already done by exceptions/failures above
                    logging.debug(
                        f"--- Emitting buffered logs for final failure after all fallbacks ---"
                    )
                    for msg in debug_buffer:
                        logging.debug(msg)
                    logging.debug(f"--- End buffered logs ---")
                    return None, None, None

                # --- End Fallback Search Logic ---

            # If we reach here, num_items_found > 0, continue processing

            spotify_duration_s = duration_ms / 1000 if duration_ms is not None else None
            min_score = float("inf")  # Reset min_score for metadata search
            best_match = None  # Reset best_match for metadata search
            candidates: List[Dict[str, Any]] = (
                []
            )  # Initialize list to store candidate details

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
                    # --- Start of block to be indented ---
                    score = 0  # Lower is better, initialize score for this track
                    mismatch_reasons: List[str] = []  # Reasons for score penalties

                    # --- Title Scoring (using normalization) ---
                    spotify_title_normalized = normalize_title(title)
                    tidal_title_normalized = normalize_title(track.title)

                    if spotify_title_normalized != tidal_title_normalized:
                        score += 1  # Penalize if normalized titles don't match
                        mismatch_reasons.append("Title Differs")
                        # Buffer this detail
                        debug_buffer.append(
                            f"    - Normalized title mismatch: Spotify='{spotify_title_normalized}', Tidal='{tidal_title_normalized}'"
                        )
                    # --- End Title Scoring ---

                    # Score based on primary artist match
                    tidal_artists_lower = (
                        [
                            a.name.lower()
                            for a in cast(List[Artist], track.artists)
                            if hasattr(a, "name") and a.name
                        ]
                        if hasattr(track, "artists") and track.artists
                        else []
                    )
                    if primary_artist.lower() not in tidal_artists_lower:
                        score += 1  # Penalize artist mismatch
                        mismatch_reasons.append("Artist Mismatch")
                        debug_buffer.append(
                            f"    - Primary artist mismatch: Spotify='{primary_artist}', Tidal Artists='{[a.name for a in cast(List[Artist], track.artists) if hasattr(a, 'name')]}'"
                        )  # Log actual names

                    # Score based on album match
                    tidal_album_title = getattr(
                        getattr(track, "album", None), "title", None
                    )
                    if (
                        tidal_album_title
                        and album
                        and tidal_album_title.lower() != album.lower()
                    ):
                        score += 1  # Penalize album mismatch
                        mismatch_reasons.append("Album Mismatch")
                        debug_buffer.append(
                            f"    - Album mismatch: Spotify='{album}', Tidal='{tidal_album_title}'"
                        )

                    # Score based on duration difference
                    duration_diff = float("inf")
                    tidal_duration = getattr(track, "duration", None)
                    if spotify_duration_s is not None and tidal_duration is not None:
                        duration_diff = abs(tidal_duration - spotify_duration_s)
                        duration_penalty = 0
                        if (
                            duration_diff > 10
                        ):  # Penalize duration difference > 10s more
                            duration_penalty = 2
                            score += 2
                            mismatch_reasons.append("Duration > 10s")
                        elif duration_diff > 5:  # Penalize duration difference > 5s
                            duration_penalty = 1
                            score += 1
                            mismatch_reasons.append("Duration > 5s")
                        if duration_penalty > 0:
                            debug_buffer.append(
                                f"    - Duration mismatch penalty: Diff={duration_diff:.2f}s, Penalty={duration_penalty}"
                            )
                    else:
                        score += 1  # Penalize if duration can't be compared
                        mismatch_reasons.append("Duration Incomparable")
                        debug_buffer.append(
                            f"    - Duration mismatch penalty: Cannot compare durations (Spotify: {spotify_duration_s}, Tidal: {tidal_duration})"
                        )

                    # --- Calculate final score including ISRC penalty BEFORE comparing ---
                    isrc_penalty = 0  # Default penalty to 0
                    tidal_isrc = getattr(track, "isrc", None)
                    if isrc and tidal_isrc and isrc.lower() != tidal_isrc.lower():
                        isrc_penalty = 2  # Define penalty value
                        mismatch_reasons.append("ISRC Mismatch")
                        # Buffer the penalty application detail
                        debug_buffer.append(
                            f"    - ISRC mismatch penalty applied: Spotify='{isrc}', Tidal='{tidal_isrc}', Penalty={isrc_penalty}"
                        )

                    # Calculate the final score for this specific track including all penalties
                    # 'score' here holds the score from title, artist, album, duration checks
                    final_score_for_this_track = score + isrc_penalty

                    # Store candidate details
                    candidate_data: Dict[str, Any] = {
                        "tidal_track": track,
                        "score": final_score_for_this_track,
                        "mismatch_reasons": mismatch_reasons,
                    }
                    candidates.append(candidate_data)
                    # Buffer the check details *before* the comparison, showing the final score
                    debug_buffer.append(
                        f"  - Checking Tidal ID {track.id} ('{track.title}'): BaseScore={score}, ISRCPenalty={isrc_penalty}, FinalScore={final_score_for_this_track}, DurationDiff={duration_diff:.2f}s"
                    )

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

            # --- NEW LOGIC: Refine candidates list ---
            # The 'candidates' list to be returned should only contain alternatives
            # to the 'best_match'. If best_match is the only candidate, or if all
            # candidates are essentially the same as best_match, then candidates_to_return should be None.

            candidates_to_return: Optional[List[Dict[str, Any]]] = None
            if best_match and processed_candidates:
                # Filter out the best_match from the candidates list if it's present
                # and only return other candidates.
                alternative_candidates = [
                    cand
                    for cand in processed_candidates
                    if cand.get("tidal_track")
                    and getattr(cand["tidal_track"], "id", None)
                    != getattr(best_match, "id", None)
                ]
                if alternative_candidates:
                    candidates_to_return = alternative_candidates
            # --- END NEW LOGIC ---

            # Log the best match found before confidence check
            if best_match:
                best_match_id = getattr(best_match, "id", "N/A")
                best_match_title = getattr(best_match, "title", "N/A")
                logging.debug(
                    f"    - Final Best Match (Pre-Confidence Check): Tidal ID {best_match_id} ('{best_match_title}'), Final Score: {min_score}"
                )
            else:
                logging.debug("    - No best match found during metadata search.")

            should_emit_buffer = (
                best_match is None or min_score >= 2
            )  # Emit if no match or uncertain

            if should_emit_buffer:  # Keep original logging for emitting buffer
                logging.debug(
                    f"--- Emitting buffered logs for uncertain/failed match (Score: {min_score}) ---"
                )
                for msg in debug_buffer:
                    logging.debug(msg)
                logging.debug(f"--- End buffered logs ---")
            # else: # Optional: Log that buffer is being discarded for confident match
            # logging.debug(f"--- Discarding buffered logs for confident match (Score: {min_score}) ---")

            if best_match and best_match.id is not None:
                is_confident = (min_score < 2) or (
                    min_score == 2
                    and (not candidates_to_return or len(candidates_to_return) == 0)
                )  # Confident if score 2 AND no *other* candidates

                if is_confident:
                    logging.info(
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
                    logging.info(
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
                    logging.warning(
                        f"No metadata match found for '{title}' by '{primary_artist}'."
                    )
                else:  # best_match exists but best_match.id is None
                    logging.error(
                        f"Best match found ({best_match}) but its ID is None. Cannot proceed."
                    )
                # Buffer should have already been emitted if should_emit_buffer was True
                # Return the processed_candidates if any were found, even if no single best_match was chosen
                # This allows UI to show candidates even if auto-linking failed completely.
                logging.debug(
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
            logging.error(
                f"Error during Tidal metadata search for '{title}': {e}", exc_info=True
            )
            # Emit buffer on exception as well
            logging.debug(
                f"--- Emitting buffered logs due to exception during metadata search ---"
            )
            for msg in debug_buffer:
                logging.debug(msg)
            logging.debug(f"--- End buffered logs ---")
            logging.debug(
                f"searchLinkTrack: Returning None, None, None due to exception."
            )  # DEBUG ADDED
            logger.debug(
                f"[searchLinkTrack] Returning EXCEPTION (metadata search): BestMatch=None, Candidates=None, Score=None"
            )
            return None, None, None  # Exception during metadata search

    # Should not be reached if ISRC match was found and returned earlier
    logging.debug(
        f"searchLinkTrack: Reached final return statement. Returning None, None, None."
    )  # DEBUG ADDED
    logger.debug(
        f"[searchLinkTrack] Returning NO MATCH (end of function): BestMatch=None, Candidates=None, Score=None"
    )
    return None, None, None  # Explicitly return None if no path led to a match


# --- Linking Worker Class (Moved from gui.py) ---
class LinkingWorker(QObject):
    started = pyqtSignal(int)  # Emitted when processing for a single track starts
    finished = pyqtSignal(
        int, object, object, object
    )  # Emitted when processing for a single track finishes (success/fail) - Args: row_index, best_match (Track/None), candidates (List[Dict]/None), score (int/None)
    error = pyqtSignal(int, str)  # Emitted on error for a single track
    allTasksFinished = pyqtSignal()  # Emitted when the loop over all tracks completes

    def __init__(
        self,
        api: TidalAPI,
        tracks_to_link: List[Tuple[int, Dict[str, Any]]],
        stop_event: threading.Event,
    ):  # Add stop_event parameter
        super().__init__()
        self.api = api
        self.tracks_to_link = (
            tracks_to_link  # List of tuples: (row_index, spotify_metadata_dict)
        )
        self._is_running = True  # Keep this for potential immediate stop before loop
        self.stop_event = stop_event  # Store the event

    # Corrected run method signature and indentation
    @pyqtSlot()  # Added decorator for clarity, though not strictly needed for QThread.started connection
    def run(self):
        logging.debug(f"LinkingWorker started for {len(self.tracks_to_link)} tracks.")
        for row_index, spotify_data in self.tracks_to_link:
            # Check stop event FIRST
            if self.stop_event.is_set():
                logging.info("LinkingWorker: Stop event detected, breaking loop.")
                break  # Exit the loop gracefully
            # Check _is_running (optional, for immediate stop)
            if not self._is_running:
                logging.info("LinkingWorker stopping early (_is_running is False).")
                break
            try:
                self.started.emit(row_index)

                # --- Extract data robustly from potentially simplified structure ---
                title: Optional[str] = spotify_data.get("name")

                artists_data: List[Dict[str, str]] = spotify_data.get("artists", [])
                artists: List[str] = []
                # Handle artists being a list of strings OR list of dicts
                if artists_data and isinstance(artists_data[0], str):
                    artists = cast(
                        List[str], artists_data
                    )  # It's already a list of strings
                elif artists_data and isinstance(artists_data[0], dict):
                    artists = [
                        artist.get("name", "")
                        for artist in artists_data
                        if artist.get("name")
                    ]  # Extract names
                else:
                    artists = []  # Empty or unknown format

                album_data: Optional[Union[str, Dict[str, str]]] = spotify_data.get(
                    "album"
                )
                album: Optional[str] = None
                # Handle album being a string OR a dict
                if isinstance(album_data, str):
                    album = album_data  # It's already the name
                elif isinstance(album_data, dict):
                    album = album_data.get("name")  # Extract name from dict
                else:
                    album = None

                # Handle ISRC potentially being top-level or nested
                isrc: Optional[str] = spotify_data.get(
                    "isrc"
                )  # Check top-level first (as seen in logs)
                if not isrc and isinstance(spotify_data.get("external_ids"), dict):
                    isrc = spotify_data.get("external_ids", {}).get(
                        "isrc"
                    )  # Check nested as fallback

                duration_ms: Optional[int] = spotify_data.get("duration_ms")
                # --- End data extraction ---

                if not title or not artists or not album:
                    logging.error(
                        f"Row {row_index}: Missing essential metadata after extraction: Title={title}, Artists={artists}, Album={album}. Original data: {spotify_data}"
                    )
                    self.error.emit(
                        row_index, "Missing essential metadata (title, artists, album)"
                    )
                    continue  # Skip this track

                # Call the linking function from linking.py (already in this module)
                best_match, candidates, score = searchLinkTrack(
                    self.api, title, artists, album, isrc, duration_ms
                )
                # ADD THESE LOGS
                logger.debug(
                    f"[LinkingWorker Row {row_index}] Emitting 'finished'. BestMatchID='{getattr(best_match, 'id', 'N/A')}', Candidates (alternatives) type: {type(candidates)}, len: {len(candidates) if candidates else 0}, Score={score}"
                )
                if candidates:
                    logger.debug(
                        f"[LinkingWorker Row {row_index}] First alternative candidate details before emit: TID='{getattr(candidates[0].get('tidal_track'),'id','N/A')}', Title='{getattr(candidates[0].get('tidal_track'),'title','N/A')}'"
                    )
                self.finished.emit(row_index, best_match, candidates, score)

            except Exception as e:
                logging.error(
                    f"Error linking track at row {row_index}: {e}", exc_info=True
                )
                self.error.emit(row_index, str(e))
        # Emit allTasksFinished signal AFTER the loop completes
        logging.debug("LinkingWorker loop finished. Emitting allTasksFinished.")
        self.allTasksFinished.emit()

    # Corrected stop method signature and indentation
    def stop(self):
        self._is_running = False


# --- Linking GUI Interaction Handler ---
# Removed the entire LinkingGuiHandler class definition (lines 491-883)
