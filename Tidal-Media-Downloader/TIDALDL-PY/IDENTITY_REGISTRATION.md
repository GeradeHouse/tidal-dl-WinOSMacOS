# Register existing files / repair identity tags

Settings → **Register existing files / repair identity tags**

## Purpose

Existing downloads stay recognized by the downloader even after you edit tags
(title, artist, key, BPM, genre, comments) or rename files in VirtualDJ. Two
layers provide this:

1. **Dedicated identity tags inside each audio file**
   - `TIDAL_DL_ID`: a stable random identity UID for this recording.
   - `SPOTIFY_TRACK_ID`: the Spotify source track ID.
   - `TIDAL_TRACK_ID`: the TIDAL audio-provider track ID.

   VirtualDJ does not expose these fields as editable metadata; they are
   separate custom fields. Registration may read descriptive tags as matching
   evidence and to verify preservation, but it only writes missing identity fields.

2. **A persistent audio-content index**
   [`identity_index.py`](tidal_dl/identity_index.py) stores, per recording, a
   fingerprint computed from the **fully decoded audio** (SHA-256 of decoded
   PCM plus sample rate and channel count) using FFmpeg's incremental hash
   muxer — no whole-track memory buffer. This fingerprint is independent of
   filenames, tags and artwork. The SQLite database lives in the profile
   directory (`local-audio-identities.sqlite3`); it is **not** placed in the
   OneDrive-synced music folder, and journaling uses the default rollback mode
   deliberately for that reason.

If a custom identity tag survives VirtualDJ edits, tag matching works instantly.
If even the custom tags are stripped, the audio fingerprint still recovers the
identity from the index (with audio verification enabled).

## How the scan works

The scan ([`identity_registration.py`](tidal_dl/identity_registration.py)) walks the
selected playlist root and, for each supported audio file (FLAC, MP3, M4A/MP4):

1. Reads any existing identity tags (conflicting duplicate values abort that
   file rather than being overwritten).
2. Computes the audio fingerprint (cached in the index when the file is
   unchanged; actual decoding is skipped for cached files).
3. Looks up the fingerprint in the index.
4. For files inside mapped Spotify playlist folders, proposes an association
   with the matching Spotify track. A persisted TIDAL link helps *ranking* but
   is **never** written as a TIDAL provider ID automatically — association
   choices are always explicit.

Each row shows one status:

| Status | Meaning | Action |
|---|---|---|
| Registered | Already indexed and tagged consistently | None needed |
| Identified | Verified via the audio index or consistent tags; association confirmed | Select explicitly to add missing identity data |
| Review association | Metadata-only legacy match; not audio proof | Choose the association explicitly, or keep as-is |
| Unmatched | No reliable association found | Left untouched |
| Conflict / error | Conflicting IDs, unreadable audio, changed file | Reported; nothing changed |

Nothing is selected automatically. Metadata-only candidates require manual judgment.

## Registration safety

- **Identity-only writes.** Before saving, the tool snapshots every non-identity
  tag value and FLAC picture, applies changes to a staged copy, and rejects the
  write if semantic non-identity metadata or artwork changes. MP3 writes
  explicitly preserve a detected ID3v2.3 or ID3v2.4 major version. Full-file
  backups protect container-level details that are not represented by the
  semantic snapshot.
- **Full byte-for-byte backup** for every file whose audio tags are written,
  stored under the profile directory (`identity-backups/<session>/`) with a
  `recovery.json` containing original path, SHA-256 checksums before/after,
  fingerprint, identity and recovery instructions. Backups are verified by hash
  before the original is touched. Index-only registrations do not modify audio
  files and therefore do not create audio-file backups.
- **Atomic replacement.** The staged file is verified (identity tags re-read,
  fingerprint recomputed) and then moved over the original with
  `os.replace` only after the source is re-verified unchanged.
- **Conflict refusal.** If an existing identity value disagrees with the index
  or the proposed association, that file is aborted, never silently overwritten.
- **Interrupt-safe.** Cancellation between steps always leaves the original
  file and any completed backup intact; a leftover `.tidal-identity-*` staging
  file in the music folder can simply be deleted.

If "identity tags saved, but index update failed" is reported, the audio file
is valid and the backup exists; re-running the scan repairs the index.

## Integration with downloads

- The download skip check ([`local_identity.py`](tidal_dl/local_identity.py))
  consults the cached audio index and dedicated identity fields. A registered
  file can be resolved through its Spotify ID, TIDAL ID, or `TIDAL_DL_ID` even
  after filename or descriptive-tag edits, provided at least one dedicated
  identity field survives.
- The GUI local-file lookup ([`gui_table_handler.py`](tidal_dl/gui/gui_table_handler.py))
  uses the same private UID fallback while checking registered TIDAL identities.
- If all dedicated identity fields are stripped, the explicit registration scan
  can recover an existing identity from decoded-audio fingerprinting. The normal
  download hot path does not decode every local file merely to perform a lookup.
- New downloads already carry `SPOTIFY_TRACK_ID` / `TIDAL_TRACK_ID` tags; run
  registration once to index older files.

## Diagnostics

All identity modules log at `WARNING` by default so normal downloads stay
quiet. Set the environment variable before launching to enable detailed
diagnostics (`IDENTITY_HASH_START/DONE`, `IDENTITY_SCAN_ROW`,
`IDENTITY_INDEX_REGISTER`, `IDENTITY_REGISTERED`, failures with tracebacks):

```
TIDAL_DL_IDENTITY_LOG_LEVEL=DEBUG
```

## Limits

- Fingerprinting decodes each file once (cached afterwards); large libraries
  take time on first scan. Progress is shown and cancellation is safe.
- The index matches audio content; two genuinely identical copies of the same
  recording share one identity by design.
- Index schema version 1 stores one Spotify ID and one TIDAL ID per decoded
  fingerprint. If identical decoded audio is associated with conflicting
  provider IDs, registration reports a conflict for manual investigation rather
  than silently replacing an existing association.
- Restoring a backup restores **all** tags to their pre-registration values —
  preserve any later VirtualDJ edits before restoring.
- Spotify login is required only to propose legacy Spotify associations. Without
  login, the scan can still verify decoded audio and existing identity tags.
- The scan never deletes, renames or moves files.
