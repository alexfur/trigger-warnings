"""Opt-in tests using real FFmpeg. Run with RUN_MEDIA_TESTS=1."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("RUN_MEDIA_TESTS") == "1"
    and shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "set RUN_MEDIA_TESTS=1 with ffmpeg and ffprobe installed",
)
class MediaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="warning integration ")
        cls.folder = Path(cls.workspace.name)
        cls.video = cls.folder / "demo 'quoted'.mp4"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/make_demo.py"),
             "--output", str(cls.video)],
            check=True, capture_output=True, timeout=120,
        )

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def region_contrast(self, preview, bottom=False):
        # The demo is a uniform colour. High contrast in these separate regions
        # proves that text was drawn, rather than merely that a PNG was produced.
        crop = "crop=iw:ih/3:0:{}".format("2*ih/3" if bottom else "0")
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
             "-i", str(preview), "-vf", crop + ",format=gray",
             "-frames:v", "1", "-f", "rawvideo", "pipe:1"],
            check=True, capture_output=True, timeout=30,
        )
        self.assertTrue(result.stdout)
        return max(result.stdout) - min(result.stdout)

    def test_extract_and_verify_real_video(self):
        output = self.folder / "extracted 'quoted'.ass"
        preview = self.folder / "preview 'quoted'.png"
        result = subprocess.run(
            [sys.executable, "-m", "trigger_warnings", "--video", str(self.video),
             "--events", str(ROOT / "examples/events.json"),
             "--output", str(output), "--verify", str(preview)],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TRIGGER INCOMING", output.read_text())
        self.assertIn("harmless subtitle demonstration", output.read_text())
        self.assertEqual(preview.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(preview.stat().st_size, 2000)
        self.assertGreater(self.region_contrast(preview), 100,
                           "preview must show a warning at the top")
        self.assertGreater(self.region_contrast(preview, bottom=True), 100,
                           "preferred preview must show dialogue at the bottom")

    def test_verify_without_dialogue_in_warning(self):
        events = self.folder / "point.json"
        events.write_text('[{"start": 8, "end": 10, "label": "demo"}]')
        output = self.folder / "fallback.ass"
        preview = self.folder / "fallback.png"
        result = subprocess.run(
            [sys.executable, "-m", "trigger_warnings", "--video", str(self.video),
             "--subtitles", str(ROOT / "examples/dialogue.srt"),
             "--events", str(events), "--lead", "4", "--output", str(output),
             "--verify", str(preview)],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(preview.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(preview.stat().st_size, 2000)
        self.assertGreater(self.region_contrast(preview), 100,
                           "fallback preview must still show the warning")
        self.assertLess(self.region_contrast(preview, bottom=True), 5,
                        "fallback fixture has no dialogue in this warning")
