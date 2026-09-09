"""Synthetic vision-provider and CLI tests. No keys, downloads or GPU needed."""

import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from trigger_warnings import cli, core, ddd, vision


class AnswerTests(unittest.TestCase):
    def answer(self, text):
        return vision._answer(
            mock.Mock(return_value=types.SimpleNamespace(text=text)),
            mock.Mock(return_value="prompt"), mock.Mock(), mock.Mock(),
            mock.Mock(), [object()], "red rectangle", 16,
        )

    def test_accepts_explicit_yes_and_no(self):
        for text, expected in [("Yes.", True), (" YES! ", True), ("no", False)]:
            with self.subTest(text=text):
                self.assertIs(self.answer(text), expected)

    def test_uncertain_or_truncated_answers_are_errors(self):
        for text in ["", "maybe", "yes and no", "No, but I cannot tell", "y"]:
            with self.subTest(text=text):
                with self.assertRaisesRegex(vision.VisionError, "unusable answer"):
                    self.answer(text)

    def test_generation_failure_is_not_a_negative_detection(self):
        with self.assertRaisesRegex(vision.VisionError, "vision model failed"):
            vision._answer(mock.Mock(side_effect=RuntimeError("synthetic failure")),
                           mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), [], "red", 16)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.video = Path(self.folder.name) / "synthetic.mp4"
        self.video.touch()
        self.backend = self.enter_patch("_load_backend", return_value=(mock.Mock(),) * 5)
        self.enter_patch("_duration", return_value=25.0)
        self.enter_patch("_binary", side_effect=lambda name: name)
        self.frame_folders = []

        def frames(argv):
            target = Path(argv[-1].replace("%05d", "00001"))
            target.touch()
            self.frame_folders.append(target.parent.parent)

        self.enter_patch("_run", side_effect=frames)
        fake_pil = types.ModuleType("PIL")
        fake_pil.Image = mock.MagicMock()
        patcher = mock.patch.dict(sys.modules, {"PIL": fake_pil})
        patcher.start()
        self.addCleanup(patcher.stop)

    def enter_patch(self, name, **kwargs):
        patcher = mock.patch.object(vision, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_each_trigger_is_checked_and_final_chunk_is_clipped(self):
        answers = self.enter_patch("_answer", side_effect=[False, True, False, False, True, False])
        result = vision.scan_video(self.video, ["red", "blue"], model="test-model", revision="abc")
        self.assertEqual(result.events, [
            {"start": 0, "end": 10, "label": "blue", "severity": "model-candidate"},
            {"start": 20, "end": 25, "label": "red", "severity": "model-candidate"},
        ])
        self.assertEqual(answers.call_count, 6)
        self.backend.assert_called_once_with("test-model", "abc")
        self.assertEqual(result.metadata["requestedTriggers"], ["red", "blue"])
        self.assertTrue(result.notes)
        self.assertTrue(all(not path.exists() for path in self.frame_folders))

    def test_no_candidates_is_an_error_and_cleans_frames(self):
        self.enter_patch("_answer", return_value=False)
        with self.assertRaisesRegex(vision.VisionError, "does not establish"):
            vision.scan_video(self.video, ["red"])
        self.assertTrue(all(not path.exists() for path in self.frame_folders))

    def test_bad_inputs_fail_before_model_loading(self):
        for triggers, kwargs in [([], {}), ([" "], {}), (["red", " RED "], {}),
                                 (["red"], {"fps": 0}), (["red"], {"chunk_seconds": 0}),
                                 (["red"], {"width": 0}), (["red"], {"max_tokens": 0})]:
            with self.subTest(triggers=triggers, kwargs=kwargs):
                with self.assertRaises(vision.VisionError):
                    vision.scan_video(self.video, triggers, **kwargs)
        self.backend.assert_not_called()


class ModelCliTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.video = self.root / "synthetic.mp4"
        self.video.touch()
        self.output = self.root / "warning.ass"
        self.provenance = self.root / "provenance.json"
        self.scan = vision.VisionScan(
            [{"start": 30, "end": 40, "label": "red", "severity": "model-candidate"}],
            ["Synthetic model caveat"], {"kind": "local-model", "chunks": 6})

    def invoke(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.keychain, "hydrate", return_value={}), \
                mock.patch.object(cli, "_obtain_subtitles", return_value=(
                    "1\n00:00:01,000 --> 00:00:05,000\nSynthetic dialogue\n", 60000, None, None)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(["--json", "--video", str(self.video),
                               "--output", str(self.output), *extra])
        self.assertEqual(err.getvalue(), "")
        return status, json.loads(out.getvalue())

    def test_model_scan_writes_dialogue_warning_and_provenance(self):
        with mock.patch.object(vision, "scan_video", return_value=self.scan) as scan:
            status, result = self.invoke("--model-trigger", "red", "--provenance", str(self.provenance))
        self.assertEqual(status, 0)
        self.assertEqual(result["mode"], "model-scan")
        self.assertIn("Synthetic model caveat", result["notes"])
        self.assertIn("TRIGGER INCOMING", self.output.read_text())
        self.assertIn("Synthetic dialogue", self.output.read_text())
        self.assertEqual(json.loads(self.provenance.read_text())["source"]["kind"], "local-model")
        self.assertEqual(scan.call_args.kwargs["model"], vision.DEFAULT_MODEL)

    def test_dry_run_scans_without_writing(self):
        with mock.patch.object(vision, "scan_video", return_value=self.scan) as scan:
            status, result = self.invoke("--model-trigger", "red", "--dry-run")
        self.assertEqual(status, 0)
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["filesWritten"], [])
        self.assertFalse(self.output.exists())
        scan.assert_called_once()

    def test_empty_scan_failure_does_not_publish_files(self):
        with mock.patch.object(vision, "scan_video", side_effect=vision.VisionError("No candidates")):
            status, result = self.invoke("--model-trigger", "red")
        self.assertEqual(status, 2)
        self.assertFalse(result["ok"])
        self.assertFalse(self.output.exists())

    def test_ddd_labels_are_deduplicated_and_timestamps_replaced(self):
        source = ddd.DddEvents([core.Event(1000, 2000, "red", "community", 1),
                                core.Event(2000, 3000, "red", "community", 2)], [], 0, 2)
        with mock.patch.object(ddd, "load_item_events", return_value=source), \
                mock.patch.object(vision, "scan_video", return_value=self.scan) as scan:
            status, result = self.invoke("--ddd-item", "42", "--model-from-ddd", "--category", "red")
        self.assertEqual(status, 0)
        self.assertEqual(scan.call_args.args[1], ["red"])
        self.assertEqual(result["mode"], "model-scan")
        self.assertEqual(result["source"]["triggerSource"]["itemId"], 42)
        self.assertIn("0:00:10.00", self.output.read_text())

    def test_list_streams_does_not_require_a_model(self):
        args = cli.build_parser().parse_args(["--list-streams", "--video", str(self.video)])
        result = {}
        with mock.patch.object(cli, "list_streams", return_value={"streams": []}):
            self.assertEqual(cli.run(args, mock.Mock(), result), 0)
        self.assertEqual(result["mode"], "list-streams")

    def test_existing_output_is_rejected_before_inference(self):
        self.output.write_text("original")
        with mock.patch.object(vision, "scan_video") as scan:
            status, result = self.invoke("--model-trigger", "red")
        self.assertEqual(status, 2)
        self.assertFalse(result["ok"])
        scan.assert_not_called()
        self.assertEqual(self.output.read_text(), "original")
