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


class TestCLICloudFlagAliases(unittest.TestCase):
    """Verify --cloud-* flags work identically to --gemini-* aliases."""

    def test_cloud_api_key_rejected_with_local_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "movie.mp4"
            video.write_bytes(b"dummy")

            parser = cli.build_parser()
            args = parser.parse_args([
                "--video", str(video), "--output", "out.ass",
                "--model-trigger", "eyes", "--cloud-api-key", "key",
            ])
            with self.assertRaisesRegex(TriggerWarningsError, "--cloud-api-key is only valid with --provider gemini"):
                cli.run(args, lambda msg: None)

    def test_cloud_model_rejected_with_local_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "movie.mp4"
            video.write_bytes(b"dummy")

            parser = cli.build_parser()
            args = parser.parse_args([
                "--video", str(video), "--output", "out.ass",
                "--model-trigger", "eyes", "--cloud-model", "my-model",
            ])
            with self.assertRaisesRegex(TriggerWarningsError, "--cloud-model is only valid with --provider gemini"):
                cli.run(args, lambda msg: None)

    def test_no_sanitize_rejected_with_local_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "movie.mp4"
            video.write_bytes(b"dummy")

            parser = cli.build_parser()
            args = parser.parse_args([
                "--video", str(video), "--output", "out.ass",
                "--model-trigger", "eyes", "--no-sanitize",
            ])
            with self.assertRaisesRegex(TriggerWarningsError, "--no-sanitize is only valid with --provider gemini"):
                cli.run(args, lambda msg: None)

    def test_cloud_api_key_resolves_from_env(self):
        """CLOUD_API_KEY env var is picked up by _cloud_api_key."""
        env = {"CLOUD_API_KEY": "env-cloud-key"}
        with mock.patch.dict("os.environ", env, clear=True):
            args = cli.build_parser().parse_args([
                "--provider", "gemini", "--model-trigger", "eyes",
            ])
            self.assertEqual(cli._cloud_api_key(args), "env-cloud-key")

    def test_cloud_api_key_env_fallback_to_gemini_key(self):
        """CLOUD_API_KEY takes precedence over GEMINI_API_KEY."""
        env = {"CLOUD_API_KEY": "cloud-val", "GEMINI_API_KEY": "gemini-val"}
        with mock.patch.dict("os.environ", env, clear=True):
            args = cli.build_parser().parse_args([
                "--provider", "gemini", "--model-trigger", "eyes",
            ])
            self.assertEqual(cli._cloud_api_key(args), "cloud-val")

    def test_cloud_api_key_cli_over_env(self):
        """CLI --cloud-api-key takes precedence over env."""
        env = {"CLOUD_API_KEY": "env-val"}
        with mock.patch.dict("os.environ", env, clear=True):
            args = cli.build_parser().parse_args([
                "--provider", "gemini", "--model-trigger", "eyes",
                "--cloud-api-key", "cli-val",
            ])
            self.assertEqual(cli._cloud_api_key(args), "cli-val")

    def test_gemini_api_key_fallback_when_no_cloud_key(self):
        """--gemini-api-key still works when --cloud-api-key is absent."""
        args = cli.build_parser().parse_args([
            "--provider", "gemini", "--model-trigger", "eyes",
            "--gemini-api-key", "legacy-key",
        ])
        self.assertEqual(cli._cloud_api_key(args), "legacy-key")

    def test_cloud_model_cli_over_default(self):
        args = cli.build_parser().parse_args([
            "--provider", "gemini", "--model-trigger", "eyes",
            "--cloud-model", "gemini-2.0-pro",
        ])
        self.assertEqual(cli._cloud_model(args), "gemini-2.0-pro")

    def test_cloud_model_default_fallback(self):
        args = cli.build_parser().parse_args([
            "--provider", "gemini", "--model-trigger", "eyes",
        ])
        self.assertEqual(cli._cloud_model(args), "gemini-3.6-flash")

    def test_gemini_model_alias_still_works(self):
        args = cli.build_parser().parse_args([
            "--provider", "gemini", "--model-trigger", "eyes",
            "--gemini-model", "gemini-2.0-pro",
        ])
        self.assertEqual(cli._cloud_model(args), "gemini-2.0-pro")

    def test_cloud_model_over_gemini_model(self):
        """--cloud-model takes precedence over --gemini-model."""
        args = cli.build_parser().parse_args([
            "--provider", "gemini", "--model-trigger", "eyes",
            "--cloud-model", "cloud-wins",
            "--gemini-model", "legacy-loses",
        ])
        self.assertEqual(cli._cloud_model(args), "cloud-wins")

    def test_no_sanitize_flag(self):
        args = cli.build_parser().parse_args([
            "--provider", "gemini", "--model-trigger", "eyes",
            "--no-sanitize",
        ])
        self.assertFalse(cli._cloud_sanitize(args))

    def test_no_gemini_sanitize_alias(self):
        args = cli.build_parser().parse_args([
            "--provider", "gemini", "--model-trigger", "eyes",
            "--no-gemini-sanitize",
        ])
        self.assertFalse(cli._cloud_sanitize(args))

    def test_sanitize_default_true(self):
        args = cli.build_parser().parse_args([
            "--provider", "gemini", "--model-trigger", "eyes",
        ])
        self.assertTrue(cli._cloud_sanitize(args))


if __name__ == "__main__":
    unittest.main()
