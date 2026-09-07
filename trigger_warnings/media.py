"""Optional, local FFmpeg and FFprobe support for :mod:`trigger_warnings`.

The command line owns output publication. This module only probes a video or
returns data produced in a private temporary directory.
"""

import copy
import decimal
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


class MediaError(ValueError):
    """A local media operation could not safely produce its promised result."""


TEXT_SUBTITLE_CODECS = frozenset((
    "ass", "ssa", "subrip", "srt", "mov_text", "text", "webvtt", "ttml",
    "sami", "realtext", "jacosub", "microdvd", "mpl2", "pjs", "stl",
))
_TIMEOUT = 120
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_LANGUAGE_ALIASES = {
    "ar": "ara", "ara": "ara", "ca": "cat", "cat": "cat",
    "cs": "ces", "ces": "ces", "cze": "ces", "da": "dan", "dan": "dan",
    "de": "deu", "deu": "deu", "ger": "deu", "el": "ell", "ell": "ell", "gre": "ell",
    "en": "eng", "eng": "eng", "es": "spa", "spa": "spa", "fi": "fin", "fin": "fin",
    "fr": "fra", "fra": "fra", "fre": "fra", "he": "heb", "heb": "heb",
    "hi": "hin", "hin": "hin", "hu": "hun", "hun": "hun", "id": "ind", "ind": "ind",
    "it": "ita", "ita": "ita", "ja": "jpn", "jpn": "jpn", "ko": "kor", "kor": "kor",
    "ms": "msa", "msa": "msa", "may": "msa", "nl": "nld", "nld": "nld", "dut": "nld",
    "no": "nor", "nor": "nor", "pl": "pol", "pol": "pol", "pt": "por", "por": "por",
    "ro": "ron", "ron": "ron", "rum": "ron", "ru": "rus", "rus": "rus", "sv": "swe", "swe": "swe",
    "th": "tha", "tha": "tha", "tr": "tur", "tur": "tur", "uk": "ukr", "ukr": "ukr",
    "vi": "vie", "vie": "vie", "zh": "zho", "zho": "zho", "chi": "zho",
}


def _resolved_file(path, flag="video"):
    path = Path(path)
    if not path.exists():
        raise MediaError("{} does not exist: {}".format(flag, path))
    if path.is_dir():
        raise MediaError("{} is a directory: {}".format(flag, path))
    return path.resolve()


def _binary(name):
    value = shutil.which(name)
    if not value:
        # Naming the missing program is not enough: people install "FFmpeg",
        # not "ffprobe", and the first run never needs either.
        raise MediaError(
            "{} was not found on PATH. It ships with FFmpeg, which this option "
            "needs. Install FFmpeg, or pass --subtitles with --events instead, "
            "which needs no FFmpeg.".format(name)
        )
    return value


def _run(argv, *, cwd=None):
    try:
        return subprocess.run(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=_TIMEOUT, shell=False,
        )
    except subprocess.TimeoutExpired:
        raise MediaError("{} timed out".format(Path(argv[0]).name))
    except OSError as error:
        raise MediaError("cannot run {}: {}".format(Path(argv[0]).name, error))


def _failure(prefix, result):
    detail = (result.stderr or result.stdout or "no diagnostic output").strip()
    raise MediaError("{}: {}".format(prefix, detail))


def probe(video):
    """Return verified FFprobe JSON for a readable video with a finite duration."""
    video = _resolved_file(video)
    ffprobe = _binary("ffprobe")
    result = _run([
        ffprobe, "-hide_banner", "-loglevel", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(video),
    ])
    if result.returncode != 0:
        _failure("ffprobe could not read {}".format(video), result)
    try:
        info = json.loads(result.stdout)
    except (TypeError, ValueError) as error:
        raise MediaError("ffprobe did not return usable JSON: {}".format(error))
    if not isinstance(info, dict) or not isinstance(info.get("streams"), list):
        raise MediaError("ffprobe returned no stream information for {}".format(video))
    if not any(stream.get("codec_type") == "video" for stream in info["streams"] if isinstance(stream, dict)):
        raise MediaError("{} has no video stream".format(video))
    duration_ms(info)
    return info


def _decimal_duration(value):
    try:
        number = decimal.Decimal(str(value))
    except (decimal.InvalidOperation, ValueError):
        raise MediaError("video duration is unavailable or invalid: {!r}".format(value))
    if not number.is_finite() or number <= 0:
        raise MediaError("video duration must be finite and positive, got {!r}".format(value))
    return int((number * 1000).to_integral_value(rounding=decimal.ROUND_HALF_UP))


def duration_ms(info):
    """Return a finite, positive video duration in integer milliseconds."""
    if not isinstance(info, dict):
        raise MediaError("ffprobe information is not an object")
    value = (info.get("format") or {}).get("duration")
    if value in (None, "N/A"):
        for stream in info.get("streams") or []:
            if isinstance(stream, dict) and stream.get("codec_type") == "video":
                value = stream.get("duration")
                if value not in (None, "N/A"):
                    break
    return _decimal_duration(value)


def subtitle_streams(info):
    """Return independent copies of subtitle streams in container order."""
    streams = info.get("streams") if isinstance(info, dict) else None
    if not isinstance(streams, list):
        return []
    return [copy.deepcopy(stream) for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "subtitle"]


def is_text_subtitle(stream):
    return isinstance(stream, dict) and str(stream.get("codec_name") or "").casefold() in TEXT_SUBTITLE_CODECS


def _normalise_language(language):
    if not isinstance(language, str) or not language.strip():
        return None
    base = language.strip().casefold().replace("_", "-").split("-", 1)[0]
    return _LANGUAGE_ALIASES.get(base, base)


def _candidate_description(stream):
    tags = stream.get("tags") or {}
    disposition = stream.get("disposition") or {}
    flags = []
    if disposition.get("hearing_impaired"):
        flags.append("hearing impaired")
    if disposition.get("forced"):
        flags.append("forced")
    if disposition.get("default"):
        flags.append("default")
    details = [
        "index {}".format(stream.get("index")),
        str(stream.get("codec_name") or "unknown codec"),
        str(tags.get("language") or "untagged"),
    ]
    if tags.get("title"):
        details.append("title {!r}".format(tags["title"]))
    details.extend(flags)
    return ", ".join(details)


def select_stream(info, language="eng", index=None):
    """Choose one text subtitle stream, never guessing between matching tracks."""
    streams = subtitle_streams(info)
    if index is not None:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MediaError("--stream must be a non-negative integer index")
        all_streams = info.get("streams") if isinstance(info, dict) else []
        candidate = next((stream for stream in all_streams if isinstance(stream, dict) and stream.get("index") == index), None)
        if candidate is None or candidate.get("codec_type") != "subtitle":
            raise MediaError("--stream {} is not a subtitle stream".format(index))
        if not is_text_subtitle(candidate):
            raise MediaError("--stream {} uses bitmap or unsupported codec {}; transcription is required".format(index, candidate.get("codec_name") or "unknown"))
        return copy.deepcopy(candidate)

    if not streams:
        raise MediaError("the video has no subtitle streams; supply --subtitles")
    text_streams = [stream for stream in streams if is_text_subtitle(stream)]
    if not text_streams:
        raise MediaError("the video has only bitmap or unsupported subtitle streams; transcription is required")
    wanted = _normalise_language(language)
    if wanted is None:
        raise MediaError("--language must name a language")
    matches = [stream for stream in text_streams
               if _normalise_language((stream.get("tags") or {}).get("language")) == wanted]
    if not matches:
        available = ", ".join(sorted({str((stream.get("tags") or {}).get("language") or "untagged") for stream in text_streams}))
        raise MediaError("no text subtitle track is tagged {}; available: {}. Use --stream INDEX to choose explicitly.".format(language, available))
    if len(matches) != 1:
        listed = "; ".join(_candidate_description(stream) for stream in matches)
        raise MediaError("{} text subtitle tracks match {}: {}. Use --stream INDEX to choose; an SDH or hearing impaired track is not discarded automatically.".format(len(matches), language, listed))
    return matches[0]


def extract_subtitles(video, index):
    """Extract one absolute text subtitle stream as SRT, entirely in a temp dir."""
    video = _resolved_file(video)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise MediaError("subtitle stream index must be a non-negative integer")
    ffmpeg = _binary("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="trigger-warnings-extract-") as folder:
        output = "subtitles.srt"
        result = _run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-i", str(video), "-map", "0:{}".format(index), "-c:s", "srt", output,
        ], cwd=folder)
        target = Path(folder) / output
        if result.returncode != 0:
            _failure("ffmpeg could not extract subtitle stream {}".format(index), result)
        try:
            text = target.read_text(encoding="utf-8-sig")
        except OSError:
            raise MediaError("ffmpeg reported success but produced no subtitle file")
        except UnicodeDecodeError:
            raise MediaError("ffmpeg produced subtitles that are not UTF-8 SRT")
        if not text.strip():
            raise MediaError("ffmpeg extracted an empty subtitle track")
        return text


def _valid_png(payload):
    if not isinstance(payload, bytes) or not payload.startswith(_PNG_SIGNATURE):
        return False
    offset = len(_PNG_SIGNATURE)
    seen_ihdr = seen_idat = seen_iend = False
    width = height = 0
    try:
        while offset < len(payload):
            length = struct.unpack(">I", payload[offset:offset + 4])[0]
            kind = payload[offset + 4:offset + 8]
            data_end = offset + 8 + length
            chunk_end = data_end + 4
            if chunk_end > len(payload):
                return False
            data = payload[offset + 8:data_end]
            if kind == b"IHDR":
                if seen_ihdr or length != 13:
                    return False
                width, height = struct.unpack(">II", data[:8])
                seen_ihdr = True
            elif kind == b"IDAT":
                seen_idat = True
            elif kind == b"IEND":
                seen_iend = True
                return seen_ihdr and seen_idat and width > 0 and height > 0 and chunk_end == len(payload)
            offset = chunk_end
    except (IndexError, struct.error):
        return False
    return False


def verify_frame(video, subtitle_text, format_suffix, target_ms):
    """Render a verified PNG at ``target_ms`` using private subtitle and image files."""
    video = _resolved_file(video)
    if format_suffix not in (".ass", ".srt"):
        raise MediaError("preview subtitles must be .ass or .srt")
    if not isinstance(subtitle_text, str) or not subtitle_text.strip():
        raise MediaError("preview subtitles are empty")
    if isinstance(target_ms, bool) or not isinstance(target_ms, int) or target_ms < 0:
        raise MediaError("preview time must be a non-negative integer millisecond value")
    ffmpeg = _binary("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="trigger-warnings-preview-") as folder:
        subtitle_name = "subs" + format_suffix
        image_name = "preview.png"
        (Path(folder) / subtitle_name).write_text(subtitle_text, encoding="utf-8")
        result = _run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-copyts", "-ss", "{:.3f}".format(target_ms / 1000.0), "-i", str(video),
            "-vf", "subtitles={}".format(subtitle_name), "-frames:v", "1", image_name,
        ], cwd=folder)
        target = Path(folder) / image_name
        if result.returncode != 0:
            _failure("ffmpeg could not render preview", result)
        try:
            payload = target.read_bytes()
        except OSError:
            raise MediaError("ffmpeg reported success but produced no preview frame")
        if not _valid_png(payload):
            raise MediaError("ffmpeg preview is not a complete, non-empty PNG")
        return payload
