import sys

print("DEBUG_TRACE: logging_config.py - Top level execution start", file=sys.stderr)
# --- START OF FILE logging_config.py ---

#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
Logging Configuration for Tidal-Media-Downloader.

This module sets up a customized logging system for the application.
It provides a console handler (writing to stderr) with features like:
- Colored output for ERROR messages for better visibility.
- Inclusion of the source filename in log records for easier debugging.
- Specific filters to suppress common noisy messages, such as:
    - "Empty response returned from API."
    - Verbose DEBUG messages from the `urllib3` library.
    - Verbose DEBUG/INFO messages from the `linking` module related to
      successful and confident track links (score <= 2).

Key Components:
- ColorFormatter: Custom formatter for colored output and filename inclusion.
- SuppressEmptyResponseFilter: Filters out specific API empty response messages.
- SuppressUrllib3DebugFilter: Filters out specific noisy `urllib3` DEBUG messages.
- SuppressConfidentLinkingFilter: Filters out noisy successful linking messages.
- setup_logging(): The main function to configure the root logger. It handles
  potential pre-existing basic handlers and ensures the custom configuration
  is applied correctly.

Dependencies:
- logging: Python's standard logging library.
- sys: Used to access stderr.

Usage:
Import `setup_logging` and call it early in the application's startup sequence:

    from .logging_config import setup_logging
    setup_logging()

Note: The `SuppressUrllib3DebugFilter` contains specific rules to target common
      `urllib3` messages. If `urllib3` changes its logging messages or if other
      noisy messages appear, this filter might need to be updated.
"""

# --- IMPORTS ---
import logging

# Monkey patch StreamHandler.emit to handle None stream (fixes PyInstaller issues)
original_emit = logging.StreamHandler.emit

def safe_emit(self, record):
    if self.stream is None:
        return
    try:
        original_emit(self, record)
    except Exception:
        pass

logging.StreamHandler.emit = safe_emit

# --- CONSTANTS ---

# ANSI escape codes for terminal colors
COLOR_RED = "\033[91m"
COLOR_RESET = "\033[0m"

# --- CUSTOM FORMATTERS ---


class ColorFormatter(logging.Formatter):
    """
    A logging formatter that adds red color to ERROR messages and includes
    the source filename in the log record.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Formats the log record, adding color and filename.

        Args:
            record (logging.LogRecord): The log record to format.

        Returns:
            str: The formatted log message string.
        """
        # Define the log format dynamically to include the filename
        # where the log message originated.
        
        log_fmt = f"%(levelname)s:{record.filename}:%(message)s"

        # Use a standard formatter temporarily to apply the format string
        formatter = logging.Formatter(log_fmt)
        log_message = formatter.format(record)

        # Apply red color only to ERROR level messages for emphasis
        if record.levelno == logging.ERROR:
            return f"{COLOR_RED}{log_message}{COLOR_RESET}"

        # Return the standard formatted message for other levels
        return log_message


# --- CUSTOM FILTERS ---


class SuppressEmptyResponseFilter(logging.Filter):
    """
    Filters out the specific 'Empty response returned from API.' message
    which can be common and often does not indicate a critical problem.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Determines if a log record should be processed.

        Args:
            record (logging.LogRecord): The log record to check.

        Returns:
            bool: False if the message should be suppressed, True otherwise.
        """
        # Suppress the record if its message matches exactly
        return record.getMessage() != "Empty response returned from API."


class SuppressUrllib3DebugFilter(logging.Filter):
    """
    Filters out specific verbose DEBUG messages originating from the urllib3
    library, particularly from connectionpool and retry logic, to reduce noise
    during operations like downloads.

    Modify the conditions inside the `filter` method to add or remove rules
    for suppressing specific `urllib3` messages based on logger name and
    message prefix.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Determines if a urllib3 DEBUG log record should be processed.

        Args:
            record (logging.LogRecord): The log record to check.

        Returns:
            bool: False if the message should be suppressed, True otherwise.
        """
        # We only care about filtering DEBUG level messages
        if record.levelno == logging.DEBUG:
            # --- Filter rules for 'urllib3.util.retry' logger ---
            if record.name == "urllib3.util.retry":
                # Suppress messages about retry value conversion
                if record.getMessage().startswith("Converted retries value:"):
                    return False  # Suppress this message

            # --- Filter rules for oauth2 / spotipy debug messages ---
            if record.name == "oauth2" or "oauth2" in record.name:
                return False  # Suppress all oauth2 debug messages
            
            # --- Filter rules for 'urllib3.connectionpool' logger ---
            if record.name == "urllib3.connectionpool":
                msg = record.getMessage()
                # Suppress messages about starting new connections (both HTTP and HTTPS)
                if msg.startswith("Starting new HTTP connection") or msg.startswith("Starting new HTTPS connection"):
                    return False
                # Suppress messages about specific Tidal domain connections
                # (audio content, API, listen endpoint, resources)
                if msg.startswith("https://sp-pr-fa.audio.tidal.com:443 ") or msg.startswith("http://sp-ad-fa.audio.tidal.com:80 "):
                    return False
                if msg.startswith("https://api.tidalhifi.com:443 "):  # Main API v1
                    return False
                if msg.startswith("https://listen.tidal.com:443 "):  # Lyrics, etc.
                    return False
                if msg.startswith("https://resources.tidal.com:443 "):  # Images
                    return False
                if msg.startswith("https://api.tidal.com:443 "):  # Base API (sessions)
                    return False
                if msg.startswith("https://lgf.audio.tidal.com:443 "):  # Non-HTTPS audio (rare)
                    return False
                # Suppress messages about specific Spotify domain connections
                if msg.startswith("https://api.spotify.com:443 "):  # Spotify API
                    return False
                if msg.startswith("https://mosaic.scdn.co:443 "):  # Spotify Image Mosaics
                    return False
                if msg.startswith("https://image-cdn-ak.spotifycdn.com:443 "):  # Spotify Image CDN
                    return False
                if msg.startswith("https://i.scdn.co:443 "):  # Spotify Image CDN
                    return False

        # If none of the suppression rules matched, allow the message to pass
        return True


# --- NEW FILTER for Linking ---


class SuppressConfidentLinkingFilter(logging.Filter):
    """
    Filters out DEBUG and INFO messages from 'linking.py' that indicate a
    confident and successful track link, reducing noise when focusing on
    linking errors or uncertain matches.

    Specifically targets:
    - INFO messages about confident matches (score <= 2).
    - DEBUG messages confirming a row was linked.
    - DEBUG messages confirming a link was persisted.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Determines if a linking log record should be processed based on context
        and confidence level. Buffering in linking.py handles intermediate steps.

        Args:
            record (logging.LogRecord): The log record to check.

        Returns:
            bool: False if the message should be suppressed, True otherwise.
        """
        # --- ADD THIS CHECK AT THE BEGINNING ---
        # Only apply linking suppression logic if the message is from the linking module
        # Assuming the logger in linking.py is logging.getLogger(__name__) which becomes 'tidal_dl.linking'
        if record.name != "tidal_dl.linking":
            # If the record is NOT from the linking logger, let it pass through this filter.
            return True
        # --- END ADDED CHECK ---

        # --- Existing logic (now only applied to linking messages) ---
        msg = record.getMessage()

        # --- Early Suppression (Non-linking specific) ---
        # Note: These might be better placed in a separate filter if they are truly global rules.
        # For now, keeping them here but they only run if record.name == 'tidal_dl.linking'
        # due to the check above. If you want these global, move them out or remove the name check.
        if record.levelno == logging.DEBUG:
            if record.filename == "gui.py" and msg.startswith(
                "LinkingWorker thread started"
            ):
                return False
            if record.filename == "persistence.py" and msg.startswith(
                "Successfully saved links to"
            ):
                return False
            if record.filename == "linking.py" and msg.startswith(
                "LinkingWorker loop finished"
            ):
                return False

        # --- Linking Specific Suppression ---
        # Apply filters to messages related to linking outcomes
        if record.filename == "linking.py":
            # Filter INFO: Suppress "Confident metadata match found" only if score <= 2
            if record.levelno == logging.INFO and msg.startswith(
                "Confident metadata match found:"
            ):
                try:
                    score_part = msg.split(" with score ")[-1]
                    score = int(score_part.split(".")[0])
                    # Suppress only if score is 0 or 1 (aligning with linking.py logic)
                    if score < 2:
                        return (
                            False  # Suppress this specific INFO for confident matches
                        )
                except (ValueError, IndexError):
                    pass  # Ignore parsing errors

            # Filter DEBUG: Suppress "Persisted link" confirmation
            if record.levelno == logging.DEBUG and msg.startswith("Persisted link:"):
                return False

            # Filter DEBUG: Suppress "Searching Tidal by ISRC:"
            if record.levelno == logging.DEBUG and msg.startswith(
                "Searching Tidal by ISRC:"
            ):
                return False  # Suppress this initial search step log

            # Filter DEBUG: Suppress "Attempting to link Spotify track:"
            if record.levelno == logging.DEBUG and msg.startswith(
                "Attempting to link Spotify track:"
            ):
                return False  # Suppress this initial step log

            # Filter DEBUG: Suppress "Searching Tidal by metadata query:"
            if record.levelno == logging.DEBUG and msg.startswith(
                "Searching Tidal by metadata query:"
            ):
                return False  # Suppress this initial search step log

            # Filter DEBUG: Suppress "Final Best Match" if score is 0 or 1
            if record.levelno == logging.DEBUG and msg.strip().startswith(
                "- Final Best Match (Pre-Confidence Check):"
            ):
                try:
                    score_part = msg.split("Final Score: ")[-1]
                    score = int(score_part.split(".")[0])
                    if score <= 1:  # Suppress for score 0 or 1
                        return False
                except (ValueError, IndexError):
                    pass  # Ignore parsing errors

            # Filter DEBUG: Suppress "Linking finished... Result: Found"
            if (
                record.levelno == logging.DEBUG
                and msg.startswith("Linking finished for row")
                and msg.endswith("Result: Found")
            ):
                return False

            # Filter DEBUG: Suppress "Row X: Linked..."
            if (
                record.levelno == logging.DEBUG
                and msg.startswith("Row ")
                and ": Linked. Tidal ID:" in msg
            ):
                return False

            # NOTE: Intermediate DEBUG messages ("Attempting...", "Searching...", etc.)
            # are now handled by the buffering logic within linking.py itself.

        # Filter INFO: Suppress "[INFO] Linking finished for row X: Found" (likely from Printf)
        # Check level, start, and end pattern. Might need adjustment if Printf changes.
        if (
            record.levelno == logging.INFO
            and msg.startswith("Linking finished for row")
            and msg.endswith(": Found")
        ):
            return False

        # If none of the suppression rules above matched, allow the message to pass
        return True


# --- LOGGING SETUP FUNCTION ---

if sys.stderr is not None:
    print("DEBUG_TRACE: logging_config.py - Inside setup_logging()", file=sys.stderr)


def setup_logging():
    """
        Configures the root logger for the application.

        This function ensures a single, custom console handler is attached to the
        root logger. It performs the following steps:
        1. Gets the root logger instance.
        2. Removes any pre-existing basic StreamHandlers pointing to stderr that
           might have been added implicitly (e.g., by logging.basicConfig()).
        3. Checks if a custom handler (identified by ColorFormatter) already exists.
        4. If no custom handler exists:
            a. Sets the root logger's level to DEBUG.
            b. Creates a new StreamHandler for stderr.
            c. Creates and applies the ColorFormatter.
            d. Creates and adds the SuppressEmptyResponseFilter.
            e. Creates and adds the SuppressUrllib3DebugFilter.
            f. Creates and adds the SuppressConfidentLinkingFilter. # Added
            g. Adds the configured handler to the root logger.
            h. Configures specific `urllib3` sub-loggers (`connectionpool`, `retry`)
               to ensure their DEBUG messages are generated and propagate correctly
               to the root handler for filtering, clearing any handlers attached
               directly to them.
            h. Logs a confirmation message.
    print(f"DEBUG_TRACE: logging_config.py - Getting root logger. Current level: {logging.getLevelName(logging.getLogger().level)}, Handlers: {logging.getLogger().handlers}", file=sys.stderr)
    """
    logger = logging.getLogger()  # Get the root logger instance
    if sys.stderr is not None:
        print(
            "DEBUG_TRACE: logging_config.py - Checking for basic handlers to remove...",
            file=sys.stderr,
        )

    # --- Remove Pre-existing Basic Handlers ---
    # Iterate over a copy of the handlers list to allow safe removal.
    # This step prevents conflicts if basicConfig was called implicitly
    # before this function, ensuring our custom handler takes precedence.
    for handler in logger.handlers[:]:
        # Check if it's a StreamHandler writing to the standard error stream
        if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stderr:
            # Heuristic check: Assume it's a basic handler if it doesn't use
            # our custom formatter or filters. This might need adjustment if
            # other parts of the code add complex stderr handlers.
            is_basic_handler = True
            if isinstance(handler.formatter, ColorFormatter):
                is_basic_handler = False  # It uses our formatter
            if any(
                isinstance(f, (SuppressEmptyResponseFilter, SuppressUrllib3DebugFilter))
                for f in handler.filters
            ):
                is_basic_handler = False  # It uses one of our filters

            # If it looks like a basic, unconfigured handler, remove it.
            if is_basic_handler:
                if sys.stderr is not None:
                    print(
                        f"DEBUG_TRACE: logging_config.py - Removing basic handler: {handler}",
                        file=sys.stderr,
                    )
                logger.removeHandler(handler)
                # print(f"DEBUG: Removed pre-existing basic stderr handler: {handler}", file=sys.stderr) # Uncomment for debug

    # --- Check if Custom Handler Already Exists ---
    # This prevents adding duplicate handlers if setup_logging is called multiple times.
    if sys.stderr is not None:
        print(
            "DEBUG_TRACE: logging_config.py - Checking if custom handler exists...",
            file=sys.stderr,
        )
    # We identify our handler by checking for the specific ColorFormatter.
    handler_exists = any(
        isinstance(h, logging.StreamHandler)
        and h.stream == sys.stderr
        and isinstance(h.formatter, ColorFormatter)
        for h in logger.handlers
    )

    # --- Instantiate Filters ---
    # Create filter instances once, to be potentially added to multiple handlers.
    empty_response_filter = SuppressEmptyResponseFilter()
    urllib3_filter = SuppressUrllib3DebugFilter()
    linking_filter = SuppressConfidentLinkingFilter()

    # --- Add Custom Handler (if it doesn't exist) ---
    if not handler_exists:
        if sys.stderr is not None:
            print(
                "DEBUG_TRACE: logging_config.py - Custom handler does NOT exist. Creating new handler.",
                file=sys.stderr,
            )
        # Check if stderr is available (might be None in bundled apps)
        if sys.stderr is None:
            if sys.stderr is not None:
                print(
                    "DEBUG_TRACE: logging_config.py - sys.stderr is None, skipping console handler setup.",
                    file=sys.stderr,
                )
            # Set logger level anyway
            logger.setLevel(logging.NOTSET)
        else:
            # Set the root logger level. All messages at this level or higher
            # will be processed by handlers unless filtered.
            logger.setLevel(logging.DEBUG)  # Allow all levels, let child loggers control their own levels

            # Create and configure the custom console handler
            # Use default stream (current sys.stderr) to follow redirection
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG)  # Set handler to DEBUG level to respect individual logger levels
            color_formatter = ColorFormatter()
            console_handler.setFormatter(color_formatter)
            # By NOT setting the handler's level, it defaults to NOTSET.
            # This makes the handler respect the level of each individual logger.

            # Add filters to the NEW handler (Re-enabled)
            console_handler.addFilter(empty_response_filter)
            console_handler.addFilter(urllib3_filter)
            console_handler.addFilter(linking_filter)
            # logger.debug("Custom filters temporarily disabled for debugging.") # Remove debug message

            # Add the fully configured handler to the root logger
            logger.addHandler(console_handler)
            if sys.stderr is not None:
                print(
                    f"DEBUG_TRACE: logging_config.py - Added new handler: {console_handler}",
                    file=sys.stderr,
                )

        # --- Configure Specific Third-Party Loggers ---
        # Ensure specific noisy loggers (like urllib3 sub-loggers) are set
        # to DEBUG level so their messages *can* reach our filter, and ensure
        # they propagate messages upwards. Also, clear any handlers attached
        # directly to them to avoid duplicate output.
        for logger_name in ["urllib3.connectionpool", "urllib3.util.retry"]:
            ul3_logger = logging.getLogger(logger_name)
            ul3_logger.setLevel(logging.DEBUG)  # Ensure DEBUG messages are generated
            ul3_logger.propagate = False  # Ensure messages DO NOT reach root handler
            if ul3_logger.hasHandlers():
                ul3_logger.handlers.clear()  # Prevent direct handling

        # Optional: Set the base 'urllib3' logger level higher if desired,
        # while keeping sub-loggers at DEBUG for filtering.
        logging.getLogger("urllib3").setLevel(logging.INFO)

        # Suppress verbose PyInstaller logs in bundled applications
        logging.getLogger('PyiFrozenFinder').setLevel(logging.WARNING)
        logging.getLogger('PyInstaller').setLevel(logging.WARNING)

        # Log confirmation that setup is complete (using the new handler)
        logger.debug("Logging setup complete using ColorFormatter.")
    # else:
    # Optional: Log if setup was skipped because handler already exists
    # logger.debug("Custom logging handler already exists. Skipping setup.")

    # --- Ensure Filters are on ALL Root Handlers ---
    # Iterate through all handlers currently on the root logger.
    # This ensures that even if a basic handler wasn't removed earlier,
    # or if other code adds handlers, our filters are applied.
    for handler in logger.handlers:
        # Check if the handler already has our specific filters.
        # We check by type to avoid adding duplicates.
        # Add any missing filters to this handler.
        # Option for temporarily commenting out filter additions for debugging API responses
        # if not has_empty_filter:
        #     handler.addFilter(empty_response_filter)
        #     logger.debug(f"Added SuppressEmptyResponseFilter to handler {handler}")
        # if not has_urllib3_filter:
        #     handler.addFilter(urllib3_filter)
        #     logger.debug(f"Added SuppressUrllib3DebugFilter to handler {handler}")
        # if not has_linking_filter:
        #     handler.addFilter(linking_filter)
        #     logger.debug(f"Added SuppressConfidentLinkingFilter to handler {handler}")
        pass  # Add pass to maintain block structure if all filters are commented out


# --- END OF FILE logging_config.py ---