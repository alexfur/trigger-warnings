"""Live scan progress on stderr, including terminals captured by agents."""

import json
import shutil
import signal
import sys
import threading
import time


def format_timestamp(seconds):
    """Format seconds into HH:MM:SS."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_candidate_notification(trigger, start, end):
    """Format candidate trigger notification line."""
    timestamp = format_timestamp(start)
    return f"Found [{trigger}] at {timestamp} ({start:.1f}s - {end:.1f}s)"


class ProgressBar:
    """Keep a heartbeat visible while decoding or model inference is busy."""

    def __init__(self, total_chunks=0, total_triggers=0, enabled=True,
                 json_progress=False):
        self.stream = sys.stderr
        self.tty = self.stream.isatty()
        self.enabled = enabled
        self.json_progress = json_progress
        self.total_chunks = total_chunks
        self.total_triggers = total_triggers
        self.current_chunk = 0
        self.current_trigger = 0
        self.trigger_name = None
        self.completed = 0
        self.stage = "Reading video"
        self.start_time = time.monotonic()
        self.scan_start = None
        self.interval = 0.2 if self.tty and not json_progress else 5.0
        self._last_draw = None
        self._last_width = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._handlers = {}

    def __enter__(self):
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._interrupt)
        self._draw(event="stage", force=True)
        if self.enabled:
            self._thread = threading.Thread(target=self._heartbeat, daemon=True)
            self._thread.start()
        return self

    @staticmethod
    def _interrupt(signum, frame):
        raise KeyboardInterrupt

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread:
            self._thread.join()
        for sig, handler in self._handlers.items():
            signal.signal(sig, handler)
        if exc_type is not None:
            self.stage = "Scan cancelled" if issubclass(exc_type, KeyboardInterrupt) else "Scan failed"
        else:
            self.stage = "Scan complete"
        self._draw(event="finish", force=True, final=True)

    def _heartbeat(self):
        while not self._stop.wait(self.interval):
            self._draw(event="heartbeat", force=True)

    def set_stage(self, stage):
        with self._lock:
            self.stage = stage
        self._draw(event="stage", force=True)

    def set_custom_progress(self, stage, fraction=None, detail=""):
        with self._lock:
            self.stage = stage
            self.custom_fraction = fraction
            self.custom_detail = detail
        self._draw(event="progress", force=True)

    def start_chunk(self, chunk_idx):
        with self._lock:
            if self.scan_start is None:
                self.scan_start = time.monotonic()
            self.current_chunk = chunk_idx + 1
            self.current_trigger = 0
            self.trigger_name = None
            self.stage = "Decoding video"
        self._draw()

    def start_trigger(self, trigger_idx, trigger_name):
        with self._lock:
            self.current_trigger = trigger_idx + 1
            self.trigger_name = trigger_name
            self.stage = "Checking triggers"
        self._draw()

    def finish_trigger(self):
        with self._lock:
            self.completed += 1
        self._draw()

    def emit_candidate(self, trigger, start, end):
        """Emit a clean notification when a candidate event is found."""
        if not self.enabled:
            return
        with self._lock:
            now = time.monotonic()
            timestamp = format_timestamp(start)
            if self.json_progress:
                total = self.total_chunks * self.total_triggers
                fraction = self.completed / total if total else 0
                elapsed = now - self.start_time
                self.stream.write(json.dumps({
                    "type": "progress", "event": "candidate", "stage": self.stage,
                    "chunk": self.current_chunk, "total_chunks": self.total_chunks,
                    "trigger_index": self.current_trigger,
                    "total_triggers": self.total_triggers,
                    "trigger_name": trigger,
                    "label": trigger,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "timestamp": timestamp,
                    "percent": round(fraction * 100, 1),
                    "elapsed_seconds": round(elapsed, 1),
                }) + "\n")
                self.stream.flush()
                return

            notification = format_candidate_notification(trigger, start, end)
            self._write_line_locked(notification)

    found_trigger = emit_candidate

    def write_line(self, text):
        """Write a text line cleanly above the active progress bar."""
        if not self.enabled:
            return
        with self._lock:
            self._write_line_locked(text)

    def _write_line_locked(self, text):
        if self.tty:
            columns = shutil.get_terminal_size((100, 24)).columns
            text = text[:max(1, columns - 1)]
            clear_len = max(0, min(self._last_width, columns - 1))
            if clear_len:
                self.stream.write("\r" + " " * clear_len + "\r")
            self.stream.write(text + "\n")
            self._last_width = 0
            self._draw_locked(force=True)
        else:
            self.stream.write(text + "\n")
            self.stream.flush()

    def _draw(self, event="trigger", force=False, final=False):
        if not self.enabled:
            return
        with self._lock:
            self._draw_locked(event=event, force=force, final=final)

    def _draw_locked(self, event="trigger", force=False, final=False):
        now = time.monotonic()
        # Captured output gets bounded, newline-delimited snapshots. JSON
        # retains every trigger event for callers tracking individual checks.
        if (not force and not self.json_progress and self._last_draw is not None
                and now - self._last_draw < self.interval):
            return
        self._last_draw = now
        if getattr(self, "custom_fraction", None) is not None:
            fraction = max(0.0, min(1.0, float(self.custom_fraction)))
        else:
            total = self.total_chunks * self.total_triggers
            fraction = self.completed / total if total else 0
        elapsed = now - self.start_time
        if self.json_progress:
            self.stream.write(json.dumps({
                "type": "progress", "event": event, "stage": self.stage,
                "chunk": self.current_chunk, "total_chunks": self.total_chunks,
                "trigger_index": self.current_trigger,
                "total_triggers": self.total_triggers,
                "trigger_name": self.trigger_name,
                "percent": round(fraction * 100, 1),
                "elapsed_seconds": round(elapsed, 1),
            }) + "\n")
        else:
            # ASCII also works in redirected files and narrow terminals.
            width = 20
            filled = int(width * fraction)
            bar = "#" * filled + "-" * (width - filled)
            spinner = "|/-\\"[int(elapsed * 5) % 4] if not final else " "
            line = f"{spinner} [{bar}] {fraction * 100:5.1f}% {self.stage} | {elapsed:.0f}s"
            custom_detail = getattr(self, "custom_detail", "")
            if custom_detail:
                line += f" | {custom_detail}"
            elif self.current_chunk:
                line += f" | chunk {self.current_chunk}/{self.total_chunks}"
            if self.completed and not final:
                eta = (now - self.scan_start) / self.completed * (total - self.completed)
                line += f" | ETA {eta:.0f}s"
            if self.trigger_name and not final:
                line += " | " + " ".join(self.trigger_name.split())
            if self.tty:
                columns = shutil.get_terminal_size((100, 24)).columns
                line = line[:max(1, columns - 1)]
                padding = " " * max(0, min(self._last_width, columns - 1) - len(line))
                self.stream.write("\r" + line + padding + ("\n" if final else ""))
                self._last_width = len(line)
            else:
                self.stream.write(line + "\n")
        self.stream.flush()
