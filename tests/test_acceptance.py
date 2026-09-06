"""Black-box acceptance tests for the trigger-warnings CLI.

These tests were written independently of the implementation, against the frozen
command-line contract only. They drive `python3 -m trigger_warnings` as a
subprocess and never import the package, so they make no assumption about
internal module layout, function names, or data structures.

Core local-file contract under test:

    python3 -m trigger_warnings --subtitles dialogue.srt --events events.json \
        --output warned.ass|warned.srt [--category LABEL]... [--lead 20] \
        [--tail 0] [--offset 0] [--video PATH] [--verify preview.png]

Events file is a JSON array of objects: {"start", "end" (optional),
"label", "severity" (optional)}. Times are numeric seconds or "HH:MM:SS.mmm".

Every input used here is synthetic and harmless: short generated subtitle text,
small JSON files, and a non-video placeholder file used only to prove that a
verify step fails loudly rather than silently.

Run with:  python3 -m unittest discover -s tests -v
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "trigger_warnings"

# Time limit for any single CLI invocation. Generous, but stops a hung
# subprocess from wedging the whole suite.
TIMEOUT_S = 60

WARNING_TEXT = "TRIGGER INCOMING"

# Matches SRT ("00:01:02,500") and ASS ("0:01:02.50") timestamps alike.
TIMESTAMP_RE = re.compile(r"(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<frac>\d{2,3})")


def _find_import_root():
    """Return the directory to put on PYTHONPATH so `trigger_warnings` imports.

    Supports both a flat layout (<root>/trigger_warnings/) and a src layout
    (<root>/src/trigger_warnings/) without constraining which one is used.
    """
    for candidate in (REPO_ROOT, REPO_ROOT / "src"):
        if (candidate / PACKAGE_NAME / "__init__.py").is_file():
            return candidate
        if (candidate / PACKAGE_NAME).is_dir():
            return candidate
    return None


IMPORT_ROOT = _find_import_root() or REPO_ROOT


# Interpreter-level failures that mean the tool never ran at all. A negative
# test must never be satisfied by one of these: "the CLI rejected my bad input"
# and "the CLI does not exist" both exit non-zero and are otherwise identical
# from the outside.
NOT_RUN_MARKERS = (
    "No module named",
    "cannot be directly executed",
    "ModuleNotFoundError",
    "ImportError",
    "SyntaxError",
)


def _run_module(*args):
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(IMPORT_ROOT) + (os.pathsep + existing if existing else "")
    return subprocess.run(
        [sys.executable, "-m", PACKAGE_NAME, *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )


def setUpModule():
    """A missing package is a failure, not a skip, and not a silent pass.

    An earlier revision skipped the suite while the implementation was absent so
    the tests could land first. That is gone deliberately: a skip is
    indistinguishable from a pass in CI.

    The second check matters more, and was added after this suite caught itself
    out. Roughly half these tests assert that a bad input is REJECTED. Against a
    package with no runnable entry point every one of them passed, because the
    interpreter's own "No module named" exit is also a non-zero exit. So before
    any test runs, prove the entry point exists and works.
    """
    if _find_import_root() is None:
        raise AssertionError(
            "package '{}' was not found under {} or {}/src. The acceptance "
            "suite fails rather than skips so a missing or unimportable "
            "package cannot pass CI.".format(PACKAGE_NAME, REPO_ROOT, REPO_ROOT)
        )
    probe = _run_module("--help")
    if probe.returncode != 0 or any(
        marker in probe.stderr for marker in NOT_RUN_MARKERS
    ):
        raise AssertionError(
            "`python -m {}` is not runnable, so no acceptance test can mean "
            "anything: a rejected-input test would pass on the interpreter's "
            "own error.\nexit={}\nstderr:\n{}".format(
                PACKAGE_NAME, probe.returncode, probe.stderr
            )
        )


def parse_timestamp(text):
    """Return seconds as a float for an SRT or ASS timestamp string."""
    match = TIMESTAMP_RE.fullmatch(text.strip())
    if match is None:
        raise ValueError("not a timestamp: {!r}".format(text))
    frac = match.group("frac")
    # ASS uses centiseconds, SRT milliseconds.
    divisor = 100.0 if len(frac) == 2 else 1000.0
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(frac) / divisor
    )


def extract_cues(text):
    """Return [(start_s, end_s, payload)] from SRT or ASS output.

    Deliberately format-tolerant: it keys on timestamp pairs rather than on a
    particular header, style name, or field order, so the implementation stays
    free to shape its own ASS header.
    """
    cues = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "-->" in stripped:  # SRT
            left, _, right = stripped.partition("-->")
            try:
                start = parse_timestamp(left)
                end = parse_timestamp(right)
            except ValueError:
                continue
            cues.append([start, end, ""])
            continue
        if stripped.startswith("Dialogue:"):  # ASS
            fields = stripped[len("Dialogue:"):].split(",", 9)
            if len(fields) < 10:
                continue
            try:
                start = parse_timestamp(fields[1])
                end = parse_timestamp(fields[2])
            except ValueError:
                continue
            cues.append([start, end, fields[9]])
            continue
        if cues and not cues[-1][2]:
            # SRT payload lines follow their timestamp line.
            cues[-1][2] = stripped
        elif cues and cues[-1][2] and not stripped.isdigit():
            cues[-1][2] += "\n" + stripped
    return [tuple(cue) for cue in cues]


def warning_cues(cues):
    return [cue for cue in cues if WARNING_TEXT in cue[2]]


def dialogue_cues(cues):
    return [cue for cue in cues if WARNING_TEXT not in cue[2]]


DIALOGUE_SRT = """1
00:00:10,000 --> 00:00:14,000
The kettle is boiling.

2
00:00:38,000 --> 00:00:42,000
Someone should answer the door.

3
00:01:00,000 --> 00:01:04,000
It stopped raining an hour ago.

4
00:02:30,000 --> 00:02:34,000
We should head back before dark.
"""


class CliTestCase(unittest.TestCase):
    """Base class: a scratch directory and a helper that runs the CLI."""

    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="trigger-warnings-acceptance-"))
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.subtitles = self.write("dialogue.srt", DIALOGUE_SRT)

    def write(self, name, content):
        path = self.workdir / name
        if not isinstance(content, str):
            content = json.dumps(content)
        path.write_text(content, encoding="utf-8")
        return path

    def write_events(self, events, name="events.json"):
        return self.write(name, json.dumps(events, indent=2))

    def run_cli(self, *args):
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(IMPORT_ROOT) + (os.pathsep + existing if existing else "")
        )
        # Keep the child's output deterministic and independent of the parent.
        env.pop("PYTHONWARNINGS", None)
        return subprocess.run(
            [sys.executable, "-m", PACKAGE_NAME, *[str(a) for a in args]],
            cwd=str(self.workdir),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )

    def build(self, events, output="warned.srt", extra=(), subtitles=None):
        """Run a build that is expected to succeed; return (path, text)."""
        events_path = (
            events if isinstance(events, Path) else self.write_events(events)
        )
        out_path = self.workdir / output
        result = self.run_cli(
            "--subtitles", subtitles or self.subtitles,
            "--events", events_path,
            "--output", out_path,
            *extra,
        )
        self.assertEqual(
            result.returncode,
            0,
            "expected success\nstdout:\n{}\nstderr:\n{}".format(
                result.stdout, result.stderr
            ),
        )
        self.assertTrue(out_path.is_file(), "no output file was written")
        return out_path, out_path.read_text(encoding="utf-8")

    def assert_no_traceback(self, result):
        """Bad input must produce a diagnosed error, never a stack trace.

        An unhandled exception still exits non-zero, so without this check every
        "must fail" test below could be satisfied by a crash. The second check
        catches the weaker case where the tool never ran at all.
        """
        self.assertNotIn(
            "Traceback (most recent call last)",
            result.stderr,
            "an unhandled exception escaped to the user:\n{}".format(result.stderr),
        )
        for marker in NOT_RUN_MARKERS:
            self.assertNotIn(
                marker,
                result.stderr,
                "the tool did not run, so this failure proves nothing:\n{}".format(
                    result.stderr
                ),
            )

    def assert_failed(self, result, out_path=None):
        """A failure must be loud: non-zero exit, and no half-written output."""
        self.assertNotEqual(
            result.returncode,
            0,
            "expected a non-zero exit\nstdout:\n{}\nstderr:\n{}".format(
                result.stdout, result.stderr
            ),
        )
        self.assert_no_traceback(result)
        if out_path is not None:
            self.assertFalse(
                Path(out_path).exists(),
                "a failed run must not leave an output file behind",
            )

    def run_expecting_failure(self, *args, output="warned.srt"):
        """Run with the standard inputs plus `args`; assert a clean failure."""
        out_path = self.workdir / output
        result = self.run_cli("--output", out_path, *args)
        self.assert_failed(result, out_path)
        return result

    def bad_events(self, raw, *extra):
        """Assert that an events file with this raw JSON text is rejected."""
        events_path = self.write("events.json", raw)
        return self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path, *extra
        )


class TestMergeSemantics(CliTestCase):
    """The core promise: warnings are added, dialogue survives untouched."""

    def test_every_dialogue_line_survives(self):
        _, text = self.build([{"start": 60, "end": 64, "label": "gore"}])
        for line in (
            "The kettle is boiling.",
            "Someone should answer the door.",
            "It stopped raining an hour ago.",
            "We should head back before dark.",
        ):
            self.assertIn(line, text, "dialogue line was dropped from the merge")

    def test_dialogue_cue_count_is_preserved(self):
        _, text = self.build([{"start": 60, "end": 64, "label": "gore"}])
        self.assertEqual(
            len(dialogue_cues(extract_cues(text))),
            4,
            "the merge must neither drop nor split dialogue cues",
        )

    def test_a_warning_cue_is_emitted(self):
        _, text = self.build([{"start": 60, "end": 64, "label": "gore"}])
        warnings = warning_cues(extract_cues(text))
        self.assertEqual(len(warnings), 1)

    def test_warning_text_is_generic(self):
        """The label must never reach the screen: it is the spoiler."""
        _, text = self.build(
            [{"start": 60, "end": 64, "label": "amputation", "severity": "major"}]
        )
        warnings = warning_cues(extract_cues(text))
        self.assertTrue(warnings, "no warning cue to inspect")
        for cue in warnings:
            self.assertNotIn("amputation", cue[2].lower())
            self.assertNotIn("major", cue[2].lower())
        # The label must not leak anywhere else in the file either.
        self.assertNotIn("amputation", text.lower())

    def test_warning_overlaps_the_dialogue_it_covers(self):
        """Both cues must be live at once, which is what makes it a merge."""
        _, text = self.build([{"start": 60, "end": 64, "label": "gore"}])
        cues = extract_cues(text)
        warning = warning_cues(cues)[0]
        overlapping = [
            cue
            for cue in dialogue_cues(cues)
            if cue[0] < warning[1] and cue[1] > warning[0]
        ]
        self.assertTrue(
            overlapping,
            "the warning window should overlap the dialogue it precedes",
        )


class TestTiming(CliTestCase):
    """Lead, tail, offset and point events, to the millisecond."""

    def assert_window(self, text, expected_start, expected_end):
        warnings = warning_cues(extract_cues(text))
        self.assertEqual(len(warnings), 1, "expected exactly one warning window")
        start, end, _ = warnings[0]
        self.assertAlmostEqual(start, expected_start, delta=0.011)
        self.assertAlmostEqual(end, expected_end, delta=0.011)

    def test_default_lead_is_twenty_seconds(self):
        _, text = self.build([{"start": 60, "end": 64, "label": "gore"}])
        self.assert_window(text, 40.0, 64.0)

    def test_explicit_lead_moves_the_start(self):
        _, text = self.build(
            [{"start": 60, "end": 64, "label": "gore"}], extra=("--lead", "5")
        )
        self.assert_window(text, 55.0, 64.0)

    def test_tail_extends_past_the_safe_time(self):
        _, text = self.build(
            [{"start": 60, "end": 64, "label": "gore"}], extra=("--tail", "3")
        )
        self.assert_window(text, 40.0, 67.0)

    def test_offset_shifts_the_whole_window(self):
        _, text = self.build(
            [{"start": 60, "end": 64, "label": "gore"}], extra=("--offset", "15")
        )
        self.assert_window(text, 55.0, 79.0)

    def test_negative_offset_is_accepted(self):
        _, text = self.build(
            [{"start": 60, "end": 64, "label": "gore"}], extra=("--offset", "-5")
        )
        self.assert_window(text, 35.0, 59.0)

    def test_point_event_still_produces_a_lead_in(self):
        """No end time must not collapse the warning to nothing."""
        _, text = self.build([{"start": 60, "label": "gore"}])
        warnings = warning_cues(extract_cues(text))
        self.assertEqual(len(warnings), 1)
        start, end, _ = warnings[0]
        self.assertAlmostEqual(start, 40.0, delta=0.011)
        self.assertGreaterEqual(
            end - start,
            1.0,
            "a point event must still yield a visible window, not a zero-length cue",
        )

    def test_integer_timing_is_exact(self):
        """Integer inputs must not arrive as 39.999 through float drift."""
        _, text = self.build([{"start": 60, "end": 64, "label": "gore"}])
        warnings = warning_cues(extract_cues(text))
        self.assertTrue(warnings)
        for start, end, _ in warnings:
            for value in (start, end):
                self.assertEqual(
                    value,
                    round(value),
                    "integer inputs must emit whole-second timings, got {!r}".format(
                        value
                    ),
                )

    def test_window_is_clamped_at_zero(self):
        """A lead that runs past the start of the file must not go negative."""
        _, text = self.build([{"start": 5, "end": 8, "label": "gore"}])
        warnings = warning_cues(extract_cues(text))
        self.assertEqual(len(warnings), 1)
        self.assertGreaterEqual(warnings[0][0], 0.0)
        self.assertAlmostEqual(warnings[0][1], 8.0, delta=0.011)

    def test_hms_time_format_is_accepted(self):
        _, text = self.build([{"start": "00:01:00.000", "end": "00:01:04.000",
                               "label": "gore"}])
        self.assert_window(text, 40.0, 64.0)

    def test_hms_and_numeric_agree(self):
        _, numeric = self.build([{"start": 60, "end": 64, "label": "gore"}],
                                output="numeric.srt")
        _, hms = self.build(
            [{"start": "00:01:00.000", "end": "00:01:04.000", "label": "gore"}],
            output="hms.srt",
        )
        self.assertEqual(
            warning_cues(extract_cues(numeric)),
            warning_cues(extract_cues(hms)),
            "the two accepted time formats must produce identical windows",
        )

    def test_hms_milliseconds_are_honoured(self):
        _, text = self.build(
            [{"start": "00:01:00.500", "end": "00:01:04.000", "label": "gore"}]
        )
        self.assert_window(text, 40.5, 64.0)


class TestCategoryFilter(CliTestCase):
    """--category selects; an unrecognised one is an error, not a silent drop."""

    EVENTS = [
        {"start": 60, "end": 64, "label": "gore"},
        {"start": 150, "end": 154, "label": "needles"},
    ]

    def test_without_category_all_events_are_used(self):
        _, text = self.build(self.EVENTS)
        self.assertEqual(len(warning_cues(extract_cues(text))), 2)

    def test_single_category_selects_one_event(self):
        _, text = self.build(self.EVENTS, extra=("--category", "gore"))
        warnings = warning_cues(extract_cues(text))
        self.assertEqual(len(warnings), 1)
        self.assertAlmostEqual(warnings[0][0], 40.0, delta=0.011)

    def test_category_flag_repeats(self):
        _, text = self.build(
            self.EVENTS, extra=("--category", "gore", "--category", "needles")
        )
        self.assertEqual(len(warning_cues(extract_cues(text))), 2)

    def test_unknown_category_exits_non_zero(self):
        """The silent-filter failure mode: 'no triggers' must never mean
        'your spelling did not match'."""
        events_path = self.write_events(self.EVENTS)
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
            "--category", "gorre",
        )
        self.assert_failed(result, out_path)

    def test_unknown_category_is_named_in_the_error(self):
        events_path = self.write_events(self.EVENTS)
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", self.workdir / "warned.srt",
            "--category", "gorre",
        )
        self.assertIn("gorre", (result.stderr + result.stdout))

    def test_one_bad_category_among_good_ones_still_fails(self):
        events_path = self.write_events(self.EVENTS)
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
            "--category", "gore",
            "--category", "nope",
        )
        self.assert_failed(result, out_path)


class TestOutputFormats(CliTestCase):
    """.ass is the reliable path; .srt is the compatible one."""

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    def test_ass_output_is_written_as_ass(self):
        _, text = self.build(self.EVENT, output="warned.ass")
        self.assertIn("[Script Info]", text)
        self.assertIn("[Events]", text)
        self.assertIn("Dialogue:", text)

    def test_ass_warning_is_top_aligned(self):
        """Top placement is the whole reason ASS is preferred."""
        _, text = self.build(self.EVENT, output="warned.ass")
        warning_lines = [
            line
            for line in text.splitlines()
            if line.startswith("Dialogue:") and WARNING_TEXT in line
        ]
        self.assertTrue(warning_lines, "no warning Dialogue line in the ASS output")
        for line in warning_lines:
            has_inline_tag = r"\an8" in line
            # Otherwise the Dialogue's Style must itself declare alignment 8.
            # In a V4+ Style line, Alignment is the 19th field, so it follows
            # the style name plus 17 further fields.
            style = line[len("Dialogue:"):].split(",", 9)[3].strip()
            style_is_top = bool(
                re.search(
                    r"^Style:\s*" + re.escape(style) + r"\s*,(?:[^,]*,){17}\s*8\s*,",
                    text,
                    re.MULTILINE,
                )
            )
            self.assertTrue(
                has_inline_tag or style_is_top,
                "warning must be top-aligned, via {\\an8} or an alignment-8 style",
            )

    def test_ass_dialogue_is_not_top_aligned(self):
        """Dialogue stays at the bottom, or the merge buys nothing."""
        _, text = self.build(self.EVENT, output="warned.ass")
        dialogue_lines = [
            line
            for line in text.splitlines()
            if line.startswith("Dialogue:") and WARNING_TEXT not in line
        ]
        self.assertEqual(
            len(dialogue_lines), 4, "expected all four dialogue lines in the ASS"
        )
        for line in dialogue_lines:
            self.assertNotIn(r"\an8", line)

    def test_ass_warning_and_dialogue_use_different_styles(self):
        """Top placement must come from an independent style, not a shared one."""
        _, text = self.build(self.EVENT, output="warned.ass")
        styles = {}
        for line in text.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            fields = line[len("Dialogue:"):].split(",", 9)
            styles.setdefault(WARNING_TEXT in fields[9], set()).add(fields[3].strip())
        self.assertIn(True, styles, "no warning Dialogue line found")
        self.assertIn(False, styles, "no dialogue Dialogue line found")
        self.assertFalse(
            styles[True] & styles[False],
            "warning and dialogue must not share a style: {} vs {}".format(
                styles[True], styles[False]
            ),
        )

    def test_srt_output_is_written_as_srt(self):
        _, text = self.build(self.EVENT, output="warned.srt")
        self.assertNotIn("[Script Info]", text)
        self.assertRegex(text, r"\d{2}:\d{2}:\d{2},\d{3}\s*-->")

    def test_srt_warning_carries_the_positioning_tag(self):
        _, text = self.build(self.EVENT, output="warned.srt")
        warnings = warning_cues(extract_cues(text))
        self.assertTrue(warnings)
        for cue in warnings:
            self.assertIn(r"{\an8}", cue[2])

    def test_srt_cues_are_numbered_from_one_and_contiguous(self):
        _, text = self.build(self.EVENT, output="warned.srt")
        indices = [
            int(line.strip())
            for line in text.splitlines()
            if line.strip().isdigit() and "-->" not in line
        ]
        self.assertEqual(
            len(indices), 5, "expected four dialogue cues plus one warning"
        )
        self.assertEqual(indices, list(range(1, len(indices) + 1)))

    def test_cues_are_in_ascending_time_order(self):
        _, text = self.build(self.EVENT, output="warned.srt")
        starts = [cue[0] for cue in extract_cues(text)]
        self.assertEqual(len(starts), 5)
        self.assertEqual(starts, sorted(starts))

    def test_warning_precedes_overlapping_dialogue_in_file_order(self):
        """Players that ignore positioning fall back to file order, so the
        warning must be the first of any two cues that start together.

        The fixture is built so a coincident pair definitely exists: a 20s lead
        on an event at 30s opens the window at exactly 10s, where the first
        dialogue cue also starts. The test asserts the pair was found, so it
        cannot pass by finding nothing to check.
        """
        _, text = self.build(
            [{"start": 30, "end": 34, "label": "gore"}], output="warned.srt"
        )
        cues = extract_cues(text)
        coincident = 0
        for index, cue in enumerate(cues[:-1]):
            following = cues[index + 1]
            if abs(cue[0] - following[0]) < 0.001:
                coincident += 1
                self.assertIn(
                    WARNING_TEXT,
                    cue[2],
                    "when two cues start together the warning must come first",
                )
        self.assertEqual(
            coincident, 1, "fixture should produce exactly one coincident pair"
        )


class TestSafety(CliTestCase):
    """Failures must be loud, and nothing may be destroyed."""

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    def test_existing_output_is_never_overwritten(self):
        out_path = self.write("warned.srt", "PRECIOUS HAND-MADE SUBTITLES")
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assertNotEqual(result.returncode, 0, "clobbering must be refused")
        self.assertEqual(
            out_path.read_text(encoding="utf-8"),
            "PRECIOUS HAND-MADE SUBTITLES",
            "the existing file must be left byte-identical",
        )

    def test_refusing_to_overwrite_the_input_subtitles(self):
        events_path = self.write_events(self.EVENT)
        before = self.subtitles.read_text(encoding="utf-8")
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", self.subtitles,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.subtitles.read_text(encoding="utf-8"), before)

    def test_empty_event_list_exits_non_zero(self):
        events_path = self.write_events([])
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_malformed_events_json_exits_non_zero(self):
        events_path = self.write("events.json", "{ this is not json")
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_events_json_that_is_not_an_array_exits_non_zero(self):
        events_path = self.write("events.json", json.dumps({"start": 60,
                                                            "label": "gore"}))
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_event_missing_start_exits_non_zero(self):
        events_path = self.write_events([{"end": 64, "label": "gore"}])
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_event_missing_label_exits_non_zero(self):
        events_path = self.write_events([{"start": 60, "end": 64}])
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_unparseable_time_exits_non_zero(self):
        events_path = self.write_events(
            [{"start": "half past nine", "label": "gore"}]
        )
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_end_before_start_exits_non_zero(self):
        """Community timecodes contain typos; a reversed window must not
        silently shrink to a one-second banner."""
        events_path = self.write_events(
            [{"start": 64, "end": 60, "label": "gore"}]
        )
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_missing_events_file_exits_non_zero(self):
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", self.workdir / "nope.json",
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_missing_subtitles_file_exits_non_zero(self):
        events_path = self.write_events(self.EVENT)
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.workdir / "nope.srt",
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_malformed_subtitles_exit_non_zero(self):
        bad = self.write("bad.srt", "this file contains no cues at all\n")
        events_path = self.write_events(self.EVENT)
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", bad,
            "--events", events_path,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)

    def test_errors_are_reported_on_stderr(self):
        events_path = self.write("events.json", "{ not json")
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", self.workdir / "warned.srt",
        )
        self.assertTrue(
            result.stderr.strip(), "a failure must say why, on stderr"
        )


class TestVerify(CliTestCase):
    """--verify is the guard that stops 'done' being claimed on an unopened file."""

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    def test_verify_without_video_exits_non_zero(self):
        events_path = self.write_events(self.EVENT)
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
            "--verify", self.workdir / "preview.png",
        )
        self.assert_failed(result, out_path)

    def test_failed_verify_exits_non_zero(self):
        """A placeholder standing in for a video: the burn cannot succeed, and
        the run must say so rather than reporting success with no frame."""
        fake_video = self.write("not-really-a-video.mkv", "not a video\n")
        events_path = self.write_events(self.EVENT)
        preview = self.workdir / "preview.png"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", self.workdir / "warned.srt",
            "--video", fake_video,
            "--verify", preview,
        )
        self.assertNotEqual(
            result.returncode,
            0,
            "a verify that produces no frame must not exit 0",
        )
        # Without this the test passes on an unhandled ImportError from the
        # lazily imported media module, which is not the failure under test.
        self.assert_no_traceback(result)
        self.assertFalse(
            preview.exists() and preview.stat().st_size == 0,
            "a zero-byte preview must not be left behind as if it were proof",
        )

    def test_missing_video_path_exits_non_zero(self):
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", self.workdir / "warned.srt",
            "--video", self.workdir / "absent.mkv",
            "--verify", self.workdir / "preview.png",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_traceback(result)

    def test_failed_verify_leaves_no_output_file(self):
        """A run that cannot prove itself must publish nothing."""
        fake_video = self.write("not-really-a-video.mkv", "not a video\n")
        events_path = self.write_events(self.EVENT)
        out_path = self.workdir / "warned.ass"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
            "--video", fake_video,
            "--verify", self.workdir / "preview.png",
        )
        self.assert_failed(result, out_path)


class TestCliContract(CliTestCase):
    """The flags themselves, and what the tool must not mention."""

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    def test_help_exits_zero(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)

    def test_help_lists_every_frozen_flag(self):
        result = self.run_cli("--help")
        for flag in (
            "--subtitles",
            "--events",
            "--ddd-item",
            "--ddd-api-key",
            "--output",
            "--category",
            "--lead",
            "--tail",
            "--offset",
            "--video",
            "--verify",
        ):
            self.assertIn(flag, result.stdout, "{} missing from --help".format(flag))

    def test_missing_required_flags_exit_non_zero(self):
        self.assertNotEqual(self.run_cli().returncode, 0)
        self.assertNotEqual(
            self.run_cli("--subtitles", self.subtitles).returncode, 0
        )

    def test_local_event_output_has_no_provider_specific_text(self):
        """A local event file remains provider-neutral even though an optional
        official API source is now available."""
        _, text = self.build(self.EVENT)
        lowered = text.lower()
        for forbidden in ("doesthedogdie", "does the dog die", "dogdie"):
            self.assertNotIn(forbidden, lowered)
        self.assertIsNone(re.search(r"\bddd\b", lowered))

    def test_output_is_utf8_and_reparses(self):
        out_path, text = self.build(self.EVENT)
        out_path.read_text(encoding="utf-8")
        self.assertTrue(extract_cues(text), "output did not parse back into cues")

    def test_run_is_deterministic(self):
        _, first = self.build(self.EVENT, output="one.srt")
        _, second = self.build(self.EVENT, output="two.srt")
        self.assertEqual(first, second, "identical inputs must give identical output")


class TestAgenticContract(CliTestCase):
    """Agent-facing plans, errors and provenance are machine-readable."""

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    def test_json_dry_run_returns_one_plan_and_writes_nothing(self):
        events = self.write_events(self.EVENT)
        result = self.run_cli(
            "--json", "--dry-run", "--subtitles", self.subtitles,
            "--events", events,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("dry-run", payload["mode"])
        self.assertEqual("local-events", payload["source"]["kind"])
        self.assertEqual(1, payload["selectedEvents"])
        self.assertEqual(1, payload["warningWindows"])
        self.assertIsNone(payload["output"])
        self.assertEqual([], list(self.workdir.glob("*.ass")))

    def test_json_error_is_structured_and_does_not_write_output(self):
        output = self.workdir / "warned.ass"
        result = self.run_cli("--json", "--subtitles", self.subtitles, "--output", output)
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("invalid-input", payload["error"]["code"])
        self.assertIn("supply --events", payload["error"]["message"])
        self.assertFalse(output.exists())

    def test_provenance_sidecar_is_published_with_output(self):
        events = self.write_events(self.EVENT)
        output = self.workdir / "warned.ass"
        provenance = self.workdir / "warned.provenance.json"
        result = self.run_cli(
            "--subtitles", self.subtitles, "--events", events,
            "--output", output, "--provenance", provenance,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        manifest = json.loads(provenance.read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["schemaVersion"])
        self.assertEqual("local-events", manifest["source"]["kind"])
        self.assertEqual(1, manifest["categories"]["selectedEvents"])
        self.assertEqual(1, manifest["result"]["warningWindows"])
        self.assertNotIn("api", provenance.read_text(encoding="utf-8").lower())

    def test_dry_run_refuses_preview_or_provenance_writes(self):
        events = self.write_events(self.EVENT)
        result = self.run_cli(
            "--dry-run", "--subtitles", self.subtitles, "--events", events,
            "--verify", self.workdir / "preview.png",
        )
        self.assert_failed(result)
        self.assertIn("--dry-run cannot render --verify", result.stderr)


class TestAgenticHardening(CliTestCase):
    """A caller that cannot read a screen must not be guessed at or leaked from.

    Each test here pins one way the machine-readable surface used to be worse
    than the human one: a mistyped flag silently redirected, a secret echoed
    back in an error, a plan that approved a write the write would refuse.
    """

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]
    # Shaped like a key so a leak is unmistakable, and belonging to nobody.
    SECRET = "ddd-key-0000-not-a-real-credential"
    # Every flag taking no value, and so every flag argparse would answer with
    # "ignored explicit argument '<value>'" if it were given one.
    VALUELESS_FLAGS = ("--json", "--dry-run", "--list-streams", "--help", "--version")

    def generation_argv(self, events, output):
        return ["--subtitles", self.subtitles, "--events", events,
                "--output", output]

    def test_flag_abbreviations_are_rejected(self):
        """argparse accepts any unambiguous prefix by default, so `--outp` set
        --output. Ambiguity is one added flag away, and it would silently
        redirect a command rather than fail it."""
        events = self.write_events(self.EVENT)
        for full, abbreviated in (
            ("--subtitles", "--subtitle"),
            ("--events", "--event"),
            ("--output", "--outp"),
        ):
            with self.subTest(flag=abbreviated):
                out_path = self.workdir / "abbreviated.srt"
                argv = self.generation_argv(events, out_path)
                argv[argv.index(full)] = abbreviated
                self.assert_failed(self.run_cli(*argv), out_path)

    def test_an_unknown_flag_is_named_but_its_value_is_not(self):
        """A mistyped --ddd-api-key must not put the key into the error text.

        Under --json that text is a field an agent may log or echo back, and
        argparse's own message joins the leftover argv verbatim.
        """
        result = self.run_cli("--json", "--ddd-api-ke=" + self.SECRET)
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("--ddd-api-ke", payload["error"]["message"])
        self.assertNotIn(self.SECRET, result.stdout)

    def test_a_separated_unknown_value_is_redacted_too(self):
        """`--flag value` leaves two leftovers; the second one is the secret."""
        result = self.run_cli(
            "--ddd-api-keyy", self.SECRET, "--subtitles", self.subtitles
        )
        self.assert_failed(result)
        self.assertIn("--ddd-api-keyy", result.stderr)
        self.assertNotIn(self.SECRET, result.stdout + result.stderr)

    def test_a_value_attached_to_a_valueless_flag_is_not_echoed(self):
        """`--dry-run=KEY` must name the flag and drop the value.

        A caller assembling a command by hand puts the key on the wrong flag
        sooner or later, and argparse answers that with `ignored explicit
        argument` followed by whatever was attached. Both output modes are
        checked: the JSON one is the text an agent stores and quotes back, and
        the prose one is what lands in a shell history or a CI log.
        """
        for flag in self.VALUELESS_FLAGS:
            for json_mode in (False, True):
                with self.subTest(flag=flag, json=json_mode):
                    argv = ["--json"] if json_mode else []
                    argv.append(flag + "=" + self.SECRET)
                    result = self.run_cli(*argv)
                    self.assert_failed(result)
                    self.assertNotIn(
                        self.SECRET,
                        result.stdout + result.stderr,
                        "the attached value came back in the error",
                    )
                    # `--json=KEY` is itself a request for JSON, so it lands
                    # on stdout however the loop reached it.
                    if json_mode or flag == "--json":
                        self.assertEqual("", result.stderr)
                        payload = json.loads(result.stdout)
                        self.assertFalse(payload["ok"])
                        self.assertIn(flag, payload["error"]["message"])
                    else:
                        self.assertIn(flag, result.stderr)

    def test_an_attached_value_still_works_where_the_flag_takes_one(self):
        """The valueless-flag check must not swallow `--lead=5` or
        `--category=gore`, which are ordinary and correct spellings."""
        events = self.write_events(self.EVENT)
        result = self.run_cli(
            "--json", "--dry-run", "--lead=5", "--category=gore",
            *self.generation_argv(events, self.workdir / "planned.srt")
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(1, payload["selectedEvents"])

    def test_json_equals_spelling_still_answers_in_json(self):
        """`--json=true` is an argument error, but the format choice has to be
        made before argparse says so, or the answer lands on the wrong stream."""
        result = self.run_cli("--json=true", "--subtitles", self.subtitles)
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("invalid-input", payload["error"]["code"])

    def test_help_and_version_are_the_documented_json_exception(self):
        """`--json` wraps every run except these two, which print their own
        text and exit 0 whatever else is on the command line.

        README.md and SKILL.md tell agents to call `--help` on its own because
        of it, so the instruction rests on this behaviour staying put.
        """
        for flag in ("--help", "--version"):
            with self.subTest(flag=flag):
                result = self.run_cli("--json", flag)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("", result.stderr)
                self.assertTrue(result.stdout.strip(), "nothing was printed")
                self.assertNotIn(
                    '"ok"', result.stdout, "this is prose, not a result object"
                )
        self.assertIn("usage:", self.run_cli("--json", "--help").stdout)

    def test_json_run_reports_progress_once(self):
        """Messages belong inside the object, not on stderr as well."""
        events = self.write_events(self.EVENT)
        output = self.workdir / "warned.srt"
        result = self.run_cli(
            "--json", *self.generation_argv(events, output)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "", result.stderr,
            "progress was repeated outside the JSON object",
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["messages"], "the run reported nothing at all")

    def test_json_result_names_every_file_it_wrote(self):
        events = self.write_events(self.EVENT)
        output = self.workdir / "warned.srt"
        provenance = self.workdir / "warned.provenance.json"
        result = self.run_cli(
            "--json", "--provenance", provenance,
            *self.generation_argv(events, output)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(str(output), payload["output"])
        self.assertEqual(str(provenance), payload["provenance"])
        self.assertIsNone(payload["preview"], "no preview was requested")
        self.assertEqual([str(output), str(provenance)], payload["filesWritten"])
        for named in payload["filesWritten"]:
            self.assertTrue(
                Path(named).is_file(), "{} was named but not written".format(named)
            )

    def test_a_dry_run_names_no_written_files(self):
        events = self.write_events(self.EVENT)
        planned = self.workdir / "planned.srt"
        result = self.run_cli(
            "--json", "--dry-run", *self.generation_argv(events, planned)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("dry-run", payload["mode"])
        self.assertEqual(str(planned), payload["output"])
        self.assertEqual([], payload["filesWritten"])
        self.assertFalse(planned.exists())

    def test_dry_run_refuses_an_output_path_that_already_exists(self):
        """The command whose whole job is "would this work?" must not answer
        yes to a path the write would refuse a second later."""
        events = self.write_events(self.EVENT)
        existing = self.write("warned.srt", "PRECIOUS")
        result = self.run_cli(
            "--dry-run", *self.generation_argv(events, existing)
        )
        self.assert_failed(result)
        self.assertIn("already exists", result.stderr)
        self.assertEqual("PRECIOUS", existing.read_text(encoding="utf-8"))

    def test_dry_run_refuses_an_output_in_a_missing_directory(self):
        events = self.write_events(self.EVENT)
        result = self.run_cli(
            "--dry-run",
            *self.generation_argv(events, self.workdir / "absent" / "warned.srt")
        )
        self.assert_failed(result)
        self.assertIn("does not exist", result.stderr)

    def test_search_rejects_a_subtitle_language(self):
        """A search reads no subtitle stream, so --language means the caller
        expected a generation run and would get a title list instead."""
        result = self.run_cli(
            "--json", "--ddd-search", "The Thing", "--language", "fre"
        )
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("--language", payload["error"]["message"])

    def test_a_stray_api_key_names_both_requests_that_use_it(self):
        """`--ddd-api-key` serves `--ddd-item` and `--ddd-search` alike, so a
        diagnostic naming only one sends a searching caller to the wrong flag."""
        events = self.write_events(self.EVENT)
        result = self.run_cli(
            "--json", "--ddd-api-key", self.SECRET,
            *self.generation_argv(events, self.workdir / "warned.srt")
        )
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        message = payload["error"]["message"]
        self.assertIn("--ddd-item", message)
        self.assertIn("--ddd-search", message)
        self.assertNotIn(
            self.SECRET,
            result.stdout + result.stderr,
            "a correctly placed key must not be echoed either",
        )


class TestUnicodeAndEncoding(CliTestCase):
    """Real subtitle files are not ASCII and not always plain LF."""

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    def test_non_ascii_dialogue_survives(self):
        source = DIALOGUE_SRT.replace(
            "It stopped raining an hour ago.", "Il a cessé de pleuvoir. Ça va?"
        )
        subtitles = self.write("accents.srt", source)
        _, text = self.build(self.EVENT, subtitles=subtitles)
        self.assertIn("Il a cessé de pleuvoir. Ça va?", text)

    def test_bom_prefixed_input_is_accepted(self):
        path = self.workdir / "bom.srt"
        path.write_bytes(b"\xef\xbb\xbf" + DIALOGUE_SRT.encode("utf-8"))
        out_path, text = self.build(self.EVENT, subtitles=path)
        self.assertIn("The kettle is boiling.", text)
        self.assertEqual(
            len(dialogue_cues(extract_cues(text))),
            4,
            "a BOM must not swallow or corrupt the first cue",
        )
        self.assertFalse(
            out_path.read_bytes().startswith(b"\xef\xbb\xbf"),
            "the BOM must not be carried into the output",
        )

    def test_crlf_input_is_accepted(self):
        path = self.workdir / "crlf.srt"
        path.write_bytes(DIALOGUE_SRT.replace("\n", "\r\n").encode("utf-8"))
        _, text = self.build(self.EVENT, subtitles=path)
        self.assertIn("Someone should answer the door.", text)

    def test_multi_line_dialogue_cue_is_kept_intact(self):
        source = DIALOGUE_SRT.replace(
            "The kettle is boiling.", "The kettle is boiling.\nShall I get it?"
        )
        subtitles = self.write("multiline.srt", source)
        _, text = self.build(self.EVENT, subtitles=subtitles)
        self.assertIn("The kettle is boiling.", text)
        self.assertIn("Shall I get it?", text)


class TestOverlappingEvents(CliTestCase):
    """Adjacent events must not stack duplicate banners."""

    def test_overlapping_windows_merge_into_one_banner(self):
        _, text = self.build(
            [
                {"start": 60, "end": 64, "label": "gore"},
                {"start": 70, "end": 74, "label": "gore"},
            ]
        )
        warnings = warning_cues(extract_cues(text))
        self.assertEqual(
            len(warnings),
            1,
            "windows 20s apart with a 20s lead overlap and must union, "
            "not stack two identical banners",
        )
        self.assertAlmostEqual(warnings[0][0], 40.0, delta=0.011)
        self.assertAlmostEqual(warnings[0][1], 74.0, delta=0.011)

    def test_distant_events_stay_separate(self):
        _, text = self.build(
            [
                {"start": 60, "end": 64, "label": "gore"},
                {"start": 300, "end": 304, "label": "gore"},
            ]
        )
        self.assertEqual(len(warning_cues(extract_cues(text))), 2)

    def test_no_two_warning_cues_overlap_in_the_output(self):
        _, text = self.build(
            [
                {"start": 60, "end": 64, "label": "gore"},
                {"start": 65, "end": 69, "label": "needles"},
                {"start": 200, "end": 204, "label": "gore"},
            ]
        )
        warnings = sorted(warning_cues(extract_cues(text)))
        # [40,64] and [45,69] overlap and must union; [180,204] stands alone.
        self.assertEqual(len(warnings), 2, "expected exactly two merged windows")
        for first, second in zip(warnings, warnings[1:]):
            self.assertLessEqual(
                first[1], second[0] + 0.001, "warning banners must never overlap"
            )

    def test_events_out_of_order_are_handled(self):
        _, text = self.build(
            [
                {"start": 300, "end": 304, "label": "gore"},
                {"start": 60, "end": 64, "label": "gore"},
            ]
        )
        warnings = sorted(warning_cues(extract_cues(text)))
        self.assertEqual(len(warnings), 2)
        self.assertAlmostEqual(warnings[0][0], 40.0, delta=0.011)


if __name__ == "__main__":
    unittest.main()


class TestStrictValidation(CliTestCase):
    """Hostile event files. Every one must be diagnosed, never crash or pass.

    JSON's number grammar is wider than the contract's: `json.loads` accepts
    the bare tokens NaN, Infinity and -Infinity by default, and 1e999 overflows
    to inf silently. Python widens it again, because bool is a subclass of int,
    so a naive `isinstance(value, (int, float))` accepts `true` as the number 1.
    """

    def test_nan_start_is_rejected(self):
        self.bad_events('[{"start": NaN, "label": "gore"}]')

    def test_infinity_start_is_rejected(self):
        self.bad_events('[{"start": Infinity, "label": "gore"}]')

    def test_negative_infinity_start_is_rejected(self):
        self.bad_events('[{"start": -Infinity, "label": "gore"}]')

    def test_overflowing_literal_is_rejected(self):
        """1e999 does not raise; it becomes inf."""
        self.bad_events('[{"start": 1e999, "label": "gore"}]')

    def test_nan_end_is_rejected(self):
        self.bad_events('[{"start": 60, "end": NaN, "label": "gore"}]')

    def test_boolean_start_is_rejected(self):
        """`true` must not be accepted as the number 1."""
        self.bad_events('[{"start": true, "label": "gore"}]')

    def test_boolean_end_is_rejected(self):
        self.bad_events('[{"start": 60, "end": false, "label": "gore"}]')

    def test_boolean_label_is_rejected(self):
        self.bad_events('[{"start": 60, "label": true}]')

    def test_numeric_label_is_rejected(self):
        self.bad_events('[{"start": 60, "label": 7}]')

    def test_empty_label_is_rejected(self):
        self.bad_events('[{"start": 60, "label": ""}]')

    def test_whitespace_only_label_is_rejected(self):
        self.bad_events('[{"start": 60, "label": "   "}]')

    def test_negative_start_is_rejected(self):
        self.bad_events('[{"start": -5, "label": "gore"}]')

    def test_non_text_severity_is_rejected(self):
        """The contract calls severity optional text metadata."""
        self.bad_events('[{"start": 60, "label": "gore", "severity": 5}]')

    def test_array_element_is_rejected(self):
        self.bad_events('[["start", 60]]')

    def test_string_element_is_rejected(self):
        self.bad_events('["gore at sixty seconds"]')

    def test_null_element_is_rejected(self):
        self.bad_events("[null]")

    def test_bare_numeric_string_time_is_rejected(self):
        """"60" is not one of the two documented time forms."""
        self.bad_events('[{"start": "60", "label": "gore"}]')

    def test_malformed_timecode_is_rejected(self):
        self.bad_events('[{"start": "00:99:99.000", "label": "gore"}]')

    def test_invalid_record_is_rejected_even_when_not_selected(self):
        """The headline strictness rule: validation precedes selection.

        Otherwise a typo in an unselected record is invisible today and becomes
        a missing warning the day that category is selected.
        """
        self.bad_events(
            '[{"start": 60, "end": 64, "label": "gore"},'
            ' {"start": "half nine", "label": "needles"}]',
            "--category", "gore",
        )

    def test_invalid_unselected_record_of_every_kind(self):
        for raw in (
            '[{"start": 60, "label": "gore"}, {"start": NaN, "label": "other"}]',
            '[{"start": 60, "label": "gore"}, {"start": true, "label": "other"}]',
            '[{"start": 60, "label": "gore"}, {"start": 10, "end": 5, "label": "x"}]',
            '[{"start": 60, "label": "gore"}, {"label": "other"}]',
        ):
            with self.subTest(raw=raw):
                events_path = self.write("events.json", raw)
                out_path = self.workdir / "warned.srt"
                result = self.run_cli(
                    "--subtitles", self.subtitles,
                    "--events", events_path,
                    "--output", out_path,
                    "--category", "gore",
                )
                self.assert_failed(result, out_path)

    def test_unusable_zero_length_window_is_rejected(self):
        """A point event with no lead has nothing to show."""
        events_path = self.write_events([{"start": 0, "label": "gore"}])
        result = self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path, "--lead", "0"
        )
        self.assertTrue(result.stderr.strip())

    def test_duplicate_events_produce_one_window(self):
        _, text = self.build(
            [
                {"start": 60, "end": 64, "label": "gore"},
                {"start": 60, "end": 64, "label": "gore"},
            ]
        )
        self.assertEqual(len(warning_cues(extract_cues(text))), 1)

    def test_events_file_that_is_empty_text_is_rejected(self):
        self.bad_events("")

    def test_events_file_of_only_whitespace_is_rejected(self):
        self.bad_events("   \n\n  ")

    def test_events_directory_instead_of_file_is_rejected(self):
        directory = self.workdir / "events_dir"
        directory.mkdir()
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", directory,
            "--output", out_path,
        )
        self.assert_failed(result, out_path)


class TestArgumentValidation(CliTestCase):
    """The numeric flags and the category matching rule."""

    EVENTS = [
        {"start": 60, "end": 64, "label": "gore"},
        {"start": 150, "end": 154, "label": "loud noises"},
    ]

    def test_negative_lead_is_rejected(self):
        events_path = self.write_events(self.EVENTS)
        self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path, "--lead", "-1"
        )

    def test_negative_tail_is_rejected(self):
        events_path = self.write_events(self.EVENTS)
        self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path, "--tail", "-1"
        )

    def test_non_numeric_lead_is_rejected(self):
        events_path = self.write_events(self.EVENTS)
        self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path, "--lead", "soon"
        )

    def test_non_numeric_offset_is_rejected(self):
        events_path = self.write_events(self.EVENTS)
        self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path, "--offset", "nan"
        )

    def test_unsupported_output_extension_is_rejected(self):
        events_path = self.write_events(self.EVENTS)
        self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path,
            output="warned.txt",
        )

    def test_category_match_is_case_insensitive(self):
        _, text = self.build(self.EVENTS, extra=("--category", "GoRe"))
        self.assertEqual(len(warning_cues(extract_cues(text))), 1)

    def test_category_match_is_exact_not_substring(self):
        """The predecessor matched substrings, so 'shot' caught 'photoshoot'.

        A substring match that silently selects the wrong events is the same
        class of failure as one that silently selects none.
        """
        events_path = self.write_events(self.EVENTS)
        self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path,
            "--category", "gor",
        )

    def test_category_with_a_space_is_selectable(self):
        _, text = self.build(self.EVENTS, extra=("--category", "loud noises"))
        warnings = warning_cues(extract_cues(text))
        self.assertEqual(len(warnings), 1)
        self.assertAlmostEqual(warnings[0][0], 130.0, delta=0.011)

    def test_surrounding_whitespace_in_a_category_still_matches(self):
        _, text = self.build(self.EVENTS, extra=("--category", " gore "))
        self.assertEqual(len(warning_cues(extract_cues(text))), 1)


class TestInjection(CliTestCase):
    """Subtitle text is untrusted input; it must not become markup."""

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    def dialogue_with(self, payload):
        source = DIALOGUE_SRT.replace("The kettle is boiling.", payload)
        return self.write("injected.srt", source)

    def test_ass_override_block_in_dialogue_is_escaped(self):
        """A dialogue line containing {\\an8} must not reposition itself."""
        subtitles = self.dialogue_with(r"{\an8}I am at the top now.")
        _, text = self.build(self.EVENT, output="warned.ass", subtitles=subtitles)
        for line in text.splitlines():
            if line.startswith("Dialogue:") and WARNING_TEXT not in line:
                self.assertNotIn(
                    r"{\an8}", line, "dialogue braces must be escaped in ASS"
                )
        self.assertIn("I am at the top now.", text)

    def test_ass_positioning_override_in_dialogue_is_escaped(self):
        subtitles = self.dialogue_with(r"{\pos(0,0)}Somewhere else entirely.")
        _, text = self.build(self.EVENT, output="warned.ass", subtitles=subtitles)
        self.assertNotIn(r"{\pos(0,0)}", text)
        self.assertIn("Somewhere else entirely.", text)

    def test_ass_dialogue_cannot_forge_a_new_event_line(self):
        """Commas and a Dialogue: prefix in text must stay inside the text field."""
        subtitles = self.dialogue_with(
            "Dialogue: 0,0:00:00.00,9:59:59.99,Warning,,0,0,0,,forged"
        )
        _, text = self.build(self.EVENT, output="warned.ass", subtitles=subtitles)
        forged = [
            cue
            for cue in extract_cues(text)
            if cue[1] > 9 * 3600
        ]
        self.assertFalse(forged, "injected text produced a real event line")

    def test_srt_dialogue_is_passed_through_verbatim(self):
        """SRT has no escaping mechanism, so preservation is the contract."""
        payload = r"{\an8}Braces and a backslash \ stay put."
        subtitles = self.dialogue_with(payload)
        _, text = self.build(self.EVENT, output="warned.srt", subtitles=subtitles)
        self.assertIn(payload, text)

    def test_dialogue_saying_trigger_incoming_is_not_treated_as_a_warning(self):
        """The tool must not confuse its own banner text with dialogue."""
        subtitles = self.dialogue_with('She said "TRIGGER INCOMING" as a joke.')
        _, text = self.build(self.EVENT, output="warned.srt", subtitles=subtitles)
        self.assertIn('She said "TRIGGER INCOMING" as a joke.', text)
        self.assertEqual(
            len(extract_cues(text)), 5, "the joke line must remain one cue"
        )

    def test_shell_metacharacters_in_the_output_name_are_literal(self):
        """Proves no shell is interposed: the name is a filename, not a command."""
        name = "warn ed;$(echo pwned)`x'.srt"
        out_path, text = self.build(self.EVENT, output=name)
        self.assertTrue(out_path.is_file())
        self.assertIn(WARNING_TEXT, text)
        self.assertFalse(
            (self.workdir / "pwned").exists(), "a subshell ran on the output name"
        )

    def test_shell_metacharacters_in_the_input_name_are_literal(self):
        subtitles = self.workdir / "dia logue;$(echo pwned).srt"
        subtitles.write_text(DIALOGUE_SRT, encoding="utf-8")
        _, text = self.build(self.EVENT, subtitles=subtitles)
        self.assertIn("The kettle is boiling.", text)
        self.assertFalse((self.workdir / "pwned").exists())

    def test_category_with_metacharacters_is_rejected_not_executed(self):
        events_path = self.write_events(self.EVENT)
        result = self.run_expecting_failure(
            "--subtitles", self.subtitles, "--events", events_path,
            "--category", "$(echo pwned)",
        )
        self.assertFalse((self.workdir / "pwned").exists())
        self.assertTrue(result.stderr.strip())


class TestFilesystemSafety(CliTestCase):
    """Nothing outside the named output path may ever be written."""

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    def supports_symlinks(self):
        probe = self.workdir / "_symlink_probe"
        try:
            os.symlink(str(self.workdir / "_absent"), str(probe))
        except (OSError, NotImplementedError, AttributeError):
            return False
        probe.unlink()
        return True

    def test_symlink_at_the_output_path_is_not_followed(self):
        if not self.supports_symlinks():
            self.skipTest("this platform or account cannot create symlinks")
        target = self.write("precious.srt", "PRECIOUS")
        link = self.workdir / "warned.srt"
        os.symlink(str(target), str(link))
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", link,
        )
        self.assertNotEqual(result.returncode, 0, "writing through a symlink")
        self.assertEqual(target.read_text(encoding="utf-8"), "PRECIOUS")

    def test_dangling_symlink_at_the_output_path_is_refused(self):
        """Path.exists() is False for a dangling link, so a naive existence
        check writes straight through it to a path the user never named."""
        if not self.supports_symlinks():
            self.skipTest("this platform or account cannot create symlinks")
        hidden = self.workdir / "elsewhere.srt"
        link = self.workdir / "warned.srt"
        os.symlink(str(hidden), str(link))
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", link,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_traceback(result)
        self.assertIn(
            "symbolic link",
            result.stderr,
            "the refusal must name the link, not blame a phantom racing writer",
        )
        self.assertFalse(
            hidden.exists(), "wrote through a dangling symlink to an unnamed path"
        )

    def test_dry_run_refuses_a_dangling_symlink_output(self):
        """The refusal must be a check of our own, not a POSIX side effect.

        ``open(O_CREAT | O_EXCL)`` fails on a symlink whatever it points at, so
        on Linux and macOS the write itself catches this and hides whether the
        tool ever looked. Windows resolves the reparse point and creates the
        target instead, so the same code writes to an unnamed path there. A dry
        run never reaches the write, which makes this one assertion behave the
        same on every platform.
        """
        if not self.supports_symlinks():
            self.skipTest("this platform or account cannot create symlinks")
        hidden = self.workdir / "elsewhere.srt"
        link = self.workdir / "warned.srt"
        os.symlink(str(hidden), str(link))
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--dry-run",
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", link,
        )
        self.assertNotEqual(
            result.returncode,
            0,
            "--dry-run promised a run that would write to an unnamed path",
        )
        self.assert_no_traceback(result)
        self.assertIn("symbolic link", result.stderr)
        self.assertFalse(hidden.exists())

    def test_dangling_symlink_at_a_sidecar_target_is_refused(self):
        """Every published path is checked, not just --output."""
        if not self.supports_symlinks():
            self.skipTest("this platform or account cannot create symlinks")
        hidden = self.workdir / "elsewhere.json"
        link = self.workdir / "run.json"
        os.symlink(str(hidden), str(link))
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", self.workdir / "warned.srt",
            "--provenance", link,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_traceback(result)
        self.assertIn("symbolic link", result.stderr)
        self.assertFalse(hidden.exists())
        self.assertFalse(
            (self.workdir / "warned.srt").exists(),
            "a refused sidecar must leave no half-published run behind",
        )

    def test_output_path_that_is_a_directory_fails_cleanly(self):
        directory = self.workdir / "warned.srt"
        directory.mkdir()
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", directory,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_traceback(result)
        self.assertTrue(directory.is_dir(), "the directory must be untouched")

    def test_output_may_not_be_the_events_file(self):
        events_path = self.write_events(self.EVENT)
        before = events_path.read_text(encoding="utf-8")
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", events_path,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events_path.read_text(encoding="utf-8"), before)

    def test_output_in_a_missing_directory_fails_cleanly(self):
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", self.workdir / "no" / "such" / "dir" / "warned.srt",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_traceback(result)

    def test_concurrent_runs_do_not_interfere(self):
        """Two builds at once must not share a temporary name or clobber."""
        events_path = self.write_events(self.EVENT)
        procs = []
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(IMPORT_ROOT) + (
            os.pathsep + existing if existing else ""
        )
        for index in range(4):
            out_path = self.workdir / "concurrent{}.srt".format(index)
            procs.append(
                (
                    out_path,
                    subprocess.Popen(
                        [
                            sys.executable, "-m", PACKAGE_NAME,
                            "--subtitles", str(self.subtitles),
                            "--events", str(events_path),
                            "--output", str(out_path),
                        ],
                        cwd=str(self.workdir),
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    ),
                )
            )
        results = []
        for out_path, proc in procs:
            _, stderr = proc.communicate(timeout=TIMEOUT_S)
            self.assertEqual(
                proc.returncode, 0, stderr.decode("utf-8", "replace")
            )
            results.append(out_path.read_text(encoding="utf-8"))
        self.assertEqual(len(set(results)), 1, "concurrent runs diverged")

    def test_no_stray_files_are_left_beside_the_output(self):
        """Temporary working files must not survive a successful run."""
        events_path = self.write_events(self.EVENT)
        before = {path.name for path in self.workdir.iterdir()}
        out_path = self.workdir / "warned.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {path.name for path in self.workdir.iterdir()}
        self.assertEqual(
            after - before,
            {"warned.srt"},
            "the run left files behind besides its output",
        )


class TestAdjudicatedContract(CliTestCase):
    """Three points the operator adjudicated explicitly. Pinned both ways.

    Each rule is tested in both directions on purpose. A rejection test alone
    would also pass against an implementation that rejects everything, which
    would pin the wrong half of the rule.
    """

    EVENTS = [
        {"start": 60, "end": 64, "label": "gore"},
        {"start": 150, "end": 154, "label": "loud noises"},
    ]

    # 1. Categories trim surrounding whitespace, then match exactly and
    #    case-insensitively.

    def test_category_leading_and_trailing_whitespace_is_trimmed(self):
        for requested in (" gore", "gore ", "  gore  ", "\tgore\n"):
            with self.subTest(requested=requested):
                _, text = self.build(
                    self.EVENTS,
                    output="trimmed{}.srt".format(abs(hash(requested))),
                    extra=("--category", requested),
                )
                self.assertEqual(len(warning_cues(extract_cues(text))), 1)

    def test_trimming_does_not_become_substring_matching(self):
        """Trimming the ends must not loosen the match itself."""
        events_path = self.write_events(self.EVENTS)
        for requested in (" gor ", "gore extra", "loud"):
            with self.subTest(requested=requested):
                out_path = self.workdir / "no{}.srt".format(abs(hash(requested)))
                result = self.run_cli(
                    "--subtitles", self.subtitles,
                    "--events", events_path,
                    "--output", out_path,
                    "--category", requested,
                )
                self.assert_failed(result, out_path)

    def test_internal_whitespace_in_a_category_is_significant(self):
        """Only the ends are trimmed; 'loud  noises' is not 'loud noises'."""
        events_path = self.write_events(self.EVENTS)
        out_path = self.workdir / "internal.srt"
        result = self.run_cli(
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", out_path,
            "--category", "loud  noises",
        )
        self.assert_failed(result, out_path)

    # 2. severity is text-only.

    def test_string_severity_is_accepted(self):
        _, text = self.build(
            [{"start": 60, "end": 64, "label": "gore", "severity": "major"}]
        )
        self.assertEqual(len(warning_cues(extract_cues(text))), 1)

    def test_null_severity_is_accepted(self):
        _, text = self.build(
            [{"start": 60, "end": 64, "label": "gore", "severity": None}]
        )
        self.assertEqual(len(warning_cues(extract_cues(text))), 1)

    def test_omitted_severity_is_accepted(self):
        _, text = self.build([{"start": 60, "end": 64, "label": "gore"}])
        self.assertEqual(len(warning_cues(extract_cues(text))), 1)

    def test_non_text_severity_of_every_kind_is_rejected(self):
        for raw in ("5", "5.5", "true", "[]", "{}"):
            with self.subTest(severity=raw):
                self.bad_events(
                    '[{"start": 60, "label": "gore", "severity": %s}]' % raw
                )

    # 3. Times are a JSON number or a documented timecode string. Nothing else.

    def test_numeric_time_strings_are_rejected(self):
        for raw in ('"60"', '"60.5"', '"0"', '"1e3"'):
            with self.subTest(start=raw):
                self.bad_events('[{"start": %s, "label": "gore"}]' % raw)

    def test_comma_millisecond_separator_is_accepted(self):
        """The contract documents both HH:MM:SS.mmm and HH:MM:SS,mmm."""
        _, text = self.build(
            [{"start": "00:01:00,000", "end": "00:01:04,000", "label": "gore"}]
        )
        warnings = warning_cues(extract_cues(text))
        self.assertEqual(len(warnings), 1)
        self.assertAlmostEqual(warnings[0][0], 40.0, delta=0.011)
        self.assertAlmostEqual(warnings[0][1], 64.0, delta=0.011)

    def test_both_millisecond_separators_agree(self):
        _, dot = self.build(
            [{"start": "00:01:00.250", "label": "gore"}], output="dot.srt"
        )
        _, comma = self.build(
            [{"start": "00:01:00,250", "label": "gore"}], output="comma.srt"
        )
        self.assertEqual(warning_cues(extract_cues(dot)),
                         warning_cues(extract_cues(comma)))

    def test_plain_json_numbers_remain_accepted(self):
        for raw in ("60", "60.5", "0"):
            with self.subTest(start=raw):
                out = "num{}.srt".format(raw.replace(".", "_"))
                events_path = self.write(
                    "events{}.json".format(raw.replace(".", "_")),
                    '[{"start": %s, "end": 200, "label": "gore"}]' % raw,
                )
                result = self.run_cli(
                    "--subtitles", self.subtitles,
                    "--events", events_path,
                    "--output", self.workdir / out,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


class TestOpenSubtitlesCommandSurface(CliTestCase):
    """The OpenSubtitles modes, exercised as a subprocess and never on a network.

    Every command below is refused during argument validation, before any
    request could be made. The environment is scrubbed of OpenSubtitles
    credentials in setUp so that a developer who happens to export a real key
    cannot turn this suite into a live API client.
    """

    EVENT = [{"start": 60, "end": 64, "label": "gore"}]

    CREDENTIAL_VARIABLES = (
        "OPENSUBTITLES_API_KEY",
        "OPENSUBTITLES_USERNAME",
        "OPENSUBTITLES_PASSWORD",
        "DDD_API_KEY",
    )

    def setUp(self):
        super().setUp()
        # Not "dialogue.srt": the base fixture already occupies that name with
        # the input track, and an existing target is refused before anything
        # else, which would mask the failure each test is actually about.
        self.download_target = self.workdir / "downloaded.srt"
        removed = {}
        for name in self.CREDENTIAL_VARIABLES:
            if name in os.environ:
                removed[name] = os.environ.pop(name)
        self.addCleanup(os.environ.update, removed)

    def test_the_opensubtitles_flags_are_documented(self):
        result = self.run_cli("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--os-search", "--os-year", "--os-season", "--os-episode",
                     "--os-language", "--os-file", "--os-api-key"):
            self.assertIn(flag, result.stdout, "{} is undocumented".format(flag))

    def test_help_says_the_password_has_no_flag(self):
        """The absence of a flag is a deliberate feature, so it is written down.
        Otherwise the next reader adds one as a convenience."""
        result = self.run_cli("--help")

        self.assertIn("OPENSUBTITLES_API_KEY", result.stdout)
        self.assertIn("environment", result.stdout)

    def test_there_is_no_username_or_password_flag(self):
        for flag in ("--os-username", "--os-password", "--os-user", "--os-pass"):
            with self.subTest(flag=flag):
                result = self.run_cli(
                    "--os-file", "1", "--output", self.download_target,
                    flag, "value",
                )
                self.assert_failed(result)
                self.assertIn("unrecognized arguments", result.stderr)

    def test_search_without_an_api_key_names_the_variable(self):
        result = self.run_cli("--os-search", "The Thing")

        self.assert_failed(result)
        self.assertIn("OPENSUBTITLES_API_KEY", result.stderr)

    def test_download_without_credentials_names_the_missing_variable(self):
        output = self.download_target
        result = self.run_cli(
            "--os-file", "7061050", "--output", output, "--os-api-key", "k",
        )

        self.assert_failed(result, output)
        self.assertIn("OPENSUBTITLES_USERNAME", result.stderr)

    def test_download_refuses_an_output_that_is_not_srt(self):
        output = self.workdir / "downloaded.ass"
        result = self.run_cli("--os-file", "7061050", "--output", output)

        self.assert_failed(result, output)
        self.assertIn(".srt", result.stderr)

    def test_download_without_an_output_is_refused(self):
        result = self.run_cli("--os-file", "7061050")

        self.assert_failed(result)
        self.assertIn("--output", result.stderr)

    def test_search_refuses_generation_flags(self):
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--os-search", "The Thing",
            "--subtitles", self.subtitles,
            "--events", events_path,
            "--output", self.workdir / "warned.srt",
        )

        self.assert_failed(result, self.workdir / "warned.srt")
        self.assertIn("--os-search", result.stderr)
        self.assertIn("--output", result.stderr)

    def test_download_refuses_generation_flags(self):
        events_path = self.write_events(self.EVENT)
        result = self.run_cli(
            "--os-file", "7061050",
            "--output", self.download_target,
            "--events", events_path,
        )

        self.assert_failed(result, self.download_target)
        self.assertIn("--os-file", result.stderr)
        self.assertIn("--events", result.stderr)

    def test_the_two_opensubtitles_modes_cannot_be_combined(self):
        result = self.run_cli(
            "--os-search", "The Thing",
            "--os-file", "7061050",
            "--output", self.download_target,
        )

        self.assert_failed(result, self.download_target)
        self.assertIn("--os-file", result.stderr)

    def test_a_search_filter_without_a_search_is_refused(self):
        events_path = self.write_events(self.EVENT)
        for flag, value in (("--os-year", "1982"), ("--os-season", "2"),
                            ("--os-episode", "7")):
            with self.subTest(flag=flag):
                out = self.workdir / "warned-{}.srt".format(flag.strip("-"))
                result = self.run_cli(
                    "--subtitles", self.subtitles,
                    "--events", events_path,
                    "--output", out,
                    flag, value,
                )
                self.assert_failed(result, out)
                self.assertIn("--os-search", result.stderr)

    def test_a_rejected_command_never_echoes_the_api_key(self):
        """The value is what must not come back; the flag name is what helps."""
        secret = "sk-live-do-not-echo-this"
        result = self.run_cli(
            "--os-search", "The Thing",
            "--os-api-key", secret,
            "--output", self.workdir / "warned.srt",
        )

        self.assert_failed(result)
        self.assertNotIn(secret, result.stderr)
        self.assertNotIn(secret, result.stdout)

    def test_a_misspelled_key_flag_does_not_echo_its_value(self):
        """A typo puts the key where argparse would normally quote it back."""
        secret = "sk-live-do-not-echo-this"
        result = self.run_cli(
            "--os-search", "The Thing", "--os-api-ke={}".format(secret),
        )

        self.assert_failed(result)
        self.assertNotIn(secret, result.stderr)
        self.assertNotIn(secret, result.stdout)
        self.assertIn("--os-api-ke", result.stderr)

    def test_a_credential_failure_under_json_is_structured_and_clean(self):
        secret = "sk-live-do-not-echo-this"
        output = self.download_target
        result = self.run_cli(
            "--os-file", "7061050", "--output", output,
            "--os-api-key", secret, "--json",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assert_no_traceback(result)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("invalid-input", payload["error"]["code"])
        self.assertIn("OPENSUBTITLES_USERNAME", payload["error"]["message"])
        self.assertNotIn(secret, result.stdout)
        self.assertFalse(output.exists())

    def test_the_new_flags_did_not_narrow_the_generation_contract(self):
        """The whole point is that an ordinary run is untouched."""
        _, text = self.build(self.EVENT)

        self.assertIn(WARNING_TEXT, text)

    def test_list_streams_still_refuses_to_generate(self):
        result = self.run_cli(
            "--list-streams", "--video", self.subtitles,
            "--os-search", "The Thing",
        )

        self.assert_failed(result)
        self.assertIn("--list-streams", result.stderr)
