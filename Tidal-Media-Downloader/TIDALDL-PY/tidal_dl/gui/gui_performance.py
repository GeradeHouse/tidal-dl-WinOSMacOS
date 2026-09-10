"""Bounded GUI heartbeat sampling; never access Qt objects from the sampler."""

import logging
import os
import sys
import threading
import time


class UIStallWatchdog:
    """Sample the GUI Python stack while its event-loop heartbeat is delayed.

    Native calls holding the GIL can also delay this thread. Stack samples are
    diagnostic evidence, not a native profiler or proof of the blocking cause.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._gui_thread_id = threading.get_ident()
        self._last_heartbeat = time.perf_counter()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="gui-stall-watchdog", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def heartbeat(self) -> None:
        self._last_heartbeat = time.perf_counter()

    def stop(self) -> None:
        # Never join from the GUI: a console handler may itself be blocked.
        self._stop.set()

    def _run(self) -> None:
        next_report = 0.0
        while not self._stop.wait(0.25):
            now = time.perf_counter()
            elapsed = now - self._last_heartbeat
            if elapsed < 0.75 or now < next_report:
                continue
            next_report = now + 5.0
            frame = None
            try:
                frame = sys._current_frames().get(self._gui_thread_id)
                stack = []
                while frame is not None and len(stack) < 24:
                    code = frame.f_code
                    stack.append(
                        f"{os.path.basename(code.co_filename)}:{frame.f_lineno} in {code.co_name}"
                    )
                    frame = frame.f_back
            finally:
                # Do not retain frame references, locals, or source text.
                del frame
            if not self._stop.is_set():
                self._logger.warning(
                    "UI stall stack sample | heartbeat_age_ms=%.1f "
                    "gui_thread=%s | innermost first:\n%s",
                    elapsed * 1000.0,
                    self._gui_thread_id,
                    "\n".join(stack) or "Python frame unavailable",
                )
