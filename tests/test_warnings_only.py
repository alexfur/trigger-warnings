import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_acceptance import (
    DIALOGUE_SRT,
    PACKAGE_NAME,
    TIMEOUT_S,
    WARNING_TEXT,
    dialogue_cues,
    extract_cues,
    warning_cues,
    _find_import_root,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
IMPORT_ROOT = _find_import_root() or REPO_ROOT


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


class TestWarningsOnlyCLI(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.subtitles = self.workdir / "dialogue.srt"
        self.subtitles.write_text(DIALOGUE_SRT, encoding="utf-8")
        self.events = self.workdir / "events.json"
        self.events.write_text(
            json.dumps([
                {"start": 10.0, "end": 20.0, "label": "spiders"},
                {"start": 60.0, "end": 75.0, "label": "violence"},
            ]),
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_generate_both_merged_and_warnings_only_srt(self):
        merged = self.workdir / "merged.srt"
        warnings = self.workdir / "warnings.srt"
        result = _run_module(
            "--subtitles", str(self.subtitles),
            "--events", str(self.events),
            "--output", str(merged),
            "--warnings-output", str(warnings),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(merged.is_file())
        self.assertTrue(warnings.is_file())

        # Check merged contains both dialogue and warnings
        merged_cues = extract_cues(merged.read_text(encoding="utf-8"))
        self.assertTrue(len(warning_cues(merged_cues)) > 0)
        self.assertTrue(len(dialogue_cues(merged_cues)) > 0)

        # Check warnings-only contains ONLY warnings
        warn_cues = extract_cues(warnings.read_text(encoding="utf-8"))
        self.assertTrue(len(warning_cues(warn_cues)) > 0)
        self.assertEqual(0, len(dialogue_cues(warn_cues)))
        self.assertIn("Wrote {}".format(merged), result.stderr)
        self.assertIn("Wrote {}".format(warnings), result.stderr)

    def test_generate_both_merged_ass_and_warnings_srt(self):
        merged = self.workdir / "merged.ass"
        warnings = self.workdir / "warnings.srt"
        result = _run_module(
            "--subtitles", str(self.subtitles),
            "--events", str(self.events),
            "--output", str(merged),
            "--warnings-output", str(warnings),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(merged.is_file())
        self.assertTrue(warnings.is_file())

        # Merged ASS has Style: Warning and Dialogue lines
        merged_text = merged.read_text(encoding="utf-8")
        self.assertIn("Style: Warning", merged_text)
        self.assertIn("Style: Dialogue", merged_text)

        # Warnings SRT has {\an8}TRIGGER INCOMING and no dialogue
        warn_text = warnings.read_text(encoding="utf-8")
        self.assertIn("{\\an8}TRIGGER INCOMING", warn_text)
        self.assertNotIn("kettle", warn_text)

    def test_generate_warnings_output_alone_without_subtitles(self):
        warnings = self.workdir / "warnings.srt"
        result = _run_module(
            "--events", str(self.events),
            "--warnings-output", str(warnings),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(warnings.is_file())
        warn_cues = extract_cues(warnings.read_text(encoding="utf-8"))
        self.assertTrue(len(warning_cues(warn_cues)) > 0)
        self.assertEqual(0, len(dialogue_cues(warn_cues)))

    def test_generate_warnings_output_ass_alone_without_subtitles(self):
        warnings = self.workdir / "warnings.ass"
        result = _run_module(
            "--events", str(self.events),
            "--warnings-output", str(warnings),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(warnings.is_file())
        warn_text = warnings.read_text(encoding="utf-8")
        self.assertIn("Style: Warning", warn_text)
        self.assertIn("Dialogue: 1,", warn_text)

    def test_generate_warnings_only_flag_with_output(self):
        warnings = self.workdir / "warnings.srt"
        result = _run_module(
            "--events", str(self.events),
            "--output", str(warnings),
            "--warnings-only",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(warnings.is_file())
        warn_cues = extract_cues(warnings.read_text(encoding="utf-8"))
        self.assertTrue(len(warning_cues(warn_cues)) > 0)
        self.assertEqual(0, len(dialogue_cues(warn_cues)))

    def test_json_output_reports_warnings_output_and_files_written(self):
        merged = self.workdir / "merged.srt"
        warnings = self.workdir / "warnings.srt"
        result = _run_module(
            "--json",
            "--subtitles", str(self.subtitles),
            "--events", str(self.events),
            "--output", str(merged),
            "--warnings-output", str(warnings),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(str(merged), payload["output"])
        self.assertEqual(str(warnings), payload["warningsOutput"])
        self.assertEqual([str(merged), str(warnings)], payload["filesWritten"])

    def test_provenance_records_both_outputs(self):
        merged = self.workdir / "merged.srt"
        warnings = self.workdir / "warnings.srt"
        provenance = self.workdir / "run.provenance.json"
        result = _run_module(
            "--subtitles", str(self.subtitles),
            "--events", str(self.events),
            "--output", str(merged),
            "--warnings-output", str(warnings),
            "--provenance", str(provenance),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(provenance.is_file())
        prov_data = json.loads(provenance.read_text(encoding="utf-8"))
        self.assertEqual(str(merged), prov_data["output"])
        self.assertEqual(str(warnings), prov_data["warningsOutput"])

    def test_dry_run_with_warnings_output_writes_no_files(self):
        warnings = self.workdir / "warnings.srt"
        result = _run_module(
            "--json", "--dry-run",
            "--events", str(self.events),
            "--warnings-output", str(warnings),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(warnings.exists())
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("dry-run", payload["mode"])
        self.assertEqual(str(warnings), payload["warningsOutput"])
        self.assertEqual([], payload["filesWritten"])

    def test_collision_between_output_and_warnings_output(self):
        same = self.workdir / "same.srt"
        result = _run_module(
            "--subtitles", str(self.subtitles),
            "--events", str(self.events),
            "--output", str(same),
            "--warnings-output", str(same),
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("same path", result.stderr)

    def test_invalid_extension_for_warnings_output(self):
        bad = self.workdir / "warnings.txt"
        result = _run_module(
            "--events", str(self.events),
            "--warnings-output", str(bad),
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--warnings-output must end in .ass or .srt", result.stderr)

    def test_combining_warnings_only_and_warnings_output_is_rejected(self):
        out = self.workdir / "out.srt"
        warn = self.workdir / "warn.srt"
        result = _run_module(
            "--events", str(self.events),
            "--output", str(out),
            "--warnings-output", str(warn),
            "--warnings-only",
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--warnings-only and --warnings-output cannot be combined", result.stderr)

    def test_warnings_only_without_output_is_rejected(self):
        result = _run_module(
            "--events", str(self.events),
            "--warnings-only",
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--warnings-only needs --output", result.stderr)

    def test_subtitles_supplied_with_warnings_only_is_rejected(self):
        out = self.workdir / "out.srt"
        result = _run_module(
            "--subtitles", str(self.subtitles),
            "--events", str(self.events),
            "--output", str(out),
            "--warnings-only",
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--subtitles was supplied with --warnings-only", result.stderr)

    def test_subtitles_supplied_with_warnings_output_alone_is_rejected(self):
        warn = self.workdir / "warn.srt"
        result = _run_module(
            "--subtitles", str(self.subtitles),
            "--events", str(self.events),
            "--warnings-output", str(warn),
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--subtitles was supplied, but --warnings-output produces a track with no dialogue", result.stderr)

    def test_reject_in_list_streams(self):
        result = _run_module(
            "--list-streams",
            "--video", "dummy.mp4",
            "--warnings-output", "warn.srt",
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--list-streams does not generate output", result.stderr)

    def test_reject_existing_warnings_output_file(self):
        existing = self.workdir / "existing.srt"
        existing.write_text("already here", encoding="utf-8")
        result = _run_module(
            "--events", str(self.events),
            "--warnings-output", str(existing),
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("already exists", result.stderr)

    def test_stream_rejected_when_no_dialogue_requested(self):
        warn = self.workdir / "warn.srt"
        result = _run_module(
            "--events", str(self.events),
            "--warnings-output", str(warn),
            "--stream", "0",
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--stream was supplied, but the output contains no dialogue", result.stderr)

    def test_language_rejected_when_no_dialogue_requested(self):
        warn = self.workdir / "warn.srt"
        result = _run_module(
            "--events", str(self.events),
            "--warnings-output", str(warn),
            "--language", "fre",
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--language was supplied, but the output contains no dialogue", result.stderr)


if __name__ == "__main__":
    unittest.main()
