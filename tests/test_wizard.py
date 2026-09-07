"""Unit tests for --setup, the guided prerequisite check.

Every response is synthetic. These tests never contact either public API and
never use an actual API key or account.
"""

import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest import mock

from trigger_warnings import cli, wizard


_FAKE_DDD_KEY = "ddd-key-not-a-real-credential"
_FAKE_OS_KEY = "os-key-not-a-real-credential"

_FULL_ENV = {
    wizard.DDD_KEY_VARIABLE: _FAKE_DDD_KEY,
    "OPENSUBTITLES_API_KEY": _FAKE_OS_KEY,
    "OPENSUBTITLES_USERNAME": "someone",
    "OPENSUBTITLES_PASSWORD": "a-password",
}


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self, *unused):
        return self.payload.encode("utf-8")


def _working_opener(request, timeout=None):
    """Answer both APIs' search calls well enough to look configured."""
    url = request.full_url
    if "doesthedogdie" in url:
        if url.endswith("/topics"):
            return _Response("[]")
        return _Response('[{"id": 1, "name": "Jaws", "releaseYear": "1975",'
                         ' "itemTypeName": "Movie"}]')
    return _Response('{"data": [], "total_count": 0}')


def _refusing_opener(request, timeout=None):
    raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))


def _offline_opener(request, timeout=None):
    raise URLError("Connection refused")


def _which_all(name):
    return "/usr/bin/" + name


def _which_none(unused):
    return None


class CheckTests(unittest.TestCase):
    def test_nothing_configured_is_reported_without_calling_anything(self):
        def explode(*unused, **unused_kwargs):
            raise AssertionError("no request may be made when nothing is set")

        checks = {c.name: c for c in wizard.collect(
            environ={}, opener=explode, which=_which_all)}
        self.assertEqual(wizard.MISSING, checks["ddd-api-key"].status)
        self.assertEqual(wizard.MISSING, checks["opensubtitles-api-key"].status)
        self.assertEqual(wizard.MISSING, checks["opensubtitles-login"].status)
        self.assertEqual(wizard.OK, checks["python"].status)

    def test_missing_check_carries_the_variable_and_where_to_get_it(self):
        check = wizard.check_ddd(environ={}, verify=False)
        self.assertEqual(wizard.DDD_KEY_VARIABLE, check.variable)
        self.assertEqual(wizard.DDD_SIGNUP_URL, check.url)
        self.assertTrue(check.fix)

    def test_working_credentials_are_verified_against_the_api(self):
        checks = {c.name: c for c in wizard.collect(
            environ=_FULL_ENV, opener=_working_opener, which=_which_all)}
        self.assertEqual(wizard.OK, checks["ddd-api-key"].status)
        self.assertEqual(wizard.OK, checks["opensubtitles-api-key"].status)
        self.assertEqual(wizard.OK, checks["opensubtitles-login"].status)

    def test_refused_key_is_invalid_not_merely_unverified(self):
        check = wizard.check_ddd(environ=_FULL_ENV, opener=_refusing_opener)
        self.assertEqual(wizard.INVALID, check.status)
        self.assertEqual(wizard.DDD_SIGNUP_URL, check.url)

    def test_offline_machine_is_not_told_its_key_was_refused(self):
        """The two failures have nothing in common and different fixes."""
        for check in (wizard.check_ddd(environ=_FULL_ENV, opener=_offline_opener),
                      wizard.check_opensubtitles_key(environ=_FULL_ENV,
                                                     opener=_offline_opener)):
            self.assertEqual(wizard.UNVERIFIED, check.status)
            self.assertIn("network", check.fix.lower())
            self.assertIsNone(check.url)

    def test_present_but_unchecked_is_its_own_status(self):
        check = wizard.check_ddd(environ=_FULL_ENV, verify=False)
        self.assertEqual(wizard.UNVERIFIED, check.status)

    def test_a_blank_variable_counts_as_unset(self):
        check = wizard.check_ddd(environ={wizard.DDD_KEY_VARIABLE: "   "},
                                 verify=False)
        self.assertEqual(wizard.MISSING, check.status)

    def test_login_needs_both_halves(self):
        env = dict(_FULL_ENV)
        del env["OPENSUBTITLES_PASSWORD"]
        check = wizard.check_opensubtitles_login(environ=env)
        self.assertEqual(wizard.MISSING, check.status)
        self.assertIn("OPENSUBTITLES_PASSWORD", check.detail)

    def test_missing_ffmpeg_is_optional_and_says_what_still_works(self):
        check = wizard.check_ffmpeg(which=_which_none)
        self.assertEqual(wizard.MISSING, check.status)
        self.assertFalse(check.required)
        self.assertIn("--subtitles", check.fix)


class SecretTests(unittest.TestCase):
    def test_no_check_ever_repeats_a_credential(self):
        for opener in (_working_opener, _refusing_opener, _offline_opener):
            checks = wizard.collect(environ=_FULL_ENV, opener=opener,
                                    which=_which_all)
            blob = json.dumps([list(c) for c in checks])
            for secret in _FULL_ENV.values():
                self.assertNotIn(secret, blob)

    def test_redaction_backstop_replaces_a_secret_that_reached_the_text(self):
        self.assertEqual("<redacted> and text",
                         wizard._redact(_FAKE_DDD_KEY + " and text",
                                        (_FAKE_DDD_KEY,)))

    def test_redaction_leaves_short_values_alone(self):
        """A short secret matches ordinary prose; shredding it helps nobody."""
        self.assertEqual("a set of keys", wizard._redact("a set of keys", ("set",)))


class CapabilityTests(unittest.TestCase):
    def test_file_paths_are_always_available(self):
        able = wizard.capabilities(wizard.collect(environ={}, which=_which_none))
        self.assertTrue(able["timestampsFromFile"])
        self.assertTrue(able["dialogueFromFile"])
        self.assertFalse(able["dialogueFromVideo"])

    def test_download_needs_the_key_and_the_login_together(self):
        env = dict(_FULL_ENV)
        del env["OPENSUBTITLES_USERNAME"]
        able = wizard.capabilities(wizard.collect(
            environ=env, opener=_working_opener, which=_which_all))
        self.assertTrue(able["dialogueFromOpenSubtitlesSearch"])
        self.assertFalse(able["dialogueFromOpenSubtitlesDownload"])

    def test_unconfigured_run_still_says_something_works(self):
        checks = wizard.collect(environ={}, which=_which_none)
        steps = wizard.next_steps(checks, wizard.capabilities(checks))
        self.assertTrue(any("--events" in step for step in steps))


class CliTests(unittest.TestCase):
    def _run(self, argv, environ):
        import os as _os
        saved = dict(_os.environ)
        _os.environ.clear()
        _os.environ.update(environ)
        messages = []
        result = {}
        try:
            args = cli.build_parser().parse_args(argv)
            status = cli.run(args, lambda m, level="info": messages.append(m), result)
        finally:
            _os.environ.clear()
            _os.environ.update(saved)
        return status, result, messages

    def test_setup_reports_and_succeeds_even_when_unconfigured(self):
        """Not configured is an answer, not a failure of the command."""
        status, result, _ = self._run(["--setup", "--no-verify"], {})
        self.assertEqual(cli.EXIT_OK, status)
        self.assertEqual("setup", result["mode"])
        self.assertFalse(result["ready"])
        self.assertFalse(result["verified"])
        self.assertEqual([], result["filesWritten"])

    def test_setup_writes_no_files(self):
        _, result, _ = self._run(["--setup", "--no-verify"], dict(_FULL_ENV))
        self.assertEqual([], result["filesWritten"])

    def test_setup_refuses_generation_flags(self):
        with self.assertRaises(cli.TriggerWarningsError) as caught:
            self._run(["--setup", "--lead", "20"], {})
        self.assertIn("--lead", str(caught.exception))

    def test_no_verify_outside_setup_is_refused(self):
        with self.assertRaises(cli.TriggerWarningsError) as caught:
            self._run(["--no-verify", "--subtitles", "a.srt", "--events",
                       "b.json", "--output", "c.ass"], {})
        self.assertIn("--setup", str(caught.exception))

    def test_every_check_is_reported_to_the_user(self):
        _, result, messages = self._run(["--setup", "--no-verify"], {})
        joined = "\n".join(messages)
        for check in result["checks"]:
            self.assertIn(check["name"], joined)

    def test_tty_setup_invites_but_a_pipe_does_not(self):
        class Prompter:
            interactive = True

            def __init__(self):
                self.confirmed = False

            def confirm(self, unused):
                self.confirmed = True
                return False

        args = cli.build_parser().parse_args(["--setup", "--no-verify"])
        prompt = Prompter()
        cli.run(args, lambda *unused: None, {}, setup_prompter=prompt)
        self.assertTrue(prompt.confirmed)

        class Pipe:
            interactive = False

        cli.run(args, lambda *unused: None, {}, setup_prompter=Pipe())

    def test_refused_key_is_reprompted_at_most_three_times_and_removed(self):
        class Prompter:
            def __init__(self):
                self.values = iter((_FAKE_DDD_KEY,) * 3)

            def secret(self, unused):
                return next(self.values)

            def emphasise(self, text):
                return text

        args = cli.build_parser().parse_args(["--setup"])
        environment = {}
        messages = []
        refused = wizard.Check(
            "ddd-api-key", wizard.INVALID, "synthetic", None,
            wizard.DDD_KEY_VARIABLE, None, False)
        with mock.patch.object(cli, "_check_credential",
                               lambda *unused: refused):
            self.assertFalse(cli._ask_for_verified_key(
                wizard.DDD_KEY_VARIABLE, "step", "loss", wizard.DDD_SIGNUP_URL,
                args, lambda message, level="info": messages.append(message),
                environment, Prompter()))
        self.assertNotIn(wizard.DDD_KEY_VARIABLE, environment)
        self.assertEqual(3, sum("was refused" in message for message in messages))
        self.assertNotIn(_FAKE_DDD_KEY, "\n".join(messages))

    def test_guided_setup_keeps_secrets_in_the_prompt_and_saves_valid_values(self):
        class Prompter:
            interactive = True
            colour = False

            def __init__(self):
                self.secrets = iter((_FAKE_DDD_KEY, _FAKE_OS_KEY, "a-password"))
                self.texts = iter(("someone",))

            def secret(self, unused):
                return next(self.secrets)

            def text(self, unused):
                return next(self.texts)

            def confirm(self, unused):
                raise AssertionError("--save must not ask for confirmation")

            def emphasise(self, text):
                return text

        def collected(environ=None, **unused):
            environ = environ or {}
            def check(name, variable=None):
                return wizard.Check(
                    name, wizard.OK if environ.get(variable) else wizard.MISSING,
                    "synthetic", None, variable, None, False)
            return [
                wizard.Check("python", wizard.OK, "synthetic", None, None, None, True),
                wizard.Check("ffmpeg", wizard.OK, "synthetic", None, None, None, False),
                check("ddd-api-key", wizard.DDD_KEY_VARIABLE),
                check("opensubtitles-api-key", "OPENSUBTITLES_API_KEY"),
                wizard.Check(
                    "opensubtitles-login",
                    wizard.OK if (environ.get("OPENSUBTITLES_USERNAME")
                                  and environ.get("OPENSUBTITLES_PASSWORD"))
                    else wizard.MISSING,
                    "synthetic", None, "OPENSUBTITLES_USERNAME", None, False),
            ]

        saved = []
        args = cli.build_parser().parse_args(["--setup", "--save"])
        environment = {}
        messages = []
        with mock.patch.object(wizard, "collect", collected), \
             mock.patch.object(cli, "_check_credential",
                               lambda variable, environ: wizard.Check(
                                   variable, wizard.OK, "synthetic", None,
                                   variable, None, False)), \
             mock.patch.object(cli.keychain, "backend", lambda: cli.keychain.MACOS), \
             mock.patch.object(cli.keychain, "store_value",
                               lambda variable, value, store=None:
                               saved.append((variable, value, store)) or True), \
             mock.patch.object(cli.os, "environ", environment):
            cli.run(args, lambda message, level="info": messages.append(message), {},
                    setup_prompter=Prompter())
        self.assertEqual({
            "DDD_API_KEY", "OPENSUBTITLES_API_KEY", "OPENSUBTITLES_USERNAME",
            "OPENSUBTITLES_PASSWORD"}, {entry[0] for entry in saved})
        self.assertTrue(any("1/3" in message for message in messages))
        self.assertTrue(any("Jaws" in message and "10154" in message
                            for message in messages))
        self.assertNotIn(_FAKE_DDD_KEY, "\n".join(messages))
        self.assertNotIn(_FAKE_OS_KEY, "\n".join(messages))


class PromptTests(unittest.TestCase):
    def test_colour_needs_a_tty_and_respects_no_color_and_dumb_term(self):
        self.assertTrue(wizard.SetupPrompter(
            isatty=lambda: True, environ={"TERM": "xterm"}).colour)
        self.assertFalse(wizard.SetupPrompter(
            isatty=lambda: False, environ={"TERM": "xterm"}).colour)
        self.assertFalse(wizard.SetupPrompter(
            isatty=lambda: True, environ={"NO_COLOR": "1", "TERM": "xterm"}).colour)
        self.assertFalse(wizard.SetupPrompter(
            isatty=lambda: True, environ={"TERM": "dumb"}).colour)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
