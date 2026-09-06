"""Deterministic subtitle logic: events, warning windows, SRT parsing, rendering.

This module is pure Python 3.9 standard library and has no media dependency. It
never touches FFmpeg, never reads a video and never writes a file. Everything it
returns is data; publication belongs to the command line layer.

Two conventions hold throughout:

*   All times are integer milliseconds. Seconds arrive as JSON numbers or
    ``HH:MM:SS.mmm`` strings and are converted once, through :class:`decimal.Decimal`,
    so that an integer second never arrives as ``39.999`` through binary float drift.
*   Failure is loud. Every rejected input raises :class:`TriggerWarningsError` with a
    message naming the record. Nothing is skipped silently, because a silently
    dropped event is indistinguishable from a video that contains no triggers.

Known limitations, stated rather than hidden:

*   **ASS uses centisecond resolution.** Millisecond input is quantised on output:
    warning windows round outwards (start down, end up) so a warning is never
    shortened, and dialogue rounds to the nearest centisecond. Dialogue timing can
    therefore move by up to 5 ms in an ``.ass`` file. SRT keeps full milliseconds.
*   **SRT inline markup becomes plain text in ASS.** Tags such as ``<i>`` are
    escaped and rendered literally rather than translated to ASS override tags.
    Dialogue content and timing are the priority; styling is not preserved.
*   **A literal backslash in dialogue is player-dependent in ASS.** Backslashes are
    doubled and braces escaped, which is what FFmpeg does. Whatever a player makes
    of ``\\\\``, the escaping order guarantees that no ``{`` in the output can open
    an override block, so dialogue can never inject styling.
"""

import decimal
import json
import re
from collections import namedtuple

__all__ = [
    "TriggerWarningsError",
    "WARNING_TEXT",
    "Event",
    "Window",
    "Cue",
    "parse_time_ms",
    "seconds_to_ms",
    "format_srt_time",
    "format_ass_time",
    "format_ass_centis",
    "load_events",
    "select_events",
    "build_windows",
    "parse_srt",
    "merge_cues",
    "render_srt",
    "render_ass",
    "render",
    "verify_target_ms",
]


class TriggerWarningsError(Exception):
    """Any rejected input or unusable result. The command line turns it into exit 2."""


#: The only text ever put on screen. The category is the spoiler, so it stays off.
WARNING_TEXT = "TRIGGER INCOMING"

#: An event as supplied, validated and converted to milliseconds.
#: ``end_ms`` is ``None`` for a point event with an unknown end.
Event = namedtuple("Event", "start_ms end_ms label severity position")

#: A merged, clamped warning window in output time.
Window = namedtuple("Window", "start_ms end_ms")

#: One line of the output. ``kind`` is ``"warning"`` or ``"dialogue"``.
Cue = namedtuple("Cue", "start_ms end_ms text kind order")

# Strict: hours are at least two digits, minutes and seconds are exactly two and
# in range, and the fractional part is exactly three digits after "." or ",".
_HMS_RE = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d)[.,](\d{3})$")

_SRT_TIME_RE = re.compile(r"^(\d{1,4}):([0-5]\d):([0-5]\d)[,.](\d{1,3})$")
_SRT_CUE_RE = re.compile(
    r"^\s*(?P<start>\d{1,4}:[0-5]\d:[0-5]\d[,.]\d{1,3})"
    r"\s*-->\s*"
    r"(?P<end>\d{1,4}:[0-5]\d:[0-5]\d[,.]\d{1,3})"
    r"(?P<trailing>\s.*)?$"
)

_KNOWN_EVENT_KEYS = frozenset(("start", "end", "label", "severity"))

_INF = float("inf")


# --------------------------------------------------------------------------- time


def _exact_seconds_ms(value, what):
    """Exact milliseconds as a Decimal, with no rounding and no sign restriction.

    Rounding happens once, at the end, so a sign or an ordering can still be
    checked on the value as supplied. Booleans, NaN and infinities are rejected
    rather than coerced, and the conversion goes through ``Decimal(str(value))``
    so 60 becomes exactly 60000 rather than 59999.999999999996.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TriggerWarningsError(
            "{} must be a number of seconds or \"HH:MM:SS.mmm\", got {!r}".format(
                what, value
            )
        )
    if isinstance(value, float) and (value != value or value in (_INF, -_INF)):
        raise TriggerWarningsError(
            "{} must be a finite number, got {!r}".format(what, value)
        )
    try:
        return decimal.Decimal(str(value)) * 1000
    except (decimal.InvalidOperation, ValueError):
        raise TriggerWarningsError("{} is not a usable number: {!r}".format(what, value))


def _round_ms(exact):
    """Round exact milliseconds to an int, half away from zero, carrying up."""
    return int(exact.to_integral_value(rounding=decimal.ROUND_HALF_UP))


def seconds_to_ms(value, what="time"):
    """Convert a signed number of seconds to integer milliseconds."""
    return _round_ms(_exact_seconds_ms(value, what))


def _exact_event_time(value, what):
    """Exact milliseconds for an event time: numeric seconds or strict HH:MM:SS.mmm."""
    if isinstance(value, str):
        match = _HMS_RE.match(value.strip())
        if match is None:
            raise TriggerWarningsError(
                "{} must be \"HH:MM:SS.mmm\" or \"HH:MM:SS,mmm\", got {!r}".format(
                    what, value
                )
            )
        hours, minutes, seconds, millis = (int(part) for part in match.groups())
        return decimal.Decimal(
            ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis
        )
    return _exact_seconds_ms(value, what)


def parse_time_ms(value, what="time"):
    """Parse an event time to integer milliseconds.

    The sign is checked before rounding, so -0.0001 seconds is rejected rather
    than quietly becoming 0.
    """
    exact = _exact_event_time(value, what)
    if exact < 0:
        raise TriggerWarningsError(
            "{} must not be negative, got {!r}".format(what, value)
        )
    return _round_ms(exact)


def _parse_srt_time_ms(text):
    match = _SRT_TIME_RE.match(text.strip())
    if match is None:
        raise TriggerWarningsError("not a subtitle timestamp: {!r}".format(text))
    hours, minutes, seconds, frac = match.groups()
    millis = int(frac.ljust(3, "0"))
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + millis


def format_srt_time(total_ms):
    """Render milliseconds as ``HH:MM:SS,mmm``."""
    if total_ms < 0:
        raise TriggerWarningsError("cannot render a negative timestamp")
    seconds, millis = divmod(int(total_ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return "{:02d}:{:02d}:{:02d},{:03d}".format(hours, minutes, seconds, millis)


def format_ass_centis(centis):
    """Render whole centiseconds as ASS ``H:MM:SS.cc``."""
    if centis < 0:
        raise TriggerWarningsError("cannot render a negative timestamp")
    seconds, centis = divmod(int(centis), 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return "{:d}:{:02d}:{:02d}.{:02d}".format(hours, minutes, seconds, centis)


def format_ass_time(total_ms, round_up=False):
    """Render milliseconds as ASS ``H:MM:SS.cc``, flooring or ceiling to the centisecond."""
    if total_ms < 0:
        raise TriggerWarningsError("cannot render a negative timestamp")
    return format_ass_centis(_ceil_cs(total_ms) if round_up else _floor_cs(total_ms))


def _floor_cs(total_ms):
    return int(total_ms) // 10


def _ceil_cs(total_ms):
    return -(-int(total_ms) // 10)


def _nearest_cs(total_ms):
    return (int(total_ms) + 5) // 10


# ------------------------------------------------------------------------- events


def load_events(text, source="events"):
    """Validate a JSON array of event records and return a list of :class:`Event`.

    Every record is validated, including records whose category will later be
    excluded, so that a typo in an unselected event is still reported. An empty
    array is an error: "no events" is a broken input, not a finding about a video.
    """
    try:
        data = json.loads(text)
    except ValueError as error:
        raise TriggerWarningsError("{} is not valid JSON: {}".format(source, error))
    if not isinstance(data, list):
        raise TriggerWarningsError(
            "{} must be a JSON array of event objects, got {}".format(
                source, type(data).__name__
            )
        )
    if not data:
        raise TriggerWarningsError(
            "{} contains no events. An empty file is a missing input, not evidence "
            "that the video is free of your triggers.".format(source)
        )

    events = []
    for position, record in enumerate(data, start=1):
        events.append(_parse_event(record, position))
    return events


def _parse_event(record, position):
    where = "event {}".format(position)
    if not isinstance(record, dict):
        raise TriggerWarningsError(
            "{} must be a JSON object, got {}".format(where, type(record).__name__)
        )

    unknown = sorted(set(record) - _KNOWN_EVENT_KEYS)
    if unknown:
        raise TriggerWarningsError(
            "{} has unrecognised field(s) {}. Allowed fields are start, end, label "
            "and severity; a misspelled field would otherwise be ignored in "
            "silence.".format(where, ", ".join(repr(key) for key in unknown))
        )

    if "start" not in record:
        raise TriggerWarningsError("{} has no \"start\"".format(where))
    start_exact = _exact_event_time(record["start"], "{} start".format(where))
    if start_exact < 0:
        raise TriggerWarningsError(
            "{} start must not be negative, got {!r}".format(where, record["start"])
        )

    end_value = record.get("end")
    if end_value is None:
        end_ms = None
    else:
        end_exact = _exact_event_time(end_value, "{} end".format(where))
        if end_exact < 0:
            raise TriggerWarningsError(
                "{} end must not be negative, got {!r}".format(where, end_value)
            )
        # Compared before rounding: 1.0004 then 1.0001 is still reversed, even
        # though both land on the same millisecond.
        if end_exact < start_exact:
            raise TriggerWarningsError(
                "{} ends before it starts ({!r} then {!r}). A reversed window is a "
                "typo, not a one-second warning.".format(
                    where, record["start"], end_value
                )
            )
        end_ms = _round_ms(end_exact)
    start_ms = _round_ms(start_exact)

    if "label" not in record:
        raise TriggerWarningsError("{} has no \"label\"".format(where))
    label = record["label"]
    if not isinstance(label, str) or not label.strip():
        raise TriggerWarningsError(
            "{} label must be a non-empty string, got {!r}".format(where, label)
        )

    severity = record.get("severity")
    if severity is not None and not isinstance(severity, str):
        raise TriggerWarningsError(
            "{} severity must be a string or null, got {!r}".format(where, severity)
        )

    return Event(start_ms, end_ms, label.strip(), severity, position)


def select_events(events, categories):
    """Split validated events into kept and dropped, exactly and case-insensitively.

    ``categories`` empty selects everything. A requested category that matches no
    event is an error naming it, because "no warnings" must never be able to mean
    "your spelling did not match".

    Returns ``(kept, dropped)``.
    """
    if not categories:
        return list(events), []

    available = {}
    for event in events:
        available.setdefault(event.label.casefold(), event.label)

    wanted = []
    unknown = []
    for requested in categories:
        folded = requested.strip().casefold()
        if folded in available:
            if folded not in wanted:
                wanted.append(folded)
        elif requested not in unknown:
            unknown.append(requested)

    if unknown:
        raise TriggerWarningsError(
            "no event uses category {}. Known categories: {}. A category that "
            "matches nothing is treated as a mistake, not as an absence of "
            "triggers.".format(
                ", ".join(repr(name) for name in unknown),
                ", ".join(sorted(available.values())) or "(none)",
            )
        )

    selected = set(wanted)
    kept = [event for event in events if event.label.casefold() in selected]
    dropped = [event for event in events if event.label.casefold() not in selected]
    return kept, dropped


def category_counts(events):
    """Return ``[(label, count)]`` sorted by label, for the terminal report."""
    counts = {}
    for event in events:
        counts[event.label] = counts.get(event.label, 0) + 1
    return sorted(counts.items(), key=lambda item: item[0].casefold())


# ------------------------------------------------------------------------ windows


def build_windows(events, lead_ms=20000, tail_ms=0, offset_ms=0, duration_ms=None):
    """Turn events into merged warning windows.

    A window runs from ``start + offset - lead`` to ``end + offset + tail``. An
    event with an unknown end is advance-only: its window ends at ``start + offset``,
    which is the last moment that is still before the event, not a safe time to
    resume watching. Touching and overlapping windows merge so the banner never
    stacks on itself.

    Returns ``(windows, notes)`` where ``notes`` is a list of human-readable strings
    the caller should print, such as a lead that had to be clipped at zero.
    """
    if lead_ms < 0:
        raise TriggerWarningsError("lead must not be negative")
    if tail_ms < 0:
        raise TriggerWarningsError("tail must not be negative")
    if not events:
        raise TriggerWarningsError(
            "no events remain after category selection, so there is nothing to warn "
            "about. This is not a statement about the video."
        )

    notes = []
    clipped = 0
    unknown_end = 0
    raw = []
    for event in events:
        shifted_start = event.start_ms + offset_ms
        if event.end_ms is None:
            unknown_end += 1
            end = shifted_start
        else:
            end = event.end_ms + offset_ms + tail_ms
        start = shifted_start - lead_ms

        if end < 0:
            raise TriggerWarningsError(
                "event {} ends at {:.3f}s after the offset, before the start of the "
                "file. The offset or the timestamps do not match this video.".format(
                    event.position, end / 1000.0
                )
            )
        if start < 0:
            clipped += 1
            start = 0
        if end <= start:
            raise TriggerWarningsError(
                "event {} produces an empty warning window ({:.3f}s to {:.3f}s). "
                "Increase --lead or supply an end time; a zero-length cue would "
                "never appear on screen.".format(
                    event.position, start / 1000.0, end / 1000.0
                )
            )
        raw.append(Window(start, end))

    raw.sort()
    merged = [raw[0]]
    for window in raw[1:]:
        last = merged[-1]
        if window.start_ms <= last.end_ms:  # touching counts as overlapping
            merged[-1] = Window(last.start_ms, max(last.end_ms, window.end_ms))
        else:
            merged.append(window)

    if duration_ms is not None:
        _check_within_duration(merged, duration_ms)

    if clipped:
        notes.append(
            "{} warning{} started before the beginning of the file and was clipped "
            "to 0:00, so the full lead time is not available there.".format(
                clipped, "" if clipped == 1 else "s"
            )
        )
    if unknown_end:
        notes.append(
            "{} event{} no end time. Those warnings end at the event start and do "
            "not indicate a safe time to resume watching.".format(
                unknown_end,
                " has" if unknown_end == 1 else "s have",
            )
        )
        if tail_ms:
            notes.append(
                "--tail was not applied to {} advance-only warning{} because those "
                "events have no supplied end time.".format(
                    unknown_end, "" if unknown_end == 1 else "s"
                )
            )
    return merged, notes


def _check_within_duration(windows, duration_ms):
    for window in windows:
        if window.start_ms >= duration_ms or window.end_ms > duration_ms:
            raise TriggerWarningsError(
                "warning window {:.3f}s to {:.3f}s falls outside the video, which is "
                "{:.3f}s long. The timestamps describe a different edition, or the "
                "offset is wrong.".format(
                    window.start_ms / 1000.0,
                    window.end_ms / 1000.0,
                    duration_ms / 1000.0,
                )
            )


# --------------------------------------------------------------------- SRT input


def parse_srt(text, source="subtitles"):
    """Parse SRT into dialogue cues, strictly.

    Accepts a UTF-8 BOM, CRLF, lone CR and a missing cue index. Rejects anything
    else: a block that does not parse raises instead of being skipped, because a
    silently dropped cue is a line of dialogue the viewer never sees. Cue text is
    kept exactly as written, including inline markup and internal line breaks.
    """
    if text.startswith("﻿"):
        text = text[1:]
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")

    cues = []
    block_lines = []
    block_start_line = 1

    def flush(lines, first_line_number):
        if lines:
            cues.append(_parse_srt_block(lines, first_line_number, source))

    for number, line in enumerate(normalised.split("\n"), start=1):
        if line.strip():
            if not block_lines:
                block_start_line = number
            block_lines.append(line)
        else:
            flush(block_lines, block_start_line)
            block_lines = []
    flush(block_lines, block_start_line)

    if not cues:
        raise TriggerWarningsError(
            "{} contains no subtitle cues. Check that it is an SRT dialogue "
            "track.".format(source)
        )
    return cues


def _parse_srt_block(lines, line_number, source):
    where = "{} line {}".format(source, line_number)
    rest = lines

    if _SRT_CUE_RE.match(rest[0]) is None:
        # The first line may be the optional cue index, and nothing else.
        if not rest[0].strip().isdigit() or len(rest) < 2:
            raise TriggerWarningsError(
                "{}: expected a cue number or a timestamp line, got {!r}".format(
                    where, rest[0]
                )
            )
        rest = rest[1:]

    match = _SRT_CUE_RE.match(rest[0])
    if match is None:
        raise TriggerWarningsError(
            "{}: expected \"HH:MM:SS,mmm --> HH:MM:SS,mmm\", got {!r}".format(
                where, rest[0]
            )
        )
    start_ms = _parse_srt_time_ms(match.group("start"))
    end_ms = _parse_srt_time_ms(match.group("end"))
    if end_ms < start_ms:
        raise TriggerWarningsError(
            "{}: cue ends before it starts ({})".format(where, rest[0].strip())
        )

    payload_lines = rest[1:]
    # A missing blank separator would otherwise turn the next cue's timestamp
    # and text into dialogue in this cue. Reject it rather than lose timing.
    for payload_line in payload_lines:
        if _SRT_CUE_RE.match(payload_line):
            raise TriggerWarningsError(
                "{}: found another cue timestamp inside dialogue. Add a blank "
                "line between subtitle cues.".format(where)
            )
    payload = "\n".join(payload_lines).strip("\n")
    if not payload.strip():
        raise TriggerWarningsError("{}: cue has no text".format(where))
    return Cue(start_ms, end_ms, payload, "dialogue", 0)


# -------------------------------------------------------------------- assembly


def merge_cues(dialogue, windows):
    """Interleave dialogue and warning cues into one ordered, numbered sequence.

    Dialogue timings are not shifted. Where a warning and a line of dialogue begin
    at the same instant the warning is emitted first, because a player that ignores
    positioning falls back to file order.
    """
    combined = []
    for index, window in enumerate(windows):
        combined.append(
            Cue(window.start_ms, window.end_ms, WARNING_TEXT, "warning", index)
        )
    for index, cue in enumerate(dialogue):
        combined.append(Cue(cue.start_ms, cue.end_ms, cue.text, "dialogue", index))

    combined.sort(
        key=lambda cue: (
            cue.start_ms,
            0 if cue.kind == "warning" else 1,
            cue.end_ms,
            cue.order,
        )
    )
    return combined


def verify_target_ms(cues, duration_ms=None):
    """Choose the millisecond to render a preview frame at.

    Prefer the midpoint of the first place where a warning and a line of dialogue
    are on screen together, which is the case worth looking at. With no such
    overlap, fall back to the midpoint of the first warning window, so that a
    warning with no dialogue under it is still verified.
    """
    warnings = [cue for cue in cues if cue.kind == "warning"]
    if not warnings:
        raise TriggerWarningsError("there is no warning to verify")
    dialogue = [cue for cue in cues if cue.kind == "dialogue"]

    target = None
    for warning in warnings:
        for line in dialogue:
            start = max(warning.start_ms, line.start_ms)
            end = min(warning.end_ms, line.end_ms)
            if start < end:
                target = (start + end) // 2
                break
        if target is not None:
            break
    if target is None:
        first = warnings[0]
        target = (first.start_ms + first.end_ms) // 2

    if target < 0:
        raise TriggerWarningsError("computed a negative preview time")
    if duration_ms is not None and target >= duration_ms:
        raise TriggerWarningsError(
            "the preview time {:.3f}s is past the end of the {:.3f}s video".format(
                target / 1000.0, duration_ms / 1000.0
            )
        )
    return target


# ------------------------------------------------------------------------ output


def render(cues, suffix, notes=None):
    """Render cues to ``.srt`` or ``.ass`` text. The suffix decides the format.

    ``notes`` is an optional list; any resolution compromise the format forced is
    appended to it so the caller can disclose it rather than hide it.
    """
    lowered = suffix.lower()
    if lowered == ".srt":
        return render_srt(cues)
    if lowered == ".ass":
        return render_ass(cues, notes)
    raise TriggerWarningsError(
        "output must end in .ass or .srt, got {!r}".format(suffix)
    )


def render_srt(cues):
    """Render SRT at full millisecond resolution.

    Warnings carry ``{\\an8}``, whose support is player-dependent.
    """
    blocks = []
    for number, cue in enumerate(cues, start=1):
        text = r"{\an8}" + cue.text if cue.kind == "warning" else cue.text
        blocks.append(
            "{}\n{} --> {}\n{}\n".format(
                number,
                format_srt_time(cue.start_ms),
                format_srt_time(cue.end_ms),
                text,
            )
        )
    return "\n".join(blocks)


_ASS_HEADER = """\
[Script Info]
; Generated by trigger-warnings. Warnings are advisory and unverified.
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Warning,Arial,72,&H00FFFFFF,&H000000FF,&H00202020,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,8,60,60,40,1
Style: Dialogue,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def render_ass(cues, notes=None):
    """Render ASS with a top-aligned Warning style and a bottom-aligned Dialogue style.

    ASS stores centiseconds. Warning windows quantise outwards, start down and end
    up, so a warning is never shortened by the format. Dialogue rounds to the
    nearest centisecond and so can move by up to 5 ms. A cue that would collapse to
    zero length is widened to one centisecond and counted, because an invisible cue
    is a dropped line of dialogue.
    """
    lines = [_ASS_HEADER]
    widened = 0
    for cue in cues:
        if cue.kind == "warning":
            start_cs, end_cs = _floor_cs(cue.start_ms), _ceil_cs(cue.end_ms)
            style, layer = "Warning", 1
        else:
            start_cs, end_cs = _nearest_cs(cue.start_ms), _nearest_cs(cue.end_ms)
            style, layer = "Dialogue", 0
        if end_cs <= start_cs:
            end_cs = start_cs + 1
            widened += 1
        lines.append(
            "Dialogue: {},{},{},{},,0,0,0,,{}\n".format(
                layer,
                format_ass_centis(start_cs),
                format_ass_centis(end_cs),
                style,
                escape_ass_text(cue.text),
            )
        )
    if widened and notes is not None:
        notes.append(
            "{} cue{} shorter than one centisecond, which ASS cannot represent, so "
            "{} widened to 0.01s. SRT output would keep the original "
            "timing.".format(
                widened,
                " was" if widened == 1 else "s were",
                "it was" if widened == 1 else "they were",
            )
        )
    return "".join(lines)


def escape_ass_text(text):
    r"""Escape arbitrary dialogue for an ASS event field.

    The rules below were measured against libass (FFmpeg 8.0.1), not assumed,
    because the conventional ``\\`` escape does not survive it. Outside an
    override block libass treats exactly five sequences specially:

    ====================  ======================================================
    ``\{`` and ``\}``     a literal brace; the backslash is consumed
    ``\n`` ``\N``         a line break
    ``\h``                a non-breaking space
    ``\`` + anything else a literal backslash, then that character as normal
    ====================  ======================================================

    So a lone backslash needs no escape at all, a brace needs ``\``, and there
    is deliberately no ``\\`` rule here: FFmpeg's own doubling renders as a
    backslash *plus a line break* and silently splits a line of dialogue in two.

    A literal backslash immediately before ``n``, ``N`` or ``h`` cannot be
    written in ASS at all: every invisible separator would have to start with
    ``{``, which the preceding backslash would swallow. That raises rather than
    quietly mangling the line. SRT output has no such limit.
    """
    parts = []
    for position, char in enumerate(text):
        if char == "\n":
            parts.append(r"\N")
        elif char == "{":
            parts.append(r"\{")
        elif char == "}":
            parts.append(r"\}")
        elif char == "\\":
            following = text[position + 1: position + 2]
            if following in ("n", "N", "h"):
                raise TriggerWarningsError(
                    "this dialogue contains a backslash before {!r}, which ASS "
                    "cannot represent as literal text: {!r}. Use --output with a "
                    ".srt extension, which keeps the line exactly as "
                    "written.".format(following, text)
                )
            parts.append("\\")
        else:
            parts.append(char)
    return "".join(parts)
