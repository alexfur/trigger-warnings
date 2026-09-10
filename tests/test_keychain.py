"""Unit tests for optional keychain storage.

No test touches a real keychain. The subprocess layer is replaced, so these
assert the contract this module promises rather than the behaviour of any one
operating system's tool.
"""

import unittest
from unittest import mock

from trigger_warnings import keychain


_SECRET = "a-credential-that-must-never-reach-argv"


class _Completed:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _which_all(name):
    return "/usr/bin/" + name


def _which_none(unused):
    return None


def _which_only(allowed):
    return lambda name: ("/usr/bin/" + name) if name == allowed else None


class BackendTests(unittest.TestCase):
    def test_macos_uses_security(self):
        self.assertEqual(keychain.MACOS, keychain.backend(
            which=_which_all, system=lambda: "Darwin"))

    def test_linux_uses_secret_tool(self):
        self.assertEqual(keychain.LIBSECRET, keychain.backend(
            which=_which_only("secret-tool"), system=lambda: "Linux"))

    def test_windows_is_unsupported_rather_than_half_supported(self):
        """A file fallback would restore exactly the risk this design avoids."""
        self.assertIsNone(keychain.backend(
            which=_which_none, system=lambda: "Windows"))

    def test_no_tool_means_no_backend(self):
        self.assertIsNone(keychain.backend(
            which=_which_none, system=lambda: "Darwin"))


class SecrecyTests(unittest.TestCase):
    def test_the_value_is_never_a_command_line_argument(self):
        """argv is readable by other processes owned by the same user."""
        for store in (keychain.MACOS, keychain.LIBSECRET):
            seen = {}

            def fake(command, stdin_text=None):
                seen["command"] = command
                seen["stdin"] = stdin_text
                return _Completed()

            with mock.patch.object(keychain, "_run", fake):
                self.assertTrue(
                    keychain.store_value("DDD_API_KEY", _SECRET, store=store))
            for token in seen["command"]:
                self.assertNotIn(_SECRET, token)
            self.assertIn(_SECRET, seen["stdin"])

    def test_macos_receives_the_value_twice_because_it_asks_twice(self):
        seen = {}

        def fake(command, stdin_text=None):
            seen["stdin"] = stdin_text
            return _Completed()

        with mock.patch.object(keychain, "_run", fake):
            keychain.store_value("DDD_API_KEY", _SECRET, store=keychain.MACOS)
        self.assertEqual("{0}\n{0}\n".format(_SECRET), seen["stdin"])


class GetTests(unittest.TestCase):
    def test_a_stored_value_comes_back_without_its_trailing_newline(self):
        with mock.patch.object(keychain, "_run",
                               lambda *a, **k: _Completed(0, _SECRET + "\n")):
            self.assertEqual(_SECRET,
                             keychain.get("DDD_API_KEY", store=keychain.MACOS))

    def test_a_missing_entry_is_none_not_an_error(self):
        with mock.patch.object(keychain, "_run", lambda *a, **k: _Completed(44, "")):
            self.assertIsNone(keychain.get("DDD_API_KEY", store=keychain.MACOS))

    def test_an_unusable_tool_is_none_not_an_error(self):
        """A refused access prompt and a broken tool both mean 'look elsewhere'."""
        with mock.patch.object(keychain, "_run", lambda *a, **k: None):
            self.assertIsNone(keychain.get("DDD_API_KEY", store=keychain.MACOS))

    def test_no_backend_reads_nothing(self):
        with mock.patch.object(keychain, "backend", lambda *a, **k: None), \
             mock.patch.object(keychain, "_run",
                               mock.Mock(side_effect=AssertionError("must not run"))):
            self.assertIsNone(keychain.get("DDD_API_KEY"))

    def test_an_empty_stored_value_counts_as_absent(self):
        with mock.patch.object(keychain, "_run", lambda *a, **k: _Completed(0, "\n")):
            self.assertIsNone(keychain.get("DDD_API_KEY", store=keychain.MACOS))


class HydrateTests(unittest.TestCase):
    def test_an_explicit_export_is_never_overwritten(self):
        """An export is the caller naming the credential for this run."""
        environ = {"DDD_API_KEY": "from-the-shell"}
        with mock.patch.object(keychain, "get", lambda *a, **k: "from-the-store"):
            sources = keychain.hydrate(environ=environ, store=keychain.MACOS,
                                       variables=("DDD_API_KEY",))
        self.assertEqual("from-the-shell", environ["DDD_API_KEY"])
        self.assertEqual(keychain.ENVIRONMENT, sources["DDD_API_KEY"])

    def test_a_blank_is_filled_from_the_store_and_reported_as_such(self):
        environ = {}
        with mock.patch.object(keychain, "get", lambda *a, **k: "from-the-store"):
            sources = keychain.hydrate(environ=environ, store=keychain.MACOS,
                                       variables=("DDD_API_KEY",))
        self.assertEqual("from-the-store", environ["DDD_API_KEY"])
        self.assertEqual(keychain.KEYCHAIN, sources["DDD_API_KEY"])

    def test_whitespace_only_counts_as_blank(self):
        environ = {"DDD_API_KEY": "   "}
        with mock.patch.object(keychain, "get", lambda *a, **k: "from-the-store"):
            keychain.hydrate(environ=environ, store=keychain.MACOS,
                             variables=("DDD_API_KEY",))
        self.assertEqual("from-the-store", environ["DDD_API_KEY"])

    def test_without_a_backend_the_environment_is_untouched(self):
        environ = {}
        # None requests backend discovery; isolate it from the real keychain.
        with mock.patch.object(keychain, "backend", return_value=None), \
                mock.patch.object(keychain, "get") as get:
            sources = keychain.hydrate(environ=environ, store=None,
                                       variables=("DDD_API_KEY",))
        get.assert_not_called()
        self.assertEqual({}, environ)
        self.assertEqual({}, sources)

    def test_an_absent_credential_appears_in_no_source(self):
        environ = {}
        with mock.patch.object(keychain, "get", lambda *a, **k: None):
            sources = keychain.hydrate(environ=environ, store=keychain.MACOS,
                                       variables=("DDD_API_KEY",))
        self.assertEqual({}, sources)
        self.assertNotIn("DDD_API_KEY", environ)

    def test_every_known_variable_is_covered(self):
        """The wizard, the hydrator and --save must not drift apart."""
        self.assertEqual(
            {"DDD_API_KEY", "OPENSUBTITLES_API_KEY",
             "OPENSUBTITLES_USERNAME", "OPENSUBTITLES_PASSWORD",
             "GEMINI_API_KEY"},
            set(keychain.VARIABLES))


class ForgetTests(unittest.TestCase):
    def test_removal_reports_success(self):
        with mock.patch.object(keychain, "_run", lambda *a, **k: _Completed(0)):
            self.assertTrue(keychain.forget("DDD_API_KEY", store=keychain.MACOS))

    def test_no_backend_removes_nothing(self):
        with mock.patch.object(keychain, "backend", lambda *a, **k: None), \
             mock.patch.object(keychain, "_run",
                               mock.Mock(side_effect=AssertionError("must not run"))):
            self.assertFalse(keychain.forget("DDD_API_KEY"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
