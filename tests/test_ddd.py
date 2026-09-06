"""Unit tests for the opt-in official DoesTheDogDie API source.

Every HTTP response is synthetic. These tests never contact the public API and
never use an actual API key.
"""

import contextlib
import io
import json
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

    def test_search_returns_candidates_without_selecting_one(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return _Response(
                '[{"id": 42, "name": "The Thing", "releaseYear": 1982, '
                '"itemTypeName": "Movie", "imdbId": "tt0084787", '
                '"tmdbId": 1091}]'
            )

        candidates = ddd.search_items("test-key", "The Thing", 1982, opener=opener)

        self.assertEqual([{
            "id": 42,
            "name": "The Thing",
            "releaseYear": 1982,
            "itemType": "Movie",
            "imdbId": "tt0084787",
            "tmdbId": 1091,
        }], candidates)
        self.assertEqual(
            ddd.API_BASE_URL + "/items?name=The+Thing&releaseYear=1982",
            requests[0][0].full_url,
        )


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


class CliUnexpectedFailureTests(unittest.TestCase):
    """What a broken tool owes a caller that cannot read a traceback.

    The API client is the fault injection point here only because it is the
    layer most able to raise something nobody anticipated: a TLS, socket or
    third-party failure that is neither a ValueError nor an OSError. The
    contract under test belongs to the CLI.
    """

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workdir = Path(self.tempdir.name)
        self.subtitles = self.workdir / "dialogue.srt"
        self.subtitles.write_text(
            "1\n00:00:00,000 --> 00:00:05,000\nHarmless dialogue.\n",
            encoding="utf-8",
        )
        self.output = self.workdir / "warned.ass"

    def _argv(self, *extra):
        return [
            "--subtitles", str(self.subtitles), "--ddd-item", "42",
            "--ddd-api-key", "test-key", "--output", str(self.output), *extra,
        ]

    def _broken_client(self, message):
        return mock.patch(
            "trigger_warnings.ddd.load_item_events", side_effect=RuntimeError(message)
        )

    def _main(self, *extra):
        """Run main with both streams captured; return (status, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(self._argv(*extra))
        return status, out.getvalue(), err.getvalue()

    def test_unexpected_failure_under_json_is_a_structured_internal_error(self):
        """A traceback on stderr with nothing on stdout is not an answer: a
        caller parsing stdout reads it as "did not run", not "failed"."""
        with self._broken_client("X-API-KEY: test-key was rejected"):
            status, stdout, stderr = self._main("--json")

        self.assertEqual(cli.EXIT_ERROR, status)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("internal-error", payload["error"]["code"])
        self.assertIn("RuntimeError", payload["error"]["message"])
        self.assertEqual("", stderr)
        self.assertFalse(self.output.exists())

    def test_the_exception_text_is_not_repeated_to_the_caller(self):
        """An unexpected exception carries whatever the failing library put in
        it, which can be a request, a header or a credential."""
        with self._broken_client("X-API-KEY: test-key was rejected"):
            _, stdout, _ = self._main("--json")
        self.assertNotIn("test-key", stdout)
        self.assertNotIn("X-API-KEY", stdout)

    def test_without_json_the_traceback_is_still_raised(self):
        """Normal use keeps the traceback; it is the only debugging signal."""
        with self._broken_client("boom"):
            with self.assertRaises(RuntimeError):
                self._main()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
