"""Optional local video trigger scanning through MLX-VLM.

The scanner is deliberately conservative about its contract. It asks one
short yes/no question per requested trigger and per time chunk, rather than
trying to parse free-form model prose into timestamps. A positive answer marks
the complete chunk as a candidate event. This gives a useful first pass while
making the timing uncertainty visible to the caller.

The dependency is optional. Importing :mod:`trigger_warnings` and every
subtitle-only command continues to work without MLX-VLM installed.
"""

from pathlib import Path
import json
import contextlib
import io
import math
import re
import shutil
import subprocess
import tempfile
from collections import namedtuple


class VisionError(ValueError):
    """The optional local vision scanner could not produce safe events."""


VisionScan = namedtuple("VisionScan", "events notes metadata")

DEFAULT_MODEL = "mlx-community/SmolVLM2-500M-Video-Instruct-mlx"
DEFAULT_CHUNK_SECONDS = 10.0
DEFAULT_FPS = 1.0
DEFAULT_WIDTH = 384
DEFAULT_MAX_TOKENS = 16
_YES_NO = re.compile(r"^\s*(yes|no)[\s.!]*$", re.IGNORECASE)


def _binary(name):
    path = shutil.which(name)
    if not path:
        raise VisionError(
            "{} was not found on PATH. Install FFmpeg for --model-trigger, "
            "or use --events instead.".format(name)
        )
    return path


def _run(argv, timeout=120):
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise VisionError("{} timed out while preparing model frames".format(Path(argv[0]).name))
    except OSError as error:
        raise VisionError("cannot run {}: {}".format(Path(argv[0]).name, error))
    if result.returncode:
        detail = (result.stderr or result.stdout or "no diagnostic output").strip()
        raise VisionError("{} failed while preparing model frames: {}".format(
            Path(argv[0]).name, detail
        ))
    return result


def _duration(video):
    result = _run([
        _binary("ffprobe"), "-hide_banner", "-loglevel", "error",
        "-show_entries", "format=duration", "-of", "json", str(video),
    ])
    try:
        value = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise VisionError("ffprobe did not return a usable video duration")
    if not math.isfinite(value) or value <= 0:
        raise VisionError("video duration must be finite and positive")
    return value


def _load_backend(model_name, revision=None):
    try:
        import mlx.core as mx
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template
    except ImportError as error:
        raise VisionError(
            "--model-trigger needs the optional local vision dependencies. "
            "Install them with `pip install 'trigger-warnings[vision]'` "
            "(Apple Silicon is required by the MLX backend)."
        ) from error
    try:
        kwargs = {} if revision is None else {"revision": revision}
        # Hugging Face's downloader emits tqdm progress on stderr. The CLI's
        # JSON contract reserves stderr for nothing, so keep that incidental
        # progress out of machine-readable runs.
        with contextlib.redirect_stderr(io.StringIO()):
            model, processor = load(model_name, **kwargs)
    except Exception as error:
        raise VisionError(
            "could not load local vision model {!r}: {}".format(model_name, error)
        ) from error
    # SmolVLM's default image processor can split a frame into many 2048px
    # tiles. Video frames use one small image input for predictable cost.
    image_processor = getattr(processor, "image_processor", None)
    if image_processor is not None:
        image_processor.size = {"longest_edge": 512}
        if hasattr(image_processor, "do_image_splitting"):
            image_processor.do_image_splitting = False
    return mx, generate, apply_chat_template, model, processor


def _answer(generate, apply_chat_template, model, processor, mx, images, trigger, max_tokens):
    prompt = (
        "These are consecutive frames from a local video. Does any frame show "
        "the following trigger: {!r}? Answer only yes or no.".format(trigger)
    )
    try:
        prompt = apply_chat_template(
            processor, model.config, prompt, num_images=len(images)
        )
        result = generate(
            model,
            processor,
            prompt,
            image=images,
            max_tokens=max_tokens,
            temperature=0.0,
            verbose=False,
        )
        mx.synchronize()
    except Exception as error:
        raise VisionError(
            "vision model failed for trigger {!r}: {}".format(trigger, error)
        ) from error
    text = str(getattr(result, "text", "")).strip()
    match = _YES_NO.fullmatch(text)
    if match is None:
        raise VisionError(
            "vision model returned an unusable answer for trigger {!r}: {!r}; "
            "the scan stopped rather than treating it as a no".format(trigger, text)
        )
    return match.group(1).casefold() == "yes"


def scan_video(
    video,
    triggers,
    *,
    model=DEFAULT_MODEL,
    revision=None,
    fps=DEFAULT_FPS,
    chunk_seconds=DEFAULT_CHUNK_SECONDS,
    width=DEFAULT_WIDTH,
    max_tokens=DEFAULT_MAX_TOKENS,
    report=None,
):
    """Scan ``video`` for each requested trigger and return candidate events.

    The input is decoded in temporary frame directories and never modified.
    Events cover the complete positive chunk because this first scanner does
    not claim sub-chunk temporal precision. ``report`` receives progress text.
    """
    video = Path(video).resolve()
    if not video.is_file():
        raise VisionError("video does not exist: {}".format(video))
    if not triggers or any(not isinstance(trigger, str) or not trigger.strip() for trigger in triggers):
        raise VisionError("--model-trigger needs at least one non-empty trigger")
    if len(set(trigger.casefold() for trigger in triggers)) != len(triggers):
        raise VisionError("--model-trigger values must be unique")
    if not math.isfinite(fps) or fps <= 0:
        raise VisionError("--model-fps must be finite and positive")
    if not math.isfinite(chunk_seconds) or chunk_seconds <= 0:
        raise VisionError("--model-chunk must be finite and positive")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise VisionError("--model-width must be a positive whole number")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise VisionError("--model-max-tokens must be a positive whole number")

    duration = _duration(video)
    mx, generate, apply_chat_template, loaded_model, processor = _load_backend(model, revision)
    events = []
    chunks = math.ceil(duration / chunk_seconds)
    with tempfile.TemporaryDirectory(prefix="trigger-warnings-vision-") as folder:
        folder = Path(folder)
        for number in range(chunks):
            start = number * chunk_seconds
            end = min(duration, start + chunk_seconds)
            frame_dir = folder / "{:06d}".format(number)
            frame_dir.mkdir()
            _run([
                _binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                "-ss", "{:.6f}".format(start), "-i", str(video),
                "-t", "{:.6f}".format(end - start),
                "-vf", "fps={:.6f},scale={}:{}".format(fps, width, -2),
                "-q:v", "3", str(frame_dir / "%05d.jpg"),
            ])
            paths = sorted(frame_dir.glob("*.jpg"))
            if not paths:
                raise VisionError("FFmpeg produced no frames for {:.3f}s".format(start))
            try:
                from PIL import Image
            except ImportError as error:
                raise VisionError(
                    "--model-trigger needs Pillow; install the optional vision dependencies"
                ) from error
            images = []
            for path in paths:
                try:
                    with Image.open(path) as image:
                        images.append(image.convert("RGB"))
                except OSError as error:
                    raise VisionError("could not read model frame {}: {}".format(path, error)) from error
            for trigger in triggers:
                if report:
                    report("Checking {!r} in {:.3f}s–{:.3f}s.".format(trigger, start, end))
                if _answer(generate, apply_chat_template, loaded_model, processor, mx,
                           images, trigger.strip(), max_tokens):
                    events.append({
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "label": trigger.strip(),
                        "severity": "model-candidate",
                    })
    if not events:
        raise VisionError(
            "the model found no candidate events. This does not establish that "
            "the video is free of the requested triggers; no subtitle was written."
        )
    notes = [
        "Model-generated candidate timestamps cover whole {:.1f}s chunks; inspect and correct them before relying on the output.".format(chunk_seconds),
        "The scan sampled video at {:.3g} frame{} per second; brief events can be missed.".format(
            fps, "" if fps == 1 else "s"
        ),
        "Model candidates are not a safety guarantee. A warning ending is not an all-clear.",
    ]
    metadata = {
        "kind": "local-model",
        "model": model,
        "revision": revision,
        "fps": fps,
        "chunkSeconds": chunk_seconds,
        "frameWidth": width,
        "maxTokens": max_tokens,
        "durationSeconds": duration,
        "chunks": chunks,
        "requestedTriggers": [trigger.strip() for trigger in triggers],
        "candidateEvents": len(events),
        "notes": notes,
    }
    return VisionScan(events, notes, metadata)
