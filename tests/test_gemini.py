"""Tests for Google Gemini cloud multimodal video trigger scanning backend."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trigger_warnings.gemini import (
    GeminiError,
    _parse_time_seconds,
    sanitize_video,
    scan_video_gemini,
)
from trigger_warnings import cli
from trigger_warnings.core import TriggerWarningsError


class TestParseTimeSeconds(unittest.TestCase):
    def test_valid_timestamps(self):
        self.assertEqual(_parse_time_seconds(42), 42.0)
        self.assertEqual(_parse_time_seconds(12.345), 12.345)
        self.assertEqual(_parse_time_seconds("15"), 15.0)
        self.assertEqual(_parse_time_seconds("01:30"), 90.0)
        self.assertEqual(_parse_time_seconds("01:30.500"), 90.5)
        self.assertEqual(_parse_time_seconds("01:30,500"), 90.5)
        self.assertEqual(_parse_time_seconds("01:02:03"), 3723.0)
        self.assertEqual(_parse_time_seconds("01:02:03.456"), 3723.456)

    def test_invalid_timestamp(self):
        with self.assertRaisesRegex(GeminiError, "unusable timestamp"):
            _parse_time_seconds("invalid:time:format:extra")


class TestScanVideoGeminiValidation(unittest.TestCase):
    def test_empty_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "test.mp4"
            video.write_bytes(b"dummy")
            with self.assertRaisesRegex(GeminiError, "at least one non-empty trigger"):
                scan_video_gemini(video, [], api_key="test_key")

    def test_duplicate_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "test.mp4"
            video.write_bytes(b"dummy")
            with self.assertRaisesRegex(GeminiError, "trigger values must be unique"):
                scan_video_gemini(video, ["eyes", "EYES"], api_key="test_key")

    def test_missing_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(GeminiError, "video does not exist"):
                scan_video_gemini(Path(tmp) / "missing.mp4", ["eyes"], api_key="test_key")

    def test_missing_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "test.mp4"
            video.write_bytes(b"dummy")
            with mock.patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(GeminiError, "Gemini API key is required"):
                    scan_video_gemini(video, ["eyes"], api_key=None)


class TestScanVideoGeminiMockFlow(unittest.TestCase):
    def test_mock_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "movie.mp4"
            video.write_bytes(b"dummy video content")

            mock_file = mock.Mock()
            mock_file.name = "files/test_file_123"
            mock_file.state = mock.Mock()
            mock_file.state.name = "ACTIVE"

            mock_response = mock.Mock()
            mock_response.text = json.dumps({
                "events": [
                    {
                        "start_time": "00:01:23.500",
                        "end_time": "00:01:30.000",
                        "trigger": "eyes",
                        "description": "Character's eye is attacked with a needle"
                    }
                ]
            })

            mock_client = mock.Mock()
            mock_client.files.upload.return_value = mock_file
            mock_client.files.get.return_value = mock_file
            mock_client.models.generate_content.return_value = mock_response

            report_lines = []

            scan = scan_video_gemini(
                video,
                ["eyes"],
                api_key="mock_key",
                model="gemini-3.6-flash",
                trigger_descriptors={"eyes": "severe eye injury"},
                report=report_lines.append,
                sanitize=False,
                client_factory=lambda api_key: mock_client,
            )

            self.assertTrue(mock_client.files.upload.called)
            self.assertTrue(mock_client.models.generate_content.called)

            call_args = mock_client.models.generate_content.call_args
            self.assertIn("severe eye injury", str(call_args))

            mock_client.files.delete.assert_called_once_with(name="files/test_file_123")

            self.assertEqual(len(scan.events), 1)
            self.assertEqual(scan.events[0]["label"], "eyes")
            self.assertEqual(scan.events[0]["start"], 83.5)
            self.assertEqual(scan.events[0]["end"], 90.0)
            self.assertTrue(any("needle" in line for line in report_lines))
            self.assertEqual(scan.metadata["kind"], "gemini-cloud-model")
            self.assertEqual(scan.metadata["provider"], "google-gemini")


class TestScanVideoGeminiCleanupOnError(unittest.TestCase):
    def test_cleanup_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "movie.mp4"
            video.write_bytes(b"dummy video content")

            mock_file = mock.Mock()
            mock_file.name = "files/test_file_cleanup"
            mock_file.state = mock.Mock()
            mock_file.state.name = "ACTIVE"

            mock_client = mock.Mock()
            mock_client.files.upload.return_value = mock_file
            mock_client.files.get.return_value = mock_file
            mock_client.models.generate_content.side_effect = RuntimeError("Generation failed")

            with self.assertRaisesRegex(RuntimeError, "Generation failed"):
                scan_video_gemini(
                    video,
                    ["eyes"],
                    api_key="mock_key",
                    sanitize=False,
                    client_factory=lambda api_key: mock_client,
                )

            mock_client.files.delete.assert_called_once_with(name="files/test_file_cleanup")


class TestCLIGeminiFlagValidation(unittest.TestCase):
    def test_gemini_api_key_with_local_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "movie.mp4"
            video.write_bytes(b"dummy")

            parser = cli.build_parser()
            args = parser.parse_args([
                "--video", str(video), "--output", "out.ass",
                "--model-trigger", "eyes", "--gemini-api-key", "key",
            ])
            with self.assertRaisesRegex(TriggerWarningsError, "--gemini-api-key is only valid with --provider gemini"):
                cli.run(args, lambda msg: None)

    def test_model_chunk_with_gemini_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "movie.mp4"
            video.write_bytes(b"dummy")

            parser = cli.build_parser()
            args = parser.parse_args([
                "--video", str(video), "--output", "out.ass",
                "--model-trigger", "eyes",
                "--provider", "gemini",
                "--gemini-api-key", "key",
                "--model-chunk", "15.0",
            ])
            with self.assertRaisesRegex(TriggerWarningsError, "--model-chunk is not supported with --provider gemini"):
                cli.run(args, lambda msg: None)


if __name__ == "__main__":
    unittest.main()
