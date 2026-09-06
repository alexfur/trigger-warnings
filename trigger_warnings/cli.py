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
import errno
import os
import sys
from pathlib import Path

from . import __version__, core
from .core import TriggerWarningsError

EXIT_OK = 0
EXIT_ERROR = 2

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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="trigger-warnings",
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
        "--language", default="eng", metavar="CODE",
        help="language for automatic subtitle stream selection (default: eng)",
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
    return parser


# ----------------------------------------------------------------------- validation


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
    """
    seen = {}
    for flag, path in targets:
        if path is None:
            continue
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
        # Check the parent the user named, rather than a dangling symlink's
        # eventual target. O_EXCL below remains the authoritative no-follow,
        # no-overwrite check.
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
    print("Subtitle streams in {} ({:.3f}s):".format(
        args.video, media.duration_ms(info) / 1000.0))
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
    return EXIT_OK


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


def _load_ddd_events(args, report):
    """Fetch one user's official API data without persisting a copy of it."""
    from . import ddd

    api_key = args.ddd_api_key or os.environ.get("DDD_API_KEY")
    source = ddd.load_item_events(api_key, args.ddd_item)
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
    return source.events


# ------------------------------------------------------------------------- the run


def run(args, report):
    if args.list_streams:
        if args.video is None:
            raise TriggerWarningsError("--list-streams needs --video")
        for flag, value in (("--output", args.output), ("--verify", args.verify),
                            ("--events", args.events), ("--ddd-item", args.ddd_item),
                            ("--ddd-api-key", args.ddd_api_key)):
            if value is not None:
                raise TriggerWarningsError(
                    "--list-streams does not generate output; run it without "
                    "{}".format(flag)
                )
        _check_inputs_exist([("--video", args.video)])
        return list_streams(args, report)

    if args.output is None:
        raise TriggerWarningsError("--output is required")
    if args.events is None and args.ddd_item is None:
        raise TriggerWarningsError("supply --events, or --ddd-item with an API key")
    if args.events is not None and args.ddd_item is not None:
        raise TriggerWarningsError("--events and --ddd-item are mutually exclusive")
    if args.ddd_item is None and args.ddd_api_key is not None:
        raise TriggerWarningsError("--ddd-api-key needs --ddd-item")
    if args.verify is not None and args.video is None:
        raise TriggerWarningsError(
            "--verify renders a frame from a video, so it needs --video"
        )

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
    _check_publication_targets(
        [("--output", args.output), ("--verify", args.verify)], inputs
    )

    if args.ddd_item is not None:
        events = _load_ddd_events(args, report)
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
    subtitle_output = core.render(cues, suffix, notes)
    payload = subtitle_output.encode("utf-8")

    report("{} dialogue cue{} kept, {} warning window{} from {} selected event{}.".format(
        len(dialogue), "" if len(dialogue) == 1 else "s",
        len(windows), "" if len(windows) == 1 else "s",
        len(kept), "" if len(kept) == 1 else "s",
    ))
    for note in notes:
        report(note)

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

    publish(targets)

    report("Wrote {}.".format(args.output))
    if args.verify is not None:
        report("Wrote {}. {}".format(args.verify, _RENDER_CAVEAT))
    report(_SYNC_CAVEAT)
    return EXIT_OK


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    def report(message):
        print(message, file=sys.stderr)

    try:
        return run(args, report)
    except TriggerWarningsError as error:
        print("error: {}".format(error), file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return EXIT_ERROR
    except Exception as error:  # media failures and anything the OS refuses
        # media is deliberately lazy so subtitle-only use has no FFmpeg
        # requirement. MediaError subclasses ValueError, which keeps this
        # resilient to future media-specific subclasses without eager imports.
        if isinstance(error, (ValueError, OSError)):
            print("error: {}".format(error), file=sys.stderr)
            return EXIT_ERROR
        raise


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
