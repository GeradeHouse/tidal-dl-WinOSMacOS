# Local playlist folder links

## User flow

- Click a playlist's folder button to open its local directory.
- When no directory exists, choose **Browse and link folder…** and select the
  existing folder that contains that playlist's tracks.
- The confirmation identifies the service, playlist name, ID and destination.
  Linking changes future downloads and local-file lookup; it does not move,
  rename, delete or retag existing files.
- Right-click the folder button, or a single playlist row, for **Local folder**
  actions to open, change or remove the link.
- Removing a link restores normal download-path/template behavior. Existing
  audio stays in the formerly linked directory.

## Destination behavior

Explicit links are keyed by service and stable playlist ID, not the display
name. Renaming a playlist or changing the global download root does not change
its explicit destination. A copied/new playlist has a different ID and does
not inherit the link.

The selected directory is the final playlist destination. FLAC, MP3 and other
formats use that directory without adding audio-type or playlist subfolders.
The existing track filename template and output-format selection still apply.
Files outside the linked destination do not count as completed for this link.
Conversions use a temporary working directory when their source and output
extensions differ, so an existing FLAC/M4A in a mixed-format linked folder is
not used as a disposable MP3 conversion intermediate.

An unavailable/unwritable linked folder fails download preflight. The app does
not silently fall back to the normal location or deliberately recreate the
missing directory. Reconnect the drive, relink the playlist or remove its link.
Filesystem permissions can still change after preflight; ordinary download
errors remain applicable.

Folder changes are blocked while queue jobs or a download worker are running,
including paused downloads, and while jobs remain queued. This avoids splitting
one job across destinations. Sidebar counts and the active table are refreshed
after saving a link.

Linking is not proof that every audio file belongs to the playlist. Existing
filename/tag matching remains in use for completion previews, and the download
pipeline retains its recording-identity and output-collision checks. Arbitrary
untagged/renamed audio is not automatically assigned a recording identity merely
because its containing folder was linked.

## Sibling discovery

The confirmation includes an optional sibling scan (enabled by default).
Discovery runs in a background worker and inspects only immediate directories
inside the selected folder's parent. It does not recursively search a drive,
read audio, query the services or follow directory symlinks.

Matches require literal, case-sensitive equality with the complete playlist
name, including punctuation. Only currently loaded TIDAL and Spotify playlists
are considered. Existing explicit links and already-assigned folders are
excluded. No automatic hints are converted to explicit links.

A review table shows each proposed playlist, service, stable ID and full path.
Unique matches are checked initially; duplicate playlist names are unchecked.
The user must choose at most one playlist per folder. A folder cannot be linked
to two playlists, including across services, to avoid mixing future downloads.
All checked changes are saved as one batch, or none are saved. Cancelling the
scan or review leaves the original, individually confirmed link intact.

## Persistence and integration

Links are stored in a versioned, machine-local profile document separate from
automatic folder hints. Writes use a temporary file and atomic replacement;
in-memory links are published only after the save succeeds. Corrupt link data
fails closed rather than silently redirecting downloads. Absolute paths are not
portable between computers or operating systems; relink on the new machine.

Shared path formatting supplies the linked destination to downloads, candidate
file lookup and post-download folder opening. Explicit folders bypass legacy
cross-playlist fallback scans. Download-structure migration skips explicitly
linked folders. Synthetic relative-template lookups used by cleanup/identity
tools explicitly bypass folder overrides to avoid cross-drive path errors.

## Validation status

Implementation was reviewed statically. No application execution, builds,
compilation or automated tests were performed as requested.
