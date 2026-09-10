"""Progress regressions using synthetic work, without a video or model."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import signal
import threading
import unittest
from unittest import mock

from trigger_warnings.progress import ProgressBar


class Stream(io.StringIO):
    def __init__(self, tty=False):
        super().__init__()
        self.tty = tty
        self.heartbeat = threading.Event()

    def isatty(self):
        return self.tty

    def write(self, text):
        result = super().write(text)
        if threading.current_thread() is not threading.main_thread():
            self.heartbeat.set()
        return result


class ProgressTests(unittest.TestCase):
    def test_terminal_and_captured_output_both_show_bar(self):
        for tty in (False, True):
            with self.subTest(tty=tty):
                stream = Stream(tty)
                with contextlib.redirect_stderr(stream):
                    with ProgressBar(1, 1) as progress:
                        progress.start_chunk(0)
                        progress.start_trigger(0, "synthetic")
                        progress.finish_trigger()
                output = stream.getvalue()
                self.assertIn("[--------------------]", output)
                self.assertIn("100.0% Scan complete", output)
                self.assertEqual("\r" in output, tty)
                self.assertTrue(output.endswith("\n"))

    def test_heartbeat_continues_without_scan_callbacks_during_stderr_redirect(self):
        stream = Stream()
        with contextlib.redirect_stderr(stream):
            progress = ProgressBar(1, 1)
            progress.interval = 0.01
            with progress:
                progress.set_stage("Loading model")
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertTrue(stream.heartbeat.wait(2), "no heartbeat while backend was busy")
        self.assertGreaterEqual(stream.getvalue().count("Loading model"), 2)
        self.assertFalse(progress._thread.is_alive())

    def test_json_progress_keeps_stdout_empty_and_counts_completed_checks(self):
        out, err = io.StringIO(), Stream()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with ProgressBar(1, 2, json_progress=True) as progress:
                progress.start_chunk(0)
                progress.start_trigger(0, "first")
                progress.finish_trigger()
                progress.start_trigger(1, "second")
                progress.finish_trigger()
        events = [json.loads(line) for line in err.getvalue().splitlines()]
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(events[0]["percent"], 0)
        self.assertEqual(next(e for e in events if e["trigger_name"] == "second")["percent"], 50)
        self.assertEqual(events[-1]["percent"], 100)
        self.assertEqual(events[-1]["event"], "finish")

    def test_failure_and_interrupt_stop_heartbeat_and_restore_signals(self):
        for error, status in ((RuntimeError("failed"), "Scan failed"),
                              (KeyboardInterrupt(), "Scan cancelled")):
            with self.subTest(status=status):
                original = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
                stream = Stream()
                with contextlib.redirect_stderr(stream):
                    with self.assertRaises(type(error)):
                        with ProgressBar(1, 1) as progress:
                            raise error
                self.assertIn(status, stream.getvalue())
                self.assertNotIn("Scan complete", stream.getvalue())
                self.assertFalse(progress._thread.is_alive())
                self.assertEqual(original, {sig: signal.getsignal(sig) for sig in original})

    def test_captured_output_is_throttled(self):
        stream = Stream()
        with contextlib.redirect_stderr(stream):
            with ProgressBar(100, 1) as progress:
                for index in range(100):
                    progress.start_chunk(index)
                    progress.start_trigger(0, "synthetic")
                    progress.finish_trigger()
        self.assertEqual(len(stream.getvalue().splitlines()), 2)

    def test_terminal_line_fits_width_and_clears_previous_text(self):
        stream = Stream(True)
        with contextlib.redirect_stderr(stream), mock.patch(
                "trigger_warnings.progress.shutil.get_terminal_size",
                return_value=os.terminal_size((80, 24))):
            with ProgressBar(1, 1) as progress:
                progress.set_stage("long stage " * 20)
                progress.set_stage("Short")
        lines = stream.getvalue().split("\r")[1:]
        self.assertTrue(all(len(line.rstrip("\n")) <= 79 for line in lines))
        self.assertTrue(next(line for line in lines if "Short" in line).endswith(" "))


class TerminalLauncherTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "scan-in-terminal.py"
        spec = importlib.util.spec_from_file_location("scan_in_terminal", path)
        self.launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.launcher)

    def test_launches_same_python_and_preserves_shell_metacharacters_as_arguments(self):
        args = ["--json", "--video", "/tmp/a $(touch bad) ' movie.mkv",
                "--model-trigger", "nails; `touch bad`"]
        with mock.patch.object(self.launcher.shutil, "which", return_value="/bin/maestri"), \
                mock.patch.object(self.launcher.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(self.launcher.main(args), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/bin/maestri", "recruit"])
        self.assertEqual(shlex.split(command[command.index("--command") + 1]),
                         [self.launcher.sys.executable, "-m", "trigger_warnings", *args])

    def test_refuses_json_progress_without_launching(self):
        with mock.patch.object(self.launcher.subprocess, "run") as run, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.launcher.main(["--progress-json"]), 2)
        run.assert_not_called()

    def test_launch_failure_is_returned_without_retry(self):
        with mock.patch.object(self.launcher.shutil, "which", return_value="/bin/maestri"), \
                mock.patch.object(self.launcher.subprocess, "run") as run:
            run.return_value.returncode = 2
            self.assertEqual(self.launcher.main(["--help", "--json"]), 2)
        run.assert_called_once()
