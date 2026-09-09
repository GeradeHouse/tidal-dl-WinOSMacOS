# Spotify downloads and local duplicate review

## What caused repeat downloads

The playlist check in [download.py](tidal_dl/download.py) used filenames and explicitly disabled audio-tag reads. Filename normalization did not make “Epsilon Girls” and “Epsilon Girls (Original Mix)” equal, nor could filenames identify a Spotify track linked to a different TIDAL release. The final output format was also not always checked when conversion changed the extension. Existing Spotify files were re-tagged even when downloads were skipped.

These are confirmed code paths, not a forensic identification of the user's existing audio files: the local music library was not inspected or changed during implementation.

## Prevention

- [local_identity.py](tidal_dl/local_identity.py) reads Spotify IDs, TIDAL IDs, ISRC, artist, version and duration from supported audio containers. Reads are cached against file size, timestamps and inode, so a later rename or tag change does not reuse an old identity.
- Stable source IDs and supported ISRC/metadata matches skip additional media downloads within the target playlist folder. Different playlist folders retain their own copies.
- Artist/title/duration-only legacy matches stop that track with a review-required error instead of automatically downloading another copy. They are not silently declared identical.
- Only the conventional “Original Mix” annotation is treated as redundant. Named remixes, radio edits, mixed and extended versions are retained.
- Destination-folder locking covers the existing-file check through finalization within one application process. Separate application instances and external audio editors are not coordinated by this lock.
- Repeated successful source IDs in a GUI download batch are processed once.
- New files retain their TIDAL audio-provider ID even when their source is Spotify.
- Skipping an existing file no longer re-tags it or replaces its artwork, lyrics, or DJ metadata.

## Reviewing existing duplicates

Open **Settings → Local playlist duplicate cleanup → Scan and review local duplicates**.

1. Log in to Spotify and select your local **Playlists** root. The default comes from the saved download location, under the FLAC playlist directory; it is not hard-coded to a particular computer.
2. Start the scan. It reads current Spotify playlists and files directly inside mapped local playlist directories. Existing path formats and saved folder hints are used. A selected alternative Playlists root is also supported.
3. Review each pair's evidence, paths and durations. No row is selected automatically. Listen to metadata-only candidates before deciding.
4. Select only unwanted copies and choose **Keep A** or **Keep B**. Keep the path used by VirtualDJ whenever possible.
5. Confirm the exact selected file list. The selected audio is moved into a recovery directory beneath the scan root. It is not permanently deleted.

The scanner implementation is in [duplicate_cleanup.py](tidal_dl/duplicate_cleanup.py); the background worker and review dialog are in [gui_duplicate_cleanup.py](tidal_dl/gui/gui_duplicate_cleanup.py).

## Safety and recovery

- Absence from Spotify is never a removal criterion. Unrelated local tracks and unmapped folders are untouched.
- There is no cross-folder deduplication. Intentional copies in different DJ folders are preserved.
- Lyrics, covers and DJ sidecars are neither deleted nor moved. Some lyrics may therefore remain beside a removed audio filename.
- A retained counterpart must exist in the same folder. Conflicting keep/remove selections are rejected.
- Both files are checked for changes since scanning. Changed, inaccessible and unsupported files are skipped or reported rather than removed.
- Each quarantined file has a durable recovery record containing its original path, retained counterpart, reason and SHA-256 checksum.
- Use **Open recovery folder** to locate moved files. Close audio applications and move the audio back to its recorded original path only if that path is unoccupied. Never overwrite an existing file during recovery. There is no automatic restore button.
- Moving a file changes its usable path; VirtualDJ references to the moved filename will not resolve until restored. Quarantine does not free disk space. On OneDrive, moves may synchronize to other devices.

## Limits

The scanner does not decode or acoustically fingerprint every recording. Matching FLAC STREAMINFO PCM checksums and stream parameters are stronger evidence than metadata, but these stored checksums are not recomputed from decoded audio. Metadata and equal duration alone cannot prove audio equivalence. Missing identifiers, incomplete metadata, inaccessible Spotify playlists, unmapped folders or major filename changes can leave duplicates undetected. Fetch failures are reported; files are never removed because an online response is empty or incomplete.

The changes were reviewed statically only. No application execution, compilation, build or tests were performed, and no test code was added.
