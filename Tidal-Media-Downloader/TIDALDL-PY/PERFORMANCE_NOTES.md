# Performance improvements for large playlist libraries

## Observed symptoms

The supplied logs show a scan of 12,298 audio files taking 1,426.516 seconds,
with no unchanged-file metadata cache hits. They also show GUI responsiveness
gaps while loading Spotify playlists, creating identity-review rows, selecting
ready files, and starting registration. Stack samples identify where the GUI
was executing; they do not by themselves establish the cost of individual calls.

## Changes

- Identity-review rows are created in timer-driven batches of at most 100 rows,
  yielding after roughly 12 milliseconds of row creation. This is a cooperative
  budget, not a guarantee that every Qt operation completes within 12 milliseconds.
  Registration stays disabled until the entire review is ready. Closing the
  dialog stops pending rendering; a rendering error discards the incomplete review.
- Ready-file selection groups adjacent rows into ranges. Selection bookkeeping
  reads ranges rather than asking Qt to enumerate and validate individual selected
  row indexes. Ready/review counts are computed during rendering, and changing
  busy state no longer rebuilds selection. Actual associations are still collected
  from the current review choices when registration is requested.
- Worker progress notifications are limited to approximately ten per second.
  Completion and failure results are not throttled.
- Identity scans reuse a thread-local read connection for the duration of a scan.
  Identity lookups still query the database rather than retaining a stale identity
  snapshot. Writes keep their own fully synchronous transactions and existing
  conflict checks. The read connection closes on success, cancellation, or failure.
- When uncached audio metadata is needed, identity validation and recording
  matching reuse the same parsed container instead of opening it twice. File
  signatures are checked before caching newly read facts.
- Spotify tree population temporarily suppresses repainting and no longer forces
  repeated per-row layout activation and viewport updates. It still constructs
  playlist widgets synchronously; this reduces work rather than eliminating every
  possible startup stall.

## Safety and remaining costs

No identity conflicts are automatically resolved. Metadata-only associations still
require review and explicit confirmation. Full audio backups, backup hash checks,
staged audio verification, source-change checks, and durable database commits have
not been removed or weakened to gain speed. Applying identity tags can therefore
remain I/O-intensive, especially with backups stored in a cloud-synchronized folder.

The unchanged-file scan cache already existed before these changes. Its future hit
rate depends on successful cache writes and unchanged file signatures. A cold scan
still needs to read metadata; the supplied logs alone cannot determine how much of
the scan time came from storage, metadata parsing, or database activity.

## Validation status

Changes were reviewed by source and diff inspection only. The application was not
run, built, compiled, or benchmarked, and no test code was created. No measured
speedup is claimed. Existing scan timing and cache counters remain available, and
an additional completion log records identity-review row count and rendering time.
