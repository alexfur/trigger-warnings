"""Unit tests for the opt-in OpenSubtitles REST API v1 source.

Every HTTP response here is synthetic. These tests never contact the public API,
never use a real API key, and never log in. The fake openers below assert on
request *shape* -- URL, method, header names, body fields -- and deliberately
never echo a credential back into a message, because a test double that prints
a password is the same leak as production code that does.

The credential rules under test are stricter than the DoesTheDogDie source's:

*   The API key may come from ``--os-api-key`` or ``OPENSUBTITLES_API_KEY``.
*   The username and password come from the environment and nowhere else. There
    is no argv flag for either, and the plaintext ``~/.opensubtitles.json`` that
    other tools keep is never read.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from trigger_warnings import __version__, cli, core, opensubtitles


SRT_BODY = (
    "1\n"
    "00:00:01,000 --> 00:00:04,000\n"
    "Harmless dialogue.\n"
    "\n"
    "2\n"
    "00:00:05,000 --> 00:00:08,000\n"
    "More harmless dialogue.\n"
)

API_KEY = "test-api-key"
USERNAME = "test-user"
PASSWORD = "test-password"
TOKEN = "test-session-token"
LINK_TOKEN = "8f3a9c2b1d4e5f60718293a4b5c6d7e8"
LINK = "https://example-cdn.invalid/download/{}/dialogue.srt".format(LINK_TOKEN)

SEARCH_PAYLOAD = json.dumps({
    "total_pages": 1,
    "total_count": 1,
    "per_page": 60,
    "page": 1,
    "data": [{
        "id": "7061050",
        "type": "subtitle",
        "attributes": {
            "subtitle_id": "7061050",
            "language": "en",
            "download_count": 1234,
            "hearing_impaired": False,
            "from_trusted": True,
            "ai_translated": False,
            "machine_translated": False,
            "upload_date": "2021-03-04T12:00:00Z",
            "release": "The.Thing.1982.1080p.BluRay",
            "feature_details": {
                "feature_type": "Movie",
                "title": "The Thing",
                "year": 1982,
                "imdb_id": 84787,
                "season_number": None,
                "episode_number": None,
            },
            "files": [{
                "file_id": 7061050,
                "cd_number": 1,
                "file_name": "The.Thing.1982.srt",
            }],
        },
    }],
})

LOGIN_PAYLOAD = json.dumps({
    "user": {"allowed_downloads": 100, "level": "Sub leecher", "vip": True},
    "token": TOKEN,
    "status": 200,
    "base_url": "vip-api.opensubtitles.com",
})

DOWNLOAD_PAYLOAD = json.dumps({
    "link": LINK,
    "file_name": "The.Thing.1982.srt",
    "requests": 3,
    "remaining": 97,
    "message": "Your quota will be renewed soon",
    "reset_time": "23 hours",
})


class _Response:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self, limit=None):
        self.limit = limit
        return self.payload if limit is None else self.payload[:limit]


class _Api:
    """A synthetic OpenSubtitles endpoint that records every request it is sent.

    It records the ``Request`` objects themselves so a test can assert on the
    header *names* and the body *fields*. Nothing here formats a credential
    into an assertion message.
    """

    def __init__(self, search=SEARCH_PAYLOAD, login=LOGIN_PAYLOAD,
                 download=DOWNLOAD_PAYLOAD, subtitle=SRT_BODY, link=LINK):
        self.search = search
        self.login = login
        self.download = download
        self.subtitle = subtitle
        self.link = link
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        url = request.full_url
        if url == self.link:
            return _Response(self.subtitle)
        if url.endswith("/login"):
            return _Response(self.login)
        if url.endswith("/download"):
            return _Response(self.download)
        if "/subtitles?" in url:
            return _Response(self.search)
        raise AssertionError("unexpected URL path: {}".format(url.split("?")[0]))

    # -- helpers ---------------------------------------------------------

    def urls(self):
        return [request.full_url for request in self.requests]

    def paths(self):
        return [request.full_url.split("?")[0] for request in self.requests]

    def methods(self):
        return [request.get_method() for request in self.requests]

    def headers(self, index):
        return dict(
            (key.lower(), value)
            for key, value in self.requests[index].header_items()
        )

    def body(self, index):
        return json.loads(self.requests[index].data.decode("utf-8"))


class UserAgentTests(unittest.TestCase):
    """OpenSubtitles rejects a request whose User-Agent is not ``Name vX.Y.Z``."""

    def test_user_agent_is_the_required_format_and_the_live_version(self):
        self.assertEqual(
            "trigger-warnings v{}".format(__version__), opensubtitles.USER_AGENT
        )

    def test_user_agent_tracks_the_package_version_rather_than_a_literal(self):
        """A hardcoded version goes stale silently; the API only sees the lie."""
        self.assertIn(__version__, opensubtitles.USER_AGENT)
        self.assertNotIn("0.2", opensubtitles.USER_AGENT)

    def test_the_does_the_dog_die_user_agent_is_not_stale_either(self):
        """The DDD client shipped a frozen ``0.2`` long after 0.3 was released."""
        from trigger_warnings import ddd

        self.assertIn(__version__, ddd.USER_AGENT)


class SearchTests(unittest.TestCase):
    def test_search_returns_candidates_without_selecting_one(self):
        api = _Api()

        found = opensubtitles.search_subtitles(API_KEY, "The Thing", opener=api)

        self.assertEqual(1, len(found.candidates))
        candidate = found.candidates[0]
        self.assertEqual(7061050, candidate["fileId"])
        self.assertEqual("The.Thing.1982.srt", candidate["fileName"])
        self.assertEqual("en", candidate["language"])
        self.assertEqual("The Thing", candidate["title"])
        self.assertEqual(1982, candidate["year"])
        self.assertEqual("Movie", candidate["featureType"])
        self.assertTrue(candidate["fromTrusted"])

    def test_search_sends_only_one_request_and_never_logs_in(self):
        """A search needs an API key. Sending a password to list titles would
        expand the blast radius of a typo'd title into a login attempt."""
        api = _Api()

        opensubtitles.search_subtitles(API_KEY, "The Thing", opener=api)

        self.assertEqual(1, len(api.requests))
        self.assertNotIn(
            opensubtitles.API_BASE_URL + "/login", api.urls()
        )
        self.assertEqual(["GET"], api.methods())

    def test_search_sends_the_api_key_header_and_required_user_agent(self):
        api = _Api()

        opensubtitles.search_subtitles(API_KEY, "The Thing", opener=api)

        headers = api.headers(0)
        self.assertEqual(API_KEY, headers["api-key"])
        self.assertEqual(opensubtitles.USER_AGENT, headers["user-agent"])
        self.assertNotIn("authorization", headers)

    def test_search_query_uses_the_documented_parameter_names(self):
        api = _Api()

        opensubtitles.search_subtitles(
            API_KEY, "Twin Peaks", year=1990, season=2, episode=7,
            language="fr", opener=api,
        )

        url = api.urls()[0]
        self.assertIn("query=Twin+Peaks", url)
        self.assertIn("year=1990", url)
        self.assertIn("season_number=2", url)
        self.assertIn("episode_number=7", url)
        self.assertIn("languages=fr", url)

    def test_search_defaults_to_english_without_inventing_other_filters(self):
        api = _Api()

        opensubtitles.search_subtitles(API_KEY, "The Thing", opener=api)

        url = api.urls()[0]
        self.assertIn("languages=en", url)
        self.assertNotIn("season_number", url)
        self.assertNotIn("episode_number", url)
        self.assertNotIn("year=", url)

    def test_search_reports_an_entry_with_no_downloadable_file(self):
        """A subtitle record with no files cannot be fetched by file id, so it
        is reported as omitted rather than listed as a candidate that fails."""
        api = _Api(search=json.dumps({"data": [{
            "id": "1", "type": "subtitle",
            "attributes": {"language": "en", "files": []},
        }]}))

        found = opensubtitles.search_subtitles(API_KEY, "Obscure", opener=api)

        self.assertEqual([], found.candidates)
        self.assertTrue(found.notes)
        self.assertIn("no downloadable file", found.notes[0])

    def test_a_multi_part_subtitle_lists_every_part_as_its_own_file_id(self):
        api = _Api(search=json.dumps({"data": [{
            "id": "1", "type": "subtitle",
            "attributes": {
                "language": "en",
                "files": [
                    {"file_id": 11, "cd_number": 1, "file_name": "part1.srt"},
                    {"file_id": 12, "cd_number": 2, "file_name": "part2.srt"},
                ],
            },
        }]}))

        found = opensubtitles.search_subtitles(API_KEY, "Long Film", opener=api)

        self.assertEqual([11, 12], [c["fileId"] for c in found.candidates])
        self.assertEqual([1, 2], [c["cdNumber"] for c in found.candidates])

    def test_an_empty_result_is_not_evidence_the_title_has_no_subtitles(self):
        api = _Api(search=json.dumps({"data": []}))

        found = opensubtitles.search_subtitles(API_KEY, "Nothing", opener=api)

        self.assertEqual([], found.candidates)

    def test_search_rejects_a_missing_key_and_an_empty_query(self):
        api = _Api()
        for key, query, expected in (
            (None, "The Thing", "API key"),
            ("   ", "The Thing", "API key"),
            (API_KEY, "  ", "must not be empty"),
            (API_KEY, None, "must not be empty"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(opensubtitles.OpenSubtitlesError, expected):
                    opensubtitles.search_subtitles(key, query, opener=api)
        self.assertEqual([], api.requests, "a rejected search still sent a request")

    def test_search_rejects_malformed_json_and_a_malformed_result(self):
        for payload, expected in (
            ("not json", "invalid JSON"),
            (json.dumps({"data": "nope"}), "invalid search results"),
            (json.dumps({"data": [{"attributes": {"files": [
                {"file_id": "not-an-int", "file_name": "x.srt"}]}}]}),
             "valid file id"),
        ):
            with self.subTest(expected=expected):
                api = _Api(search=payload)
                with self.assertRaisesRegex(opensubtitles.OpenSubtitlesError, expected):
                    opensubtitles.search_subtitles(API_KEY, "The Thing", opener=api)


class CredentialTests(unittest.TestCase):
    """The three environment variables are the only credential source."""

    def test_full_credentials_come_from_the_environment(self):
        environ = {
            "OPENSUBTITLES_API_KEY": API_KEY,
            "OPENSUBTITLES_USERNAME": USERNAME,
            "OPENSUBTITLES_PASSWORD": PASSWORD,
        }

        creds = opensubtitles.credentials_from_environment(environ=environ)

        self.assertEqual(API_KEY, creds.api_key)
        self.assertEqual(USERNAME, creds.username)
        self.assertEqual(PASSWORD, creds.password)

    def test_an_explicit_api_key_overrides_the_environment_key_only(self):
        environ = {
            "OPENSUBTITLES_API_KEY": "environment-key",
            "OPENSUBTITLES_USERNAME": USERNAME,
            "OPENSUBTITLES_PASSWORD": PASSWORD,
        }

        creds = opensubtitles.credentials_from_environment(
            api_key="flag-key", environ=environ
        )

        self.assertEqual("flag-key", creds.api_key)
        self.assertEqual(USERNAME, creds.username)

    def test_each_missing_credential_is_named_without_printing_the_others(self):
        full = {
            "OPENSUBTITLES_API_KEY": API_KEY,
            "OPENSUBTITLES_USERNAME": USERNAME,
            "OPENSUBTITLES_PASSWORD": PASSWORD,
        }
        for missing in sorted(full):
            with self.subTest(missing=missing):
                environ = dict(full)
                del environ[missing]
                with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
                    opensubtitles.credentials_from_environment(environ=environ)
                message = str(caught.exception)
                self.assertIn(missing, message)
                for secret in (API_KEY, USERNAME, PASSWORD):
                    self.assertNotIn(secret, message)

    def test_a_blank_credential_counts_as_missing(self):
        environ = {
            "OPENSUBTITLES_API_KEY": API_KEY,
            "OPENSUBTITLES_USERNAME": "   ",
            "OPENSUBTITLES_PASSWORD": PASSWORD,
        }
        with self.assertRaisesRegex(
            opensubtitles.OpenSubtitlesError, "OPENSUBTITLES_USERNAME"
        ):
            opensubtitles.credentials_from_environment(environ=environ)

    def test_no_code_path_reaches_for_a_credential_file(self):
        """Other subtitle tools keep credentials in a plaintext config file in
        the home directory. Reading one would widen this tool's credential
        surface to a file the user never pointed at.

        The check walks the AST rather than grepping the text, so the module
        stays free to *document* the rule in a docstring -- which it does --
        without the documentation being mistaken for an implementation of the
        thing it forbids.
        """
        import ast

        tree = ast.parse(Path(opensubtitles.__file__).read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    docstrings.add(doc)

        called = set()
        literals = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                called.add(node.attr)
            elif isinstance(node, ast.Name):
                called.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value not in docstrings:
                    literals.append(node.value)

        for forbidden in ("expanduser", "expandvars", "home", "read_text",
                          "read_bytes", "load", "Path"):
            self.assertNotIn(
                forbidden, called,
                "the module reaches for the filesystem via {!r}".format(forbidden),
            )
        for literal in literals:
            self.assertNotIn("~", literal, "a home-directory path was named")
            self.assertNotIn(
                ".json", literal.lower(), "a JSON file on disk was named"
            )

    def test_no_config_file_is_opened_during_a_real_download(self):
        opened = []
        real_open = open

        def recording_open(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        api = _Api()
        with mock.patch("builtins.open", recording_open):
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                7061050, opener=api,
            )

        self.assertEqual(
            [], [path for path in opened if "opensubtitles" in path.lower()]
        )


class DownloadTests(unittest.TestCase):
    def _credentials(self):
        return opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD)

    def test_download_logs_in_then_downloads_then_fetches_the_link(self):
        api = _Api()

        result = opensubtitles.download_subtitle(
            self._credentials(), 7061050, opener=api
        )

        self.assertEqual(SRT_BODY, result.text)
        self.assertEqual("The.Thing.1982.srt", result.file_name)
        self.assertEqual(97, result.remaining)
        self.assertEqual(
            [
                opensubtitles.API_BASE_URL + "/login",
                "https://vip-api.opensubtitles.com/api/v1/download",
                LINK,
            ],
            api.urls(),
        )
        self.assertEqual(["POST", "POST", "GET"], api.methods())

    def test_the_returned_base_url_is_honoured_for_the_download(self):
        """The login response redirects a VIP account to another host. Ignoring
        it works until it does not, and then it fails only for that account."""
        api = _Api(login=json.dumps({
            "token": TOKEN, "base_url": "vip-api.opensubtitles.com",
        }))

        opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        self.assertEqual(
            "https://vip-api.opensubtitles.com/api/v1/download", api.urls()[1]
        )

    def test_a_base_url_that_already_carries_a_scheme_is_not_doubled(self):
        api = _Api(login=json.dumps({
            "token": TOKEN, "base_url": "https://vip-api.opensubtitles.com",
        }))

        opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        self.assertEqual(
            "https://vip-api.opensubtitles.com/api/v1/download", api.urls()[1]
        )

    def test_a_login_with_no_base_url_stays_on_the_default_host(self):
        api = _Api(login=json.dumps({"token": TOKEN}))

        opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        self.assertEqual(opensubtitles.API_BASE_URL + "/download", api.urls()[1])

    def test_a_non_opensubtitles_base_url_is_refused(self):
        """``base_url`` chooses the host the password-derived token is sent to.
        An unexpected host there would redirect the token off-service."""
        api = _Api(login=json.dumps({
            "token": TOKEN, "base_url": "attacker.invalid",
        }))

        with self.assertRaisesRegex(opensubtitles.OpenSubtitlesError, "base_url"):
            opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        self.assertEqual(1, len(api.requests), "the download was still attempted")

    def test_the_download_endpoint_is_posted_exactly_once(self):
        """Each POST spends one of a small daily quota, so a retry is not free."""
        api = _Api()

        opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        self.assertEqual(
            1, api.paths().count("https://vip-api.opensubtitles.com/api/v1/download")
        )

    def test_the_download_body_names_the_file_id_and_asks_for_srt(self):
        api = _Api()

        opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        self.assertEqual({"file_id": 7061050, "sub_format": "srt"}, api.body(1))

    def test_the_login_body_carries_the_credentials_and_nothing_else(self):
        api = _Api()

        opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        self.assertEqual(
            {"username": USERNAME, "password": PASSWORD}, api.body(0)
        )

    def test_the_download_sends_the_api_key_and_the_bearer_token(self):
        api = _Api()

        opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        headers = api.headers(1)
        self.assertEqual(API_KEY, headers["api-key"])
        self.assertEqual("Bearer " + TOKEN, headers["authorization"])
        self.assertEqual(opensubtitles.USER_AGENT, headers["user-agent"])
        self.assertEqual("application/json", headers["content-type"])

    def test_the_temporary_link_is_fetched_without_credentials_attached(self):
        """The link already carries its own token. Re-sending the API key and
        the session token to whatever host it names leaks both."""
        api = _Api()

        opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        headers = api.headers(2)
        self.assertNotIn("api-key", headers)
        self.assertNotIn("authorization", headers)
        self.assertEqual(opensubtitles.USER_AGENT, headers["user-agent"])

    def test_the_temporary_link_is_not_returned_to_the_caller(self):
        """A temporary link is a bearer credential for one file. Handing it back
        invites it into a log, a provenance sidecar or a bug report."""
        api = _Api()

        result = opensubtitles.download_subtitle(
            self._credentials(), 7061050, opener=api
        )

        self.assertNotIn(LINK, repr(result))
        for value in result._asdict().values():
            self.assertNotIn(LINK, str(value))

    def test_a_login_response_with_no_token_fails_before_any_download(self):
        api = _Api(login=json.dumps({"status": 200}))

        with self.assertRaisesRegex(opensubtitles.OpenSubtitlesError, "token"):
            opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

        self.assertEqual(1, len(api.requests))

    def test_a_download_response_with_no_link_fails_loudly(self):
        api = _Api(download=json.dumps({"file_name": "x.srt", "remaining": 5}))

        with self.assertRaisesRegex(opensubtitles.OpenSubtitlesError, "link"):
            opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

    def test_a_download_link_on_an_unexpected_scheme_is_refused(self):
        api = _Api(download=json.dumps({"link": "file:///etc/passwd"}), link=None)

        with self.assertRaisesRegex(opensubtitles.OpenSubtitlesError, "https"):
            opensubtitles.download_subtitle(self._credentials(), 7061050, opener=api)

    def test_download_rejects_a_file_id_that_is_not_a_positive_integer(self):
        api = _Api()
        for file_id in (0, -1, "7061050", True, None):
            with self.subTest(file_id=file_id):
                with self.assertRaisesRegex(
                    opensubtitles.OpenSubtitlesError, "file id"
                ):
                    opensubtitles.download_subtitle(
                        self._credentials(), file_id, opener=api
                    )
        self.assertEqual([], api.requests)

    def test_download_requires_every_credential(self):
        api = _Api()
        for creds in (
            opensubtitles.Credentials("", USERNAME, PASSWORD),
            opensubtitles.Credentials(API_KEY, "", PASSWORD),
            opensubtitles.Credentials(API_KEY, USERNAME, ""),
        ):
            with self.subTest(creds=creds._fields):
                with self.assertRaises(opensubtitles.OpenSubtitlesError):
                    opensubtitles.download_subtitle(creds, 7061050, opener=api)
        self.assertEqual([], api.requests)


class BaseUrlHostTests(unittest.TestCase):
    """``base_url`` decides where a password-derived token is sent.

    It is server-controlled, so the verdict has to be taken on the PARSED host.
    Checking a suffix against the raw string is satisfiable by putting the real
    host at the front and the expected suffix anywhere after it, which is a
    credential-exfiltration bug rather than a validation nicety.
    """

    REFUSED = (
        "evil.example/api.opensubtitles.com",
        "evil.example/?x=.opensubtitles.com",
        "evil.example#.opensubtitles.com",
        "api.opensubtitles.com@evil.example",
        "evil.example:443/.opensubtitles.com",
        "opensubtitles.com.evil.example",
        "notopensubtitles.com",
        "//evil.example",
        "http://vip-api.opensubtitles.com",
        "attacker.invalid",
    )

    def test_a_host_that_only_looks_right_is_refused(self):
        for value in self.REFUSED:
            with self.subTest(base_url=value):
                with self.assertRaises(opensubtitles.OpenSubtitlesError):
                    opensubtitles._resolve_base_url(value, (API_KEY,))

    def test_the_documented_shapes_still_work(self):
        for value, expected in (
            (None, opensubtitles.API_BASE_URL),
            ("", opensubtitles.API_BASE_URL),
            ("vip-api.opensubtitles.com",
             "https://vip-api.opensubtitles.com/api/v1"),
            ("https://vip-api.opensubtitles.com",
             "https://vip-api.opensubtitles.com/api/v1"),
            ("opensubtitles.com", "https://opensubtitles.com/api/v1"),
            ("VIP-API.OpenSubtitles.COM",
             "https://vip-api.opensubtitles.com/api/v1"),
        ):
            with self.subTest(base_url=value):
                self.assertEqual(
                    expected, opensubtitles._resolve_base_url(value, (API_KEY,))
                )

    def test_userinfo_is_discarded_from_the_rebuilt_url(self):
        """The host is the part after ``@``, so this one is genuinely on the
        right server, but the credentials in the userinfo must not be carried
        into the URL that gets used."""
        resolved = opensubtitles._resolve_base_url(
            "evil.example@api.opensubtitles.com", (API_KEY,)
        )

        self.assertEqual(opensubtitles.API_BASE_URL, resolved)
        self.assertNotIn("@", resolved)
        self.assertNotIn("evil.example", resolved)

    def test_the_token_never_reaches_a_host_outside_the_service(self):
        """The end-to-end proof: drive a real download through a hostile
        ``base_url`` and assert nothing is sent off-service."""
        api = _Api(login=json.dumps({
            "token": TOKEN, "base_url": "evil.example/?x=.opensubtitles.com",
        }))

        with self.assertRaises(opensubtitles.OpenSubtitlesError):
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                7061050, opener=api,
            )

        for request in api.requests:
            host = urlsplit(request.full_url).hostname or ""
            self.assertTrue(
                host.endswith("opensubtitles.com") or host.endswith(".invalid"),
                "a request went to {}".format(host),
            )
            if not host.endswith("opensubtitles.com"):
                headers = dict(
                    (k.lower(), v) for k, v in request.header_items()
                )
                self.assertNotIn("api-key", headers)
                self.assertNotIn("authorization", headers)


class DownloadedBodyIsNeverEchoedTests(unittest.TestCase):
    """The parse diagnosis must not quote bytes that arrived over the network.

    ``core.parse_srt`` quotes the line it rejected, which is exactly right for a
    local file the user can open and wrong for a CDN response. Scrubbing that
    body is a denylist over attacker-controlled bytes, and a denylist loses to
    the next encoding: redacting the exact link missed a percent-encoded echo,
    redacting encoded forms missed a case-folded one. So the body is not echoed
    at all, and the remedy the user needs is identical either way.
    """

    MARKER = "UNIQUE-BODY-MARKER-9f2c1d"

    def _download(self, body):
        api = _Api(subtitle=body)
        return opensubtitles.download_subtitle(
            opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
            7061050, opener=api,
        )

    def test_no_part_of_a_rejected_body_reaches_the_message(self):
        with self.assertRaises(core.TriggerWarningsError) as caught:
            self._download("<html>" + self.MARKER + "</html>")

        self.assertNotIn(self.MARKER, str(caught.exception))

    def test_a_case_folded_token_cannot_be_recovered_either(self):
        """The one encoding the scrubber still lost to, made unreachable by
        not echoing the body rather than by widening the denylist."""
        with self.assertRaises(core.TriggerWarningsError) as caught:
            self._download("Invalid token: " + LINK_TOKEN.upper())

        message = str(caught.exception)
        self.assertNotIn(LINK_TOKEN.upper(), message)
        self.assertNotIn(LINK_TOKEN, message)

    def test_the_message_still_says_what_to_do_about_it(self):
        with self.assertRaises(core.TriggerWarningsError) as caught:
            self._download("<html>nope</html>")

        message = str(caught.exception)
        self.assertIn("SRT", message)
        self.assertIn("--os-search", message)


class StrictSubtitleTests(unittest.TestCase):
    """What arrives over the wire is validated before it can be published."""

    def _download(self, subtitle):
        api = _Api(subtitle=subtitle)
        return opensubtitles.download_subtitle(
            opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
            7061050, opener=api,
        )

    def test_a_non_utf8_body_is_refused_rather_than_guessed_at(self):
        """Latin-1 subtitles are common. Guessing an encoding silently mangles
        every accented line, which is worse than refusing the file."""
        with self.assertRaisesRegex(opensubtitles.OpenSubtitlesError, "UTF-8"):
            self._download("Café dialogue".encode("latin-1"))

    def test_a_utf8_bom_is_accepted_because_srt_files_carry_one(self):
        result = self._download(b"\xef\xbb\xbf" + SRT_BODY.encode("utf-8"))

        self.assertTrue(result.text.lstrip("﻿").startswith("1\n"))
        core.parse_srt(result.text)

    def test_a_body_that_is_not_srt_is_refused_before_it_is_returned(self):
        """Every one of these is refused, and none of them is quoted back.

        An earlier version asserted the parse diagnosis appeared in the message.
        That assertion was the leak: the diagnosis quotes the offending line,
        and the line comes from the network.
        """
        for payload in (
            "<html>Not found</html>",
            "",
            "1\n00:00:01,000 --> 00:00:00,000\nReversed.\n",
        ):
            with self.subTest(payload=payload[:20]):
                with self.assertRaises(core.TriggerWarningsError) as caught:
                    self._download(payload)
                message = str(caught.exception)
                self.assertIn("not valid SRT", message)
                if payload:
                    self.assertNotIn(payload.strip()[:15], message)

    def test_the_validated_text_parses_to_the_cues_that_were_sent(self):
        result = self._download(SRT_BODY)

        cues = core.parse_srt(result.text)
        self.assertEqual(2, len(cues))
        self.assertEqual(1000, cues[0].start_ms)
        self.assertEqual("Harmless dialogue.", cues[0].text)


class SecretLeakTests(unittest.TestCase):
    """No message this module produces may carry a credential or a link."""

    def _secrets(self):
        return (API_KEY, USERNAME, PASSWORD, TOKEN, LINK)

    def assert_clean(self, message):
        for secret in self._secrets():
            self.assertNotIn(
                secret, message,
                "a secret value reached a message the caller can log",
            )

    def test_an_http_failure_names_the_status_not_the_credentials(self):
        from urllib.error import HTTPError

        def failing(request, timeout=None):
            raise HTTPError(
                request.full_url, 401,
                "Unauthorized: {} / {}".format(API_KEY, PASSWORD),
                {"Api-Key": API_KEY}, None,
            )

        with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                7061050, opener=failing,
            )

        message = str(caught.exception)
        self.assertIn("401", message)
        self.assert_clean(message)

    def test_a_transport_failure_that_quotes_the_link_is_scrubbed(self):
        """``urlopen`` errors routinely interpolate the URL they were given, and
        the temporary link is itself a credential."""
        from urllib.error import URLError

        api = _Api()
        real_call = api.__call__

        def failing(request, timeout=None):
            if request.full_url == LINK:
                raise URLError("cannot reach {}".format(LINK))
            return real_call(request, timeout)

        with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                7061050, opener=failing,
            )

        self.assert_clean(str(caught.exception))

    def test_an_encoded_echo_of_the_link_still_hides_the_token(self):
        """Redacting only the exact URL string is not enough.

        A CDN does not reliably echo a URL back whole. It may percent-encode it,
        HTML-escape the separators, or name the token on its own. The token
        survives all three transformations intact, so matching the full link
        alone leaves the credential readable in every one of them.
        """
        from urllib.parse import quote

        bodies = {
            "percent-encoded": "could not fetch " + quote(LINK, safe=""),
            "html-escaped": LINK.replace("/", "&#47;"),
            "token named alone": "token " + LINK_TOKEN + " is not valid",
        }
        for name, body in bodies.items():
            with self.subTest(echo=name):
                api = _Api(subtitle=body)
                with self.assertRaises(core.TriggerWarningsError) as caught:
                    opensubtitles.download_subtitle(
                        opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                        7061050, opener=api,
                    )
                self.assertNotIn(
                    LINK_TOKEN, str(caught.exception),
                    "the download token survived an encoded echo",
                )

    def test_a_percent_encoded_credential_is_redacted_too(self):
        """A credential with URL-unsafe characters changes shape in a URL."""
        from urllib.parse import quote

        password = "p@ss/word+123"
        encoded = quote(password, safe="")
        api = _Api(subtitle="rejected credential " + encoded)

        with self.assertRaises(core.TriggerWarningsError) as caught:
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, password),
                7061050, opener=api,
            )

        self.assertNotIn(encoded, str(caught.exception))

    def test_scrub_does_not_blank_the_ordinary_words_of_a_message(self):
        """Redaction that blanks ordinary words makes an error useless.

        ``_scrub`` no longer runs over a downloaded body, but it still guards
        the transport paths, where the wording is ours and has to survive.
        """
        message = (
            "Could not reach OpenSubtitles for the subtitle download: "
            "the connection timed out after 30 seconds"
        )

        cleaned = opensubtitles._scrub(
            message, [API_KEY, USERNAME, PASSWORD, TOKEN, LINK]
        )

        self.assertEqual(message, cleaned)

    def test_scrub_still_redacts_where_it_is_relied_on(self):
        cleaned = opensubtitles._scrub(
            "could not fetch " + LINK, [LINK]
        )

        self.assertNotIn(LINK, cleaned)
        self.assertNotIn(LINK_TOKEN, cleaned)
        self.assertIn("could not fetch", cleaned)

    def test_a_cdn_error_document_that_quotes_the_link_is_scrubbed(self):
        """The regression test for a leak that shipped past the first review.

        ``core.parse_srt`` quotes the line it rejected. The body most likely to
        be rejected is a CDN error document, and S3-style error documents echo
        the requested URL back inside ``<Resource>``. That URL carries the
        download token, so an unguarded parse failure publishes a live
        credential into ``error.message``, which under ``--json`` is exactly the
        field an agent logs.

        The earlier version of this suite used ``<html>Not found</html>`` as its
        not-SRT body. That body contains no URL, so it proved the parse rejected
        junk while proving nothing at all about the leak.
        """
        cdn_error = (
            '<?xml version="1.0"?><Error><Code>AccessDenied</Code>'
            "<Resource>" + LINK + "</Resource></Error>"
        )
        api = _Api(subtitle=cdn_error)

        with self.assertRaises(core.TriggerWarningsError) as caught:
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                7061050, opener=api,
            )

        message = str(caught.exception)
        # Originally this asserted the parse diagnosis survived redaction. The
        # diagnosis is now dropped instead of scrubbed, which is the stronger
        # guarantee: there is no attacker-controlled text in the message to
        # redact in the first place. What the user needs is the remedy.
        self.assertIn("not valid SRT", message)
        self.assertIn("--os-search", message)
        self.assertNotIn("AccessDenied", message)
        self.assert_clean(message)

    def test_the_same_leak_is_not_reachable_through_the_command_line(self):
        """The CLI used to parse the body a second time, outside the guard."""
        cdn_error = (
            '<?xml version="1.0"?><Error><Code>AccessDenied</Code>'
            "<Resource>" + LINK + "</Resource></Error>"
        )
        output = Path(tempfile.mkdtemp()) / "dialogue.srt"
        environ = {
            "OPENSUBTITLES_API_KEY": API_KEY,
            "OPENSUBTITLES_USERNAME": USERNAME,
            "OPENSUBTITLES_PASSWORD": PASSWORD,
        }
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True):
            with mock.patch(
                "trigger_warnings.opensubtitles.urlopen", _Api(subtitle=cdn_error)
            ):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = cli.main([
                        "--os-file", "7061050", "--output", str(output), "--json",
                    ])

        self.assertEqual(cli.EXIT_ERROR, status)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assert_clean(payload["error"]["message"])
        self.assert_clean(out.getvalue())
        self.assertFalse(output.exists())

    def test_a_url_that_cannot_be_parsed_does_not_echo_the_link(self):
        """``Request`` raises ``ValueError: unknown url type: <the whole URL>``.
        The link is a bearer credential, so that construction must not sit
        outside the guard that scrubs it."""
        api = _Api()
        real_request = opensubtitles.Request

        def exploding(url, *args, **kwargs):
            if url == LINK:
                raise ValueError("unknown url type: {!r}".format(url))
            return real_request(url, *args, **kwargs)

        with mock.patch("trigger_warnings.opensubtitles.Request", exploding):
            with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
                opensubtitles.download_subtitle(
                    opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                    7061050, opener=api,
                )

        self.assert_clean(str(caught.exception))

    def test_an_unanticipated_transport_failure_reports_a_type_not_a_message(self):
        """http.client.IncompleteRead is neither an OSError nor a ValueError, so
        it would otherwise escape the scrubbing entirely."""
        from http.client import IncompleteRead

        api = _Api()
        real_call = api.__call__

        def failing(request, timeout=None):
            if request.full_url == LINK:
                raise IncompleteRead(PASSWORD.encode("utf-8"))
            return real_call(request, timeout)

        with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                7061050, opener=failing,
            )

        message = str(caught.exception)
        self.assertIn("IncompleteRead", message)
        self.assert_clean(message)

    def test_a_search_failure_does_not_echo_the_api_key(self):
        from urllib.error import HTTPError

        def failing(request, timeout=None):
            raise HTTPError(
                request.full_url, 403, "Forbidden " + API_KEY, {}, None
            )

        with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
            opensubtitles.search_subtitles(API_KEY, "The Thing", opener=failing)

        self.assertNotIn(API_KEY, str(caught.exception))

    def test_a_403_names_both_of_its_causes_and_neither_value(self):
        """403 means the API key was refused or the User-Agent was, and the two
        have different fixes. Naming only one sends the user the wrong way."""
        from urllib.error import HTTPError

        api = _Api()
        real_call = api.__call__

        def failing(request, timeout=None):
            if request.full_url.endswith("/login"):
                raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
            return real_call(request, timeout)

        with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                7061050, opener=failing,
            )

        message = str(caught.exception)
        self.assertIn("403", message)
        self.assertIn("API key", message)
        self.assertIn("User-Agent", message)
        self.assert_clean(message)

    def test_quota_exhaustion_is_reported_as_a_quota_problem(self):
        """A 406 here means the day's downloads are spent, not that the file is
        missing. Reporting it as a generic failure sends the user hunting."""
        from urllib.error import HTTPError

        api = _Api()
        real_call = api.__call__

        def failing(request, timeout=None):
            if request.full_url.endswith("/download"):
                raise HTTPError(request.full_url, 406, "Not Acceptable", {}, None)
            return real_call(request, timeout)

        with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
            opensubtitles.download_subtitle(
                opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
                7061050, opener=failing,
            )

        message = str(caught.exception)
        self.assertIn("quota", message.lower())
        self.assert_clean(message)


class HostileSearchDataTests(unittest.TestCase):
    """Every string in a search result was written by whoever uploaded it.

    The candidate table is the surface the user reads a file id off, so a row
    that can be forged is a choice that can be forged. Sanitising at the print
    site would leave the report line, the provenance sidecar and the ``--json``
    payload exposed, so it happens once, where the record is built.
    """

    def _search(self, attributes=None, entry_file=None):
        base = {
            "subtitle_id": "1",
            "language": "en",
            "from_trusted": False,
            "feature_details": {"title": "T", "year": 2020, "feature_type": "Movie"},
        }
        base.update(attributes or {})
        base["files"] = [dict(
            {"file_id": 42, "cd_number": 1, "file_name": "a.srt"}, **(entry_file or {})
        )]
        api = _Api(search=json.dumps({
            "data": [{"id": "1", "type": "subtitle", "attributes": base}]
        }))
        return opensubtitles.search_subtitles(API_KEY, "X", opener=api)

    def _table(self, attributes=None):
        api = _Api(search=json.dumps({"data": [{
            "id": "1", "type": "subtitle",
            "attributes": dict({
                "subtitle_id": "1", "language": "en", "from_trusted": False,
                "feature_details": {"title": "T", "year": 2020,
                                    "feature_type": "Movie"},
                "files": [{"file_id": 42, "cd_number": 1, "file_name": "a.srt"}],
            }, **(attributes or {})),
        }]}))
        out = io.StringIO()
        with mock.patch.dict(
            os.environ, {"OPENSUBTITLES_API_KEY": API_KEY}, clear=True
        ):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                with contextlib.redirect_stdout(out):
                    cli.run(
                        cli.build_parser().parse_args(["--os-search", "X"]),
                        lambda *a: None, {},
                    )
        return out.getvalue()

    def test_a_carriage_return_and_erase_line_cannot_redraw_a_row(self):
        """CR returns the cursor to column 0 and ESC[2K wipes the line just
        drawn, so the row the user sees is not the row that was printed."""
        evil = ("A\r\x1b[2K  99999999   en    2021   yes     "
                "Totally Trusted Release")

        table = self._table({"release": evil})

        self.assertNotIn("\r", table)
        self.assertNotIn("\x1b", table)

    def test_a_bare_newline_cannot_forge_an_extra_row(self):
        """No escape codes needed: one newline is a whole extra candidate, and
        an agent parsing the table line by line reads a file id that is not
        real."""
        table = self._table({
            "release": "Real\n  99999999   en    2021   yes     Forged"
        })

        rows = [line for line in table.split("\n") if line.strip()]
        self.assertEqual(3, len(rows), "expected a title, a header and one row")
        # The forged text survives as the CONTENT of the real row, which is
        # honest: it is what the uploader called the release. What must not
        # happen is it becoming a row of its own with a file id of its choosing.
        self.assertFalse(
            any(line.strip().startswith("99999999") for line in rows),
            "the forged file id became a row in its own right",
        )

    def test_a_right_to_left_override_is_stripped(self):
        """U+202E reverses the display of everything after it, which is the
        classic way to disguise what a file is called."""
        found = self._search({"release": "harmless\u202egnp.exe"})

        self.assertNotIn("\u202e", found.candidates[0]["release"])

    def test_an_over_long_field_cannot_flood_the_terminal(self):
        found = self._search({"release": "A" * 5000})

        self.assertLessEqual(len(found.candidates[0]["release"]), 130)

    def test_a_field_that_is_only_control_characters_becomes_none(self):
        found = self._search({"release": "\r\n\t"})

        self.assertIsNone(found.candidates[0]["release"])

    def test_a_wrong_typed_field_does_not_crash_the_table(self):
        """``year`` reaches a ``{:<6}`` format. A list there is a TypeError, and
        outside --json the command line deliberately re-raises, so remote data
        would print a Python traceback."""
        for name, attributes in (
            ("year", {"feature_details": {"title": "T", "year": [1, 2],
                                          "feature_type": "M"}}),
            ("language", {"language": []}),
            ("release", {"release": 12345}),
            ("from_trusted", {"from_trusted": "yes"}),
            ("feature_details", {"feature_details": "not a dict"}),
        ):
            with self.subTest(field=name):
                table = self._table(attributes)
                self.assertIn("42", table)

    def test_wrong_typed_fields_become_none_rather_than_being_kept(self):
        found = self._search({
            "language": [], "release": 12345, "from_trusted": "yes",
            "feature_details": {"title": "T", "year": [1, 2], "feature_type": "M"},
        })

        candidate = found.candidates[0]
        for field in ("language", "release", "fromTrusted", "year"):
            self.assertIsNone(candidate[field], field)
        self.assertEqual(42, candidate["fileId"])

    def test_a_hostile_file_name_is_sanitised_before_it_is_reported(self):
        """The download response's file_name reaches a report line and the
        provenance sidecar, so it is the same class of string."""
        api = _Api(download=json.dumps({
            "link": LINK, "file_name": "a\r\x1b[2Kforged.srt", "remaining": 5,
        }))

        result = opensubtitles.download_subtitle(
            opensubtitles.Credentials(API_KEY, USERNAME, PASSWORD),
            7061050, opener=api,
        )

        self.assertNotIn("\r", result.file_name)
        self.assertNotIn("\x1b", result.file_name)


class ResponseSizeTests(unittest.TestCase):
    """A hostile endpoint must not be able to stream unbounded bytes at us."""

    class _Huge:
        def __init__(self): self.limit = None
        def __enter__(self): return self
        def __exit__(self, *unused): return False
        def read(self, limit=None):
            self.limit = limit
            return b"x" * (limit if limit else 64 * 1024 * 1024)

    def test_the_transport_reads_with_a_limit_not_to_exhaustion(self):
        huge = self._Huge()

        with self.assertRaises(opensubtitles.OpenSubtitlesError) as caught:
            opensubtitles.search_subtitles(
                API_KEY, "X", opener=lambda request, timeout=None: huge
            )

        self.assertIsNotNone(huge.limit, "read() was called with no limit")
        self.assertIn("large", str(caught.exception).lower())

    def test_an_ordinary_response_is_unaffected(self):
        api = _Api()

        found = opensubtitles.search_subtitles(API_KEY, "The Thing", opener=api)

        self.assertEqual(1, len(found.candidates))


class CliSearchModeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workdir = Path(self.tempdir.name)
        # The candidate table is a deliberate stdout side effect; capture it
        # rather than letting it interleave with the test runner's output.
        self.stdout = io.StringIO()
        patcher = contextlib.redirect_stdout(self.stdout)
        patcher.__enter__()
        self.addCleanup(patcher.__exit__, None, None, None)

    def _args(self, *extra):
        return cli.build_parser().parse_args(list(extra))

    def test_search_lists_candidates_and_writes_nothing(self):
        api = _Api()
        reports = []
        result = {}
        with mock.patch.dict(
            os.environ, {"OPENSUBTITLES_API_KEY": API_KEY}, clear=True
        ):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                status = cli.run(
                    self._args("--os-search", "The Thing"), reports.append, result
                )

        self.assertEqual(cli.EXIT_OK, status)
        self.assertEqual("os-search", result["mode"])
        self.assertEqual([], result["filesWritten"])
        self.assertEqual(7061050, result["candidates"][0]["fileId"])
        self.assertIn(opensubtitles.ATTRIBUTION, reports)
        table = self.stdout.getvalue()
        self.assertIn("7061050", table)
        self.assertIn("fileId", table)

    def test_search_needs_only_an_api_key_not_a_login(self):
        api = _Api()
        with mock.patch.dict(
            os.environ, {"OPENSUBTITLES_API_KEY": API_KEY}, clear=True
        ):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                cli.run(self._args("--os-search", "The Thing"), lambda *a: None, {})

        self.assertEqual(["GET"], api.methods())

    def test_the_api_key_flag_is_accepted_and_never_echoed(self):
        api = _Api()
        reports = []
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                cli.run(
                    self._args("--os-search", "The Thing", "--os-api-key", API_KEY),
                    reports.append, {},
                )

        self.assertEqual(API_KEY, api.headers(0)["api-key"])
        self.assertNotIn(API_KEY, "\n".join(reports))

    def test_a_missing_api_key_fails_and_names_the_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(core.TriggerWarningsError) as caught:
                cli.run(self._args("--os-search", "The Thing"), lambda *a: None, {})
        self.assertIn("OPENSUBTITLES_API_KEY", str(caught.exception))

    def test_search_passes_the_season_and_episode_filters_through(self):
        api = _Api()
        with mock.patch.dict(
            os.environ, {"OPENSUBTITLES_API_KEY": API_KEY}, clear=True
        ):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                cli.run(self._args(
                    "--os-search", "Twin Peaks", "--os-year", "1990",
                    "--os-season", "2", "--os-episode", "7",
                    "--os-language", "fr",
                ), lambda *a: None, {})

        url = api.urls()[0]
        self.assertIn("season_number=2", url)
        self.assertIn("episode_number=7", url)
        self.assertIn("languages=fr", url)
        self.assertIn("year=1990", url)


class CliDownloadModeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workdir = Path(self.tempdir.name)
        self.output = self.workdir / "dialogue.srt"
        self.env = {
            "OPENSUBTITLES_API_KEY": API_KEY,
            "OPENSUBTITLES_USERNAME": USERNAME,
            "OPENSUBTITLES_PASSWORD": PASSWORD,
        }

    def _args(self, *extra):
        return cli.build_parser().parse_args(list(extra))

    def _download_args(self, *extra):
        return self._args("--os-file", "7061050", "--output", str(self.output), *extra)

    def test_the_downloaded_file_is_published_and_reported_as_written(self):
        api = _Api()
        result = {}
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                status = cli.run(self._download_args(), lambda *a: None, result)

        self.assertEqual(cli.EXIT_OK, status)
        self.assertEqual("os-download", result["mode"])
        self.assertEqual([str(self.output)], result["filesWritten"])
        self.assertEqual(SRT_BODY, self.output.read_text(encoding="utf-8"))

    def test_the_published_file_parses_as_srt(self):
        api = _Api()
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                cli.run(self._download_args(), lambda *a: None, {})

        cues = core.parse_srt(self.output.read_text(encoding="utf-8"))
        self.assertEqual(2, len(cues))

    def test_download_requires_an_srt_output_extension(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(core.TriggerWarningsError, r"\.srt"):
                cli.run(self._args(
                    "--os-file", "7061050",
                    "--output", str(self.workdir / "dialogue.ass"),
                ), lambda *a: None, {})

    def test_download_requires_an_output_path(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(core.TriggerWarningsError, "--output"):
                cli.run(self._args("--os-file", "7061050"), lambda *a: None, {})

    def test_download_requires_the_full_environment_credentials(self):
        for missing in sorted(self.env):
            with self.subTest(missing=missing):
                environ = dict(self.env)
                del environ[missing]
                with mock.patch.dict(os.environ, environ, clear=True):
                    with self.assertRaises(core.TriggerWarningsError) as caught:
                        cli.run(self._download_args(), lambda *a: None, {})
                self.assertIn(missing, str(caught.exception))
                self.assertFalse(self.output.exists())

    def test_a_credential_never_reaches_the_report_stream(self):
        api = _Api()
        reports = []
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                cli.run(self._download_args(), reports.append, {})

        text = "\n".join(reports)
        for secret in (API_KEY, USERNAME, PASSWORD, TOKEN, LINK):
            self.assertNotIn(secret, text)

    def test_an_existing_output_is_never_replaced(self):
        self.output.write_text("mine\n", encoding="utf-8")
        api = _Api()
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                with self.assertRaisesRegex(core.TriggerWarningsError, "already exists"):
                    cli.run(self._download_args(), lambda *a: None, {})

        self.assertEqual("mine\n", self.output.read_text(encoding="utf-8"))
        self.assertEqual([], api.requests, "the network was used before the check")

    def test_a_body_that_is_not_srt_leaves_no_file_behind(self):
        api = _Api(subtitle="<html>rate limited</html>")
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                with self.assertRaises(core.TriggerWarningsError):
                    cli.run(self._download_args(), lambda *a: None, {})

        self.assertFalse(self.output.exists())

    def test_a_failed_provenance_write_rolls_the_subtitle_back(self):
        """The subtitle is really written, then really removed again.

        Mocking the writer outright would make this pass without testing
        anything: no file would ever have existed to roll back. So the first
        write runs for real and only the second one fails.
        """
        api = _Api()
        provenance = self.workdir / "run.json"
        real_write = cli._write_exclusive
        attempts = []

        def flaky(path, payload, created):
            attempts.append(Path(path))
            if len(attempts) == 1:
                return real_write(path, payload, created)
            raise core.TriggerWarningsError("no space left on device")

        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                with mock.patch("trigger_warnings.cli._write_exclusive", flaky):
                    with self.assertRaisesRegex(core.TriggerWarningsError, "no space"):
                        cli.run(
                            self._download_args("--provenance", str(provenance)),
                            lambda *a: None, {},
                        )

        self.assertEqual(
            [self.output, provenance], attempts,
            "the subtitle must be written before the sidecar that describes it",
        )
        self.assertFalse(
            self.output.exists(),
            "a half-published run left a subtitle with no sidecar behind",
        )
        self.assertFalse(provenance.exists())

    def test_the_rollback_test_above_would_notice_a_missing_rollback(self):
        """Guards the guard: prove the first write really does create the file.

        Without this, a future change that stopped creating the output at all
        would leave the rollback test passing for the wrong reason.
        """
        api = _Api()
        seen = {}
        real_write = cli._write_exclusive

        def observing(path, payload, created):
            real_write(path, payload, created)
            seen[Path(path)] = Path(path).is_file()

        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                with mock.patch("trigger_warnings.cli._write_exclusive", observing):
                    cli.run(self._download_args(), lambda *a: None, {})

        self.assertEqual({self.output: True}, seen)


class CliProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workdir = Path(self.tempdir.name)
        self.output = self.workdir / "dialogue.srt"
        self.provenance = self.workdir / "run.json"
        self.env = {
            "OPENSUBTITLES_API_KEY": API_KEY,
            "OPENSUBTITLES_USERNAME": USERNAME,
            "OPENSUBTITLES_PASSWORD": PASSWORD,
        }

    def _run(self):
        api = _Api()
        result = {}
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", api):
                cli.run(cli.build_parser().parse_args([
                    "--os-file", "7061050", "--output", str(self.output),
                    "--provenance", str(self.provenance),
                ]), lambda *a: None, result)
        return result

    def test_provenance_records_the_source_and_is_reported_as_written(self):
        result = self._run()

        self.assertEqual(
            [str(self.output), str(self.provenance)], result["filesWritten"]
        )
        data = json.loads(self.provenance.read_text(encoding="utf-8"))
        self.assertEqual("opensubtitles", data["source"]["kind"])
        self.assertEqual(7061050, data["source"]["fileId"])
        self.assertEqual(opensubtitles.ATTRIBUTION, data["source"]["attribution"])

    def test_provenance_carries_no_credential_no_token_and_no_link(self):
        self._run()

        text = self.provenance.read_text(encoding="utf-8")
        for secret in (API_KEY, USERNAME, PASSWORD, TOKEN, LINK):
            self.assertNotIn(secret, text)
        self.assertNotIn("password", text.lower())
        self.assertNotIn("token", text.lower())

    def test_provenance_does_not_embed_the_subtitle_text(self):
        """The sidecar is an audit record, not a second copy of the dialogue."""
        self._run()

        text = self.provenance.read_text(encoding="utf-8")
        self.assertNotIn("Harmless dialogue.", text)


class ModeConflictTests(unittest.TestCase):
    """Search and download are separate modes from generation, and say so."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workdir = Path(self.tempdir.name)
        self.subtitles = self.workdir / "dialogue.srt"
        self.subtitles.write_text(SRT_BODY, encoding="utf-8")
        self.events = self.workdir / "events.json"
        self.events.write_text(
            json.dumps([{"start": 2.0, "label": "gore"}]), encoding="utf-8"
        )

    def _fails(self, argv, expected):
        with mock.patch.dict(os.environ, {
            "OPENSUBTITLES_API_KEY": API_KEY,
            "OPENSUBTITLES_USERNAME": USERNAME,
            "OPENSUBTITLES_PASSWORD": PASSWORD,
            "DDD_API_KEY": "ddd-key",
        }, clear=True):
            with self.assertRaises(core.TriggerWarningsError) as caught:
                cli.run(cli.build_parser().parse_args(argv), lambda *a: None, {})
        self.assertIn(expected, str(caught.exception))
        return str(caught.exception)

    def test_search_rejects_every_generation_flag(self):
        for flag, value in (
            ("--subtitles", str(self.subtitles)),
            ("--events", str(self.events)),
            ("--output", str(self.workdir / "warned.srt")),
            ("--ddd-item", "42"),
            ("--category", "gore"),
            ("--lead", "5"),
            ("--tail", "5"),
            ("--offset", "5"),
            ("--provenance", str(self.workdir / "run.json")),
            ("--language", "fra"),
        ):
            with self.subTest(flag=flag):
                message = self._fails(
                    ["--os-search", "The Thing", flag, value], "--os-search"
                )
                self.assertIn(flag, message)

    def test_search_rejects_the_valueless_generation_flags(self):
        for flag in ("--dry-run", "--list-streams"):
            with self.subTest(flag=flag):
                self._fails(["--os-search", "The Thing", flag], flag)

    def test_the_two_opensubtitles_modes_are_mutually_exclusive(self):
        self._fails(
            ["--os-search", "The Thing", "--os-file", "7061050",
             "--output", str(self.workdir / "dialogue.srt")],
            "--os-file",
        )

    def test_download_rejects_generation_and_search_flags(self):
        out = str(self.workdir / "dialogue.srt")
        for flag, value in (
            ("--subtitles", str(self.subtitles)),
            ("--events", str(self.events)),
            ("--ddd-item", "42"),
            ("--ddd-search", "The Thing"),
            ("--category", "gore"),
            ("--lead", "5"),
            ("--video", str(self.subtitles)),
            ("--os-year", "1982"),
            ("--os-season", "2"),
            ("--os-episode", "7"),
            ("--os-language", "fr"),
        ):
            with self.subTest(flag=flag):
                message = self._fails(
                    ["--os-file", "7061050", "--output", out, flag, value],
                    "--os-file",
                )
                self.assertIn(flag, message)

    def test_a_flag_written_out_at_its_own_default_is_still_rejected(self):
        """``--lead 20`` and no ``--lead`` at all are the same value but not the
        same command. A user who writes ``--lead 20 --os-file N`` believes the
        lead was applied; the download ignores it entirely."""
        out = str(self.workdir / "downloaded.srt")
        for flag, value in (
            ("--lead", "20"),
            ("--tail", "0"),
            ("--offset", "0"),
            ("--language", "eng"),
            ("--os-language", "en"),
        ):
            with self.subTest(flag=flag):
                message = self._fails(
                    ["--os-file", "7061050", "--output", out, flag, value],
                    "--os-file",
                )
                self.assertIn(flag, message)

    def test_a_default_valued_flag_is_still_fine_on_a_generation_run(self):
        """The tracking must not make an ordinary run reject its own defaults."""
        output = self.workdir / "warned.srt"
        result = {}
        with mock.patch.dict(os.environ, {}, clear=True):
            status = cli.run(cli.build_parser().parse_args([
                "--subtitles", str(self.subtitles),
                "--events", str(self.events),
                "--output", str(output),
                "--lead", "20", "--tail", "0", "--language", "eng",
            ]), lambda *a: None, result)

        self.assertEqual(cli.EXIT_OK, status)
        self.assertTrue(output.is_file())

    def test_download_rejects_dry_run_and_verify(self):
        out = str(self.workdir / "dialogue.srt")
        self._fails(["--os-file", "7061050", "--output", out, "--dry-run"], "--dry-run")

    def test_search_only_filters_need_a_search(self):
        for flag, value in (
            ("--os-year", "1982"),
            ("--os-season", "2"),
            ("--os-episode", "7"),
        ):
            with self.subTest(flag=flag):
                self._fails(
                    ["--subtitles", str(self.subtitles), "--events", str(self.events),
                     "--output", str(self.workdir / "warned.srt"), flag, value],
                    "--os-search",
                )

    def test_the_api_key_flag_needs_an_opensubtitles_mode(self):
        self._fails(
            ["--subtitles", str(self.subtitles), "--events", str(self.events),
             "--output", str(self.workdir / "warned.srt"),
             "--os-api-key", API_KEY],
            "--os-api-key",
        )

    def test_a_plain_generation_run_still_works_alongside_the_new_flags(self):
        """The new modes must not have narrowed the existing contract."""
        output = self.workdir / "warned.srt"
        result = {}
        with mock.patch.dict(os.environ, {}, clear=True):
            status = cli.run(cli.build_parser().parse_args([
                "--subtitles", str(self.subtitles),
                "--events", str(self.events),
                "--output", str(output),
            ]), lambda *a: None, result)

        self.assertEqual(cli.EXIT_OK, status)
        self.assertEqual("generate", result["mode"])
        self.assertTrue(output.is_file())


class JsonInterfaceTests(unittest.TestCase):
    """The machine interface keeps one shape across every mode."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workdir = Path(self.tempdir.name)

    def _main(self, argv, environ):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True):
            with mock.patch("trigger_warnings.opensubtitles.urlopen", _Api()):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = cli.main(argv)
        return status, out.getvalue(), err.getvalue()

    def test_search_json_is_one_object_with_the_usual_keys(self):
        status, stdout, stderr = self._main(
            ["--os-search", "The Thing", "--json"],
            {"OPENSUBTITLES_API_KEY": API_KEY},
        )

        payload = json.loads(stdout)
        self.assertEqual(cli.EXIT_OK, status)
        self.assertTrue(payload["ok"])
        self.assertEqual("os-search", payload["mode"])
        self.assertEqual([], payload["filesWritten"])
        self.assertIn("messages", payload)
        self.assertEqual("", stderr)

    def test_download_json_reports_the_file_it_wrote(self):
        output = self.workdir / "dialogue.srt"
        status, stdout, _ = self._main(
            ["--os-file", "7061050", "--output", str(output), "--json"],
            {
                "OPENSUBTITLES_API_KEY": API_KEY,
                "OPENSUBTITLES_USERNAME": USERNAME,
                "OPENSUBTITLES_PASSWORD": PASSWORD,
            },
        )

        payload = json.loads(stdout)
        self.assertEqual(cli.EXIT_OK, status)
        self.assertEqual("os-download", payload["mode"])
        self.assertEqual([str(output)], payload["filesWritten"])
        self.assertTrue(output.is_file())

    def test_a_json_error_carries_no_credential(self):
        status, stdout, _ = self._main(
            ["--os-file", "7061050", "--output",
             str(self.workdir / "dialogue.srt"), "--json"],
            {"OPENSUBTITLES_API_KEY": API_KEY},
        )

        self.assertEqual(cli.EXIT_ERROR, status)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        self.assertNotIn(API_KEY, stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
