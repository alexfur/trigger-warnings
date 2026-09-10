"""Live scan progress on stderr, including terminals captured by agents."""

import json
import shutil
import signal
import sys
import threading
import time


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
        self._lock = threading.Lock()
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

    def _draw(self, event="trigger", force=False, final=False):
        if not self.enabled:
            return
        with self._lock:
            now = time.monotonic()
            # Captured output gets bounded, newline-delimited snapshots. JSON
            # retains every trigger event for callers tracking individual checks.
            if (not force and not self.json_progress and self._last_draw is not None
                    and now - self._last_draw < self.interval):
                return
            self._last_draw = now
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
                if self.current_chunk:
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
