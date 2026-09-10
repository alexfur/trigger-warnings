"""Video windows follow presentation timestamps and never write frame files."""

from fractions import Fraction
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from trigger_warnings.video import VideoError, VideoReader


class VideoReaderTests(unittest.TestCase):
    def setUp(self):
        # A variable-rate video with a nonzero container start time.
        self.frames = []
        for offset in (0, 0.2, 0.9, 1.1, 1.8, 2, 2.9):
            frame = mock.Mock(time=5 + offset, is_corrupt=False, width=100, height=50)
            frame.reformat.return_value.to_ndarray.return_value = offset
            self.frames.append(frame)
        stream = types.SimpleNamespace(duration=30, start_time=50, time_base=Fraction(1, 10))
        self.container = mock.Mock(start_time=5000000, duration=3000000)
        self.container.streams.video = [stream]
        self.container.decode.return_value = iter(self.frames)
        av = types.ModuleType("av")
        av.time_base = 1000000
        av.open = mock.Mock(return_value=self.container)
        self.open = av.open
        patcher = mock.patch.dict(sys.modules, {"av": av})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_variable_timestamps_chunk_boundaries_and_resize(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("no subprocess allowed")):
            with VideoReader("movie.mkv", 1, 40) as reader:
                self.assertEqual(reader.duration, 3)
                first = reader.read_clip(0, 2)
                second = reader.read_clip(2, 3)
                self.assertEqual(first.frames, [0, 1.1])
                self.assertEqual(second.frames, [2])
                self.assertAlmostEqual(first.timestamps[1], 1.1)
                self.assertEqual(second.timestamps, [2])
                self.assertEqual(second.source, "movie.mkv")
        self.frames[0].reformat.assert_called_once_with(width=40, height=20, format="rgb24")
        self.open.assert_called_once_with("movie.mkv")
        self.container.decode.assert_called_once()
        self.container.close.assert_called_once()

    def test_chunk_without_frames_fails_instead_of_claiming_a_negative(self):
        self.container.decode.return_value = iter(self.frames[:1])
        with self.assertRaisesRegex(VideoError, "no sampled frames"):
            with VideoReader("movie.mkv", 1, 40) as reader:
                reader.read_clip(0, 2)
                reader.read_clip(2, 3)
        self.container.close.assert_called_once()

    def test_corrupt_missing_or_backwards_timestamps_fail_and_close(self):
        for attr, value in (("is_corrupt", True), ("time", None), ("time", 4)):
            with self.subTest(attr=attr, value=value):
                self.container.reset_mock()
                self.container.decode.return_value = iter(self.frames)
                original = getattr(self.frames[1], attr)
                setattr(self.frames[1], attr, value)
                with self.assertRaises(VideoError):
                    with VideoReader("movie.mkv", 1, 40) as reader:
                        reader.read_clip(0, 3)
                setattr(self.frames[1], attr, original)
                self.container.close.assert_called_once()

    def test_container_closes_on_inference_failure_or_cancellation(self):
        for error in (RuntimeError(), KeyboardInterrupt()):
            self.container.reset_mock()
            self.container.decode.return_value = iter(self.frames)
            with self.assertRaises(type(error)):
                with VideoReader("movie.mkv", 1, 40) as reader:
                    reader.read_clip(0, 2)
                    raise error
            self.container.close.assert_called_once()

    def test_missing_video_stream_closes_container(self):
        self.container.streams.video = []
        with self.assertRaisesRegex(VideoError, "no video stream"):
            with VideoReader("audio.mkv", 1, 40):
                pass
        self.container.close.assert_called_once()


@unittest.skipUnless(importlib.util.find_spec("av"), "optional PyAV is not installed")
class VideoDecodeTests(unittest.TestCase):
    def test_real_video_decode_without_ffmpeg_subprocess_or_image_files(self):
        import av
        import numpy as np

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.mkv"
            with av.open(str(path), "w") as container:
                stream = container.add_stream("ffv1", rate=10)
                stream.width, stream.height = 64, 32
                stream.pix_fmt = "yuv420p"
                for index in (0, 2, 9, 11, 18, 20, 29):
                    pixels = np.full((32, 64, 3), 255 if index < 20 else 0, dtype=np.uint8)
                    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                    frame.pts, frame.time_base = index, Fraction(1, 10)
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            with mock.patch("subprocess.run", side_effect=AssertionError("no subprocess allowed")):
                with VideoReader(path, 1, 32) as reader:
                    first = reader.read_clip(0, 2)
                    second = reader.read_clip(2, reader.duration)
                    self.assertEqual(first.timestamps, [0, 1.1])
                    self.assertEqual(second.timestamps, [2])
                    self.assertEqual(first.frames[0].shape, (16, 32, 3))
                    self.assertGreater(first.frames[0].mean(), 240)
                    self.assertLess(second.frames[0].mean(), 10)
            self.assertEqual(list(Path(folder).iterdir()), [path])
