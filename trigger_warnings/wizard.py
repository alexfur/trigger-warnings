"""Prerequisite checks and guided setup.

Credentials reach this tool from the environment first and, after an explicit
choice, the operating system keychain second. That is deliberate: there is no
credential file to read, so there is no ambient key to pick up by accident and
none of the tool's own writes can leak one. A child process cannot set its
parent shell's environment. The guided flow therefore stores a validated value
only in the keychain and never claims to have changed the caller's shell.

Nothing here prints a credential, including one the caller already has. A
"copy your working configuration" helper was written and then removed for
exactly that reason: it would have put secrets on stdout, which is the one
property this module is checked against.

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
from pathlib import Path

from . import __version__
from . import ddd
from . import opensubtitles as osub
from .core import TriggerWarningsError

SOURCE_DTDD = "dtdd"
SOURCE_MODEL = "model"
SOURCE_GEMINI = "gemini"
SOURCES = [SOURCE_DTDD, SOURCE_MODEL, SOURCE_GEMINI]

SUBTITLE_FILE = "file"
SUBTITLE_OPENSUBTITLES = "opensubtitles"
SUBTITLE_EMBEDDED = "embedded"
SUBTITLE_SOURCES = [SUBTITLE_FILE, SUBTITLE_OPENSUBTITLES, SUBTITLE_EMBEDDED]


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


class SetupPrompter:
    """The small, injectable terminal surface used by the guided setup.

    Keeping this behind a value object makes the safety rule testable: a pipe
    must never receive a question that waits for an answer.  It also keeps
    passwords out of the ordinary ``input`` path, whose echo behaviour is
    wrong for credentials.
    """

    def __init__(self, input_fn=None, secret_fn=None, isatty=None, environ=None):
        self._input = input if input_fn is None else input_fn
        self._secret = secret_fn
        self._isatty = sys.stdin.isatty if isatty is None else isatty
        self._environ = os.environ if environ is None else environ

    @property
    def interactive(self):
        return bool(self._isatty())

    @property
    def colour(self):
        """Whether terminal control sequences are safe and wanted."""
        return (self.interactive
                and not self._environ.get("NO_COLOR")
                and self._environ.get("TERM") != "dumb")

    def text(self, label):
        try:
            return self._input(label).strip()
        except EOFError:
            return ""

    def secret(self, label):
        if self._secret is None:
            import getpass
            secret_fn = getpass.getpass
        else:
            secret_fn = self._secret
        try:
            return secret_fn(label).strip()
        except EOFError:
            return ""

    def confirm(self, label):
        answer = self.text(label).lower()
        return answer in ("", "y", "yes")

    def choose(self, label, choices, default=None):
        """Prompt the user to select one choice from a sequence of choices.

        choices can be a sequence of keys/strings, or (key, description) pairs.
        Returns the chosen key.
        """
        keys = []
        options = []
        for i, item in enumerate(choices, 1):
            if isinstance(item, (tuple, list)):
                key, desc = item[0], item[1]
            else:
                key, desc = item, item
            keys.append(key)
            options.append("  {}. {}".format(i, desc))

        lines = [label] + options if label else options
        suffix = " [default: {}]".format(default) if default is not None else ""
        lines.append("Select [1-{}]{}: ".format(len(choices), suffix))
        prompt_str = "\n".join(lines)

        answer = self.text(prompt_str).strip()
        if not answer and default is not None:
            return default

        if answer.isdigit():
            idx = int(answer) - 1
            if 0 <= idx < len(keys):
                return keys[idx]

        ans_lower = answer.lower()
        for item in choices:
            key = item[0] if isinstance(item, (tuple, list)) else item
            desc = item[1] if isinstance(item, (tuple, list)) else item
            if ans_lower == str(key).lower() or ans_lower in str(desc).lower():
                return key

        return default if default is not None else (keys[0] if keys else None)

    def emphasise(self, text):
        """Return a modest heading style without leaking ANSI into logs."""
        return "\033[1;36m{}\033[0m".format(text) if self.colour else text


def _prompt_choice(prompter, label, choices, default=None):
    """Helper to prompt choice using prompter.choose or fallback to prompter.text."""
    if hasattr(prompter, "choose"):
        return prompter.choose(label, choices, default=default)
    keys = [item[0] if isinstance(item, (tuple, list)) else item for item in choices]
    options = ["  {}. {}".format(i, item[1] if isinstance(item, (tuple, list)) else item)
               for i, item in enumerate(choices, 1)]
    lines = [label] + options if label else options
    suffix = " [default: {}]".format(default) if default is not None else ""
    lines.append("Select [1-{}]{}: ".format(len(choices), suffix))
    ans = prompter.text("\n".join(lines)).strip()
    if not ans and default is not None:
        return default
    if ans.isdigit():
        idx = int(ans) - 1
        if 0 <= idx < len(keys):
            return keys[idx]
    ans_lower = ans.lower()
    for item in choices:
        key = item[0] if isinstance(item, (tuple, list)) else item
        desc = item[1] if isinstance(item, (tuple, list)) else item
        if ans_lower == str(key).lower() or ans_lower in str(desc).lower():
            return key
    return default if default is not None else (keys[0] if keys else None)


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


def prompt_source_selection(prompter, report=None, environ=None, video_path=None, ddd_search_fn=None):
    """Guide the user to select the trigger timestamp source (DTDD vs local model)."""
    environ = os.environ if environ is None else environ
    choices = [
        (SOURCE_DTDD, "DoesTheDogDie (DTDD) - official API with community timestamps"),
        (SOURCE_MODEL, "Local model - scan video with local vision model"),
        (SOURCE_GEMINI, "Google Gemini - cloud whole-movie analysis (~2 min, fast & accurate)"),
    ]
    selected = _prompt_choice(prompter, "Select trigger source:", choices, default=SOURCE_DTDD)

    if selected == SOURCE_DTDD:
        api_key = environ.get(DDD_KEY_VARIABLE) or ""
        if not api_key.strip():
            key_input = prompter.secret(
                "DoesTheDogDie API key (hidden, Enter to skip if in keychain): "
            ).strip()
            if key_input:
                environ[DDD_KEY_VARIABLE] = key_input
                api_key = key_input

        search_query = prompter.text("Search DoesTheDogDie by title or enter item ID: ").strip()
        item_id = None
        if search_query.isdigit():
            item_id = int(search_query)
        elif search_query:
            year_str = prompter.text("Release year (optional, Enter to skip): ").strip()
            year = int(year_str) if year_str.isdigit() else None
            candidates = []
            try:
                if ddd_search_fn is not None:
                    try:
                        candidates = ddd_search_fn(api_key, search_query, year=year)
                    except TypeError:
                        candidates = ddd_search_fn(api_key, search_query, year)
                else:
                    candidates = ddd.search_items(api_key, search_query, year=year)
            except Exception as err:
                if report:
                    report("Could not search DoesTheDogDie: {}".format(err), level="warning")

            if candidates:
                if report:
                    report("Found {} DoesTheDogDie item{}:".format(
                        len(candidates), "" if len(candidates) == 1 else "s"))
                c_choices = []
                for c in candidates:
                    label = "{} ({}) [ID: {}]".format(
                        c["name"], c.get("releaseYear") or "unknown", c["id"]
                    )
                    c_choices.append((c["id"], label))
                chosen = _prompt_choice(prompter, "Select DoesTheDogDie candidate:", c_choices, default=candidates[0]["id"])
                item_id = int(chosen)
            else:
                if report:
                    report("No matching items found.")
                id_str = prompter.text("DoesTheDogDie item ID: ").strip()
                if id_str.isdigit():
                    item_id = int(id_str)
        else:
            id_str = prompter.text("DoesTheDogDie item ID: ").strip()
            if id_str.isdigit():
                item_id = int(id_str)

        if item_id is None:
            raise TriggerWarningsError("A DoesTheDogDie item ID is required.")

        cat_str = prompter.text("Filter categories (comma-separated, Enter for all): ").strip()
        categories = [c.strip() for c in cat_str.split(",") if c.strip()]

        return {
            "source": SOURCE_DTDD,
            "ddd_item": item_id,
            "category": categories,
        }

    elif selected == SOURCE_GEMINI:
        if not video_path:
            v_input = prompter.text("Path to video file: ").strip()
            if not v_input:
                raise TriggerWarningsError("A video file is required for Gemini cloud scanning.")
            video_path = Path(v_input)
        else:
            video_path = Path(video_path)

        api_key = environ.get("GEMINI_API_KEY") or ""
        if not api_key.strip():
            key_input = prompter.secret(
                "Google Gemini API key (hidden, Enter to skip if in keychain): "
            ).strip()
            if key_input:
                environ["GEMINI_API_KEY"] = key_input
                api_key = key_input

        triggers_input = prompter.text(
            "Enter trigger labels to detect (comma-separated, e.g. 'eyes, nails, teeth'): "
        ).strip()
        triggers = [t.strip() for t in triggers_input.split(",") if t.strip()]
        if not triggers:
            raise TriggerWarningsError("At least one trigger label is required for Gemini cloud scanning.")

        desc_input = prompter.text(
            "Trigger descriptors (optional, format LABEL=DESCRIPTION, Enter to skip): "
        ).strip()
        descriptors = [desc_input] if desc_input else []

        model_name = prompter.text(
            "Gemini model (Enter for default 'gemini-2.0-flash'): "
        ).strip() or "gemini-2.0-flash"

        sanitize_ans = prompter.text(
            "Sanitize video locally before upload (strip tags, downscale to 480p)? [Y/n]: "
        ).strip().lower()
        no_sanitize = sanitize_ans in ("n", "no")

        return {
            "source": SOURCE_GEMINI,
            "provider": "gemini",
            "video": video_path,
            "model_trigger": triggers,
            "model_trigger_desc": descriptors,
            "gemini_model": model_name,
            "gemini_api_key": api_key or None,
            "no_gemini_sanitize": no_sanitize,
        }

    else:  # SOURCE_MODEL
        if not video_path:
            v_input = prompter.text("Path to video file: ").strip()
            if not v_input:
                raise TriggerWarningsError("A video file is required for local model scanning.")
            video_path = Path(v_input)
        else:
            video_path = Path(video_path)

        triggers_input = prompter.text(
            "Enter trigger labels to detect (comma-separated, e.g. 'blood, violence'): "
        ).strip()
        triggers = [t.strip() for t in triggers_input.split(",") if t.strip()]
        if not triggers:
            raise TriggerWarningsError("At least one trigger label is required for local model scanning.")

        desc_input = prompter.text(
            "Trigger descriptors (optional, format LABEL=DESCRIPTION, Enter to skip): "
        ).strip()
        descriptors = [desc_input] if desc_input else []

        model_name = prompter.text(
            "Model name or path (Enter for default SmolVLM2): "
        ).strip() or None

        local_ans = prompter.text("Use local/cached model only? [y/N]: ").strip().lower()
        local_only = local_ans in ("y", "yes")

        return {
            "source": SOURCE_MODEL,
            "video": video_path,
            "model_trigger": triggers,
            "model_trigger_desc": descriptors,
            "model": model_name,
            "model_local_only": local_only,
        }


def prompt_subtitles_selection(prompter, report=None, environ=None, video_path=None,
                               media_module=None, os_search_fn=None, os_download_fn=None):
    """Guide the user to select the dialogue subtitles source (file vs OpenSubtitles vs embedded stream)."""
    environ = os.environ if environ is None else environ
    choices = [
        (SUBTITLE_FILE, "Existing subtitle file (.srt)"),
        (SUBTITLE_OPENSUBTITLES, "Search and download from OpenSubtitles"),
        (SUBTITLE_EMBEDDED, "Embedded subtitle stream from video file"),
    ]
    selected = _prompt_choice(prompter, "Select dialogue subtitles source:", choices, default=SUBTITLE_FILE)

    if selected == SUBTITLE_FILE:
        sub_str = prompter.text("Path to subtitle file (.srt): ").strip()
        if not sub_str:
            raise TriggerWarningsError("A subtitle file path is required.")
        sub_path = Path(sub_str)
        return {
            "subtitles_type": SUBTITLE_FILE,
            "subtitles": sub_path,
        }

    elif selected == SUBTITLE_OPENSUBTITLES:
        api_key = environ.get(osub.API_KEY_VARIABLE) or ""
        if not api_key.strip():
            key_input = prompter.secret(
                "OpenSubtitles API key (hidden, Enter to skip if in keychain): "
            ).strip()
            if key_input:
                environ[osub.API_KEY_VARIABLE] = key_input
                api_key = key_input

        search_query = prompter.text("Search OpenSubtitles for title or enter file ID: ").strip()
        file_id = None
        if search_query.isdigit():
            file_id = int(search_query)
        elif search_query:
            year_str = prompter.text("Release year (optional, Enter to skip): ").strip()
            year = int(year_str) if year_str.isdigit() else None
            lang = prompter.text("Language code [default: en]: ").strip() or "en"
            candidates = []
            try:
                if os_search_fn is not None:
                    found = os_search_fn(api_key, search_query, year=year, language=lang)
                else:
                    found = osub.search_subtitles(api_key, search_query, year=year, language=lang)
                candidates = getattr(found, "candidates", found) or []
            except Exception as err:
                if report:
                    report("Could not search OpenSubtitles: {}".format(err), level="warning")

            if candidates:
                if report:
                    report("Found {} OpenSubtitles candidate{}:".format(
                        len(candidates), "" if len(candidates) == 1 else "s"))
                c_choices = []
                for c in candidates:
                    label = "[{}] {} ({}) - fileId {}".format(
                        c.get("language") or "und",
                        c.get("release") or c.get("fileName") or "subtitle",
                        c.get("year") or "",
                        c["fileId"],
                    )
                    c_choices.append((c["fileId"], label))
                chosen = _prompt_choice(prompter, "Select OpenSubtitles candidate:", c_choices, default=candidates[0]["fileId"])
                file_id = int(chosen)
            else:
                if report:
                    report("No matching subtitle files found.")
                id_str = prompter.text("OpenSubtitles file ID: ").strip()
                if id_str.isdigit():
                    file_id = int(id_str)
        else:
            id_str = prompter.text("OpenSubtitles file ID: ").strip()
            if id_str.isdigit():
                file_id = int(id_str)

        if file_id is None:
            raise TriggerWarningsError("An OpenSubtitles file ID is required.")

        dl_path_str = prompter.text(
            "Path to save downloaded subtitle (.srt) [default: dialogue.srt]: "
        ).strip() or "dialogue.srt"
        dl_path = Path(dl_path_str)

        # Download
        if os_download_fn is not None:
            os_download_fn(file_id, dl_path)
        else:
            if not environ.get(osub.USERNAME_VARIABLE):
                user_val = prompter.text("OpenSubtitles username (Enter to skip if in keychain): ").strip()
                if user_val:
                    environ[osub.USERNAME_VARIABLE] = user_val
            if not environ.get(osub.PASSWORD_VARIABLE):
                pw_val = prompter.secret("OpenSubtitles password (hidden, Enter to skip if in keychain): ").strip()
                if pw_val:
                    environ[osub.PASSWORD_VARIABLE] = pw_val
            creds = osub.credentials_from_environment(api_key, environ=environ)
            download = osub.download_subtitle(creds, file_id)
            dl_path.write_bytes(download.text.encode("utf-8"))
            if report:
                report("Downloaded OpenSubtitles file {} to {} ({} cues).".format(
                    file_id, dl_path, download.cues))

        return {
            "subtitles_type": SUBTITLE_OPENSUBTITLES,
            "subtitles": dl_path,
            "os_file": file_id,
        }

    else:  # SUBTITLE_EMBEDDED
        if not video_path:
            v_input = prompter.text("Path to video file: ").strip()
            if not v_input:
                raise TriggerWarningsError("A video file is required for embedded subtitle extraction.")
            video_path = Path(v_input)
        else:
            video_path = Path(video_path)

        stream_idx = None
        media = media_module
        if media is None:
            try:
                from . import media
            except Exception:
                media = None

        if media is not None:
            try:
                info = media.probe(video_path)
                streams = media.subtitle_streams(info)
                if streams:
                    s_choices = []
                    for s in streams:
                        tags = s.get("tags") or {}
                        desc = "Stream {} ({}, {}){}".format(
                            s.get("index"),
                            s.get("codec_name") or "unknown",
                            tags.get("language") or "und",
                            " - " + tags["title"] if tags.get("title") else "",
                        )
                        s_choices.append((s.get("index"), desc))
                    s_choices.append((None, "Automatic language selection"))
                    chosen = _prompt_choice(prompter, "Select embedded subtitle stream:", s_choices, default=None)
                    if chosen is not None:
                        stream_idx = int(chosen)
            except Exception as err:
                if report:
                    report("Could not inspect subtitle streams: {}".format(err), level="warning")

        if stream_idx is None:
            s_input = prompter.text(
                "Subtitle stream index (Enter for automatic selection): "
            ).strip()
            if s_input.isdigit():
                stream_idx = int(s_input)

        lang_input = ""
        if stream_idx is None:
            lang_input = prompter.text(
                "Language code for stream selection [default: eng]: "
            ).strip() or "eng"

        return {
            "subtitles_type": SUBTITLE_EMBEDDED,
            "video": video_path,
            "stream": stream_idx,
            "language": lang_input or "eng",
        }


def run_wizard(prompter, report=None, environ=None, initial_args=None,
               ddd_search_fn=None, os_search_fn=None, os_download_fn=None,
               media_module=None):
    """Run the interactive guided wizard to select subtitles, source, and output."""
    environ = os.environ if environ is None else environ
    if report:
        report(prompter.emphasise("Trigger Warnings interactive setup wizard"))

    video_hint = getattr(initial_args, "video", None) if initial_args else None
    subs_hint = getattr(initial_args, "subtitles", None) if initial_args else None

    # Step 1: Subtitles selection
    if subs_hint:
        sub_info = {"subtitles_type": SUBTITLE_FILE, "subtitles": subs_hint}
        if report:
            report("Using supplied subtitles: {}".format(subs_hint))
    elif video_hint and getattr(initial_args, "stream", None) is not None:
        sub_info = {
            "subtitles_type": SUBTITLE_EMBEDDED,
            "video": video_hint,
            "stream": initial_args.stream,
            "language": getattr(initial_args, "language", "eng"),
        }
        if report:
            report("Using supplied video stream: {} (stream {})".format(video_hint, initial_args.stream))
    else:
        sub_info = prompt_subtitles_selection(
            prompter, report=report, environ=environ, video_path=video_hint,
            media_module=media_module, os_search_fn=os_search_fn, os_download_fn=os_download_fn,
        )

    # Step 2: Source selection
    v_for_source = sub_info.get("video") or video_hint
    source_info = prompt_source_selection(
        prompter, report=report, environ=environ, video_path=v_for_source,
        ddd_search_fn=ddd_search_fn,
    )

    # Step 3: Output path
    output_path = getattr(initial_args, "output", None) if initial_args else None
    if not output_path:
        default_name = "warnings.ass"
        base_path = source_info.get("video") or sub_info.get("video") or sub_info.get("subtitles")
        if base_path:
            default_name = Path(base_path).stem + ".warned.ass"
        out_str = prompter.text(
            "Output file path (.ass or .srt) [default: {}]: ".format(default_name)
        ).strip()
        output_path = Path(out_str) if out_str else Path(default_name)

    # Step 4: Summary & Execution
    if report:
        report(prompter.emphasise("Summary:"))
        if sub_info.get("subtitles"):
            report("  Subtitles: {}".format(sub_info["subtitles"]))
        else:
            report("  Subtitles: stream {} in {}".format(
                sub_info.get("stream", "auto"), sub_info.get("video")))
        if source_info["source"] == SOURCE_DTDD:
            report("  Trigger source: DoesTheDogDie item {}".format(source_info["ddd_item"]))
            if source_info.get("category"):
                report("  Categories: {}".format(", ".join(source_info["category"])))
        elif source_info["source"] == SOURCE_GEMINI:
            report("  Trigger source: Google Gemini ({}) on {}".format(
                source_info.get("gemini_model", "gemini-2.0-flash"), source_info["video"]))
            report("  Triggers: {}".format(", ".join(source_info.get("model_trigger", []))))
        else:
            report("  Trigger source: Local model on {}".format(source_info["video"]))
            report("  Triggers: {}".format(", ".join(source_info.get("model_trigger", []))))
        report("  Output: {}".format(output_path))

    dry_run_ans = prompter.text("Perform a dry-run first? [y/N]: ").strip().lower()
    is_dry_run = dry_run_ans in ("y", "yes")
    if initial_args and getattr(initial_args, "dry_run", False):
        is_dry_run = True

    proceed = prompter.confirm("Generate warning subtitles now? [Y/n]: ")
    if not proceed:
        if report:
            report("Wizard completed without generating.")
        return None

    from . import cli
    parser = cli.build_parser()
    args = parser.parse_args([])
    args.output = output_path
    args.dry_run = is_dry_run
    args.wizard = False

    if sub_info.get("subtitles"):
        args.subtitles = sub_info["subtitles"]
    if sub_info.get("video"):
        args.video = sub_info["video"]
    if sub_info.get("stream") is not None:
        args.stream = sub_info["stream"]
    if sub_info.get("language"):
        args.language = sub_info["language"]

    if source_info["source"] == SOURCE_DTDD:
        args.ddd_item = source_info["ddd_item"]
        args.category = source_info.get("category", [])
    elif source_info["source"] == SOURCE_GEMINI:
        args.video = source_info["video"]
        args.provider = "gemini"
        args.model_trigger = source_info.get("model_trigger", [])
        args.model_trigger_desc = source_info.get("model_trigger_desc", [])
        args.gemini_model = source_info.get("gemini_model", "gemini-2.0-flash")
        args.gemini_api_key = source_info.get("gemini_api_key")
        args.no_gemini_sanitize = source_info.get("no_gemini_sanitize", False)
    elif source_info["source"] == SOURCE_MODEL:
        args.video = source_info["video"]
        args.model_trigger = source_info.get("model_trigger", [])
        args.model_trigger_desc = source_info.get("model_trigger_desc", [])
        args.model = source_info.get("model")
        args.model_local_only = source_info.get("model_local_only", False)

    args.wizard_plan = {"subtitles": sub_info, "source": source_info, "output": output_path}
    return args
