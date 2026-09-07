"""Tests for the optional FFmpeg/ffprobe media layer.

Two tiers:

* Unit tests that mock `subprocess.run`. They pin the argv, the timeouts, the
  absence of a shell, and every failure that FFmpeg reports by producing no file
  rather than by exiting non-zero.
* Smoke tests that drive real `ffmpeg`/`ffprobe`, skipped when either is absent.
  They build a synthetic silent video from `scripts/make_demo.py` at a path
  containing a space and an apostrophe, which is the case that breaks a naively
  quoted subtitles filter.

Every input here is synthetic: a generated colour video, the repository's own
harmless example dialogue, and small byte strings. No real film, subtitle or
timeline data is used.
"""

import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trigger_warnings import media  # noqa: E402


HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# --------------------------------------------------------------------------
# Synthetic fixtures
# --------------------------------------------------------------------------

def png_chunk(kind, payload):
    """Return one length-prefixed, CRC-suffixed PNG chunk."""
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def make_png(width=8, height=8):
    """Return the bytes of a real, complete, non-empty PNG."""
    row = b"\x00" + b"\x7f\x7f\x7f" * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(row * height))
        + png_chunk(b"IEND", b"")
    )


def video_stream(index=0, duration=None):
    """A video stream. ffprobe often omits a per-stream duration, so does this."""
    stream = {
        "index": index,
        "codec_name": "h264",
        "codec_type": "video",
        "tags": {"language": "und"},
    }
    if duration is not None:
        stream["duration"] = duration
    return stream


def subtitle_stream(index, codec_name="subrip", language="eng", **extra):
    stream = {
        "index": index,
        "codec_name": codec_name,
        "codec_type": "subtitle",
        "tags": {},
        "disposition": {"default": 0, "forced": 0, "hearing_impaired": 0},
    }
    if language is not None:
        stream["tags"]["language"] = language
    for key, value in extra.items():
        if key in ("title",):
            stream["tags"][key] = value
        elif key in ("default", "forced", "hearing_impaired"):
            stream["disposition"][key] = value
        else:
            stream[key] = value
    return stream


def probe_json(streams=None, duration="60.000000"):
    """Return an ffprobe-shaped dict with a video stream and the given extras."""
    if streams is None:
        streams = []
    payload = {"streams": [video_stream()] + list(streams), "format": {}}
    if duration is not None:
        payload["format"]["duration"] = duration
    return payload


class FakeCompleted(object):
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def fake_run(returncode=0, stdout="", stderr="", writes=None):
    """Build a `subprocess.run` stand-in that records calls and fakes output.

    `writes` is the content FFmpeg would have written to the last argv element,
    resolved against the call's `cwd`. None means FFmpeg produced no file, which
    is how a seek past the end of a video and a bad stream map both behave.
    """
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if writes is not None:
            target = Path(kwargs.get("cwd") or os.getcwd()) / argv[-1]
            if isinstance(writes, bytes):
                target.write_bytes(writes)
            else:
                target.write_text(writes, encoding="utf-8")
        return FakeCompleted(returncode, stdout, stderr)

    runner.calls = calls
    return runner


class MediaTestCase(unittest.TestCase):
    """Shared scaffolding: a temp workspace and a stubbed FFmpeg on PATH."""

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="media tests ")
        self.folder = Path(self.workspace.name)
        self.addCleanup(self.workspace.cleanup)
        # A path that would break an unquoted or naively quoted filter argument.
        self.video = self.folder / "some movie 'edition'.mkv"
        self.video.write_bytes(b"synthetic placeholder, never decoded\n")
        which = mock.patch.object(
            media.shutil, "which", side_effect=lambda name: "/usr/bin/" + name
        )
        which.start()
        self.addCleanup(which.stop)

    def patch_run(self, runner):
        patcher = mock.patch.object(media.subprocess, "run", side_effect=runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return runner

    def assert_argv_is_safe(self, argv, kwargs):
        """No shell, a real argv list, a timeout, and never an overwrite."""
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(item, str) for item in argv), argv)
        self.assertFalse(kwargs.get("shell", False), "media must never use a shell")
        self.assertIsInstance(kwargs.get("timeout"), (int, float))
        self.assertGreater(kwargs["timeout"], 0)
        self.assertNotIn("-y", argv, "media must never let FFmpeg overwrite a file")


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------

class ProbeTests(MediaTestCase):
    def test_builds_a_safe_ffprobe_argv_with_an_absolute_path(self):
        runner = self.patch_run(fake_run(stdout='{"streams": [{"index": 0, '
                                                '"codec_type": "video"}], '
                                                '"format": {"duration": "60.0"}}'))
        media.probe(self.video)
        argv, kwargs = runner.calls[0]
        self.assert_argv_is_safe(argv, kwargs)
        self.assertIn("ffprobe", argv[0])
        self.assertIn("-show_streams", argv)
        self.assertIn("-show_format", argv)
        self.assertIn(str(self.video.resolve()), argv)
        self.assertTrue(Path(argv[-1]).is_absolute())

    def test_returns_the_parsed_ffprobe_json(self):
        self.patch_run(fake_run(stdout='{"streams": [{"index": 0, '
                                       '"codec_type": "video"}], '
                                       '"format": {"duration": "60.0"}}'))
        info = media.probe(self.video)
        self.assertEqual(info["streams"][0]["codec_type"], "video")

    def test_missing_file_fails_without_running_ffprobe(self):
        runner = self.patch_run(fake_run())
        with self.assertRaises(media.MediaError):
            media.probe(self.folder / "absent.mkv")
        self.assertEqual(runner.calls, [])

    def test_directory_is_rejected(self):
        self.patch_run(fake_run())
        with self.assertRaises(media.MediaError):
            media.probe(self.folder)

    def test_missing_ffprobe_binary_is_reported(self):
        self.patch_run(fake_run())
        with mock.patch.object(media.shutil, "which", return_value=None):
            with self.assertRaises(media.MediaError) as caught:
                media.probe(self.video)
        self.assertIn("ffprobe", str(caught.exception))

    def test_missing_binary_names_ffmpeg_and_the_way_round_it(self):
        """People install FFmpeg, not ffprobe, and the first run needs neither."""
        self.patch_run(fake_run())
        with mock.patch.object(media.shutil, "which", return_value=None):
            with self.assertRaises(media.MediaError) as caught:
                media.probe(self.video)
        message = str(caught.exception)
        self.assertIn("FFmpeg", message)
        self.assertIn("--subtitles", message)
        self.assertIn("--events", message)

    def test_non_zero_exit_includes_the_ffprobe_message(self):
        self.patch_run(fake_run(returncode=1, stdout="{}\n",
                                stderr="Invalid data found when processing input"))
        with self.assertRaises(media.MediaError) as caught:
            media.probe(self.video)
        self.assertIn("Invalid data found", str(caught.exception))

    def test_placeholder_file_probing_to_empty_json_is_rejected(self):
        """ffprobe answers `{}` for a non-video file. That is not a video."""
        self.patch_run(fake_run(stdout="{}\n"))
        with self.assertRaises(media.MediaError):
            media.probe(self.video)

    def test_unparseable_output_is_rejected(self):
        self.patch_run(fake_run(stdout="not json"))
        with self.assertRaises(media.MediaError):
            media.probe(self.video)

    def test_audio_only_file_is_rejected(self):
        payload = '{"streams": [{"index": 0, "codec_type": "audio"}], ' \
                  '"format": {"duration": "60.0"}}'
        self.patch_run(fake_run(stdout=payload))
        with self.assertRaises(media.MediaError) as caught:
            media.probe(self.video)
        self.assertIn("video", str(caught.exception).lower())

    def test_absent_duration_is_rejected(self):
        payload = '{"streams": [{"index": 0, "codec_type": "video"}], "format": {}}'
        self.patch_run(fake_run(stdout=payload))
        with self.assertRaises(media.MediaError):
            media.probe(self.video)

    def test_zero_duration_is_rejected(self):
        payload = '{"streams": [{"index": 0, "codec_type": "video"}], ' \
                  '"format": {"duration": "0.000000"}}'
        self.patch_run(fake_run(stdout=payload))
        with self.assertRaises(media.MediaError):
            media.probe(self.video)

    def test_non_finite_duration_is_rejected(self):
        payload = '{"streams": [{"index": 0, "codec_type": "video"}], ' \
                  '"format": {"duration": "inf"}}'
        self.patch_run(fake_run(stdout=payload))
        with self.assertRaises(media.MediaError):
            media.probe(self.video)

    def test_timeout_is_reported_as_a_media_error(self):
        def runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 1))

        self.patch_run(runner)
        with self.assertRaises(media.MediaError):
            media.probe(self.video)

    def test_unusable_binary_is_reported_as_a_media_error(self):
        def runner(argv, **kwargs):
            raise OSError(13, "Permission denied")

        self.patch_run(runner)
        with self.assertRaises(media.MediaError):
            media.probe(self.video)


# --------------------------------------------------------------------------
# duration_ms
# --------------------------------------------------------------------------

class DurationTests(unittest.TestCase):
    def test_whole_seconds(self):
        self.assertEqual(media.duration_ms(probe_json(duration="60.000000")), 60000)

    def test_millisecond_accuracy_is_preserved(self):
        self.assertEqual(media.duration_ms(probe_json(duration="12.345")), 12345)

    def test_sub_millisecond_rounds_to_nearest(self):
        self.assertEqual(media.duration_ms(probe_json(duration="12.3456")), 12346)

    def test_falls_back_to_the_video_stream_duration(self):
        info = {"streams": [video_stream(duration="60.000000")], "format": {}}
        self.assertEqual(media.duration_ms(info), 60000)

    def test_container_duration_wins_over_the_stream_duration(self):
        info = {"streams": [video_stream(duration="12.000")],
                "format": {"duration": "60.000"}}
        self.assertEqual(media.duration_ms(info), 60000)

    def test_not_available_is_rejected(self):
        with self.assertRaises(media.MediaError):
            media.duration_ms(probe_json(duration="N/A"))

    def test_negative_duration_is_rejected(self):
        with self.assertRaises(media.MediaError):
            media.duration_ms(probe_json(duration="-1.0"))

    def test_returns_an_int(self):
        self.assertIsInstance(media.duration_ms(probe_json()), int)


# --------------------------------------------------------------------------
# subtitle_streams
# --------------------------------------------------------------------------

class SubtitleStreamsTests(unittest.TestCase):
    def test_returns_only_subtitle_streams_in_file_order(self):
        info = probe_json([
            subtitle_stream(3, language="eng"),
            {"index": 4, "codec_type": "audio", "codec_name": "aac"},
            subtitle_stream(5, language="nld"),
        ])
        found = media.subtitle_streams(info)
        self.assertEqual([s["index"] for s in found], [3, 5])

    def test_exposes_the_raw_ffprobe_shape(self):
        info = probe_json([subtitle_stream(3, title="Full", hearing_impaired=1)])
        stream = media.subtitle_streams(info)[0]
        self.assertEqual(stream["codec_name"], "subrip")
        self.assertEqual(stream["tags"]["language"], "eng")
        self.assertEqual(stream["tags"]["title"], "Full")
        self.assertEqual(stream["disposition"]["hearing_impaired"], 1)

    def test_includes_bitmap_streams_so_a_listing_can_show_them(self):
        info = probe_json([subtitle_stream(3, codec_name="hdmv_pgs_subtitle")])
        self.assertEqual(len(media.subtitle_streams(info)), 1)

    def test_result_is_a_copy_that_cannot_corrupt_the_probe_info(self):
        info = probe_json([subtitle_stream(3)])
        media.subtitle_streams(info)[0]["tags"]["language"] = "zzz"
        self.assertEqual(info["streams"][1]["tags"]["language"], "eng")

    def test_no_subtitle_streams_returns_an_empty_list(self):
        self.assertEqual(media.subtitle_streams(probe_json()), [])


# --------------------------------------------------------------------------
# select_stream
# --------------------------------------------------------------------------

class SelectStreamTests(unittest.TestCase):
    def test_selects_the_single_english_text_stream(self):
        info = probe_json([subtitle_stream(3, language="eng")])
        self.assertEqual(media.select_stream(info)["index"], 3)

    def test_explicit_index_is_absolute_not_ordinal(self):
        """`--stream 5` means ffprobe index 5, never 'the fifth subtitle'."""
        info = probe_json([
            subtitle_stream(3, language="nld"),
            subtitle_stream(5, language="fra"),
        ])
        self.assertEqual(media.select_stream(info, index=5)["index"], 5)

    def test_explicit_index_one_does_not_mean_the_second_subtitle(self):
        info = probe_json([
            subtitle_stream(3, language="nld"),
            subtitle_stream(5, language="fra"),
        ])
        with self.assertRaises(media.MediaError):
            media.select_stream(info, index=1)

    def test_explicit_index_wins_over_the_language_preference(self):
        info = probe_json([
            subtitle_stream(3, language="eng"),
            subtitle_stream(4, language="nld"),
        ])
        self.assertEqual(media.select_stream(info, language="eng", index=4)["index"], 4)

    def test_explicit_index_pointing_at_a_video_stream_is_rejected(self):
        info = probe_json([subtitle_stream(3)])
        with self.assertRaises(media.MediaError):
            media.select_stream(info, index=0)

    def test_explicit_index_pointing_at_a_bitmap_stream_is_rejected(self):
        info = probe_json([subtitle_stream(3, codec_name="hdmv_pgs_subtitle")])
        with self.assertRaises(media.MediaError) as caught:
            media.select_stream(info, index=3)
        self.assertIn("hdmv_pgs_subtitle", str(caught.exception))

    def test_two_letter_language_tag_matches_a_three_letter_request(self):
        info = probe_json([subtitle_stream(3, language="en")])
        self.assertEqual(media.select_stream(info, language="eng")["index"], 3)

    def test_region_subtag_and_case_are_ignored(self):
        info = probe_json([subtitle_stream(3, language="EN-US")])
        self.assertEqual(media.select_stream(info, language="eng")["index"], 3)

    def test_ambiguous_english_tracks_fail_instead_of_guessing(self):
        info = probe_json([
            subtitle_stream(3, language="eng", title="Full"),
            subtitle_stream(4, language="eng", title="SDH", hearing_impaired=1),
        ])
        with self.assertRaises(media.MediaError):
            media.select_stream(info, language="eng")

    def test_ambiguity_message_names_every_candidate_and_the_way_out(self):
        """An SDH track must be offered, never silently discarded."""
        info = probe_json([
            subtitle_stream(3, language="eng", title="Full"),
            subtitle_stream(4, language="eng", title="SDH", hearing_impaired=1),
        ])
        with self.assertRaises(media.MediaError) as caught:
            media.select_stream(info, language="eng")
        message = str(caught.exception)
        self.assertIn("3", message)
        self.assertIn("4", message)
        self.assertIn("SDH", message)
        self.assertIn("hearing impaired", message.lower())
        self.assertIn("--stream", message)

    def test_a_default_disposition_does_not_break_the_tie(self):
        info = probe_json([
            subtitle_stream(3, language="eng", default=1),
            subtitle_stream(4, language="eng", hearing_impaired=1),
        ])
        with self.assertRaises(media.MediaError):
            media.select_stream(info, language="eng")

    def test_no_subtitle_streams_at_all_says_so(self):
        with self.assertRaises(media.MediaError) as caught:
            media.select_stream(probe_json())
        self.assertIn("no subtitle", str(caught.exception).lower())

    def test_only_bitmap_subtitles_asks_for_transcription(self):
        info = probe_json([subtitle_stream(3, codec_name="dvd_subtitle")])
        with self.assertRaises(media.MediaError) as caught:
            media.select_stream(info, language="eng")
        self.assertIn("bitmap", str(caught.exception).lower())

    def test_wrong_language_lists_what_is_available(self):
        info = probe_json([subtitle_stream(3, language="nld")])
        with self.assertRaises(media.MediaError) as caught:
            media.select_stream(info, language="eng")
        self.assertIn("nld", str(caught.exception))

    def test_untagged_single_track_is_not_assumed_to_be_english(self):
        info = probe_json([subtitle_stream(3, language=None)])
        with self.assertRaises(media.MediaError):
            media.select_stream(info, language="eng")

    def test_unknown_codec_is_not_assumed_to_be_text(self):
        info = probe_json([subtitle_stream(3, codec_name="some_future_codec")])
        with self.assertRaises(media.MediaError):
            media.select_stream(info, language="eng")


# --------------------------------------------------------------------------
# extract_subtitles
# --------------------------------------------------------------------------

SAMPLE_SRT = "1\n00:00:12,000 --> 00:00:17,000\nHarmless demonstration line.\n"


class ExtractSubtitlesTests(MediaTestCase):
    def test_returns_the_extracted_srt_text(self):
        self.patch_run(fake_run(writes=SAMPLE_SRT))
        self.assertEqual(media.extract_subtitles(self.video, 1), SAMPLE_SRT)

    def test_maps_the_absolute_stream_index_and_asks_for_srt(self):
        runner = self.patch_run(fake_run(writes=SAMPLE_SRT))
        media.extract_subtitles(self.video, 5)
        argv, kwargs = runner.calls[0]
        self.assert_argv_is_safe(argv, kwargs)
        self.assertIn("ffmpeg", argv[0])
        self.assertIn("-map", argv)
        self.assertEqual(argv[argv.index("-map") + 1], "0:5")
        self.assertEqual(argv[argv.index("-c:s") + 1], "srt")
        self.assertIn(str(self.video.resolve()), argv)

    def test_writes_only_inside_its_own_temporary_directory(self):
        runner = self.patch_run(fake_run(writes=SAMPLE_SRT))
        before = sorted(p.name for p in self.folder.iterdir())
        media.extract_subtitles(self.video, 1)
        argv, kwargs = runner.calls[0]
        self.assertIsNotNone(kwargs.get("cwd"))
        self.assertFalse(Path(argv[-1]).is_absolute(),
                         "the output name must be relative to the temp cwd")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), before)
        self.assertFalse(Path(kwargs["cwd"]).exists(), "temp dir must be removed")

    def test_non_zero_exit_reports_the_ffmpeg_message(self):
        self.patch_run(fake_run(returncode=8, stderr="Error selecting an encoder"))
        with self.assertRaises(media.MediaError) as caught:
            media.extract_subtitles(self.video, 0)
        self.assertIn("Error selecting an encoder", str(caught.exception))

    def test_success_exit_with_no_file_is_still_a_failure(self):
        """FFmpeg can exit 0 and write nothing. That is not an extraction."""
        self.patch_run(fake_run(returncode=0, writes=None))
        with self.assertRaises(media.MediaError):
            media.extract_subtitles(self.video, 1)

    def test_empty_track_is_rejected_rather_than_returned_as_no_dialogue(self):
        self.patch_run(fake_run(writes=""))
        with self.assertRaises(media.MediaError):
            media.extract_subtitles(self.video, 1)

    def test_missing_video_fails_without_running_ffmpeg(self):
        runner = self.patch_run(fake_run(writes=SAMPLE_SRT))
        with self.assertRaises(media.MediaError):
            media.extract_subtitles(self.folder / "absent.mkv", 1)
        self.assertEqual(runner.calls, [])

    def test_negative_index_is_rejected(self):
        self.patch_run(fake_run(writes=SAMPLE_SRT))
        with self.assertRaises(media.MediaError):
            media.extract_subtitles(self.video, -1)

    def test_non_integer_index_is_rejected(self):
        self.patch_run(fake_run(writes=SAMPLE_SRT))
        with self.assertRaises(media.MediaError):
            media.extract_subtitles(self.video, "1")


# --------------------------------------------------------------------------
# verify_frame
# --------------------------------------------------------------------------

class VerifyFrameTests(MediaTestCase):
    ASS = "[Script Info]\n\n[Events]\nDialogue: 0,0:00:12.00,0:00:16.00,,,,,,,X\n"

    def render(self, **kwargs):
        return media.verify_frame(self.video, self.ASS, ".ass", 12000, **kwargs)

    def test_returns_the_png_bytes(self):
        expected = make_png()
        self.patch_run(fake_run(writes=expected))
        self.assertEqual(self.render(), expected)

    def test_seeks_before_the_input_and_keeps_the_original_timestamps(self):
        """Input seeking without -copyts renders a blank frame. That would make
        verification prove nothing, so both flags are part of the contract."""
        runner = self.patch_run(fake_run(writes=make_png()))
        self.render()
        argv, kwargs = runner.calls[0]
        self.assert_argv_is_safe(argv, kwargs)
        self.assertIn("-copyts", argv)
        self.assertLess(argv.index("-ss"), argv.index("-i"),
                        "-ss must precede -i so long videos are not decoded whole")

    def test_seek_position_is_the_requested_millisecond(self):
        runner = self.patch_run(fake_run(writes=make_png()))
        media.verify_frame(self.video, self.ASS, ".ass", 12345)
        argv = runner.calls[0][0]
        self.assertEqual(argv[argv.index("-ss") + 1], "12.345")

    def test_zero_is_a_valid_target(self):
        runner = self.patch_run(fake_run(writes=make_png()))
        media.verify_frame(self.video, self.ASS, ".ass", 0)
        argv = runner.calls[0][0]
        self.assertEqual(argv[argv.index("-ss") + 1], "0.000")

    def test_subtitle_filter_gets_a_relative_filename(self):
        """The filter argument is parsed by FFmpeg, so a path with a space, an
        apostrophe or a colon must never reach it."""
        runner = self.patch_run(fake_run(writes=make_png()))
        self.render()
        argv, kwargs = runner.calls[0]
        filter_arg = argv[argv.index("-vf") + 1]
        self.assertIn("subtitles=subs.ass", filter_arg)
        self.assertNotIn("/", filter_arg)
        self.assertNotIn("'", filter_arg)
        self.assertNotIn(str(self.video), filter_arg)
        self.assertIsNotNone(kwargs.get("cwd"),
                             "the filter name only resolves relative to a cwd")

    def test_video_is_passed_as_a_resolved_absolute_argv_element(self):
        runner = self.patch_run(fake_run(writes=make_png()))
        self.render()
        argv = runner.calls[0][0]
        self.assertEqual(argv[argv.index("-i") + 1], str(self.video.resolve()))

    def test_srt_suffix_writes_an_srt_temp_file(self):
        runner = self.patch_run(fake_run(writes=make_png()))
        media.verify_frame(self.video, "1\n00:00:01,000 --> 00:00:02,000\nX\n",
                           ".srt", 1500)
        argv = runner.calls[0][0]
        self.assertIn("subtitles=subs.srt", argv[argv.index("-vf") + 1])

    def test_the_subtitle_text_reaches_ffmpeg_unchanged(self):
        seen = {}

        def runner(argv, **kwargs):
            seen["text"] = (Path(kwargs["cwd"]) / "subs.ass").read_text(encoding="utf-8")
            (Path(kwargs["cwd"]) / argv[-1]).write_bytes(make_png())
            return FakeCompleted(0)

        self.patch_run(runner)
        self.render()
        self.assertEqual(seen["text"], self.ASS)

    def test_writes_nothing_outside_its_temp_dir_and_cleans_up(self):
        runner = self.patch_run(fake_run(writes=make_png()))
        before = sorted(p.name for p in self.folder.iterdir())
        self.render()
        kwargs = runner.calls[0][1]
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), before)
        self.assertFalse(Path(kwargs["cwd"]).exists(), "temp dir must be removed")

    def test_temp_dir_is_removed_even_when_ffmpeg_fails(self):
        runner = self.patch_run(fake_run(returncode=1, stderr="boom"))
        with self.assertRaises(media.MediaError):
            self.render()
        self.assertFalse(Path(runner.calls[0][1]["cwd"]).exists())

    def test_success_exit_with_no_frame_is_still_a_failure(self):
        """A seek past the end exits 0 and writes nothing at all."""
        self.patch_run(fake_run(returncode=0, writes=None))
        with self.assertRaises(media.MediaError):
            self.render()

    def test_zero_byte_frame_is_rejected(self):
        self.patch_run(fake_run(writes=b""))
        with self.assertRaises(media.MediaError):
            self.render()

    def test_output_that_is_not_a_png_is_rejected(self):
        self.patch_run(fake_run(writes=b"GIF89a" + b"\x00" * 64))
        with self.assertRaises(media.MediaError) as caught:
            self.render()
        self.assertIn("PNG", str(caught.exception))

    def test_truncated_png_is_rejected(self):
        self.patch_run(fake_run(writes=make_png()[:-12]))
        with self.assertRaises(media.MediaError):
            self.render()

    def test_png_with_zero_dimensions_is_rejected(self):
        header = struct.pack(">IIBBBBB", 0, 0, 8, 2, 0, 0, 0)
        empty = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
                 + png_chunk(b"IDAT", zlib.compress(b"")) + png_chunk(b"IEND", b""))
        self.patch_run(fake_run(writes=empty))
        with self.assertRaises(media.MediaError):
            self.render()

    def test_unsupported_format_suffix_is_rejected(self):
        runner = self.patch_run(fake_run(writes=make_png()))
        with self.assertRaises(media.MediaError):
            media.verify_frame(self.video, self.ASS, ".vtt", 12000)
        self.assertEqual(runner.calls, [])

    def test_empty_subtitle_text_is_rejected(self):
        self.patch_run(fake_run(writes=make_png()))
        with self.assertRaises(media.MediaError):
            media.verify_frame(self.video, "   ", ".ass", 12000)

    def test_negative_target_is_rejected(self):
        self.patch_run(fake_run(writes=make_png()))
        with self.assertRaises(media.MediaError):
            media.verify_frame(self.video, self.ASS, ".ass", -1)

    def test_non_integer_target_is_rejected(self):
        self.patch_run(fake_run(writes=make_png()))
        with self.assertRaises(media.MediaError):
            media.verify_frame(self.video, self.ASS, ".ass", 12.5)

    def test_missing_video_fails_without_running_ffmpeg(self):
        runner = self.patch_run(fake_run(writes=make_png()))
        with self.assertRaises(media.MediaError):
            media.verify_frame(self.folder / "absent.mkv", self.ASS, ".ass", 1000)
        self.assertEqual(runner.calls, [])


# --------------------------------------------------------------------------
# Module hygiene
# --------------------------------------------------------------------------

class ModuleContractTests(unittest.TestCase):
    def test_media_error_is_a_value_error(self):
        self.assertTrue(issubclass(media.MediaError, ValueError))

    def test_media_does_not_import_the_core_module(self):
        source = (REPO_ROOT / "trigger_warnings" / "media.py").read_text(encoding="utf-8")
        self.assertNotIn("import core", source)
        self.assertNotIn("from .core", source)
        self.assertNotIn("from trigger_warnings.core", source)

    def test_media_uses_no_third_party_imports(self):
        source = (REPO_ROOT / "trigger_warnings" / "media.py").read_text(encoding="utf-8")
        for banned in ("import requests", "import ffmpeg", "import numpy"):
            self.assertNotIn(banned, source)

    def test_media_prints_nothing(self):
        source = (REPO_ROOT / "trigger_warnings" / "media.py").read_text(encoding="utf-8")
        self.assertNotIn("print(", source, "the CLI owns all reporting")

    def test_the_frozen_names_exist(self):
        for name in ("MediaError", "probe", "duration_ms", "subtitle_streams",
                     "select_stream", "extract_subtitles", "verify_frame"):
            self.assertTrue(hasattr(media, name), name)


# --------------------------------------------------------------------------
# Real FFmpeg smoke tests
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are required")
class RealFFmpegTests(unittest.TestCase):
    """End-to-end against a generated silent video. No real film data."""

    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="media smoke ")
        cls.folder = Path(cls.workspace.name)
        # Space and apostrophe on purpose: the filter-quoting case.
        cls.video = cls.folder / "demo 'edition'.mp4"
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "make_demo.py"),
             "--output", str(cls.video)],
            check=True, capture_output=True, timeout=180,
        )

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.info = media.probe(self.video)

    def test_probe_finds_a_sixty_second_video(self):
        self.assertEqual(media.duration_ms(self.info), 60000)

    def test_subtitle_streams_reports_the_embedded_english_track(self):
        streams = media.subtitle_streams(self.info)
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["tags"]["language"], "eng")
        self.assertEqual(streams[0]["codec_name"], "mov_text")

    def test_select_stream_picks_the_english_track_by_absolute_index(self):
        chosen = media.select_stream(self.info, language="eng")
        self.assertEqual(chosen["index"], 1)

    def test_extract_subtitles_returns_usable_srt(self):
        text = media.extract_subtitles(self.video, 1)
        self.assertIn("harmless subtitle demonstration", text)
        self.assertIn("00:00:12,000 --> 00:00:17,000", text)

    def test_extracting_a_video_stream_fails_loudly(self):
        with self.assertRaises(media.MediaError):
            media.extract_subtitles(self.video, 0)

    def test_extracting_an_absent_stream_fails_loudly(self):
        with self.assertRaises(media.MediaError):
            media.extract_subtitles(self.video, 9)

    def test_verify_frame_renders_a_real_png(self):
        png = media.verify_frame(self.video, ASS_FIXTURE, ".ass", 12000)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(png), 2000)

    def test_the_rendered_frame_actually_contains_the_subtitle(self):
        """Guards the -copyts contract: a naive input seek yields a valid but
        blank PNG, which would let verification pass while proving nothing."""
        inside = media.verify_frame(self.video, ASS_FIXTURE, ".ass", 12000)
        outside = media.verify_frame(self.video, ASS_FIXTURE, ".ass", 45000)
        self.assertNotEqual(inside, outside)
        self.assertGreater(len(inside), len(outside) * 3)

    def test_srt_input_renders_too(self):
        png = media.verify_frame(self.video, SRT_FIXTURE, ".srt", 12000)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_seeking_past_the_end_fails_instead_of_returning_nothing(self):
        with self.assertRaises(media.MediaError):
            media.verify_frame(self.video, ASS_FIXTURE, ".ass", 65000)

    def test_a_placeholder_file_is_not_accepted_as_a_video(self):
        fake = self.folder / "not-really-a-video.mkv"
        fake.write_text("not a video\n", encoding="utf-8")
        with self.assertRaises(media.MediaError):
            media.probe(fake)

    def test_rendering_from_a_placeholder_file_fails(self):
        fake = self.folder / "also-not-a-video.mkv"
        fake.write_text("not a video\n", encoding="utf-8")
        with self.assertRaises(media.MediaError):
            media.verify_frame(fake, ASS_FIXTURE, ".ass", 1000)


ASS_FIXTURE = """[Script Info]
ScriptType: v4.00+
PlayResX: 960
PlayResY: 540

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, \
Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, \
MarginV, Encoding
Style: Warning,Arial,56,&H0000FFFF,&H00000000,&H80000000,-1,0,1,3,1,8,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:10.00,0:00:16.00,Warning,,0,0,0,,TRIGGER INCOMING
"""

SRT_FIXTURE = """1
00:00:10,000 --> 00:00:16,000
TRIGGER INCOMING
"""


if __name__ == "__main__":
    unittest.main()
