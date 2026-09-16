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

    _STALL_THRESHOLD_SECONDS = 0.75
    _SAMPLE_INTERVAL_SECONDS = 1.5
    _MAX_STACK_DEPTH = 24

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._gui_thread_id = threading.get_ident()
        self._last_heartbeat = time.perf_counter()
        self._stop = threading.Event()
        self._state_lock = threading.Lock()

        self._episode_counter = 0
        self._active_episode_id = None
        self._episode_started = 0.0
        self._episode_samples = 0
        self._episode_peak_age_ms = 0.0
        self._episode_last_stack_signature = "Python frame unavailable"
        self._episode_expected_block = None
        self._next_sample_at = 0.0
        self._expected_block_reason = None

        self._thread = threading.Thread(
            target=self._run, name="gui-stall-watchdog", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def heartbeat(self) -> None:
        now = time.perf_counter()
        recovery = None

        with self._state_lock:
            self._last_heartbeat = now

            if self._active_episode_id is not None:
                recovery = (
                    self._active_episode_id,
                    (now - self._episode_started) * 1000.0,
                    self._episode_samples,
                    self._episode_peak_age_ms,
                    self._episode_expected_block,
                    self._episode_last_stack_signature,
                )
                self._active_episode_id = None
                self._episode_started = 0.0
                self._episode_samples = 0
                self._episode_peak_age_ms = 0.0
                self._episode_last_stack_signature = "Python frame unavailable"
                self._episode_expected_block = None
                self._next_sample_at = 0.0

        if recovery is not None and not self._stop.is_set():
            (
                episode_id,
                duration_ms,
                sample_count,
                peak_age_ms,
                expected_block,
                last_stack_signature,
            ) = recovery
            self._logger.warning(
                "PERF UI stall recovered | episode=%d duration_ms=%.1f "
                "samples=%d peak_heartbeat_age_ms=%.1f expected_block=%r "
                "last_stack=%s",
                episode_id,
                duration_ms,
                sample_count,
                peak_age_ms,
                expected_block,
                last_stack_signature,
            )

    def set_expected_block(self, reason) -> None:
        with self._state_lock:
            self._expected_block_reason = str(reason) if reason else None
            if (
                self._active_episode_id is not None
                and self._expected_block_reason
                and not self._episode_expected_block
            ):
                self._episode_expected_block = self._expected_block_reason

    def stop(self) -> None:
        # Never join from the GUI: a console handler may itself be blocked.
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            now = time.perf_counter()

            with self._state_lock:
                elapsed = now - self._last_heartbeat
                if elapsed < self._STALL_THRESHOLD_SECONDS:
                    continue

                if self._active_episode_id is None:
                    self._episode_counter += 1
                    self._active_episode_id = self._episode_counter
                    self._episode_started = self._last_heartbeat
                    self._episode_samples = 0
                    self._episode_peak_age_ms = elapsed * 1000.0
                    self._episode_last_stack_signature = "Python frame unavailable"
                    self._episode_expected_block = self._expected_block_reason
                    self._next_sample_at = 0.0

                if now < self._next_sample_at:
                    self._episode_peak_age_ms = max(
                        self._episode_peak_age_ms,
                        elapsed * 1000.0,
                    )
                    continue

                self._next_sample_at = now + self._SAMPLE_INTERVAL_SECONDS
                episode_id = self._active_episode_id
                expected_block = (
                    self._expected_block_reason or self._episode_expected_block
                )

            frame = None
            stack = []
            try:
                frame = sys._current_frames().get(self._gui_thread_id)
                while frame is not None and len(stack) < self._MAX_STACK_DEPTH:
                    code = frame.f_code
                    stack.append(
                        f"{os.path.basename(code.co_filename)}:{frame.f_lineno} in {code.co_name}"
                    )
                    frame = frame.f_back
            finally:
                # Do not retain frame references, locals, or source text.
                del frame

            stack_signature = " > ".join(stack[:6]) or "Python frame unavailable"

            with self._state_lock:
                if self._active_episode_id != episode_id:
                    continue

                self._episode_samples += 1
                sample_number = self._episode_samples
                self._episode_peak_age_ms = max(
                    self._episode_peak_age_ms,
                    elapsed * 1000.0,
                )
                self._episode_last_stack_signature = stack_signature
                if expected_block and not self._episode_expected_block:
                    self._episode_expected_block = expected_block

            if not self._stop.is_set():
                self._logger.warning(
                    "PERF UI stall stack sample | episode=%d sample=%d "
                    "heartbeat_age_ms=%.1f gui_thread=%s expected_block=%r "
                    "| innermost first:\n%s",
                    episode_id,
                    sample_number,
                    elapsed * 1000.0,
                    self._gui_thread_id,
                    expected_block,
                    "\n".join(stack) or "Python frame unavailable",
                )
