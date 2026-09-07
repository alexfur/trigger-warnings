"""Prerequisite checks and guided setup.

Credentials reach this tool from the environment and from nowhere else. That is
deliberate: there is no credential file to read, so there is no ambient key to
pick up by accident and none of the tool's own writes can leak one. A wizard
inherits that constraint and cannot escape it, because a child process cannot
set its parent shell's environment. So a guided run ends in an export block to
paste or evaluate, never in a file this tool wrote.

The checks answer one question per source: can this machine actually do the
thing, right now. Where that is answerable for free it is answered for real,
by calling the live search endpoint, because a key that is merely *present* is
the failure this is meant to catch. Searching costs no download quota. Signing
in is not attempted: it is rate limited far more tightly than search, and a
username and password that are present are verified by the first download
anyway.
"""

import os
import shutil
import sys
from collections import namedtuple

from . import __version__
from . import ddd
from . import opensubtitles as osub


#: One row of the report. ``status`` is one of the constants below; ``detail``
#: is a sentence for a human; ``fix`` is the single next action, or None when
#: there is nothing to do.
Check = namedtuple("Check", "name status detail fix variable url required")

OK = "ok"
MISSING = "missing"
INVALID = "invalid"
UNVERIFIED = "unverified"

DDD_KEY_VARIABLE = "DDD_API_KEY"
DDD_SIGNUP_URL = "https://www.doesthedogdie.com/api"
OS_SIGNUP_URL = "https://www.opensubtitles.com/en/consumers"

#: Verification queries. A well known title keeps the call cheap and its
#: outcome unambiguous: anything other than a clean response means the
#: credential, not the query, is the problem.
_PROBE_TITLE = "Jaws"
_PROBE_YEAR = 1975


def _redact(text, secrets):
    """Backstop only.

    Both API clients already refuse to echo a credential, and that refusal is
    the actual defence. This exists because the text arriving here has passed
    through an exception on its way, and a wizard whose whole purpose is to
    handle secrets is the wrong place to assume that held.
    """
    cleaned = str(text)
    for secret in secrets:
        if secret and len(secret) >= 8 and secret in cleaned:
            cleaned = cleaned.replace(secret, "<redacted>")
    return cleaned


def _classify(error, secrets):
    """Split "your credential is wrong" from "the network is not here".

    An offline machine must not be told its key was refused. The distinction is
    worth making precisely because the two have nothing in common: one is fixed
    by getting a new key, the other by trying again later.
    """
    text = _redact(error, secrets)
    lowered = text.lower()
    offline = ("temporarily unavailable" in lowered
               or "could not be reached" in lowered
               or "unreachable" in lowered
               or "connection" in lowered
               or "timed out" in lowered
               or "network" in lowered)
    return (UNVERIFIED if offline else INVALID), text


def check_python():
    version = "{}.{}.{}".format(*sys.version_info[:3])
    return Check(
        name="python",
        status=OK,
        detail="Python {} runs this tool; {} is the minimum.".format(version, "3.9"),
        fix=None, variable=None, url=None, required=True,
    )


def check_ffmpeg(which=None):
    """FFmpeg is optional, and only --video, --list-streams and --verify need it."""
    which = which or shutil.which
    found = which("ffmpeg")
    probe = which("ffprobe")
    if found and probe:
        return Check(
            name="ffmpeg", status=OK,
            detail="FFmpeg is installed, so --video can read the subtitle "
                   "track already inside a video file.",
            fix=None, variable=None, url=None, required=False,
        )
    return Check(
        name="ffmpeg", status=MISSING,
        detail="FFmpeg was not found. Without it --video, --list-streams and "
               "--verify cannot run; every other mode is unaffected.",
        fix="Install FFmpeg, or supply a dialogue track with --subtitles instead.",
        variable=None, url="https://ffmpeg.org/download.html", required=False,
    )


def check_ddd(environ=None, opener=None, verify=True):
    environ = os.environ if environ is None else environ
    key = (environ.get(DDD_KEY_VARIABLE) or "").strip()
    if not key:
        return Check(
            name="ddd-api-key", status=MISSING,
            detail="No DoesTheDogDie key, so timestamps cannot come from the "
                   "API. Writing them yourself with --events still works.",
            fix="Request a key, then export {}.".format(DDD_KEY_VARIABLE),
            variable=DDD_KEY_VARIABLE, url=DDD_SIGNUP_URL, required=False,
        )
    if not verify:
        return Check(
            name="ddd-api-key", status=UNVERIFIED,
            detail="{} is set. It was not checked against the API.".format(
                DDD_KEY_VARIABLE),
            fix=None, variable=DDD_KEY_VARIABLE, url=None, required=False,
        )
    try:
        kwargs = {"opener": opener} if opener is not None else {}
        ddd.search_items(key, _PROBE_TITLE, _PROBE_YEAR, **kwargs)
    except Exception as error:  # the client raises its own error type
        status, text = _classify(error, (key,))
        return Check(
            name="ddd-api-key", status=status,
            detail="{} is set, and a test search {}: {}".format(
                DDD_KEY_VARIABLE,
                "failed" if status == INVALID else "could not be completed",
                text),
            fix=("Check the key, or request a new one."
                 if status == INVALID else "Try again when the network is back."),
            variable=DDD_KEY_VARIABLE,
            url=DDD_SIGNUP_URL if status == INVALID else None,
            required=False,
        )
    return Check(
        name="ddd-api-key", status=OK,
        detail="{} works: a test search returned results.".format(DDD_KEY_VARIABLE),
        fix=None, variable=DDD_KEY_VARIABLE, url=None, required=False,
    )


def check_opensubtitles_key(environ=None, opener=None, verify=True):
    environ = os.environ if environ is None else environ
    key = (environ.get(osub.API_KEY_VARIABLE) or "").strip()
    if not key:
        return Check(
            name="opensubtitles-api-key", status=MISSING,
            detail="No OpenSubtitles key, so dialogue cannot be searched for or "
                   "downloaded. A track from --video or your own .srt still works.",
            fix="Register a free API consumer, then export {}.".format(
                osub.API_KEY_VARIABLE),
            variable=osub.API_KEY_VARIABLE, url=OS_SIGNUP_URL, required=False,
        )
    if not verify:
        return Check(
            name="opensubtitles-api-key", status=UNVERIFIED,
            detail="{} is set. It was not checked against the API.".format(
                osub.API_KEY_VARIABLE),
            fix=None, variable=osub.API_KEY_VARIABLE, url=None, required=False,
        )
    try:
        kwargs = {"opener": opener} if opener is not None else {}
        osub.search_subtitles(key, _PROBE_TITLE, year=_PROBE_YEAR, **kwargs)
    except Exception as error:
        status, text = _classify(error, (key,))
        return Check(
            name="opensubtitles-api-key", status=status,
            detail="{} is set, and a test search {}: {}".format(
                osub.API_KEY_VARIABLE,
                "failed" if status == INVALID else "could not be completed",
                text),
            fix=("Check the key, or register a new consumer."
                 if status == INVALID else "Try again when the network is back."),
            variable=osub.API_KEY_VARIABLE,
            url=OS_SIGNUP_URL if status == INVALID else None,
            required=False,
        )
    return Check(
        name="opensubtitles-api-key", status=OK,
        detail="{} works: a test search returned results.".format(
            osub.API_KEY_VARIABLE),
        fix=None, variable=osub.API_KEY_VARIABLE, url=None, required=False,
    )


def check_opensubtitles_login(environ=None):
    """Presence only, and the detail says so rather than implying more.

    Signing in to prove these would spend the tightest rate limit the API has,
    on the one call this tool makes exactly once per download anyway.
    """
    environ = os.environ if environ is None else environ
    missing = [name for name in (osub.USERNAME_VARIABLE, osub.PASSWORD_VARIABLE)
               if not (environ.get(name) or "").strip()]
    if missing:
        return Check(
            name="opensubtitles-login", status=MISSING,
            detail="{} not set. Downloading a subtitle file signs in, so a "
                   "download needs both; searching does not.".format(
                       " and ".join(missing)),
            fix="Export {}.".format(" and ".join(missing)),
            variable=missing[0], url=OS_SIGNUP_URL, required=False,
        )
    return Check(
        name="opensubtitles-login", status=OK,
        detail="{} and {} are set. They are proved by the first download, not "
               "here.".format(osub.USERNAME_VARIABLE, osub.PASSWORD_VARIABLE),
        fix=None, variable=None, url=None, required=False,
    )


def collect(environ=None, opener=None, verify=True, which=None):
    """Every check, in the order a reader should act on them."""
    return [
        check_python(),
        check_ffmpeg(which=which),
        check_ddd(environ=environ, opener=opener, verify=verify),
        check_opensubtitles_key(environ=environ, opener=opener, verify=verify),
        check_opensubtitles_login(environ=environ),
    ]


def capabilities(checks):
    """What the machine can do now, phrased as the choices the README offers."""
    by_name = {check.name: check.status for check in checks}
    os_key_ok = by_name.get("opensubtitles-api-key") == OK
    return {
        # Always true, and stated so that "nothing is set up" never reads as
        # "nothing works": the tool's original path needs no account at all.
        "timestampsFromFile": True,
        "timestampsFromApi": by_name.get("ddd-api-key") == OK,
        "dialogueFromFile": True,
        "dialogueFromVideo": by_name.get("ffmpeg") == OK,
        "dialogueFromOpenSubtitlesSearch": os_key_ok,
        "dialogueFromOpenSubtitlesDownload": (
            os_key_ok and by_name.get("opensubtitles-login") == OK
        ),
    }


def next_steps(checks, able):
    """The shortest honest description of what to do next."""
    steps = []
    for check in checks:
        if check.status in (MISSING, INVALID) and check.fix:
            if check.url:
                steps.append("{} See {}".format(check.fix, check.url))
            else:
                steps.append(check.fix)
    if able["timestampsFromApi"] and (able["dialogueFromVideo"]
                                      or able["dialogueFromOpenSubtitlesDownload"]):
        steps.append(
            "Ready. Find a title with --ddd-search, then --dry-run to see which "
            "categories it has, then generate with --output."
        )
    elif not able["timestampsFromApi"]:
        steps.append(
            "Without an API key you can still generate: write the times into a "
            "JSON file and pass --events with --subtitles."
        )
    return steps


def export_block(variables):
    """Shell lines that put the named variables into the caller's environment.

    Values are read from the current environment, so this only ever re-states
    what the caller already has. It exists for `eval`, which is the only way a
    child process can affect its parent's shell.
    """
    lines = []
    for name in variables:
        value = os.environ.get(name)
        if value:
            lines.append("export {}={}".format(name, _quote(value)))
    return "\n".join(lines)


def _quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def summary(checks):
    """A one-line count, so a human sees the shape before the detail."""
    counts = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    parts = []
    for status in (OK, MISSING, INVALID, UNVERIFIED):
        if counts.get(status):
            parts.append("{} {}".format(counts[status], status))
    return ", ".join(parts)


def version():
    return __version__
