"""Unit tests for the opt-in official DoesTheDogDie API source.

Every HTTP response is synthetic. These tests never contact the public API and
never use an actual API key.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trigger_warnings import cli, core, ddd


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self):
        return self.payload.encode("utf-8")


class DddApiTests(unittest.TestCase):
    def _opener(self, topics, ratings):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/topics"):
                return _Response(topics)
            if request.full_url.endswith("/items/42/ratings"):
                return _Response(ratings)
            raise AssertionError("unexpected URL: {}".format(request.full_url))

        return opener, requests

    def test_converts_timestamped_ratings_and_reports_omissions(self):
        opener, requests = self._opener(
            '[{"id": 7, "name": "a dog dies"}]',
            """[
                {"id": 1, "topicId": 7, "position1": 0, "position2": 1,
                 "position3": 2, "safePosition1": 0, "safePosition2": 1,
                 "safePosition3": 8, "isSceneAlert": false},
                {"id": 2, "topicId": 7, "position1": 1, "position2": 2,
                 "position3": 3, "safePosition1": null, "safePosition2": null,
                 "safePosition3": null, "isSceneAlert": true},
                {"id": 3, "topicId": 7}
            ]""",
        )

        source = ddd.load_item_events("test-key", 42, opener=opener)

        self.assertEqual(2, len(source.events))
        self.assertEqual(62000, source.events[0].start_ms)
        self.assertEqual(68000, source.events[0].end_ms)
        self.assertEqual("a dog dies", source.events[0].label)
        self.assertEqual("community", source.events[0].severity)
        self.assertIsNone(source.events[1].end_ms)
        self.assertEqual("scene-alert", source.events[1].severity)
        self.assertEqual(1, source.community)
        self.assertEqual(1, source.scene_alerts)
        self.assertIn("without timestamps", source.notes[0])
        self.assertEqual(
            [
                ddd.API_BASE_URL + "/topics",
                ddd.API_BASE_URL + "/items/42/ratings",
            ],
            [request.full_url for request, _ in requests],
        )
        self.assertTrue(all(timeout == 15 for _, timeout in requests))
        self.assertTrue(all(
            dict((key.lower(), value) for key, value in request.header_items())
            ["x-api-key"] == "test-key"
            for request, _ in requests
        ))

    def test_partial_or_reversed_api_timestamps_fail_loudly(self):
        for ratings, expected in (
            ('[{"id": 1, "topicId": 7, "position1": 0}]', "partial"),
            ('[{"id": 1, "topicId": 7, "position1": 1, "position2": 2, '
             '"position3": 3, "safePosition1": 1, "safePosition2": 0, '
             '"safePosition3": 0}]', "ends before"),
        ):
            with self.subTest(expected=expected):
                opener, _ = self._opener('[{"id": 7, "name": "gore"}]', ratings)
                with self.assertRaisesRegex(ddd.DddApiError, expected):
                    ddd.load_item_events("test-key", 42, opener=opener)

    def test_empty_timestamp_set_is_not_an_all_clear(self):
        opener, _ = self._opener(
            '[{"id": 7, "name": "gore"}]', '[{"id": 1, "topicId": 7}]'
        )
        with self.assertRaisesRegex(ddd.DddApiError, "not evidence"):
            ddd.load_item_events("test-key", 42, opener=opener)

    def test_all_negative_one_timestamp_fields_mean_absent_not_malformed(self):
        opener, _ = self._opener(
            '[{"id": 7, "name": "gore"}]',
            '[{"id": 1, "topicId": 7, "position1": -1, "position2": -1, '
            '"position3": -1}]',
        )
        with self.assertRaisesRegex(ddd.DddApiError, "not evidence"):
            ddd.load_item_events("test-key", 42, opener=opener)


class DddCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tempdir.name)
        self.subtitles = self.workdir / "dialogue.srt"
        self.subtitles.write_text(
            "1\n00:00:00,000 --> 00:00:05,000\nHarmless dialogue.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _args(self, *extra):
        return cli.build_parser().parse_args((
            "--subtitles", str(self.subtitles), "--ddd-item", "42",
            "--output", str(self.workdir / "warned.ass"), *extra,
        ))

    def test_ddd_source_is_opt_in_uses_the_key_and_prints_attribution(self):
        source = ddd.DddEvents(
            [core.Event(1000, 2000, "gore", "community", 1)], [], 0, 1
        )
        reports = []
        with mock.patch("trigger_warnings.ddd.load_item_events", return_value=source) as load:
            result = cli.run(self._args("--ddd-api-key", "test-key"), reports.append)

        self.assertEqual(cli.EXIT_OK, result)
        load.assert_called_once_with("test-key", 42)
        self.assertIn(ddd.ATTRIBUTION, reports)
        self.assertTrue((self.workdir / "warned.ass").is_file())
        self.assertNotIn("gore", (self.workdir / "warned.ass").read_text())

    def test_environment_key_is_used_without_echoing_it(self):
        source = ddd.DddEvents(
            [core.Event(1000, None, "gore", "community", 1)], [], 0, 1
        )
        reports = []
        with mock.patch.dict(os.environ, {"DDD_API_KEY": "environment-key"}, clear=False):
            with mock.patch("trigger_warnings.ddd.load_item_events", return_value=source) as load:
                self.assertEqual(cli.EXIT_OK, cli.run(self._args(), reports.append))
        load.assert_called_once_with("environment-key", 42)
        self.assertNotIn("environment-key", "\n".join(reports))

    def test_missing_key_fails_without_writing_output(self):
        reports = []
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ddd.DddApiError, "API key"):
                cli.run(self._args(), reports.append)
        self.assertFalse((self.workdir / "warned.ass").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
