"""Tests for Google Gemini cloud multimodal video trigger scanning backend."""

import json
from pathlib import Path
import pytest
from unittest import mock

from trigger_warnings.gemini import (
    GeminiError,
    _parse_time_seconds,
    sanitize_video,
    scan_video_gemini,
)
from trigger_warnings import cli
from trigger_warnings.core import TriggerWarningsError


def test_parse_time_seconds():
    assert _parse_time_seconds(42) == 42.0
    assert _parse_time_seconds(12.345) == 12.345
    assert _parse_time_seconds("15") == 15.0
    assert _parse_time_seconds("01:30") == 90.0
    assert _parse_time_seconds("01:30.500") == 90.5
    assert _parse_time_seconds("01:30,500") == 90.5
    assert _parse_time_seconds("01:02:03") == 3723.0
    assert _parse_time_seconds("01:02:03.456") == 3723.456

    with pytest.raises(GeminiError, match="unusable timestamp"):
        _parse_time_seconds("invalid:time:format:extra")


def test_scan_video_gemini_validation(tmp_path):
    video = tmp_path / "test.mp4"
    video.write_bytes(b"dummy")

    with pytest.raises(GeminiError, match="at least one non-empty trigger"):
        scan_video_gemini(video, [], api_key="test_key")

    with pytest.raises(GeminiError, match="trigger values must be unique"):
        scan_video_gemini(video, ["eyes", "EYES"], api_key="test_key")

    with pytest.raises(GeminiError, match="video does not exist"):
        scan_video_gemini(tmp_path / "missing.mp4", ["eyes"], api_key="test_key")

    with pytest.raises(GeminiError, match="Gemini API key is required"):
        with mock.patch.dict("os.environ", {}, clear=True):
            scan_video_gemini(video, ["eyes"], api_key=None)


def test_scan_video_gemini_mock_flow(tmp_path):
    video = tmp_path / "movie.mp4"
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
        model="gemini-2.0-flash",
        trigger_descriptors={"eyes": "severe eye injury"},
        report=report_lines.append,
        sanitize=False,  # Skip ffmpeg during mock test
        client_factory=lambda api_key: mock_client,
    )

    # Verify upload called
    assert mock_client.files.upload.called
    assert mock_client.models.generate_content.called

    # Verify prompt received descriptor
    call_args = mock_client.models.generate_content.call_args
    assert "severe eye injury" in str(call_args)

    # Verify cleanup was called
    mock_client.files.delete.assert_called_once_with(name="files/test_file_123")

    # Verify events and metadata
    assert len(scan.events) == 1
    assert scan.events[0]["label"] == "eyes"
    assert scan.events[0]["start"] == 83.5
    assert scan.events[0]["end"] == 90.0
    assert "needle" in scan.events[0]["description"]
    assert scan.metadata["kind"] == "gemini-cloud-model"
    assert scan.metadata["provider"] == "google-gemini"


def test_scan_video_gemini_cleanup_on_error(tmp_path):
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"dummy video content")

    mock_file = mock.Mock()
    mock_file.name = "files/test_file_cleanup"
    mock_file.state = mock.Mock()
    mock_file.state.name = "ACTIVE"

    mock_client = mock.Mock()
    mock_client.files.upload.return_value = mock_file
    mock_client.files.get.return_value = mock_file
    # Simulate an error during model generation
    mock_client.models.generate_content.side_effect = RuntimeError("Generation failed")

    with pytest.raises(RuntimeError, match="Generation failed"):
        scan_video_gemini(
            video,
            ["eyes"],
            api_key="mock_key",
            sanitize=False,
            client_factory=lambda api_key: mock_client,
        )

    # Cleanup must still have happened in finally: block
    mock_client.files.delete.assert_called_once_with(name="files/test_file_cleanup")


def test_cli_gemini_flag_validation(tmp_path):
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"dummy")

    parser = cli.build_parser()

    # --gemini-api-key with default local provider
    args = parser.parse_args(["--video", str(video), "--output", "out.ass", "--model-trigger", "eyes", "--gemini-api-key", "key"])
    with pytest.raises(TriggerWarningsError, match="--gemini-api-key is only valid with --provider gemini"):
        cli.run(args, lambda msg: None)

    # --model-chunk with gemini provider
    args = parser.parse_args([
        "--video", str(video),
        "--output", "out.ass",
        "--model-trigger", "eyes",
        "--provider", "gemini",
        "--gemini-api-key", "key",
        "--model-chunk", "15.0"
    ])
    with pytest.raises(TriggerWarningsError, match="--model-chunk is not supported with --provider gemini"):
        cli.run(args, lambda msg: None)
