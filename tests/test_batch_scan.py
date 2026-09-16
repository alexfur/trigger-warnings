"""The optional batch runner must never infer a user's media or trigger choices."""

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scan-videos.py"
SPEC = importlib.util.spec_from_file_location("scan_videos", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class BatchScanTests(unittest.TestCase):
    def test_missing_input_rejects_whole_batch_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "sample.mp4"
            video.touch()
            with mock.patch.object(runner.cli, "main") as scan:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    runner.main([str(video), str(video.with_name("missing.mp4")),
                                 "--trigger", "blood"])
                scan.assert_not_called()

    def test_explicit_inputs_are_forwarded_and_failure_is_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            videos = [Path(directory) / name for name in ("one.mp4", "two.mp4")]
            for video in videos:
                video.touch()
            with mock.patch.object(runner.cli, "main", side_effect=[1, 0]) as scan:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = runner.main([str(video) for video in videos] + [
                        "--trigger", "blood", "--trigger-desc", "blood=visible blood",
                        "--model", "example-model"])
            self.assertEqual(result, 1)
            self.assertEqual(scan.call_count, 2)
            for call, video in zip(scan.call_args_list, videos):
                command = call.args[0]
                self.assertEqual(command[command.index("--video") + 1], str(video))
                self.assertEqual(command[command.index("--model-trigger") + 1], "blood")
                self.assertEqual(command[command.index("--model-trigger-desc") + 1], "blood=visible blood")
                self.assertEqual(command[command.index("--gemini-model") + 1], "example-model")
                self.assertEqual(command[command.index("--output") + 1],
                                 str(video.with_suffix(".gemini-warnings.ass")))

    def test_trigger_is_required(self):
        with mock.patch.object(runner.cli, "main") as scan:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main(["sample.mp4"])
            scan.assert_not_called()
