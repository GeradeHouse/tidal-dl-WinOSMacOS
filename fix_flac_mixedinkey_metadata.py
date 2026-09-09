from __future__ import annotations

import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

try:
    from colorama import Fore, Style, init as colorama_init
except ImportError:
    class _NoColor:
        RED = GREEN = YELLOW = CYAN = ""
        BRIGHT = RESET_ALL = ""

    Fore = Style = _NoColor()

    def colorama_init() -> None:
        pass

try:
    from mutagen.flac import FLAC, FLACNoHeaderError
except ImportError:
    print("ERROR: Required Python package 'mutagen' is not available.")
    raise SystemExit(2)


ROOT_DIR = Path(r"F:\Muziek\flac")
LOG_FILENAME = "fix_flac_mixedinkey_metadata.log"
BAR_WIDTH = 28
PRESERVE_FILE_TIMESTAMPS = True

# Recognizes the normal musical-key forms that can have been written by
# TIDAL-DL or other conventional taggers. Examples:
# C, Cm, C minor, C major, F#m, F# minor, Bb major, 5A, 12B.
MUSICAL_KEY_RE = re.compile(
    r"^(?:"
    r"[A-G](?:#|b)?(?:\s*(?:m|min|minor|maj|major))?"
    r"|(?:1[0-2]|[1-9])[AB]"
    r")$",
    re.IGNORECASE,
)


@dataclass
class Counters:
    processed: int = 0
    fixed: int = 0
    compatible: int = 0
    preserved_key: int = 0
    errors: int = 0


def display_path(path: Path, max_width: int = 78) -> str:
    text = str(path)
    if len(text) <= max_width:
        return text
    return "..." + text[-(max_width - 3):]


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--:--"

    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"


def log_line(log_handle: TextIO | None, level: str, message: str) -> None:
    if log_handle is None:
        return

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_handle.write(f"{timestamp} [{level}] {message}\n")
    log_handle.flush()


def scan_flac_files(root: Path) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    scan_errors: list[str] = []

    def onerror(error: OSError) -> None:
        scan_errors.append(f"{error.filename or root}: {error}")

    for directory, dirnames, filenames in os.walk(
        root,
        topdown=True,
        onerror=onerror,
        followlinks=False,
    ):
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)

        base = Path(directory)
        for filename in filenames:
            if filename.lower().endswith(".flac"):
                files.append(base / filename)

    return files, scan_errors


def find_tag_name(audio: FLAC, wanted: str) -> str | None:
    wanted_folded = wanted.casefold()

    for key in audio.keys():
        if str(key).casefold() == wanted_folded:
            return str(key)

    return None


def clean_values(values: list[str] | None) -> list[str]:
    if not values:
        return []

    return [
        str(value).strip()
        for value in values
        if str(value).strip()
    ]


def normalize_key_for_detection(value: str) -> str:
    return (
        value.strip()
        .replace("♯", "#")
        .replace("♭", "b")
    )


def is_musical_key(value: str) -> bool:
    return bool(
        MUSICAL_KEY_RE.fullmatch(
            normalize_key_for_detection(value)
        )
    )


def progress_line(
    counters: Counters,
    total: int,
    started_at: float,
    current_path: Path | None = None,
) -> str:
    fraction = counters.processed / total if total else 1.0
    filled = min(BAR_WIDTH, int(BAR_WIDTH * fraction))
    bar = "#" * filled + "-" * (BAR_WIDTH - filled)

    elapsed = max(time.monotonic() - started_at, 0.000001)
    rate = counters.processed / elapsed if counters.processed else 0.0
    remaining = total - counters.processed
    eta = remaining / rate if rate > 0 else None

    line = (
        f"[{bar}] "
        f"{fraction * 100:6.2f}%  "
        f"{counters.processed:,}/{total:,}  "
        f"Fixed {counters.fixed:,}  "
        f"OK {counters.compatible:,}  "
        f"Kept {counters.preserved_key:,}  "
        f"Err {counters.errors:,}  "
        f"{rate:5.1f} files/s  "
        f"ETA {format_duration(eta)}"
    )

    if current_path is not None:
        line += f"  |  {display_path(current_path)}"

    terminal_width = shutil.get_terminal_size((160, 20)).columns
    if len(line) >= terminal_width:
        line = line[: max(1, terminal_width - 1)]

    return line


def print_progress(
    counters: Counters,
    total: int,
    started_at: float,
    current_path: Path | None = None,
) -> None:
    print(
        "\r" + progress_line(
            counters,
            total,
            started_at,
            current_path,
        ),
        end="",
        flush=True,
    )


def repair_file(path: Path, log_handle: TextIO | None) -> str:
    original_stat = path.stat()
    audio = FLAC(path)

    key_name = find_tag_name(audio, "KEY")
    if key_name is None:
        return "compatible"

    key_values = clean_values(audio.get(key_name))

    # An empty KEY value is not the TIDAL-DL musical-key conflict. Leave it
    # untouched rather than deleting unrelated metadata.
    if not key_values:
        return "preserved_key"

    # Mixed In Key can use KEY for application-specific data. Only plain,
    # recognizable musical-key values are migrated. Anything else is
    # preserved to avoid destroying existing Mixed In Key metadata.
    if not all(is_musical_key(value) for value in key_values):
        log_line(
            log_handle,
            "PRESERVED",
            f"Non-musical KEY left untouched in '{path}': {key_values!r}",
        )
        return "preserved_key"

    initial_key_name = find_tag_name(audio, "INITIALKEY")
    initial_values = (
        clean_values(audio.get(initial_key_name))
        if initial_key_name is not None
        else []
    )

    # Preserve an existing INITIALKEY. If none exists, migrate the first
    # plain musical KEY value into INITIALKEY before removing KEY.
    if not initial_values:
        audio["INITIALKEY"] = [key_values[0]]

    del audio[key_name]
    audio.save()

    # Metadata writes normally change the filesystem modification timestamp.
    # Restoring timestamps prevents a metadata-only repair from making an
    # entire music library appear newly modified.
    if PRESERVE_FILE_TIMESTAMPS:
        try:
            os.utime(
                path,
                ns=(
                    original_stat.st_atime_ns,
                    original_stat.st_mtime_ns,
                ),
            )
        except OSError as exc:
            log_line(
                log_handle,
                "WARN",
                (
                    "Metadata fixed but timestamps could not be restored "
                    f"for '{path}': {exc}"
                ),
            )

    if initial_values:
        detail = (
            f"Removed plain musical KEY={key_values!r}; "
            f"existing INITIALKEY={initial_values!r} was preserved"
        )
    else:
        detail = (
            f"Moved plain musical KEY={key_values!r} to "
            f"INITIALKEY={key_values[0]!r} and removed KEY"
        )

    log_line(log_handle, "FIXED", f"{detail}: '{path}'")
    return "fixed"


def open_log_file() -> tuple[TextIO | None, Path]:
    log_path = Path(__file__).resolve().with_name(LOG_FILENAME)

    try:
        return (
            log_path.open(
                "w",
                encoding="utf-8",
                newline="\n",
            ),
            log_path,
        )
    except OSError:
        return None, log_path


def print_header() -> None:
    print()
    print(
        f"{Style.BRIGHT}{Fore.CYAN}"
        "FLAC Mixed In Key Metadata Repair"
        f"{Style.RESET_ALL}"
    )
    print(
        f"{Fore.CYAN}"
        f"{'=' * 42}"
        f"{Style.RESET_ALL}"
    )
    print(f"Root folder : {ROOT_DIR}")
    print(
        "Repair      : plain musical KEY -> INITIALKEY, "
        "then remove KEY"
    )
    print(
        "Protection  : non-musical/application-specific KEY "
        "values remain untouched"
    )
    print()


def print_summary(
    counters: Counters,
    total: int,
    elapsed: float,
    scan_errors: list[str],
    log_path: Path,
    log_enabled: bool,
) -> None:
    print()
    print()
    print(
        f"{Style.BRIGHT}{Fore.CYAN}"
        "Summary"
        f"{Style.RESET_ALL}"
    )
    print(
        f"{Fore.CYAN}"
        f"{'-' * 42}"
        f"{Style.RESET_ALL}"
    )
    print(f"Found                  : {total:,}")
    print(
        f"{Fore.GREEN}"
        f"Fixed                  : {counters.fixed:,}"
        f"{Style.RESET_ALL}"
    )
    print(f"Already compatible     : {counters.compatible:,}")
    print(f"Existing KEY preserved : {counters.preserved_key:,}")
    print(
        f"{Fore.RED}"
        f"File errors            : {counters.errors:,}"
        f"{Style.RESET_ALL}"
    )
    print(
        f"{Fore.YELLOW}"
        f"Scan errors            : {len(scan_errors):,}"
        f"{Style.RESET_ALL}"
    )
    print(f"Elapsed                : {format_duration(elapsed)}")

    if log_enabled:
        print(f"Log                    : {log_path}")
    else:
        print(
            f"{Fore.YELLOW}"
            "Log                    : unavailable"
            f"{Style.RESET_ALL}"
        )


def main() -> int:
    colorama_init()

    print_header()

    if not ROOT_DIR.exists():
        print(
            f"{Fore.RED}"
            f"ERROR: Root folder does not exist: {ROOT_DIR}"
            f"{Style.RESET_ALL}"
        )
        return 2

    if not ROOT_DIR.is_dir():
        print(
            f"{Fore.RED}"
            f"ERROR: Root path is not a directory: {ROOT_DIR}"
            f"{Style.RESET_ALL}"
        )
        return 2

    log_handle, log_path = open_log_file()

    try:
        print(
            f"{Fore.CYAN}"
            "Scanning folders for FLAC files..."
            f"{Style.RESET_ALL}"
        )

        files, scan_errors = scan_flac_files(ROOT_DIR)

        for message in scan_errors:
            log_line(log_handle, "SCAN-ERROR", message)

        total = len(files)
        print(
            f"Found "
            f"{Style.BRIGHT}{total:,}{Style.RESET_ALL} "
            "FLAC file(s)."
        )

        if total == 0:
            print(
                f"{Fore.YELLOW}"
                "No FLAC files were found. Nothing was changed."
                f"{Style.RESET_ALL}"
            )
            return 0

        counters = Counters()
        started_at = time.monotonic()

        for path in files:
            try:
                status = repair_file(path, log_handle)

                if status == "fixed":
                    counters.fixed += 1
                elif status == "preserved_key":
                    counters.preserved_key += 1
                else:
                    counters.compatible += 1

            except FLACNoHeaderError as exc:
                counters.errors += 1
                print()
                print(
                    f"{Fore.RED}[INVALID FLAC]{Style.RESET_ALL} "
                    f"{display_path(path, 100)}: {exc}"
                )
                log_line(
                    log_handle,
                    "ERROR",
                    f"Invalid FLAC '{path}': {exc}",
                )

            except PermissionError as exc:
                counters.errors += 1
                print()
                print(
                    f"{Fore.RED}[PERMISSION]{Style.RESET_ALL} "
                    f"{display_path(path, 100)}: {exc}"
                )
                log_line(
                    log_handle,
                    "ERROR",
                    f"Permission error '{path}': {exc}",
                )

            except OSError as exc:
                counters.errors += 1
                print()
                print(
                    f"{Fore.RED}[I/O ERROR]{Style.RESET_ALL} "
                    f"{display_path(path, 100)}: {exc}"
                )
                log_line(
                    log_handle,
                    "ERROR",
                    f"I/O error '{path}': {exc}",
                )

            except Exception as exc:
                counters.errors += 1
                print()
                print(
                    f"{Fore.RED}[ERROR]{Style.RESET_ALL} "
                    f"{display_path(path, 100)}: "
                    f"{type(exc).__name__}: {exc}"
                )
                log_line(
                    log_handle,
                    "ERROR",
                    (
                        f"Unexpected error '{path}': "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )

            finally:
                counters.processed += 1
                print_progress(
                    counters,
                    total,
                    started_at,
                    path,
                )

        elapsed = time.monotonic() - started_at

        print_summary(
            counters,
            total,
            elapsed,
            scan_errors,
            log_path,
            log_handle is not None,
        )

        if counters.errors or scan_errors:
            return 1

        return 0

    except KeyboardInterrupt:
        print()
        print(
            f"{Fore.YELLOW}"
            "Interrupted. Files not yet processed remain unchanged."
            f"{Style.RESET_ALL}"
        )
        return 130

    finally:
        if log_handle is not None:
            log_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
