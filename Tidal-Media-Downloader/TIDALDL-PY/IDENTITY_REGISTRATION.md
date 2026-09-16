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

2. **A persistent recording, audio-content and scan index**
   [`identity_index.py`](tidal_dl/identity_index.py) stores durable recording
   UIDs separately from exact audio fingerprints. One recording can therefore
   accumulate multiple fingerprint forms over time. The same local SQLite file
   also keeps a signature-keyed read-only snapshot of identity and matching
   fields so unchanged files do not need to be reopened on every maintenance scan.

   FLAC uses the native STREAMINFO decoded-PCM MD5 together with sample rate,
   channel count, bit depth and total sample count. Reading this identity does
   not decode the track.

   MP3 and M4A/MP4 use SHA-256 over the demuxed encoded audio stream through
   FFmpeg stream copy. The compressed audio is read, but it is not decoded to
   PCM. Filename, ordinary tags, artwork and MP4/ID3 metadata do not participate
   in this fingerprint.

   Version-1 decoded-PCM fingerprints remain mapped during the automatic SQLite
   schema migration and can coexist with the newer fast fingerprints.

If a dedicated identity tag survives metadata edits, identity lookup is
immediate. If dedicated tags are unavailable, the exact content fingerprint
can recover the recording through the local index.

## How the scan works

Before walking local files, the scanner builds its playlist-folder catalog
exclusively from locally persisted data (`spotify_to_tidal_links.json`, stored
Spotify track details and cached folder hints). It does not log in to Spotify,
refresh playlists, fetch tracks, or make any Spotify API call. Cached playlist
data can be incomplete or stale, so it is supporting evidence rather than an
authoritative statement of current playlist membership.

The scan ([`identity_registration.py`](tidal_dl/identity_registration.py)) then
walks the selected playlist root and, for each supported audio file (FLAC,
MP3, M4A/MP4):

1. Checks the persistent unchanged-file scan cache using normalized path plus
   file size, modification time, creation/change time and inode/file ID.
2. On a cache hit, reuses the previously read identity and matching fields without
   reopening the audio container. On a miss, reads the local audio metadata and
   stores those facts for later scans.
3. Reuses a valid path/fingerprint cache when available.
4. Defers content fingerprinting when dedicated identity fields already provide
   sufficient identity evidence.
5. When embedded identity is unavailable, computes the fast format-specific
   fingerprint: FLAC STREAMINFO MD5 or encoded-audio stream-copy SHA-256.
6. Transparently maps a current fast fingerprint to an existing version-1
   identity when an unchanged legacy cache entry proves that association.
7. Uses locally cached Spotify playlist metadata only as legacy association
   evidence. Metadata-only candidates remain review-required.

The first scan after this cache is introduced will populate it. Later scans will
reuse it for unchanged paths. Renames, tag edits, file replacement or any stored
signature change will invalidate the entry automatically and force a fresh read.
Missing local Spotify cache data never triggers an online refresh.

Each row shows one status:

| Status | Meaning | Action |
|---|---|---|
| Registered | Already indexed and tagged consistently | None needed |
| Ready to register | Verified via the audio index or consistent tags; association confirmed | Select explicitly to add missing identity data |
| Needs review | Metadata-only legacy match; not audio proof | Choose the association explicitly, or keep as-is |
| Unmatched | No reliable association found | Left untouched |
| Conflict / error | Conflicting IDs, unreadable audio, changed file | Reported; nothing changed |

Nothing is selected automatically. **Select ready to register** selects only
confirmed, unregistered rows. **Select ready + review** also includes metadata-only
**Needs review** rows and is disabled when there are no review rows.
**Registered** rows are never selectable.

## Registration safety

- **Identity-only writes.** Before saving, the tool snapshots every non-identity
  tag value and FLAC picture, applies changes only to a temporary staged copy,
  and rejects the write if semantic non-identity metadata or artwork changes.
  MP3 writes explicitly preserve a detected ID3v2.3 or ID3v2.4 major version.
- **No persistent full-audio backups.** Registration does not retain complete
  recovery copies of modified audio files. Each tag-writing operation creates
  only one temporary `.tidal-identity-*` copy beside the source file so the
  final `os.replace` remains on the same filesystem.
- **Bounded temporary storage.** Selected files are registered sequentially, so
  one registration worker retains at most one full-sized staged audio file at a
  time. The staged copy is removed immediately after that file completes,
  fails, or is cancelled through the normal code path.
- **Crash-recovery journal.** Before a staged audio copy can be created, a tiny
  transaction manifest is atomically written and flushed to machine-local
  application state (`%LOCALAPPDATA%\Tidal-DL\identity-staging` on Windows).
  Full audio data is never written to that journal directory.
- **Automatic stale cleanup.** Normal cleanup removes the staged audio file and
  its transaction manifest. If hard termination, operating-system failure or
  power loss prevents normal cleanup, the next application launch examines the
  journal and removes staged files owned by processes that no longer exist.
  Pending journal writes from dead processes are also removed.
- **Concurrent-instance safety.** Transaction manifests contain the creator PID.
  Startup cleanup leaves transactions belonging to a still-running process
  untouched, preventing another active application instance from having its
  staging file removed.
- **Verified staging.** The temporary audio copy is SHA-256 checked against the
  original before mutation. After identity tags are written, non-identity
  metadata, artwork, embedded identity and the audio fingerprint are verified
  before replacement.
- **Atomic replacement.** The verified staged file is moved over the original
  with `os.replace` only after the source is re-verified unchanged.
- **Conflict refusal.** If an existing identity value disagrees with the index
  or the proposed association, that file is aborted and never silently
  overwritten.
- **Deferred cleanup on file locks.** If a staging file cannot be deleted
  immediately because another process still holds it, its transaction manifest
  is retained so cleanup can be retried on a later application launch.

If "identity tags saved, but index update failed" is reported, the audio file is
valid and re-running the scan repairs the index. No persistent rollback copy is
retained.

## Integration with downloads

- New downloads reserve or reuse a durable recording UID from the locally
  stored Spotify/TIDAL provider IDs before metadata writing.
- `TIDAL_DL_ID`, `SPOTIFY_TRACK_ID` when applicable, and `TIDAL_TRACK_ID` are
  written into the completed file.
- After tagging, the completed audio is registered with the fast
  format-specific fingerprint. FLAC registration reads STREAMINFO only;
  MP3/M4A registration hashes the compressed audio stream without decoding it.
- Failure to update the local content index does not invalidate an otherwise
  completed download; embedded provider identity remains available and the
  maintenance scan can repair the index later.
- The download skip check ([`local_identity.py`](tidal_dl/local_identity.py))
  consults the cached audio index and dedicated identity fields.
- The GUI local-file lookup
  ([`gui_table_handler.py`](tidal_dl/gui/gui_table_handler.py)) uses the same
  private UID fallback.
- Content fingerprinting remains a recovery layer rather than the first lookup
  mechanism. Normal recognition through dedicated identity fields does not
  require audio hashing.

## Diagnostics

High-level identity operations log at `INFO` by default, including worker
start/completion, cache-only catalog counts, mapped folders, scan totals,
unchanged-file scan-cache hits, fingerprint cache hits, computed fingerprints,
deferred fingerprints, legacy migrations and elapsed time. Per-file fingerprint
details remain at `DEBUG`.

Set `TIDAL_DL_IDENTITY_LOG_LEVEL=DEBUG` before launch for detailed identity
diagnostics.

Useful detailed events include `IDENTITY_FINGERPRINT_CACHE_HIT`,
`IDENTITY_FINGERPRINT_LEGACY_CACHE`,
`IDENTITY_FINGERPRINT_STREAMCOPY_START`,
`IDENTITY_FINGERPRINT_DONE`, `IDENTITY_SCAN_CACHE_WRITE`,
`IDENTITY_SCAN_ROW`,
`IDENTITY_INDEX_REGISTER`, `DL_IDENTITY_INDEXED`,
`IDENTITY_REGISTERED`, and failure tracebacks.

## Limits

- FLAC STREAMINFO MD5 is extremely cheap to read and identifies identical
  decoded lossless PCM. Re-encoding identical PCM as FLAC therefore retains
  the same FLAC content identity.
- MP3/M4A fingerprints require one pass over the compressed audio packets, but
  do not perform codec decoding or expand the stream to PCM.
- A metadata or filename change does not alter either current fingerprint form
  as long as the encoded audio itself is unchanged.
- Transcoding changes the MP3/M4A encoded-audio fingerprint. Such cases remain
  review-oriented rather than being silently treated as identical.
- Acoustic-similarity fingerprinting is intentionally not used for automatic
  duplicate suppression because similarity is weaker evidence than exact audio
  identity.
- SQLite schema version 2 stores recording identity independently of
  fingerprints, allowing legacy decoded-PCM fingerprints and newer fast
  fingerprints to resolve to the same recording UID. The additive `scan_cache`
  table is only an acceleration layer; a signature mismatch makes its row unusable.
- Cached Spotify metadata can be incomplete or outdated. Identity maintenance
  never contacts Spotify to fill those gaps automatically.
- Registration does not retain full-file rollback backups; source protection
  relies on verified staging followed by atomic replacement.
- Legacy `identity-backups` directories created by earlier versions are not
  deleted automatically because they contain intentionally retained recovery
  material. New registrations do not add files to those directories.
- The registration scan never deletes, renames or moves normal audio files.
