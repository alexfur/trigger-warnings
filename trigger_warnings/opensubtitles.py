"""Small, local-only client for the official OpenSubtitles REST API v1.

The client caches nothing and writes nothing. It hands the command line layer a
validated SRT string; publication stays where every other side effect lives.

Three rules shape everything below.

*   **Credentials come from the environment, and the key may come from a flag.**
    ``OPENSUBTITLES_API_KEY`` (or ``--os-api-key``) identifies the application;
    ``OPENSUBTITLES_USERNAME`` and ``OPENSUBTITLES_PASSWORD`` identify the
    account and have no argv spelling at all, because a password on a command
    line lands in the shell history and in every process listing on the box.
    The plaintext ``~/.opensubtitles.json`` that other subtitle tools keep is
    never read: a credential this tool was not pointed at is a credential the
    user did not agree to spend.

*   **A download costs quota, so it is spent once.** An account gets a small
    number of downloads a day. ``/download`` is therefore posted exactly once
    per run and never retried; a failure is reported rather than papered over
    with a second attempt that costs another download.

*   **Nothing secret reaches a message, and network bytes are never quoted.**
    The API key, the username, the password, the session token and the
    temporary download link all pass through :func:`_scrub` before any error
    text escapes this module. The temporary link matters as much as the
    password: it is a bearer credential for one file, so it is fetched, used
    and dropped, never returned and never recorded.

    :func:`_scrub` is the backstop, not the strategy. Where the text is
    attacker-controlled it is discarded rather than filtered, because a
    denylist over bytes someone else chose loses to the next encoding. That
    was measured, not assumed: redacting the exact link still leaked a
    percent-encoded echo of it, and redacting the encoded forms too still
    leaked a case-folded one. So the one place a response body could reach a
    message, the SRT parse, reports the diagnosis it generated itself and drops
    the line :func:`core.parse_srt` quoted.

*   **The login's ``base_url`` is checked as a parsed host, not as a string.**
    It decides where a token minted from the password is sent, so it is the
    most dangerous field in any response here. See :func:`_resolve_base_url`.

Search and download are deliberately separate. A search needs only the API key
and never sends the password, so a mistyped title cannot turn into a login
attempt. Neither one chooses a subtitle for the user: the search lists file ids
and stops.
"""

import datetime
import json
import time
import unicodedata
from collections import namedtuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from . import __version__
from .core import TriggerWarningsError, parse_srt


#: The documented entry point. ``base_url`` from a login may move a VIP account
#: to another host in the same domain; see :func:`_resolve_base_url`.
API_BASE_URL = "https://api.opensubtitles.com/api/v1"

#: OpenSubtitles requires a User-Agent of ``AppName vX.Y.Z`` and rejects a
#: request without one. Derived from the package version so it cannot go stale
#: the way a literal does.
USER_AGENT = "trigger-warnings v{}".format(__version__)

ATTRIBUTION = "Subtitles from OpenSubtitles.com"

API_KEY_VARIABLE = "OPENSUBTITLES_API_KEY"
USERNAME_VARIABLE = "OPENSUBTITLES_USERNAME"
PASSWORD_VARIABLE = "OPENSUBTITLES_PASSWORD"

DEFAULT_LANGUAGE = "en"

_ALLOWED_HOST_SUFFIX = "opensubtitles.com"
_REDACTED = "<redacted>"
# A path or query segment at least this long is treated as opaque, and so as
# part of the credential. Below it lie the ordinary words of a URL --
# "download", "sub.srt" -- which are not worth blanking.
_OPAQUE_SEGMENT_LEN = 16
# Below this length a secret is not substring-matched at all. See _scrub:
# this is about not shredding our own prose over a coincidence, and is
# emphatically NOT what makes the redaction sound.
_MIN_SCRUBBABLE_LEN = 8
# A server-supplied string is truncated to this many characters before it
# reaches a terminal, a sidecar or the JSON result.
_MAX_FIELD_CHARS = 120
# Nothing this API returns is remotely this big. The cap exists so a broken
# or hostile endpoint cannot stream unbounded bytes into memory before
# anything has had a chance to reject them.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# A quota reset is a daily event. This window only rules out the absurd,
# because a strict one risks the field never being surfaced at all.
_RESET_PLAUSIBILITY_DAYS = 400

# What each request is for. These decide the advice a failure gives, and
# whether it may be repeated, so they are named rather than spelled out at the
# call sites.
_WHAT_LOGIN = "sign-in"
_WHAT_SEARCH = "subtitle search"
_WHAT_DOWNLOAD_REQUEST = "download request"
_WHAT_FILE_FETCH = "subtitle download"

# Only the file fetch may be repeated, and only on a status the server chose to
# send. See _open.
_RETRYABLE_CODES = (429, 503)
_LINK_RETRIES = 1
_RETRY_WAIT_S = 2.0

#: Module-level so a test can replace it. Resolved from globals at call time,
#: unlike a default argument, which would bind at definition.
_sleep = time.sleep
_API_TIMEOUT_S = 15
_LINK_TIMEOUT_S = 30

__all__ = [
    "API_BASE_URL",
    "ATTRIBUTION",
    "USER_AGENT",
    "Credentials",
    "Download",
    "OpenSubtitlesError",
    "SearchResults",
    "api_key_from_environment",
    "credentials_from_environment",
    "download_subtitle",
    "search_subtitles",
]


class OpenSubtitlesError(TriggerWarningsError):
    """An OpenSubtitles request or response could not produce a usable subtitle."""


#: The full set needed to spend a download. Never logged, never serialised.
Credentials = namedtuple("Credentials", "api_key username password")

#: ``candidates`` is a list of plain dicts; ``notes`` are strings to print.
SearchResults = namedtuple("SearchResults", "candidates notes")

#: A validated subtitle plus the quota facts worth telling the user about.
#: ``cues`` is the parsed dialogue count, carried so that no caller has to parse
#: the body again outside the scrubbing guard. There is deliberately no
#: ``link`` field.
Download = namedtuple(
    "Download", "text file_name remaining used notes cues reset_at"
)


# ------------------------------------------------------------------ redaction


def _secret_forms(secret):
    """Every shape one secret plausibly takes in a message we did not write.

    Nothing shorter than :data:`_MIN_SCRUBBABLE_LEN` is matched. Every value
    this module actually guards clears that easily: an API key, a JWT, a
    temporary link, and the opaque link segments, which are gated at 16
    characters of their own. What falls below it is a weak password that
    happens to be an ordinary word.
    """
    if not secret or len(secret) < _MIN_SCRUBBABLE_LEN:
        return []
    return [form for form in {secret, quote(secret, safe="")} if form]


def _link_forms(link):
    """A link, plus the opaque parts of it that are the actual credential.

    Matching the exact URL string is not enough, because a CDN does not
    reliably echo a URL back whole. It may percent-encode it, HTML-escape the
    separators, or name the token on its own. All three leave the opaque path
    segment intact, so the long segments are redacted in their own right.

    Short segments are deliberately left alone. Redaction that blanks ordinary
    words turns a diagnosis into noise, and the caller still has to be told
    what went wrong.
    """
    forms = _secret_forms(link)
    split = urlsplit(link)
    segments = split.path.split("/")
    for pair in split.query.split("&"):
        segments.extend(pair.split("=", 1))
    for segment in segments:
        segment = segment.strip()
        if len(segment) >= _OPAQUE_SEGMENT_LEN:
            forms.extend(_secret_forms(segment))
    return forms


def _scrub(text, secrets):
    """Remove any secret value that leaked into a message.

    Belt and braces. Nothing here formats a credential into a message on
    purpose, but the messages are not all ours: ``urlopen`` interpolates the URL
    it was handed into its own errors, ``core.parse_srt`` quotes the line it
    rejected, and both of those can carry the download token. One filter on the
    way out is cheaper than trusting every library we call to stay quiet.

    Two limits, stated rather than hidden.

    It matches case-sensitively. A server that changed the case of a credential
    before echoing it would defeat it, and case-insensitive matching was
    rejected because it would blank ordinary words wherever they coincided.

    More importantly, substring redaction of *predictable* text is an oracle,
    and no length rule closes it. Given a scrubbed message and the template it
    came from, the secret is recoverable uniquely: ``account`` is seven
    characters and ``download`` is eight, and both are pinpointed exactly by
    the hole they leave. :data:`_MIN_SCRUBBABLE_LEN` is therefore not a
    security boundary. It exists so that a weak password that happens to be an
    English word does not reduce our own diagnostics to rubble, and it is safe
    only because of the rule above it: the sole text this runs over is text we
    wrote ourselves. What actually closes the oracle is refusing to echo
    attacker-controlled bytes, which :func:`download_subtitle` does at the
    parse. Do not mistake the length rule for the defence.
    """
    result = str(text)
    forms = []
    for secret in secrets:
        forms.extend(_secret_forms(secret))
    # Longest first, so redacting a token does not leave the enclosing link
    # half-rewritten and still readable.
    for form in sorted(set(forms), key=lambda item: (len(item), item), reverse=True):
        if form in result:
            result = result.replace(form, _REDACTED)
    return result


def _fail(message, secrets=()):
    raise OpenSubtitlesError(_scrub(message, secrets))


# ---------------------------------------------------------------- credentials


def _clean(value):
    return value.strip() if isinstance(value, str) else None


def api_key_from_environment(api_key=None, environ=None):
    """Return the API key from the flag or the environment, in that order."""
    if environ is None:
        import os

        environ = os.environ
    key = _clean(api_key) or _clean(environ.get(API_KEY_VARIABLE))
    if not key:
        raise OpenSubtitlesError(
            "--os-api-key or {} must be a non-empty API key. Register a free "
            "one at opensubtitles.com; the key is never written to "
            "disk.".format(API_KEY_VARIABLE)
        )
    return key


def credentials_from_environment(api_key=None, environ=None):
    """Return the full login credentials, from the environment and nowhere else.

    The username and password have no flag by design, so this is the only place
    they can come from. A blank variable counts as unset: an empty password
    would otherwise be sent to the login endpoint and come back as a confusing
    401 rather than the local mistake it is.
    """
    if environ is None:
        import os

        environ = os.environ
    key = api_key_from_environment(api_key, environ)
    username = _clean(environ.get(USERNAME_VARIABLE))
    password = _clean(environ.get(PASSWORD_VARIABLE))
    for name, value in ((USERNAME_VARIABLE, username), (PASSWORD_VARIABLE, password)):
        if not value:
            raise OpenSubtitlesError(
                "{} is not set. Downloading a subtitle file signs in to your "
                "OpenSubtitles account, so it needs {} and {} in the "
                "environment. There is no command line flag for either, and no "
                "credential file is read.".format(
                    name, USERNAME_VARIABLE, PASSWORD_VARIABLE
                )
            )
    return Credentials(key, username, password)


def _require_credentials(credentials):
    if not isinstance(credentials, Credentials):
        raise OpenSubtitlesError("internal error: credentials were not supplied")
    for name, value in (
        (API_KEY_VARIABLE, credentials.api_key),
        (USERNAME_VARIABLE, credentials.username),
        (PASSWORD_VARIABLE, credentials.password),
    ):
        if not _clean(value):
            raise OpenSubtitlesError(
                "{} is not set, so this download cannot sign in.".format(name)
            )
    return credentials


# ------------------------------------------------- server-supplied field types


def _text_field(value, limit=_MAX_FIELD_CHARS):
    """One server-supplied string, made safe to print, store and re-emit.

    Every string in a search result was written by whoever uploaded the
    subtitle, and it lands in four places: the candidate table the user reads a
    file id off, a report line, the provenance sidecar, and the ``--json``
    payload an agent parses. So it is cleaned once, here, where the record is
    built. Cleaning at the print site would leave the other three exposed.

    Every character in a Unicode ``C`` category goes. That covers the control
    codes, where a carriage return plus ``ESC[2K`` erases the row just drawn and
    replaces it with a forged one, and a bare newline forges a whole extra row
    with no escape sequence at all. It also covers the format characters, which
    include the right-to-left override used to disguise what a file is called.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        char for char in value if unicodedata.category(char)[0] != "C"
    ).strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "..."
    return cleaned


def _parse_reset_time(value):
    """One quota reset instant, validated and re-rendered by us, or ``None``.

    This is the only server-supplied *text* that reaches a message, and it gets
    there by being parsed rather than filtered. Its siblings ``message`` and
    ``reset_time`` stay out: they are free prose, and the threat there is not
    the one :func:`_text_field` addresses. Terminal forgery needs control
    characters; semantic injection ("ignore previous instructions") is plain
    ASCII that survives any character filter intact, and the ``--json`` result
    is fed straight into an agent's context.

    A timestamp has no such problem, because it is not filtered and re-emitted,
    it is understood and rebuilt. The output alphabet is fixed by construction,
    which is the same argument :func:`_resolve_base_url` makes about rebuilding
    a URL from the parsed host rather than returning the string it checked.

    Two decisions worth knowing about:

    *   **The trailing ``Z`` is rewritten before parsing.** Python 3.9's
        ``fromisoformat`` rejects it and 3.13 accepts it, and this API sends it.
        Without the rewrite the field parses locally, fails on the 3.9 CI leg,
        and is dropped there in silence rather than turning anything red.
    *   **A naive timestamp is taken as UTC**, because the field is named
        ``reset_time_utc`` and that is the server asserting the zone. Being a
        few hours out about a quota reset costs a retry, not correctness.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    # A bare date is not an instant, and fromisoformat would accept one.
    if not text or "T" not in text:
        return None
    if text[-1] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        moment = moment.astimezone(datetime.timezone.utc).replace(microsecond=0)
        drift = moment - datetime.datetime.now(datetime.timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    if abs(drift.days) > _RESET_PLAUSIBILITY_DAYS:
        return None
    return moment.isoformat().replace("+00:00", "Z")


def _reset_from_http_error(error):
    """The reset instant carried by a 406 body. Parsed, never quoted.

    One field comes out and the rest is discarded unread. The read is bounded
    and this is called only for a 406, because :func:`_open` is shared by four
    call sites and reading an error body is a behaviour change for all of them.
    """
    try:
        payload = error.read(_MAX_RESPONSE_BYTES + 1)
        if len(payload) > _MAX_RESPONSE_BYTES:
            return None
        data = json.loads(payload.decode("utf-8"))
    except Exception:  # a failure here means "no timestamp", never a new error
        return None
    if not isinstance(data, dict):
        return None
    return _parse_reset_time(data.get("reset_time_utc"))


def _int_field(value):
    """An integer, or ``None``. A wrong type here reaches a format spec."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bool_field(value):
    """A boolean, or ``None``. A truthy string must not read as ``yes``."""
    return value if isinstance(value, bool) else None


# ------------------------------------------------------------------ validation


def _require_text(value, what):
    cleaned = _clean(value)
    if not cleaned:
        raise OpenSubtitlesError("{} must not be empty".format(what))
    return cleaned


def _require_file_id(file_id):
    if isinstance(file_id, bool) or not isinstance(file_id, int) or file_id <= 0:
        raise OpenSubtitlesError(
            "--os-file must be a positive OpenSubtitles file id, taken from the "
            "fileId column of --os-search"
        )
    return file_id


def _require_number(value, what):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OpenSubtitlesError("{} must be a non-negative whole number".format(what))
    return value


def _require_year(year):
    if year is None:
        return None
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise OpenSubtitlesError("--os-year must be a four-digit year")
    return year


# --------------------------------------------------------------------- transport


def _status_hint(code, what):
    """Turn a status code into the thing the user can actually act on.

    Keyed on the code alone this gave advice that costs money. The quota is
    charged when ``/download`` issues the link, not when the file is fetched,
    so every failure *after* that point was being described in pre-charge
    words: 410 said "run the download again", which spends a second unit out
    of an allowance that can be twenty a day, and 406 asserted the quota was
    spent when a link had demonstrably just been issued. None of them said a
    download had already been charged. Hence ``what``.
    """
    if what == _WHAT_FILE_FETCH:
        spent = (
            " A download was already charged for this run, when the link was "
            "issued, so running the command again spends another one."
        )
        return {
            406: "The file server refused the request." + spent,
            410: "The download link had expired or been used." + spent,
            429: "Too many requests to the file server." + spent,
            503: "The file server is temporarily unavailable." + spent,
        }.get(code, "The file could not be fetched." + spent)
    return {
        401: "The sign-in was rejected or the session expired. Check "
             "{} and {}.".format(USERNAME_VARIABLE, PASSWORD_VARIABLE),
        # Genuinely ambiguous at the protocol level, and the two causes have
        # very different fixes, so both are named and neither value is shown.
        403: "Either the API key was refused, or the User-Agent was. This tool "
             "sends {!r}, which is the format OpenSubtitles requires; a refused "
             "key is the likelier of the two.".format(USER_AGENT),
        # The docs list a fourth cause, a missing Accept header, which is
        # deliberately not repeated here: _headers always sends one, so it is
        # not a cause a reader of this message can have, and naming it sends
        # them to check something they cannot change. The related worry, that
        # the docs advise Accept: */* while this client sends
        # application/json, was settled by probing the live /subtitles
        # endpoint: application/json, */* and no Accept header at all each
        # returned a byte-identical 200, so the header is not honoured and
        # what this client sends is accepted.
        406: "Documented causes are a spent daily download quota, a file id "
             "that is not valid, or an expired sign-in. Whether a refused "
             "request still counts against the quota is not documented, so "
             "treat this run as possibly charged.",
        410: "The resource is gone.",
        # The ~1/s figure that used to sit here is the SIGN-IN limit, not the
        # general one. Stating it as general sends the user to fix a
        # non-problem.
        429: "Too many requests: OpenSubtitles documents about 5 requests a "
             "second per IP, and about 1 a second for sign-in. Wait, then try "
             "again.",
        503: "OpenSubtitles is temporarily unavailable. This one is worth "
             "retrying later.",
    }.get(code, "")


def _open(opener, request, timeout, secrets, what):
    """Perform one request, converting every failure into a scrubbed error.

    The response body is never quoted back. A server that echoes a submitted
    field into its error text would otherwise put the password in the message.
    """
    # Only the file fetch repeats, and only on a status the server chose to
    # send. Every other request either spends quota (the download POST), or is
    # the endpoint the vendor limits hardest and asks callers not to hammer
    # (the sign-in). The retry lives here rather than in _fetch_link because
    # this function converts HTTPError into a scrubbed error, so by the time a
    # caller sees it the status code is gone.
    attempts = _LINK_RETRIES + 1 if what == _WHAT_FILE_FETCH else 1
    for attempt in range(attempts):
        try:
            with opener(request, timeout=timeout) as response:
                # Bounded on purpose: one byte over the cap is enough to know
                # the response is not usable, and reading to exhaustion would
                # let a broken endpoint decide how much memory this uses.
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
            break
        except HTTPError as error:
            if error.code in _RETRYABLE_CODES and attempt + 1 < attempts:
                _sleep(_RETRY_WAIT_S)
                continue
            hint = _status_hint(error.code, what)
            if error.code == 406 and what == _WHAT_DOWNLOAD_REQUEST:
                # The quota reset belongs to the API's own refusal. Parsing it
                # out of a file server's error document would be incoherent.
                moment = _reset_from_http_error(error)
                if moment:
                    hint += " The quota resets at {}.".format(moment)
            _fail(
                "OpenSubtitles returned HTTP {} for the {}.{}".format(
                    error.code, what, " " + hint if hint else ""
                ),
                secrets,
            )
        except (URLError, OSError, ValueError) as error:
            # Never retried, at any call site. A received status means the
            # server answered; a lost response does not, and a request that
            # timed out may have been processed.
            _fail(
                "Could not reach OpenSubtitles for the {}: {}".format(what, error),
                secrets,
            )
        except Exception as error:
            # Deliberately reports the type and not the text. An exception
            # nobody anticipated carries whatever the failing library put in
            # it, and on this path that can be the request, a header or the
            # link.
            _fail(
                "The {} failed unexpectedly ({}). Re-run to see whether it "
                "persists.".format(what, type(error).__name__),
                secrets,
            )
    # Outside the try, so this refusal is not swallowed by the arm above.
    if len(payload) > _MAX_RESPONSE_BYTES:
        _fail(
            "the {} returned more than {} MB, which is far larger than "
            "anything this API sends, so it was refused unread.".format(
                what, _MAX_RESPONSE_BYTES // (1024 * 1024)
            ),
            secrets,
        )
    return payload


def _build_request(url, secrets, what, data=None, headers=None):
    """Construct a Request without letting the URL escape in an exception.

    ``Request`` parses the URL eagerly and raises ``ValueError: unknown url
    type: <the whole URL>``. For the temporary download link that URL is itself
    a bearer credential, so this construction cannot sit outside the guard.
    """
    try:
        return Request(url, data=data, headers=headers or {})
    except Exception as error:
        _fail(
            "the {} could not be prepared ({})".format(what, type(error).__name__),
            secrets,
        )


def _json_request(url, headers, secrets, what, opener, body=None, timeout=None):
    data = None
    if body is not None:
        try:
            data = json.dumps(body).encode("utf-8")
        except (TypeError, ValueError) as error:
            # The sign-in body holds the password, so the serialiser's own
            # error text is not safe to quote.
            _fail(
                "the {} body could not be encoded ({})".format(
                    what, type(error).__name__
                ),
                secrets,
            )
    request = _build_request(url, secrets, what, data=data, headers=headers)
    payload = _open(
        opener, request, timeout or _API_TIMEOUT_S, secrets, what
    )
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        _fail(
            "OpenSubtitles returned invalid JSON for the {}: {}".format(what, error),
            secrets,
        )


def _headers(api_key, token=None, json_body=False):
    headers = {
        "Api-Key": api_key,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _resolve_base_url(value, secrets):
    """Turn a login's ``base_url`` into the API root to use for the download.

    The field arrives as a bare host (``vip-api.opensubtitles.com``) but is
    documented loosely enough that a scheme sometimes appears, so both are
    accepted.

    This value decides where a token minted from the user's password is sent,
    which makes it the most security-sensitive field in the whole response. Two
    rules follow, and both matter:

    *   **The verdict is taken on the parsed host, never on the raw string.** A
        suffix test against the string is satisfiable by putting the real host
        at the front and the expected suffix anywhere after it, in a path, a
        query or a fragment. ``evil.example/?x=.opensubtitles.com`` ends with
        the right characters and resolves to ``evil.example``.
    *   **The result is rebuilt from the parsed host alone.** Returning the
        original string would hand back something that was never the thing
        checked, and would carry any userinfo along with it.
    """
    if value is None:
        return API_BASE_URL
    raw = _clean(value)
    if not raw:
        return API_BASE_URL

    # A bare host is the documented shape; give it a scheme so one parser
    # handles both spellings, and so "//evil.example" cannot arrive schemeless.
    candidate = raw if "://" in raw else "https://" + raw.lstrip("/")
    split = urlsplit(candidate)
    if split.scheme != "https":
        _fail(
            "the login base_url used the {!r} scheme; only https is "
            "accepted".format(split.scheme),
            secrets,
        )
    # .hostname strips userinfo and port and lowercases; .port validates.
    bare = (split.hostname or "").lower()
    try:
        port = split.port
    except ValueError:
        _fail("the login base_url named an invalid port", secrets)
    if bare != _ALLOWED_HOST_SUFFIX and not bare.endswith("." + _ALLOWED_HOST_SUFFIX):
        _fail(
            "the login base_url named an unexpected host. Only an "
            "{} host is used, because that value decides where the session "
            "token is sent.".format(_ALLOWED_HOST_SUFFIX),
            secrets,
        )
    netloc = "{}:{}".format(bare, port) if port else bare
    return "https://{}/api/v1".format(netloc)


# ------------------------------------------------------------------- searching


def _candidate(attributes, entry_file):
    feature = attributes.get("feature_details")
    if not isinstance(feature, dict):
        feature = {}
    return {
        "fileId": entry_file["file_id"],
        "fileName": _text_field(entry_file.get("file_name")),
        "cdNumber": _int_field(entry_file.get("cd_number")),
        "subtitleId": _text_field(attributes.get("subtitle_id"), 40),
        "language": _text_field(attributes.get("language"), 16),
        "release": _text_field(attributes.get("release")),
        "downloadCount": _int_field(attributes.get("download_count")),
        "hearingImpaired": _bool_field(attributes.get("hearing_impaired")),
        "fromTrusted": _bool_field(attributes.get("from_trusted")),
        "aiTranslated": _bool_field(attributes.get("ai_translated")),
        "machineTranslated": _bool_field(attributes.get("machine_translated")),
        "uploadDate": _text_field(attributes.get("upload_date"), 40),
        "featureType": _text_field(feature.get("feature_type"), 32),
        "title": _text_field(feature.get("title")),
        "year": _int_field(feature.get("year")),
        "seasonNumber": _int_field(feature.get("season_number")),
        "episodeNumber": _int_field(feature.get("episode_number")),
        "imdbId": _int_field(feature.get("imdb_id")),
    }


def search_subtitles(api_key, query, year=None, season=None, episode=None,
                     language=DEFAULT_LANGUAGE, opener=None):
    """List downloadable subtitle files without choosing one.

    Returns :class:`SearchResults`. Each candidate carries the ``fileId`` that
    ``--os-file`` takes, so a multi-part subtitle is listed once per part rather
    than once per record: a caller that could only see the record would have no
    way to name the second disc.

    An empty result is not evidence that no subtitle exists. It is reported as
    an empty list and the caller says so.
    """
    if opener is None:  # read at call time so the default stays patchable
        opener = urlopen
    key = api_key_from_environment(api_key, environ={})
    text = _require_text(query, "--os-search")
    parameters = [
        ("query", text),
        ("languages", _require_text(language, "--os-language")),
    ]
    for name, value, what in (
        ("year", _require_year(year), "--os-year"),
        ("season_number", _require_number(season, "--os-season"), "--os-season"),
        ("episode_number", _require_number(episode, "--os-episode"), "--os-episode"),
    ):
        if value is not None:
            parameters.append((name, str(value)))

    secrets = (key,)
    data = _json_request(
        API_BASE_URL + "/subtitles?" + urlencode(parameters),
        _headers(key), secrets, _WHAT_SEARCH, opener,
    )
    return _parse_search(data, secrets)


def _parse_search(data, secrets):
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        _fail("OpenSubtitles returned invalid search results", secrets)

    candidates = []
    fileless = 0
    for entry in data["data"]:
        if not isinstance(entry, dict):
            _fail("OpenSubtitles returned an invalid search result", secrets)
        attributes = entry.get("attributes")
        if not isinstance(attributes, dict):
            _fail("OpenSubtitles returned a result without attributes", secrets)
        files = attributes.get("files")
        if files is None:
            files = []
        if not isinstance(files, list):
            _fail("OpenSubtitles returned a result with invalid files", secrets)
        if not files:
            fileless += 1
            continue
        for entry_file in files:
            if not isinstance(entry_file, dict):
                _fail("OpenSubtitles returned an invalid subtitle file", secrets)
            file_id = entry_file.get("file_id")
            if isinstance(file_id, bool) or not isinstance(file_id, int) or file_id <= 0:
                _fail(
                    "OpenSubtitles returned a subtitle file without a valid file id",
                    secrets,
                )
            candidates.append(_candidate(attributes, entry_file))

    notes = []
    if fileless:
        notes.append(
            "{} search result{} carried no downloadable file and {} not "
            "listed.".format(
                fileless,
                "" if fileless == 1 else "s",
                "was" if fileless == 1 else "were",
            )
        )
    return SearchResults(candidates, notes)


# ----------------------------------------------------------------- downloading


def _login(credentials, opener, secrets):
    data = _json_request(
        API_BASE_URL + "/login",
        _headers(credentials.api_key, json_body=True),
        secrets, _WHAT_LOGIN, opener,
        body={"username": credentials.username, "password": credentials.password},
    )
    if not isinstance(data, dict):
        _fail("OpenSubtitles returned an invalid sign-in response", secrets)
    token = _clean(data.get("token"))
    if not token:
        _fail(
            "the OpenSubtitles sign-in returned no token, so the download "
            "cannot be authorised",
            secrets,
        )
    return token, _resolve_base_url(data.get("base_url"), secrets)


def _request_download(base_url, credentials, token, file_id, opener, secrets):
    """Spend exactly one download. There is no retry here, by design."""
    data = _json_request(
        base_url + "/download",
        _headers(credentials.api_key, token=token, json_body=True),
        secrets, _WHAT_DOWNLOAD_REQUEST, opener,
        body={"file_id": file_id, "sub_format": "srt"},
    )
    if not isinstance(data, dict):
        _fail("OpenSubtitles returned an invalid download response", secrets)
    link = _clean(data.get("link"))
    if not link:
        # The second refusal path. It already holds the parsed body, so the
        # timestamp is free here.
        moment = _parse_reset_time(data.get("reset_time_utc"))
        _fail(
            "OpenSubtitles returned no download link. The quota may be spent; "
            "this run did not receive a file.{}".format(
                " The quota resets at {}.".format(moment) if moment else ""
            ),
            secrets,
        )
    if urlsplit(link).scheme != "https":
        _fail("the download link was not an https URL, so it was not fetched", secrets)
    return link, data


def _fetch_link(link, opener, secrets):
    """Fetch the temporary link and forget it.

    No API key and no session token go with this request. The link already
    carries its own one-shot token, and it may point at a CDN host, so sending
    the account credentials there would hand them to a third party for nothing.
    """
    request = _build_request(
        link, secrets, _WHAT_FILE_FETCH,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    return _open(opener, request, _LINK_TIMEOUT_S, secrets, _WHAT_FILE_FETCH)


def _decode(payload, secrets):
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        _fail(
            "the downloaded subtitle is not UTF-8. Guessing an encoding would "
            "corrupt the dialogue, so it was not written. Try another file id "
            "from --os-search.",
            secrets,
        )


def download_subtitle(credentials, file_id, opener=None):
    """Sign in, spend one download, fetch the link, and validate what arrives.

    Returns a :class:`Download` whose ``text`` has already been through
    :func:`core.parse_srt`, so a caller cannot publish an HTML error page or a
    truncated file as if it were dialogue. The temporary link is not part of the
    return value and is not recorded anywhere.
    """
    if opener is None:  # read at call time so the default stays patchable
        opener = urlopen
    creds = _require_credentials(credentials)
    identifier = _require_file_id(file_id)

    secrets = [creds.api_key, creds.username, creds.password]
    token, base_url = _login(creds, opener, secrets)
    secrets.append(token)
    link, response = _request_download(
        base_url, creds, token, identifier, opener, secrets
    )
    # The opaque segments go in as secrets of their own; see _link_forms.
    secrets.extend(_link_forms(link))
    text = _decode(_fetch_link(link, opener, secrets), secrets)

    # Validate before the caller can publish. A rate-limit page and a subtitle
    # are both 200 OK with a body; only a parse tells them apart.
    #
    # The diagnosis is deliberately thrown away rather than scrubbed. parse_srt
    # quotes the line it rejected, which is right for a local file the user can
    # open and wrong for bytes off the network: the likeliest rejected body is a
    # CDN error document, and those echo the requested URL, which is the
    # download token. Scrubbing that is a denylist over attacker-controlled
    # bytes, and a denylist loses to the next encoding -- redacting the exact
    # link missed a percent-encoded echo, and redacting encoded forms still
    # missed a case-folded one. Not echoing the body ends the arms race.
    #
    # The user loses nothing they could act on. They cannot repair a CDN error
    # page, and the remedy is the same whatever the body said.
    try:
        cues = parse_srt(text, "the OpenSubtitles subtitle file")
    except TriggerWarningsError:
        _fail(
            "the downloaded file is not valid SRT, so nothing was written. The "
            "response is not quoted here because it came from the network and "
            "can carry the download token. Try another file id from "
            "--os-search.",
            secrets,
        )

    remaining = _int_field(response.get("remaining"))
    used = _int_field(response.get("requests"))
    reset_at = _parse_reset_time(response.get("reset_time_utc"))
    notes = []
    if remaining == 0:
        # A reset time is noise at 97 remaining and the whole point at 0.
        notes.append("No downloads left on this account today.{}".format(
            " The quota resets at {}.".format(reset_at) if reset_at else ""
        ))
    elif remaining is not None:
        notes.append(
            "{} download{} left on this account today.".format(
                remaining, "" if remaining == 1 else "s"
            )
        )
    return Download(
        text,
        _text_field(response.get("file_name")),
        remaining,
        used,
        notes,
        len(cues),
        reset_at,
    )
