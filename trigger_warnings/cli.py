"""Command line entry point: argument handling, reporting and file publication.

This layer owns every side effect. :mod:`trigger_warnings.core` decides what the
output should contain and :mod:`trigger_warnings.media` talks to FFmpeg, but only
this module creates files, and it does so under three rules:

*   **Nothing is ever overwritten.** Files are created with ``O_EXCL``, so the
    no-clobber check is the create itself and cannot lose a race with another
    process. An input path may not be reused as an output path.
*   **Nothing is published until everything has succeeded.** The subtitle text is
    built in memory and verification runs against that text, so a failed render
    leaves no output file to be mistaken for a checked one.
*   **A partial run is rolled back.** If the preview cannot be written, the output
    file this run created is removed again. Only files this run created are
    touched.

``media`` is imported lazily, inside the functions that need it, so a
subtitle-only run works with no FFmpeg installed.
"""

import argparse
import datetime
import errno
import json
import os
import re
import sys
from pathlib import Path

from . import __version__, core
from .core import TriggerWarningsError

EXIT_OK = 0
EXIT_ERROR = 2

_DEFAULT_LANGUAGE = "eng"
# OpenSubtitles speaks ISO 639-1, so its default is not --language's.
_DEFAULT_OS_LANGUAGE = "en"

_REDACTED = "<redacted>"
# An option name, and nothing else: a leading dash, a letter, then name
# characters. Anything failing this is treated as a value.
_OPTION_NAME_RE = re.compile(r"^--?[A-Za-z][A-Za-z0-9_-]*$")

_SYNC_CAVEAT = (
    "Timing has not been checked against this video. Play the file and check a "
    "known warning, then check synchronisation again later in the running time. "
    "A warning ending is not an all-clear."
)
_RENDER_CAVEAT = (
    "The preview proves only that FFmpeg drew a frame with this subtitle file. It "
    "does not prove the timestamps describe this video, and another player may "
    "position the text differently."
)


def _redact_argument(text):
    """Name an unrecognised argument without echoing any value it carried.

    argparse's own message joins the leftover argv verbatim, so a mistyped
    ``--ddd-api-key`` would put the key itself in the error text. Under
    ``--json`` that text is a machine-readable field an agent may log or print
    back. The option name is what the caller needs to fix the command; the
    value never is.

    A dash-led token is taken to be a name and anything else a value, because a
    caller writes a value where an option was expected far more often than the
    reverse.
    """
    name = text.partition("=")[0]
    if not _OPTION_NAME_RE.match(name):
        return _REDACTED
    return name if name == text else name + "=" + _REDACTED


class _ArgumentParser(argparse.ArgumentParser):
    """Raise a normal tool error so ``--json`` can report argument failures."""

    def error(self, message):
        raise TriggerWarningsError(message)

    def _valueless_option_strings(self):
        """Every spelling of every flag that takes no value, ``-h`` included."""
        return {option
                for action in self._actions if action.nargs == 0
                for option in action.option_strings}

    def _reject_attached_values(self, args):
        """Reject ``--flag=value`` where the flag takes no value.

        argparse answers this with ``ignored explicit argument 'value'``, which
        hands the value straight back, so a caller who put an API key on the
        wrong flag reads it in the error. That message is built deep inside
        ``_parse_known_args``, before :meth:`error` ever sees it, so the only
        place to stop it is ahead of the parse.

        The scan stops at ``--`` because a token after the separator is a
        value, not a flag.
        """
        valueless = self._valueless_option_strings()
        for item in args:
            if item == "--":
                return
            name, separator, _value = item.partition("=")
            if separator and name in valueless:
                raise TriggerWarningsError(
                    "argument {}: this flag takes no value. Drop the '=' and "
                    "everything after it. The value is redacted here rather "
                    "than echoed back.".format(name)
                )

    def _supplied_options(self, args):
        """The option strings actually written on the command line.

        argparse cannot tell a flag left at its default from one spelled out
        with that same value, so ``--lead 20`` and no ``--lead`` at all were
        indistinguishable and the first rode along silently into a mode that
        ignores it. Scanning argv is the only place that distinction survives.

        The scan stops at ``--`` because a token after the separator is a value.
        """
        known = set()
        for action in self._actions:
            known.update(action.option_strings)
        supplied = set()
        for item in args:
            if item == "--":
                break
            name = item.partition("=")[0]
            if name in known:
                supplied.add(name)
        return supplied

    def parse_args(self, args=None, namespace=None):
        """Reject unknown or over-supplied arguments by name only, never by value.

        The base implementation formats the offending argv into the message
        itself, so both checks have to be intercepted here rather than in
        :meth:`error`, where the value has already been interpolated.
        """
        if args is None:
            args = sys.argv[1:]
        self._reject_attached_values(args)
        namespace, extras = self.parse_known_args(args, namespace)
        namespace.supplied_options = self._supplied_options(args)
        if extras:
            raise TriggerWarningsError(
                "unrecognized arguments: {}. A value attached to one is "
                "redacted, so check the spelling against --help.".format(
                    " ".join(_redact_argument(item) for item in extras)
                )
            )
        return namespace


def _json_requested(arguments):
    """Answer, before parsing, whether this run must speak JSON.

    Both spellings count. ``--json=x`` is an argument error rather than a way to
    switch the flag off, but argparse reaches that verdict well after this
    answer is needed, and the error itself has to be reported somewhere. The
    scan stops at ``--`` so an argument after the separator, which is a value
    and not a flag, cannot change the output format.

    This is not cosmetic. The answer decides where progress messages go, so a
    pre-parse answer that disagrees with the parsed flag prints every message on
    stderr *and* repeats it inside the JSON object on stdout.
    """
    for item in arguments:
        if item == "--":
            break
        if item == "--json" or item.startswith("--json="):
            return True
    return False


# ------------------------------------------------------------------- argument types


def _non_negative_seconds(text):
    value = _finite_float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative, got {!r}".format(text))
    return value


def _finite_float(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a number of seconds, got {!r}".format(text))
    if value != value or value in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("must be a finite number, got {!r}".format(text))
    return value


def _stream_index(text):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be an integer stream index, got {!r}".format(text))
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative, got {!r}".format(text))
    return value


def _ddd_item_id(text):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a positive DoesTheDogDie item id")
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive DoesTheDogDie item id")
    return value


def _year(text):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a four-digit year")
    if not 1 <= value <= 9999:
        raise argparse.ArgumentTypeError("must be a four-digit year")
    return value


def _os_file_id(text):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a positive OpenSubtitles file id")
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive OpenSubtitles file id")
    return value


def _os_number(text):
    """A season or episode number. Zero is allowed; specials use it."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a whole number")
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative, got {!r}".format(text))
    return value


def build_parser():
    parser = _ArgumentParser(
        prog="trigger-warnings",
        # No abbreviations. `--ddd` is ambiguous today and every prefix is one
        # new flag away from changing meaning, which would silently redirect an
        # agent's command rather than fail it.
        allow_abbrev=False,
        description=(
            "Add advance warnings to a dialogue subtitle track using local event "
            "timestamps or an explicit official DoesTheDogDie API request. This "
            "tool does not detect scenes and cannot check that timestamps "
            "describe your video."
        ),
        epilog=(
            "Existing files are never replaced. A missing warning can mean missing "
            "data, a different video edition or a playback problem."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--subtitles", type=Path, metavar="PATH",
        help="input SRT dialogue track; without it, --video supplies one",
    )
    parser.add_argument(
        "--events", type=Path, metavar="PATH",
        help="local event JSON array; mutually exclusive with --ddd-item",
    )
    parser.add_argument(
        "--ddd-item", type=_ddd_item_id, metavar="ID",
        help="official DoesTheDogDie API item id; mutually exclusive with --events",
    )
    parser.add_argument(
        "--ddd-api-key", metavar="KEY",
        help="official DoesTheDogDie API key; alternatively set DDD_API_KEY",
    )
    parser.add_argument(
        "--ddd-search", metavar="TITLE",
        help="search official DoesTheDogDie API titles; choose an ID before generating",
    )
    parser.add_argument(
        "--ddd-year", type=_year, metavar="YEAR",
        help="release year to narrow --ddd-search",
    )
    parser.add_argument(
        "--os-search", metavar="TITLE",
        help="search OpenSubtitles for a dialogue track; choose a file id "
             "before downloading",
    )
    parser.add_argument(
        "--os-year", type=_year, metavar="YEAR",
        help="release year to narrow --os-search",
    )
    parser.add_argument(
        "--os-season", type=_os_number, metavar="N",
        help="season number to narrow --os-search",
    )
    parser.add_argument(
        "--os-episode", type=_os_number, metavar="N",
        help="episode number to narrow --os-search",
    )
    parser.add_argument(
        "--os-language", default=_DEFAULT_OS_LANGUAGE, metavar="CODE",
        help="ISO 639-1 language for --os-search (default: {})".format(
            _DEFAULT_OS_LANGUAGE
        ),
    )
    parser.add_argument(
        "--os-file", type=_os_file_id, metavar="FILE_ID",
        help="download one OpenSubtitles file id from --os-search into "
             "--output PATH.srt; signs in with OPENSUBTITLES_USERNAME and "
             "OPENSUBTITLES_PASSWORD",
    )
    parser.add_argument(
        "--os-api-key", metavar="KEY",
        help="OpenSubtitles API key; alternatively set OPENSUBTITLES_API_KEY. "
             "The username and password have no flag and are read from the "
             "environment only",
    )
    parser.add_argument(
        "--output", type=Path, metavar="PATH",
        help="new .ass or .srt file to create; required for generation",
    )
    parser.add_argument(
        "--category", action="append", default=[], metavar="LABEL",
        help="select a category, exact and case-insensitive; repeat for several; "
             "omitted selects every category",
    )
    parser.add_argument(
        "--lead", type=_non_negative_seconds, default=20.0, metavar="SECONDS",
        help="advance warning duration before the event start (default: 20)",
    )
    parser.add_argument(
        "--tail", type=_non_negative_seconds, default=0.0, metavar="SECONDS",
        help="extra time after a known event end (default: 0)",
    )
    parser.add_argument(
        "--offset", type=_finite_float, default=0.0, metavar="SECONDS",
        help="constant signed shift applied to event times only, never to "
             "dialogue (default: 0)",
    )
    parser.add_argument(
        "--video", type=Path, metavar="PATH",
        help="video for subtitle extraction, duration checks and preview rendering",
    )
    parser.add_argument(
        "--language", default=_DEFAULT_LANGUAGE, metavar="CODE",
        help="language for automatic subtitle stream selection (default: {})".format(
            _DEFAULT_LANGUAGE
        ),
    )
    parser.add_argument(
        "--stream", type=_stream_index, metavar="INDEX",
        help="explicit absolute subtitle stream index from --list-streams",
    )
    parser.add_argument(
        "--list-streams", action="store_true",
        help="list the subtitle streams in --video and exit, without generating output",
    )
    parser.add_argument(
        "--verify", type=Path, metavar="PATH",
        help="render a new PNG inside a warning window; requires --video",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate inputs and show the generation plan without writing files",
    )
    parser.add_argument(
        "--provenance", type=Path, metavar="PATH",
        help="new JSON sidecar recording source and generation details",
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="write one machine-readable result or error object to standard "
             "output; --help and --version are the exception and always print "
             "plain text, so run them on their own",
    )
    return parser


# ----------------------------------------------------------------------- validation


def _utc_timestamp():
    """The current time as a whole-second UTC ISO 8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _resolve(path):
    """Absolute, symlink-resolved path, usable for aliasing comparisons."""
    return Path(os.path.realpath(str(path)))


def _check_inputs_exist(pairs):
    for flag, path in pairs:
        if path is None:
            continue
        if not path.exists():
            raise TriggerWarningsError("{} does not exist: {}".format(flag, path))
        if path.is_dir():
            raise TriggerWarningsError("{} is a directory: {}".format(flag, path))


def _check_publication_targets(targets, inputs):
    """Refuse to write over anything: an existing file, an input, or a sibling target.

    This is an early, readable rejection. The create itself still uses ``O_EXCL``,
    which is what actually makes the guarantee hold against a concurrent writer.
    Symlinks are the exception that has to be caught here, because ``O_EXCL``
    only refuses them on POSIX.
    """
    seen = {}
    for flag, path in targets:
        if path is None:
            continue
        # lstat, not exists(): a link pointing at nothing is invisible to every
        # check that follows it, so a naive existence test would write straight
        # through it to a path the user never named. POSIX open(O_CREAT|O_EXCL)
        # refuses a symlink whatever it points at, but Windows resolves the
        # reparse point and creates the target, so this check -- not the create
        # -- is what makes the refusal hold on every platform.
        if path.is_symlink():
            raise TriggerWarningsError(
                "{} is a symbolic link: {}. Links are never followed, because "
                "the file written would not be the one named; pass the real "
                "path you want created.".format(flag, path)
            )
        if path.exists():
            raise TriggerWarningsError(
                "{} already exists: {}. Existing files are never replaced; choose a "
                "new name.".format(flag, path)
            )
        resolved = _resolve(path)
        if resolved in seen:
            raise TriggerWarningsError(
                "{} and {} are the same path: {}".format(seen[resolved], flag, path)
            )
        seen[resolved] = flag
        # Check the parent the user named. The path itself is known not to be a
        # link by now, so this is the directory the file really lands in.
        parent = path.parent
        if not parent.is_dir():
            raise TriggerWarningsError(
                "{} is in a directory that does not exist: {}. Create it first; "
                "the tool does not make directories.".format(flag, parent)
            )
        for input_flag, input_path in inputs:
            if input_path is None:
                continue
            if resolved == _resolve(input_path):
                raise TriggerWarningsError(
                    "{} would write over the {} input: {}. The tool never modifies "
                    "its own inputs.".format(flag, input_flag, path)
                )


def _read_text(path, flag):
    try:
        data = path.read_bytes()
    except OSError as error:
        raise TriggerWarningsError("cannot read {} {}: {}".format(flag, path, error))
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise TriggerWarningsError(
            "{} {} is not UTF-8. Convert it first; guessing an encoding would "
            "corrupt the dialogue.".format(flag, path)
        )


# ----------------------------------------------------------------------- publication


def _write_exclusive(path, payload, created):
    """Create ``path`` and write ``payload`` bytes, failing if it already exists."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    try:
        handle = os.open(str(path), flags, 0o644)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise TriggerWarningsError(
                "{} appeared while this run was working; refusing to replace "
                "it".format(path)
            )
        raise TriggerWarningsError("cannot create {}: {}".format(path, error))
    created.append(path)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
    except OSError as error:
        raise TriggerWarningsError("cannot write {}: {}".format(path, error))


def _rollback(created):
    for path in created:
        try:
            os.unlink(str(path))
        except OSError:
            pass


def publish(items):
    """Create every target atomically, removing this run's own files on failure.

    ``items`` is a sequence of ``(path, payload_bytes)``. Either all of them exist
    afterwards or none of the ones this run created do.
    """
    created = []
    try:
        for path, payload in items:
            _write_exclusive(path, payload, created)
    except BaseException:
        _rollback(created)
        raise
    return [path for path, _ in items]


# --------------------------------------------------------------------------- media


def _load_media():
    try:
        from . import media  # imported here so a subtitle-only run needs no FFmpeg
    except Exception as error:
        raise TriggerWarningsError(
            "could not load local media support: {}".format(error)
        )
    return media


def _is_text_stream(media, stream):
    checker = getattr(media, "is_text_subtitle", None)
    if callable(checker):
        return checker(stream)
    codecs = getattr(media, "TEXT_SUBTITLE_CODECS", None)
    if codecs is None:
        return None
    return stream.get("codec_name") in codecs


def _describe_stream(media, stream):
    tags = stream.get("tags") or {}
    disposition = stream.get("disposition") or {}
    flags = sorted(name for name, on in disposition.items() if on)
    text = _is_text_stream(media, stream)
    kind = "text" if text else ("bitmap, needs transcription" if text is False else "")
    return "  {:<6} {:<12} {:<6} {:<10} {}".format(
        stream.get("index", "?"),
        stream.get("codec_name") or "?",
        tags.get("language") or "und",
        kind,
        "; ".join(part for part in (tags.get("title"), ", ".join(flags)) if part),
    )


def list_streams(args, report):
    media = _load_media()
    info = media.probe(args.video)
    streams = media.subtitle_streams(info)
    if not streams:
        raise TriggerWarningsError(
            "{} has no subtitle streams. Supply a dialogue track with "
            "--subtitles.".format(args.video)
        )
    duration_ms = media.duration_ms(info)
    records = []
    for stream in streams:
        tags = stream.get("tags") or {}
        records.append({
            "index": stream.get("index"),
            "codec": stream.get("codec_name"),
            "language": tags.get("language"),
            "title": tags.get("title"),
            "text": _is_text_stream(media, stream),
        })
    if not args.json_output:
        print("Subtitle streams in {} ({:.3f}s):".format(args.video, duration_ms / 1000.0))
        print("  {:<6} {:<12} {:<6} {:<10} {}".format(
            "index", "codec", "lang", "kind", "title / flags"))
        for stream in streams:
            print(_describe_stream(media, stream))
        # Keep the table ahead of the stderr guidance when callers redirect both
        # streams into one file.
        sys.stdout.flush()
        report(
            "Pass one of these absolute indices as --stream INDEX. Selecting a stream "
            "does not confirm it matches your video edition."
        )
    return {"video": str(args.video), "durationMs": duration_ms, "streams": records}


def _obtain_subtitles(args, report):
    """Return ``(srt_text, duration_ms_or_None)``, extracting from video if needed."""
    duration_ms = None
    media = None
    info = None

    if args.video is not None:
        media = _load_media()
        info = media.probe(args.video)
        duration_ms = media.duration_ms(info)

    if args.subtitles is not None:
        if args.stream is not None:
            raise TriggerWarningsError(
                "--stream selects an embedded track, but --subtitles was supplied. "
                "Drop one of them so it is clear which dialogue is used."
            )
        return _read_text(args.subtitles, "--subtitles"), duration_ms, media, info

    if args.video is None:
        raise TriggerWarningsError(
            "supply --subtitles, or --video to extract an embedded track"
        )

    # select_stream fails loudly on ambiguity, including the regular/SDH pair.
    # Its message names the candidates, so it is passed through untouched.
    stream = media.select_stream(info, language=args.language, index=args.stream)
    index = stream["index"]
    tags = stream.get("tags") or {}
    report("Extracted subtitle stream {} ({}, {}){}.".format(
        index,
        stream.get("codec_name") or "unknown codec",
        (tags.get("language") or "language not tagged"),
        ", titled {!r}".format(tags["title"]) if tags.get("title") else "",
    ))
    return media.extract_subtitles(args.video, index), duration_ms, media, info


# ------------------------------------------------------------------------ reporting


def _report_categories(kept, dropped, report):
    selected = core.category_counts(kept)
    excluded = core.category_counts(dropped)
    report("Selected {}: {}".format(
        "category" if len(selected) == 1 else "categories",
        ", ".join("{} ({} event{})".format(label, count, "" if count == 1 else "s")
                  for label, count in selected),
    ))
    if excluded:
        report("Excluded {}: {}".format(
            "category" if len(excluded) == 1 else "categories",
            ", ".join("{} ({} event{})".format(label, count, "" if count == 1 else "s")
                      for label, count in excluded),
        ))
    else:
        report("Excluded categories: none.")


def _ddd_api_key(args):
    return args.ddd_api_key or os.environ.get("DDD_API_KEY")


def _load_ddd_events(args, report):
    """Fetch one user's official API data without persisting a copy of it."""
    from . import ddd

    source = ddd.load_item_events(_ddd_api_key(args), args.ddd_item)
    report(ddd.ATTRIBUTION)
    report(
        "Fetched {} timestamped DoesTheDogDie rating{} for item {} ({} community, "
        "{} Scene Alert{}).".format(
            len(source.events),
            "" if len(source.events) == 1 else "s",
            args.ddd_item,
            source.community,
            source.scene_alerts,
            "" if source.scene_alerts == 1 else "s",
        )
    )
    for note in source.notes:
        report(note)
    return source


def _search_ddd_items(args, report):
    from . import ddd

    candidates = ddd.search_items(
        _ddd_api_key(args), args.ddd_search, args.ddd_year
    )
    report(ddd.ATTRIBUTION)
    if not args.json_output:
        if candidates:
            print("DoesTheDogDie candidates:")
            print("  {:<8} {:<6} {:<12} {}".format("id", "year", "type", "title"))
            for candidate in candidates:
                print("  {:<8} {:<6} {:<12} {}".format(
                    candidate["id"],
                    candidate["releaseYear"] if candidate["releaseYear"] is not None else "",
                    candidate["itemType"] or "",
                    candidate["name"],
                ))
        else:
            print("No DoesTheDogDie candidates matched. This does not establish that "
                  "the title is absent from the service.")
    return candidates


# ------------------------------------------------------------------------- the run


def _source_metadata(args, source=None):
    if source is None:
        return {"kind": "local-events", "eventsPath": str(args.events)}
    return {
        "kind": "does-the-dog-die",
        "itemId": args.ddd_item,
        "timestampedRatings": len(source.events),
        "communityRatings": source.community,
        "sceneAlerts": source.scene_alerts,
        "queriedAt": _utc_timestamp(),
        "attribution": "Powered by DoesTheDogDie.com",
        "notes": list(source.notes),
    }


def _provenance_payload(args, source, kept, dropped, windows, dialogue, notes):
    """Return a human-auditable sidecar without secrets or source text."""
    data = {
        "schemaVersion": 1,
        "generatedAt": _utc_timestamp(),
        "source": source,
        "output": str(args.output),
        "video": str(args.video) if args.video is not None else None,
        "categories": {
            "requested": list(args.category),
            "selectedEvents": len(kept),
            "excludedEvents": len(dropped),
        },
        "timing": {
            "leadSeconds": args.lead,
            "tailSeconds": args.tail,
            "offsetSeconds": args.offset,
        },
        "result": {
            "dialogueCues": len(dialogue),
            "warningWindows": len(windows),
            "notes": list(notes),
        },
    }
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _search_os_subtitles(args, report):
    """List candidates. It never picks one, and it never sends the password."""
    from . import opensubtitles

    found = opensubtitles.search_subtitles(
        opensubtitles.api_key_from_environment(args.os_api_key),
        args.os_search,
        year=args.os_year,
        season=args.os_season,
        episode=args.os_episode,
        language=args.os_language,
    )
    report(opensubtitles.ATTRIBUTION)
    for note in found.notes:
        report(note)
    if not args.json_output:
        if found.candidates:
            print("OpenSubtitles candidates:")
            print("  {:<10} {:<5} {:<6} {:<7} {}".format(
                "fileId", "lang", "year", "trusted", "release or file name"))
            for candidate in found.candidates:
                print("  {:<10} {:<5} {:<6} {:<7} {}".format(
                    candidate["fileId"],
                    candidate["language"] or "",
                    candidate["year"] if candidate["year"] is not None else "",
                    "yes" if candidate["fromTrusted"] else "",
                    candidate["release"] or candidate["fileName"] or "",
                ))
            # Keep the table ahead of the stderr guidance when callers redirect
            # both streams into one file.
            sys.stdout.flush()
            report(
                "Pass one of these file ids as --os-file ID with --output PATH.srt. "
                "Listing a subtitle does not confirm it matches your video edition."
            )
        else:
            print("No OpenSubtitles candidates matched. This does not establish "
                  "that the title has no subtitles.")
    return found


def _os_provenance_payload(args, source):
    """An audit record of one download, with nothing secret in it.

    Deliberately absent: the API key, the username, the password, the session
    token, the temporary download link, and the dialogue itself. What is left is
    what a reader needs to know where the file came from and what it cost.
    """
    stamp = _utc_timestamp()
    data = {
        "schemaVersion": 1,
        "generatedAt": stamp,
        "source": {
            "kind": "opensubtitles",
            "fileId": args.os_file,
            "fileName": source.file_name,
            "downloadedAt": stamp,
            "attribution": _os_attribution(),
            "downloadsUsedToday": source.used,
            "downloadsRemainingToday": source.remaining,
            "notes": list(source.notes),
        },
        "output": str(args.output),
        "result": {"dialogueCues": source.cues},
    }
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _os_attribution():
    from . import opensubtitles

    return opensubtitles.ATTRIBUTION


def _download_os_subtitle(args, report, result):
    """Spend one download and publish the file, or publish nothing at all."""
    from . import opensubtitles

    if args.output is None:
        raise TriggerWarningsError(
            "--os-file downloads a subtitle file, so it needs --output PATH.srt"
        )
    if args.output.suffix.lower() != ".srt":
        raise TriggerWarningsError(
            "--os-file writes the track exactly as OpenSubtitles supplied it, so "
            "--output must end in .srt, got {!r}".format(args.output.name)
        )
    # Checked before signing in: a download costs quota from a small daily
    # allowance, and spending one to then refuse to write the file wastes it.
    _check_publication_targets(
        [("--output", args.output), ("--provenance", args.provenance)], []
    )

    credentials = opensubtitles.credentials_from_environment(args.os_api_key)
    source = opensubtitles.download_subtitle(credentials, args.os_file)
    report(opensubtitles.ATTRIBUTION)
    report("Downloaded OpenSubtitles file {}{}.".format(
        args.os_file,
        " ({})".format(source.file_name) if source.file_name else "",
    ))
    for note in source.notes:
        report(note)

    # The client parsed the body already, inside its own scrubbing guard, and
    # hands back the count. Parsing it a second time here would put a rejected
    # CDN error document -- which can quote the download token -- through an
    # unguarded error path.
    targets = [(args.output, source.text.encode("utf-8"))]
    if args.provenance is not None:
        targets.append((args.provenance, _os_provenance_payload(args, source)))

    result.update({
        "mode": "os-download",
        "source": {
            "kind": "opensubtitles",
            "fileId": args.os_file,
            "fileName": source.file_name,
            "attribution": opensubtitles.ATTRIBUTION,
            "downloadsRemainingToday": source.remaining,
        },
        "output": str(args.output),
        "provenance": str(args.provenance) if args.provenance is not None else None,
        "filesWritten": [],
        "dialogueCues": source.cues,
        "notes": list(source.notes),
    })
    result["filesWritten"] = [str(path) for path in publish(targets)]

    report("Wrote {} ({} dialogue cue{}).".format(
        args.output, source.cues, "" if source.cues == 1 else "s"))
    if args.provenance is not None:
        report("Wrote {}.".format(args.provenance))
    report(
        "This is the dialogue track only; no warnings were added. Run the tool "
        "again with --subtitles {} to generate them.".format(args.output)
    )
    return EXIT_OK


def _reject_os_flags(args, mode, allowed):
    """Refuse any flag belonging to another mode, naming every one supplied."""
    candidates = (
        ("--subtitles", args.subtitles, None),
        ("--events", args.events, None),
        ("--output", args.output, None),
        ("--video", args.video, None),
        ("--stream", args.stream, None),
        ("--verify", args.verify, None),
        ("--provenance", args.provenance, None),
        ("--ddd-item", args.ddd_item, None),
        ("--ddd-search", args.ddd_search, None),
        ("--ddd-year", args.ddd_year, None),
        ("--ddd-api-key", args.ddd_api_key, None),
        ("--os-search", args.os_search, None),
        ("--os-file", args.os_file, None),
        ("--os-year", args.os_year, None),
        ("--os-season", args.os_season, None),
        ("--os-episode", args.os_episode, None),
        ("--os-language", args.os_language, _DEFAULT_OS_LANGUAGE),
        ("--category", args.category, []),
        ("--lead", args.lead, 20.0),
        ("--tail", args.tail, 0.0),
        ("--offset", args.offset, 0.0),
        ("--dry-run", args.dry_run, False),
        ("--list-streams", args.list_streams, False),
        ("--language", args.language, _DEFAULT_LANGUAGE),
    )
    # Prefer the flags actually written over a value comparison: a flag set
    # to its own default is still a flag the caller asked for, and answering
    # "accepted" there tells them a lead or a language was applied when the
    # mode ignores both. The value comparison remains for a hand-built
    # Namespace, which carries no record of an argv.
    written = getattr(args, "supplied_options", None)
    supplied = [
        flag for flag, value, default in candidates
        if flag not in allowed
        and (flag in written if written is not None else value != default)
    ]
    if supplied:
        raise TriggerWarningsError("{}; run it without {}".format(
            mode, ", ".join(supplied)
        ))


def _reject_search_generation_flags(args):
    supplied = []
    for flag, value, default in (
        ("--subtitles", args.subtitles, None),
        ("--events", args.events, None),
        ("--ddd-item", args.ddd_item, None),
        ("--output", args.output, None),
        ("--video", args.video, None),
        ("--stream", args.stream, None),
        ("--verify", args.verify, None),
        ("--provenance", args.provenance, None),
        ("--category", args.category, []),
        ("--lead", args.lead, 20.0),
        ("--tail", args.tail, 0.0),
        ("--offset", args.offset, 0.0),
        ("--dry-run", args.dry_run, False),
        # A search reads no subtitle stream, so a language here means the
        # caller expected a generation run and would get a title list instead.
        ("--language", args.language, _DEFAULT_LANGUAGE),
        ("--os-search", args.os_search, None),
        ("--os-file", args.os_file, None),
        ("--os-api-key", args.os_api_key, None),
        ("--os-year", args.os_year, None),
        ("--os-season", args.os_season, None),
        ("--os-episode", args.os_episode, None),
        ("--os-language", args.os_language, _DEFAULT_OS_LANGUAGE),
    ):
        if value != default:
            supplied.append(flag)
    if supplied:
        raise TriggerWarningsError(
            "--ddd-search only searches for an item ID; run it without {}".format(
                ", ".join(supplied)
            )
        )


def run(args, report, result=None):
    """Run one command and optionally fill a machine-readable result mapping."""
    if result is None:
        result = {}

    if args.list_streams:
        if args.video is None:
            raise TriggerWarningsError("--list-streams needs --video")
        for flag, value in (("--output", args.output), ("--verify", args.verify),
                            ("--events", args.events), ("--ddd-item", args.ddd_item),
                            ("--ddd-api-key", args.ddd_api_key),
                            ("--ddd-search", args.ddd_search),
                            ("--ddd-year", args.ddd_year),
                            ("--provenance", args.provenance),
                            ("--os-search", args.os_search),
                            ("--os-file", args.os_file),
                            ("--os-api-key", args.os_api_key),
                            ("--os-year", args.os_year),
                            ("--os-season", args.os_season),
                            ("--os-episode", args.os_episode)):
            if value is not None:
                raise TriggerWarningsError(
                    "--list-streams does not generate output; run it without "
                    "{}".format(flag)
                )
        if args.dry_run:
            raise TriggerWarningsError("--list-streams and --dry-run cannot be combined")
        _check_inputs_exist([("--video", args.video)])
        result.update({
            "mode": "list-streams",
            "filesWritten": [],
            "listStreams": list_streams(args, report),
        })
        return EXIT_OK

    if args.os_search is not None:
        _reject_os_flags(
            args,
            "--os-search only lists subtitle files to choose from",
            allowed=("--os-search", "--os-year", "--os-season", "--os-episode",
                     "--os-language"),
        )
        found = _search_os_subtitles(args, report)
        result.update({
            "mode": "os-search",
            "source": {"kind": "opensubtitles", "attribution": _os_attribution()},
            "filesWritten": [],
            "candidates": found.candidates,
            "notes": list(found.notes),
        })
        return EXIT_OK

    if args.os_file is not None:
        _reject_os_flags(
            args,
            "--os-file downloads one dialogue track and adds no warnings",
            allowed=("--os-file", "--output", "--provenance"),
        )
        return _download_os_subtitle(args, report, result)

    for flag, value in (("--os-year", args.os_year),
                        ("--os-season", args.os_season),
                        ("--os-episode", args.os_episode)):
        if value is not None:
            raise TriggerWarningsError("{} needs --os-search".format(flag))
    if args.os_language != _DEFAULT_OS_LANGUAGE:
        raise TriggerWarningsError(
            "--os-language narrows --os-search; --language selects an embedded "
            "stream for a generation run"
        )
    if args.os_api_key is not None:
        raise TriggerWarningsError("--os-api-key needs --os-search or --os-file")

    if args.ddd_search is not None:
        _reject_search_generation_flags(args)
        result.update({
            "mode": "ddd-search",
            "source": {"kind": "does-the-dog-die", "attribution": "Powered by DoesTheDogDie.com"},
            "filesWritten": [],
            "candidates": _search_ddd_items(args, report),
        })
        return EXIT_OK

    if args.ddd_year is not None:
        raise TriggerWarningsError("--ddd-year needs --ddd-search")

    if args.output is None and not args.dry_run:
        raise TriggerWarningsError("--output is required")
    if args.events is None and args.ddd_item is None:
        raise TriggerWarningsError("supply --events, or --ddd-item with an API key")
    if args.events is not None and args.ddd_item is not None:
        raise TriggerWarningsError("--events and --ddd-item are mutually exclusive")
    if args.ddd_item is None and args.ddd_api_key is not None:
        raise TriggerWarningsError(
            "--ddd-api-key needs --ddd-item or --ddd-search"
        )
    if args.dry_run and args.verify is not None:
        raise TriggerWarningsError("--dry-run cannot render --verify; it writes no files")
    if args.dry_run and args.provenance is not None:
        raise TriggerWarningsError("--dry-run cannot write --provenance")
    if args.verify is not None and args.video is None:
        raise TriggerWarningsError(
            "--verify renders a frame from a video, so it needs --video"
        )

    suffix = None
    if args.output is not None:
        suffix = args.output.suffix.lower()
        if suffix not in (".ass", ".srt"):
            raise TriggerWarningsError(
                "--output must end in .ass or .srt, got {!r}".format(args.output.name)
            )
    if args.verify is not None and args.verify.suffix.lower() != ".png":
        raise TriggerWarningsError(
            "--verify writes a PNG, so its path must end in .png, got {!r}".format(
                args.verify.name
            )
        )

    inputs = [("--subtitles", args.subtitles), ("--events", args.events),
              ("--video", args.video)]
    _check_inputs_exist(inputs)
    # A dry run checks the targets too. Skipping them made the one command whose
    # job is to answer "would this work?" the one command that could answer yes
    # and then fail on the very next invocation, at the write.
    _check_publication_targets(
        [("--output", args.output), ("--verify", args.verify),
         ("--provenance", args.provenance)],
        inputs,
    )

    ddd_source = None
    if args.ddd_item is not None:
        ddd_source = _load_ddd_events(args, report)
        events = ddd_source.events
    else:
        events = core.load_events(
            _read_text(args.events, "--events"), str(args.events)
        )
    kept, dropped = core.select_events(events, args.category)
    _report_categories(kept, dropped, report)

    subtitle_text, duration_ms, media, _info = _obtain_subtitles(args, report)
    dialogue = core.parse_srt(subtitle_text, str(args.subtitles or args.video))

    windows, notes = core.build_windows(
        kept,
        lead_ms=core.seconds_to_ms(args.lead, "--lead"),
        tail_ms=core.seconds_to_ms(args.tail, "--tail"),
        offset_ms=core.seconds_to_ms(args.offset, "--offset"),
        duration_ms=duration_ms,
    )
    cues = core.merge_cues(dialogue, windows)
    source = _source_metadata(args, ddd_source)

    result.update({
        "mode": "dry-run" if args.dry_run else "generate",
        "source": source,
        # The three named targets are reported whether or not they were asked
        # for, so a caller reads one shape in every mode. ``filesWritten`` is
        # the fact: it stays empty until publication has actually happened.
        "output": str(args.output) if args.output is not None else None,
        "preview": str(args.verify) if args.verify is not None else None,
        "provenance": str(args.provenance) if args.provenance is not None else None,
        "filesWritten": [],
        "selectedEvents": len(kept),
        "excludedEvents": len(dropped),
        "dialogueCues": len(dialogue),
        "warningWindows": len(windows),
        "notes": list(notes),
    })

    report("{} dialogue cue{} kept, {} warning window{} from {} selected event{}.".format(
        len(dialogue), "" if len(dialogue) == 1 else "s",
        len(windows), "" if len(windows) == 1 else "s",
        len(kept), "" if len(kept) == 1 else "s",
    ))
    for note in notes:
        report(note)

    if args.dry_run:
        report("Dry run complete. No files were written.")
        return EXIT_OK

    subtitle_output = core.render(cues, suffix, notes)
    payload = subtitle_output.encode("utf-8")

    targets = [(args.output, payload)]
    if args.verify is not None:
        target_ms = core.verify_target_ms(cues, duration_ms)
        # verify_frame returns the PNG bytes and writes nothing, so a failed
        # render cannot leave a preview behind or an unchecked output file.
        png = media.verify_frame(args.video, subtitle_output, suffix, target_ms)
        if not png:
            raise TriggerWarningsError("the preview render produced no image data")
        targets.append((args.verify, png))
        report("Rendered a preview frame at {:.3f}s.".format(target_ms / 1000.0))
    if args.provenance is not None:
        targets.append((args.provenance, _provenance_payload(
            args, source, kept, dropped, windows, dialogue, notes
        )))

    result["filesWritten"] = [str(path) for path in publish(targets)]

    report("Wrote {}.".format(args.output))
    if args.verify is not None:
        report("Wrote {}. {}".format(args.verify, _RENDER_CAVEAT))
    if args.provenance is not None:
        report("Wrote {}.".format(args.provenance))
    report(_SYNC_CAVEAT)
    return EXIT_OK


def main(argv=None):
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    # The pre-parse answer covers a failure during parsing itself; the parsed
    # flag replaces it below, and both closures read whichever is current.
    # Nothing has been reported at the point of the swap, so the two answers can
    # never disagree about a message that has already gone out.
    speaks_json = _json_requested(arguments)
    messages = []

    def report(message, level="info"):
        messages.append({"level": level, "message": message})
        if not speaks_json:
            print(message, file=sys.stderr)

    def fail(code, message):
        if speaks_json:
            print(json.dumps({
                "ok": False,
                "error": {"code": code, "message": message},
                "messages": messages,
            }, sort_keys=True))
        else:
            print("error: {}".format(message), file=sys.stderr)
        return EXIT_ERROR

    try:
        args = parser.parse_args(arguments)
        speaks_json = bool(args.json_output)
        result = {}
        status = run(args, report, result)
        if args.json_output:
            result["ok"] = True
            result["messages"] = messages
            print(json.dumps(result, sort_keys=True))
        return status
    except TriggerWarningsError as error:
        return fail("invalid-input", str(error))
    except KeyboardInterrupt:
        return fail("interrupted", "interrupted")
    except Exception as error:  # media failures and anything the OS refuses
        # media is deliberately lazy so subtitle-only use has no FFmpeg
        # requirement. MediaError subclasses ValueError, which keeps this
        # resilient to future media-specific subclasses without eager imports.
        if isinstance(error, (ValueError, OSError)):
            return fail("operation-failed", str(error))
        if speaks_json:
            # A caller that asked for JSON gets JSON even when the tool is the
            # thing that broke: a traceback on stderr with nothing on stdout is
            # unparseable, and an agent reads it as "no answer" rather than
            # "failed". The exception type is reported and its text is not,
            # because an unexpected exception carries whatever the failing
            # library put in it, which can include a request or a credential.
            return fail(
                "internal-error",
                "unexpected internal failure ({}). Re-run without --json to see "
                "the traceback.".format(type(error).__name__),
            )
        raise


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
