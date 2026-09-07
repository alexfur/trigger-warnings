"""The documented first run, proved end to end from the bundled fixtures.

The README promises a visitor one command that works with no account, no
network and no FFmpeg. Continuous integration runs that command, but a green
workflow step is not a test: it does not check that the inputs survive
untouched, and it cannot tell a local run from a remote call.

So this module drives the exact documented command and asserts the four
properties the promise rests on:

*   it succeeds using only files tracked in this repository;
*   the output carries both the warning banner and the original dialogue;
*   both inputs are byte-identical afterwards, and the output is a new path;
*   it completes with an empty PATH and with the socket module disabled, so
    neither FFmpeg nor a network service can be involved.

Every fixture used here is the synthetic material already shipped in
`examples/`.
"""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
DIALOGUE = REPO_ROOT / "examples" / "dialogue.srt"
EVENTS = REPO_ROOT / "examples" / "events.json"

TIMEOUT_S = 60
WARNING_TEXT = "TRIGGER INCOMING"

# Imported by the interpreter before anything else runs, so the tool cannot
# reach a network service even if it wanted to. A network call fails loudly
# here instead of passing quietly.
#
# Only the connecting calls are blocked, never the socket class itself: `ssl`
# subclasses `socket.socket` at import time, so replacing the class breaks
# every import of `urllib.request` and turns this into a test of the blocker.
_NO_NETWORK = """
import socket


class _Blocked(Exception):
    pass


def _refuse(*args, **kwargs):
    raise _Blocked("this run must not touch the network")


socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse
socket.getaddrinfo = _refuse
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FirstRunTests(unittest.TestCase):
    """The no-account path a new visitor is told to run first."""

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="first run ")
        self.folder = Path(self.workspace.name)
        self.addCleanup(self.workspace.cleanup)
        self.output = self.folder / "example.warned.ass"
        self.before = {path: digest(path) for path in (DIALOGUE, EVENTS)}

    def run_documented_command(self, **overrides):
        """Run the README command with no FFmpeg and no network available."""
        empty = self.folder / "empty path"
        empty.mkdir(exist_ok=True)
        blocker = self.folder / "no network"
        blocker.mkdir(exist_ok=True)
        (blocker / "sitecustomize.py").write_text(_NO_NETWORK)
        env = dict(os.environ)
        env["PATH"] = str(empty)
        env["PYTHONPATH"] = os.pathsep.join([str(blocker), str(REPO_ROOT)])
        env.pop("DDD_API_KEY", None)
        env.pop("OPENSUBTITLES_API_KEY", None)
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-m", "trigger_warnings",
             "--subtitles", str(DIALOGUE), "--events", str(EVENTS),
             "--output", str(self.output)],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
            timeout=TIMEOUT_S,
        )

    def test_bundled_example_succeeds_offline_without_ffmpeg(self):
        result = self.run_documented_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.output.is_file())
        self.assertGreater(self.output.stat().st_size, 0)

    def test_output_carries_the_banner_and_the_original_dialogue(self):
        self.run_documented_command()
        written = self.output.read_text(encoding="utf-8")
        self.assertIn(WARNING_TEXT, written)
        self.assertIn("This is a harmless subtitle demonstration.", written)
        self.assertIn("An ended warning is not an all-clear.", written)

    def test_the_banner_never_names_the_category(self):
        self.run_documented_command()
        written = self.output.read_text(encoding="utf-8")
        for line in written.splitlines():
            if WARNING_TEXT in line:
                self.assertNotIn("example", line.lower())

    def test_generation_is_additive(self):
        self.run_documented_command()
        for path, expected in self.before.items():
            self.assertEqual(digest(path), expected,
                             "{} must not be modified".format(path.name))
        self.assertNotEqual(self.output.resolve(), DIALOGUE.resolve())

    def test_the_report_names_the_file_it_wrote(self):
        """The prose report goes to stderr, leaving stdout for --json."""
        result = self.run_documented_command()
        self.assertIn(str(self.output), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_second_run_refuses_rather_than_replacing_the_output(self):
        self.run_documented_command()
        first = digest(self.output)
        result = self.run_documented_command()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(digest(self.output), first)
        self.assertIn("already exists", result.stderr + result.stdout)

    def test_the_terminal_asset_still_shows_what_the_tool_says(self):
        """A picture of stale output is worse than no picture.

        `assets/terminal.svg` embeds the captured report in its `desc`, so this
        compares that text with what the tool prints today. Regenerate the
        asset with `python3 scripts/make_visuals.py` when this fails.
        """
        card = REPO_ROOT / "assets" / "terminal.svg"
        if not card.is_file():
            self.skipTest("terminal.svg has not been generated yet")
        described = " ".join(card.read_text(encoding="utf-8").split())
        for sentence in self.run_documented_command().stderr.split(". "):
            trimmed = " ".join(sentence.split()).strip(". ")
            if len(trimmed) > 20 and "example.warned.ass" not in trimmed:
                self.assertTrue(
                    trimmed in described,
                    "assets/terminal.svg no longer shows what the tool prints. "
                    "Missing: {!r}. Regenerate it with "
                    "`python3 scripts/make_visuals.py`.".format(trimmed),
                )

    def test_the_network_blocker_itself_works(self):
        """Without this the offline claim above could pass by accident."""
        blocker = self.folder / "no network"
        blocker.mkdir(exist_ok=True)
        (blocker / "sitecustomize.py").write_text(_NO_NETWORK)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(blocker)
        probe = subprocess.run(
            [sys.executable, "-c",
             "import urllib.request; urllib.request.urlopen('https://example.com')"],
            env=env, capture_output=True, text=True, timeout=TIMEOUT_S,
        )
        self.assertNotEqual(probe.returncode, 0)
        self.assertIn("must not touch the network", probe.stderr)


if __name__ == "__main__":
    unittest.main()
