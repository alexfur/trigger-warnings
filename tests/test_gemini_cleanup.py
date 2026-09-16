"""Regression tests for Gemini upload privacy, cleanup, and fail-closed sanitisation."""

import json
import unittest
import tempfile
from pathlib import Path
from unittest import mock

from trigger_warnings.gemini import (
    GeminiError,
    sanitize_video,
    scan_video_gemini,
)


def _make_mock_client(upload_name="files/scan_abc123", state_name="ACTIVE"):
    """Build a mock Gemini client that behaves like the real API."""
    mock_file = mock.Mock()
    mock_file.name = upload_name
    mock_file.state = mock.Mock()
    mock_file.state.name = state_name

    mock_response = mock.Mock()
    mock_response.text = json.dumps({
        "events": [{
            "start_time": "00:01:00",
            "end_time": "00:01:10",
            "trigger": "eyes",
            "description": "needle to the eye",
        }]
    })

    client = mock.Mock()
    client.files.upload.return_value = mock_file
    client.files.get.return_value = mock_file
    client.files.delete.return_value = None
    client.models.generate_content.return_value = mock_response
    return client


class GeminiCleanupTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.video = self.directory / "movie.mp4"
        self.video.write_bytes(b"synthetic video")


class TestFailClosedSanitisation(GeminiCleanupTestCase):
    """When sanitize=True (default), missing ffmpeg must abort, never upload original."""

    def _client_factory(self, api_key):
        return mock.Mock()

    def test_no_ffmpeg_raises_not_upload(self):
        """sanitize=True + no ffmpeg on PATH → GeminiError, zero uploads."""
        video = self.video
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("os.environ", {"GEMINI_API_KEY": "test"}):
            with self.assertRaises(GeminiError) as ctx:
                scan_video_gemini(video, ["eyes"], sanitize=True,
                                  client_factory=self._client_factory)
            self.assertIn("sanitisation failed", str(ctx.exception).lower())

    def test_no_ffmpeg_no_upload_calls(self):
        """Verify client upload is never called when ffmpeg is missing."""
        client = mock.Mock()
        video = self.video
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("os.environ", {"GEMINI_API_KEY": "test"}):
            with self.assertRaises(GeminiError):
                scan_video_gemini(video, ["eyes"], sanitize=True,
                                  client_factory=lambda api_key: client)
        client.files.upload.assert_not_called()

    def test_ffmpeg_error_raises_not_upload(self):
        """sanitize_video returns None on ffmpeg error → scan aborts."""
        video = self.video
        with mock.patch("trigger_warnings.gemini.sanitize_video", return_value=None), \
             mock.patch("os.environ", {"GEMINI_API_KEY": "test"}):
            with self.assertRaises(GeminiError) as ctx:
                scan_video_gemini(video, ["eyes"], sanitize=True,
                                  client_factory=self._client_factory)
            self.assertIn("sanitisation failed", str(ctx.exception).lower())

    def test_sanitize_false_escapes_hatch(self):
        """sanitize=False skips sanitisation and uploads original."""
        video = self.video
        video.write_bytes(b"dummy")
        client = _make_mock_client()
        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}):
            scan = scan_video_gemini(
                video, ["eyes"],
                sanitize=False,
                client_factory=lambda api_key: client,
            )
        client.files.upload.assert_called_once()
        self.assertEqual(len(scan.events), 1)

    def test_fail_closed_message_mentions_cli_flag(self):
        """Error message references --no-gemini-sanitize for CLI users."""
        video = self.video
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("os.environ", {"GEMINI_API_KEY": "test"}):
            with self.assertRaises(GeminiError) as ctx:
                scan_video_gemini(video, ["eyes"], sanitize=True,
                                  client_factory=lambda api_key: mock.Mock())
            self.assertIn("--no-gemini-sanitize", str(ctx.exception))


class TestBothRetryUploadsDeleted(GeminiCleanupTestCase):
    """When ingestion fails on attempt 1 and succeeds on attempt 2, both files are deleted."""

    def test_two_uploads_both_deleted(self):
        video = self.video
        video.write_bytes(b"dummy")

        file_attempt1 = mock.Mock()
        file_attempt1.name = "files/attempt1_failed"
        file_attempt1.state = mock.Mock()
        file_attempt1.state.name = "FAILED"
        file_attempt1.error = "ingestion error"

        file_attempt2 = mock.Mock()
        file_attempt2.name = "files/attempt2_ok"
        file_attempt2.state = mock.Mock()
        file_attempt2.state.name = "ACTIVE"

        mock_response = mock.Mock()
        mock_response.text = json.dumps({
            "events": [{
                "start_time": "00:00:30",
                "end_time": "00:00:40",
                "trigger": "eyes",
                "description": "injury",
            }]
        })

        client = mock.Mock()
        client.files.upload.side_effect = [file_attempt1, file_attempt2]
        client.files.get.return_value = file_attempt2
        client.models.generate_content.return_value = mock_response

        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}), \
             mock.patch("time.sleep"):
            scan_video_gemini(
                video, ["eyes"],
                sanitize=False,
                client_factory=lambda api_key: client,
            )

        # Both files should be deleted
        self.assertEqual(client.files.delete.call_count, 2)
        deleted_names = [call.kwargs["name"] for call in client.files.delete.call_args_list]
        self.assertIn("files/attempt1_failed", deleted_names)
        self.assertIn("files/attempt2_ok", deleted_names)


class TestFailedInferenceCleanup(GeminiCleanupTestCase):
    """Upload succeeds but model call fails → all uploads still cleaned up."""

    def test_model_error_deletes_uploads(self):
        video = self.video
        video.write_bytes(b"dummy")

        mock_file = mock.Mock()
        mock_file.name = "files/scan_xyz"
        mock_file.state = mock.Mock()
        mock_file.state.name = "ACTIVE"

        client = mock.Mock()
        client.files.upload.return_value = mock_file
        client.files.get.return_value = mock_file
        client.models.generate_content.side_effect = RuntimeError("model exploded")

        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}):
            with self.assertRaises(RuntimeError):
                scan_video_gemini(
                    video, ["eyes"],
                    sanitize=False,
                    client_factory=lambda api_key: client,
                )

        client.files.delete.assert_called_once_with(name="files/scan_xyz")


class TestDeletionFailureReporting(GeminiCleanupTestCase):
    """Cleanup failure is reported via report() without masking the primary result."""

    def test_delete_failure_reported_still_returns(self):
        video = self.video
        video.write_bytes(b"dummy")

        mock_file = mock.Mock()
        mock_file.name = "files/scan_del_fail"
        mock_file.state = mock.Mock()
        mock_file.state.name = "ACTIVE"

        mock_response = mock.Mock()
        mock_response.text = json.dumps({
            "events": [{
                "start_time": "10",
                "end_time": "20",
                "trigger": "eyes",
                "description": "test",
            }]
        })

        client = mock.Mock()
        client.files.upload.return_value = mock_file
        client.files.get.return_value = mock_file
        client.files.delete.side_effect = PermissionError("denied")
        client.models.generate_content.return_value = mock_response

        report_lines = []
        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}):
            scan = scan_video_gemini(
                video, ["eyes"],
                sanitize=False,
                report=report_lines.append,
                client_factory=lambda api_key: client,
            )

        # Primary result still returned despite cleanup failure
        self.assertEqual(len(scan.events), 1)
        self.assertEqual(scan.events[0]["label"], "eyes")
        # Report contains deletion failure note
        delete_notes = [l for l in report_lines if "could not delete" in l.lower()]
        self.assertTrue(len(delete_notes) >= 1, "deletion failure not reported")

    def test_delete_failure_not_masking_error(self):
        """When model call fails AND deletion fails, the original error propagates."""
        video = self.video
        video.write_bytes(b"dummy")

        mock_file = mock.Mock()
        mock_file.name = "files/scan_both_fail"
        mock_file.state = mock.Mock()
        mock_file.state.name = "ACTIVE"

        client = mock.Mock()
        client.files.upload.return_value = mock_file
        client.files.get.return_value = mock_file
        client.files.delete.side_effect = PermissionError("denied")
        client.models.generate_content.side_effect = RuntimeError("model broke")

        report_lines = []
        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}):
            with self.assertRaises(RuntimeError) as ctx:
                scan_video_gemini(
                    video, ["eyes"],
                    sanitize=False,
                    report=report_lines.append,
                    client_factory=lambda api_key: client,
                )
            self.assertIn("model broke", str(ctx.exception))

        # Deletion failure was reported but did not mask the primary error
        delete_notes = [l for l in report_lines if "could not delete" in l.lower()]
        self.assertTrue(len(delete_notes) >= 1)

    def test_report_callback_error_does_not_mask_cleanup(self):
        """If report() raises during cleanup, remaining files are still deleted."""
        video = self.video
        video.write_bytes(b"dummy")

        file1 = mock.Mock()
        file1.name = "files/first"
        file1.state = mock.Mock()
        file1.state.name = "FAILED"
        file1.error = "ingestion error"

        file2 = mock.Mock()
        file2.name = "files/second"
        file2.state = mock.Mock()
        file2.state.name = "ACTIVE"

        mock_response = mock.Mock()
        mock_response.text = json.dumps({
            "events": [{"start_time": "1", "end_time": "2",
                        "trigger": "eyes", "description": "x"}]
        })

        client = mock.Mock()
        client.files.upload.side_effect = [file1, file2]
        client.files.get.return_value = file2
        client.models.generate_content.return_value = mock_response

        # report raises when called with cleanup messages, works otherwise
        def flaky_report(msg):
            if "cleaned up" in msg.lower() or "could not delete" in msg.lower():
                raise RuntimeError("reporter broke")

        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}), \
             mock.patch("time.sleep"):
            scan_video_gemini(
                video, ["eyes"],
                sanitize=False,
                report=flaky_report,
                client_factory=lambda api_key: client,
            )

        # Both files deleted despite report raising during first cleanup log
        self.assertEqual(client.files.delete.call_count, 2)


class TestLocalTempCleanup(GeminiCleanupTestCase):
    """Sanitised temp file is cleaned up even when scan raises."""

    def test_temp_deleted_on_success(self):
        video = self.video
        video.write_bytes(b"dummy")

        tmp_file = self.directory / "scan_tmp_test.mp4"
        tmp_file.write_bytes(b"sanitised")

        client = _make_mock_client()
        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}), \
             mock.patch("trigger_warnings.gemini.sanitize_video", return_value=tmp_file):
            scan_video_gemini(
                video, ["eyes"],
                sanitize=True,
                client_factory=lambda api_key: client,
            )
        self.assertFalse(tmp_file.exists())

    def test_temp_deleted_on_error(self):
        video = self.video
        video.write_bytes(b"dummy")

        tmp_file = self.directory / "scan_tmp_err.mp4"
        tmp_file.write_bytes(b"sanitised")

        client = mock.Mock()
        client.files.upload.side_effect = RuntimeError("upload boom")

        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}), \
             mock.patch("trigger_warnings.gemini.sanitize_video", return_value=tmp_file):
            with self.assertRaises(RuntimeError):
                scan_video_gemini(
                    video, ["eyes"],
                    sanitize=True,
                    client_factory=lambda api_key: client,
                )
        self.assertFalse(tmp_file.exists())

    def test_temp_deleted_on_keyboard_interrupt(self):
        """Temp file cleaned up when KeyboardInterrupt fires after sanitisation."""
        video = self.video
        video.write_bytes(b"dummy")

        tmp_file = self.directory / "scan_tmp_ki.mp4"
        tmp_file.write_bytes(b"sanitised")

        client = mock.Mock()
        client.files.upload.side_effect = KeyboardInterrupt()

        with mock.patch("os.environ", {"GEMINI_API_KEY": "test"}), \
             mock.patch("trigger_warnings.gemini.sanitize_video", return_value=tmp_file):
            with self.assertRaises(KeyboardInterrupt):
                scan_video_gemini(
                    video, ["eyes"],
                    sanitize=True,
                    client_factory=lambda api_key: client,
                )
        self.assertFalse(tmp_file.exists())


class TestInterruptedSanitisation(GeminiCleanupTestCase):
    def test_interrupt_stops_ffmpeg_and_removes_partial_file(self):
        process = mock.MagicMock()
        process.stdout.__iter__.side_effect = KeyboardInterrupt()
        process.poll.return_value = None
        create_temp = tempfile.NamedTemporaryFile

        with mock.patch("trigger_warnings.gemini.shutil.which", return_value="ffmpeg"), \
             mock.patch("trigger_warnings.media.probe", side_effect=ValueError("synthetic input")), \
             mock.patch("trigger_warnings.gemini.tempfile.NamedTemporaryFile",
                        side_effect=lambda **kwargs: create_temp(dir=self.directory, **kwargs)), \
             mock.patch("trigger_warnings.gemini.subprocess.Popen", return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                sanitize_video(self.video)

        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        process.stdout.close.assert_called_once()
        self.assertEqual(list(self.directory.glob("scan_*.mp4")), [])


if __name__ == "__main__":
    unittest.main()
