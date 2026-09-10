"""Decode a local video once, keeping only the current sampled clip in memory."""

import math
from collections import namedtuple


class VideoError(ValueError):
    """Video decoding could not preserve a usable timeline."""


VideoClip = namedtuple("VideoClip", "start end frames timestamps source", defaults=[None])


class VideoReader:
    """A sequential, timestamp-based reader for MLX-VLM's video input.

    PyAV opens the original container directly. No subprocess, intermediate
    movie or image file is created. Presentation timestamps preserve variable
    frame rates; frame indices are never treated as seconds.
    """

    def __init__(self, path, fps, width):
        self.path = str(path)
        self.fps = fps
        self.width = width
        self.container = None
        self._pending = None
        self._previous_time = None
        self._end = 0.0

    def __enter__(self):
        try:
            import av
        except ImportError as error:
            raise VideoError(
                "video scanning needs PyAV; install the updated 'trigger-warnings[vision]' extra"
            ) from error
        try:
            self.container = av.open(self.path)
            if not self.container.streams.video:
                raise VideoError("the input has no video stream")
            stream = self.container.streams.video[0]
            self.origin = (self.container.start_time / av.time_base
                           if self.container.start_time is not None else
                           float((stream.start_time or 0) * stream.time_base))
            if stream.duration is not None:
                self.duration = float((stream.start_time or 0) * stream.time_base
                                      + stream.duration * stream.time_base) - self.origin
            elif self.container.duration is not None:
                self.duration = self.container.duration / av.time_base
            else:
                raise VideoError("video duration is unavailable")
            if not math.isfinite(self.duration) or self.duration <= 0:
                raise VideoError("video duration must be finite and positive")
            self._frames = iter(self.container.decode(stream))
            self._advance()
            return self
        except BaseException:
            if self.container is not None:
                self.container.close()
            raise

    def __exit__(self, exc_type, exc, traceback):
        self._pending = None
        self._frames = None
        self.container.close()

    def _advance(self):
        frame = next(self._frames, None)
        if frame is None:
            self._pending = None
            return
        if frame.is_corrupt or frame.time is None:
            raise VideoError("video contains a corrupt frame or missing presentation timestamp")
        timestamp = float(frame.time) - self.origin
        if not math.isfinite(timestamp) or (
                self._previous_time is not None and timestamp < self._previous_time):
            raise VideoError("video presentation timestamps are invalid or go backwards")
        self._previous_time = timestamp
        self._pending = (frame, timestamp)

    def read_clip(self, start, end):
        """Read adjacent [start, end) windows in playback seconds."""
        if not math.isclose(start, self._end) or not start < end <= self.duration:
            raise VideoError("video clips must be adjacent and within the video duration")
        frames, timestamps = [], []
        next_sample = start
        while self._pending is not None:
            frame, timestamp = self._pending
            if timestamp >= end:
                break
            if timestamp >= next_sample:
                height = max(1, round(frame.height * self.width / frame.width))
                frames.append(frame.reformat(width=self.width, height=height,
                                             format="rgb24").to_ndarray())
                timestamps.append(timestamp)
                next_sample = start + (math.floor((timestamp - start) * self.fps + 1e-7) + 1) / self.fps
            self._advance()
        if not frames:
            raise VideoError("video produced no sampled frames in {:.3f}s–{:.3f}s".format(start, end))
        self._end = end
        return VideoClip(start, end, frames, timestamps, self.path)
