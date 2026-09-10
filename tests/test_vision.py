"""Synthetic vision-provider and CLI tests. No keys, downloads or GPU needed."""

import contextlib
import io
import json
import signal
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from trigger_warnings import cli, core, ddd, vision
from trigger_warnings.video import VideoClip


class AnswerTests(unittest.TestCase):
    def answer(self, text):
        return vision._answer(
            mock.Mock(return_value=types.SimpleNamespace(text=text)),
            mock.Mock(return_value="prompt"), mock.Mock(), mock.Mock(),
            mock.Mock(), VideoClip(0, 1, [object()], [0]), "red rectangle", 16, 1,
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
                           mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(),
                           VideoClip(0, 1, [], []), "red", 16, 1)

    def test_video_pixels_and_timestamps_reach_inference_with_the_right_prompt(self):
        clip = VideoClip(10, 12, [object(), object()], [10, 11], "/video.mkv")
        for kind in ("smolvlm", "qwen2_5_vl"):
            with self.subTest(kind=kind):
                model = types.SimpleNamespace(config=types.SimpleNamespace(model_type=kind))
                generate = mock.Mock(return_value=types.SimpleNamespace(text="yes"))
                template = mock.Mock(return_value="prompt")
                self.assertTrue(vision._answer(generate, template, model, mock.Mock(),
                                               mock.Mock(), clip, "red", 16, 1))
                self.assertEqual(generate.call_args.kwargs["video"], [clip.frames])
                self.assertNotIn("image", generate.call_args.kwargs)
                self.assertIn("10.000s, 11.000s", template.call_args.args[2])
                if kind == "smolvlm":
                    self.assertEqual(template.call_args.kwargs["num_images"], 2)
                else:
                    self.assertEqual(template.call_args.kwargs["video"], clip.source)
                    self.assertEqual(template.call_args.kwargs["num_images"], 0)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.video = Path(self.folder.name) / "synthetic.mp4"
        self.video.touch()
        self.backend = self.enter_patch("_load_backend", return_value=(mock.Mock(),) * 5)
        self.model_path = self.video.parent / "model"
        self.resolve = self.enter_patch("resolve_model", return_value=self.model_path)
        self.reader = mock.MagicMock()
        self.reader.duration = 25.0
        self.reader.read_clip.side_effect = lambda start, end: VideoClip(start, end, [object()], [start])
        self.reader_context = self.enter_patch("VideoReader")
        self.reader_context.return_value.__enter__.return_value = self.reader

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
        self.resolve.assert_called_once_with("test-model", revision="abc", local_only=False)
        self.backend.assert_called_once_with(self.model_path)
        self.assertEqual(self.reader.read_clip.call_args_list,
                         [mock.call(0, 10), mock.call(10, 20), mock.call(20, 25)])
        self.assertEqual(result.metadata["requestedTriggers"], ["red", "blue"])
        self.assertTrue(result.notes)
        self.reader_context.return_value.__exit__.assert_called_once()

    def test_no_candidates_is_an_error_and_closes_video(self):
        self.enter_patch("_answer", return_value=False)
        with self.assertRaisesRegex(vision.VisionError, "does not establish"):
            vision.scan_video(self.video, ["red"])
        self.reader_context.return_value.__exit__.assert_called_once()

    def test_bad_inputs_fail_before_model_loading(self):
        for triggers, kwargs in [([], {}), ([" "], {}), (["red", " RED "], {}),
                                 (["red"], {"fps": 0}), (["red"], {"chunk_seconds": 0}),
                                 (["red"], {"width": 0}), (["red"], {"max_tokens": 0})]:
            with self.subTest(triggers=triggers, kwargs=kwargs):
                with self.assertRaises(vision.VisionError):
                    vision.scan_video(self.video, triggers, **kwargs)
        self.backend.assert_not_called()

    def test_cli_json_result_and_human_progress_use_separate_streams(self):
        self.enter_patch("_answer", return_value=True)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.keychain, "hydrate", return_value={}), \
                mock.patch.object(cli, "_obtain_subtitles", return_value=(
                    "1\n00:00:01,000 --> 00:00:05,000\nSynthetic dialogue\n", 25000, None, None)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(["--json", "--video", str(self.video),
                               "--model-trigger", "red", "--output",
                               str(self.video.parent / "warnings.ass")])
        self.assertEqual(status, 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])
        self.assertIn("Loading model", err.getvalue())
        self.assertIn("100.0% Scan complete", err.getvalue())

    def test_model_load_failure_shows_progress_and_failure(self):
        err = io.StringIO()

        def fail_loading(*args):
            self.assertIn("Loading model", err.getvalue())
            raise vision.VisionError("synthetic loading failure")

        self.backend.side_effect = fail_loading
        with contextlib.redirect_stderr(err):
            with self.assertRaisesRegex(vision.VisionError, "loading failure"):
                vision.scan_video(self.video, ["red"], report=mock.Mock())
        self.assertIn("Scan failed", err.getvalue())
        self.assertNotIn("Scan complete", err.getvalue())

    def test_sigterm_cancels_inference_and_closes_video(self):
        self.enter_patch("_answer", side_effect=lambda *args: signal.raise_signal(signal.SIGTERM))
        original = signal.getsignal(signal.SIGTERM)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(KeyboardInterrupt):
                vision.scan_video(self.video, ["red"], report=mock.Mock())
        self.assertIn("Scan cancelled", err.getvalue())
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)
        self.reader_context.return_value.__exit__.assert_called_once()


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

    def test_local_only_choice_reaches_model_resolution(self):
        with mock.patch.object(vision, "scan_video", return_value=self.scan) as scan:
            status, _ = self.invoke("--model-trigger", "red", "--model-local-only")
        self.assertEqual(status, 0)
        self.assertTrue(scan.call_args.kwargs["local_only"])

    def test_local_only_flag_is_rejected_in_non_model_modes(self):
        for mode in (["--setup"], ["--os-search", "synthetic"],
                     ["--ddd-search", "synthetic"], ["--list-streams", "--video", str(self.video)]):
            with self.subTest(mode=mode), mock.patch.object(cli.keychain, "hydrate", return_value={}), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                status = cli.main(["--json", *mode, "--model-local-only"])
                result = json.loads(out.getvalue())
                self.assertEqual(status, 2)
                self.assertIn("--model-local-only", result["error"]["message"])
