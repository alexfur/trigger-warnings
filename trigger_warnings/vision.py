"""Optional local video trigger scanning through MLX-VLM.

The scanner is deliberately conservative about its contract. It asks one
short yes/no question per requested trigger and per time chunk, rather than
trying to parse free-form model prose into timestamps. A positive answer marks
the complete chunk as a candidate event. This gives a useful first pass while
making the timing uncertainty visible to the caller.

Within each chunk, the KV cache from the first trigger's prefill is reused
for every subsequent trigger via MLX-VLM's ``PromptCacheState``. The video
frame tokens and shared prompt text are identical across triggers — only the
short trigger-question suffix differs — so triggers 2–N skip the expensive
vision prefill entirely.

The dependency is optional. Importing :mod:`trigger_warnings` and every
subtitle-only command continues to work without MLX-VLM installed.
"""

from pathlib import Path
import contextlib
import io
import math
import re
from collections import namedtuple

from .progress import ProgressBar
from .models import resolve_model
from .video import VideoReader


class VisionError(ValueError):
    """The optional local vision scanner could not produce safe events."""


VisionScan = namedtuple("VisionScan", "events notes metadata")

DEFAULT_MODEL = "mlx-community/SmolVLM2-500M-Video-Instruct-mlx"
DEFAULT_CHUNK_SECONDS = 10.0
DEFAULT_FPS = 1.0
DEFAULT_WIDTH = 384
DEFAULT_MAX_TOKENS = 16
_YES_NO = re.compile(r"^\s*(yes|no)[\s.!]*$", re.IGNORECASE)


def _load_backend(model_path):
    try:
        import mlx.core as mx
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.generate.video import processor_handles_video
        from mlx_vlm.generate.common import PromptCacheState
    except ImportError as error:
        raise VisionError(
            "--model-trigger needs the optional local vision dependencies. "
            "Install them with `pip install 'trigger-warnings[vision]'` "
            "(Apple Silicon is required by the MLX backend)."
        ) from error
    try:
        # Inference receives a resolved directory and never chooses a download.
        # Keep incidental library output separate from the scan heartbeat.
        with contextlib.redirect_stderr(io.StringIO()):
            model, processor = load(str(model_path))
    except Exception as error:
        raise VisionError(
            "could not load local vision model {!r}: {}".format(str(model_path), error)
        ) from error
    if model.config.model_type != "smolvlm" and not processor_handles_video(processor):
        raise VisionError("this model's MLX processor does not consume video input; use a video-capable model")
    # Some VLM image processors split a frame into many large tiles. Video
    # frames use one small image input for predictable cost.
    image_processor = getattr(processor, "image_processor", None)
    if image_processor is not None:
        image_processor.size = {"longest_edge": 512}
        if hasattr(image_processor, "do_image_splitting"):
            image_processor.do_image_splitting = False
    return mx, generate, apply_chat_template, model, processor, PromptCacheState


def _answer(generate, apply_chat_template, model, processor, mx, clip, trigger, max_tokens, fps,
            prompt_cache_state=None, descriptor=None):
    question = descriptor if descriptor else trigger
    prompt = (
        "This video clip covers {:.3f} to {:.3f} seconds. "
        "Its sampled frames are in chronological order at these video times: {}. "
        "Does this clip show the following: {}? Answer only yes or no.".format(
            clip.start, clip.end, ", ".join("{:.3f}s".format(t) for t in clip.timestamps), question)
    )
    try:
        # SmolVLM's video processor expands one visual token per frame. Native
        # video processors (such as Qwen's) need a video token instead.
        prompt_options = ({"num_images": len(clip.frames)}
                          if model.config.model_type == "smolvlm" else
                          {"num_images": 0, "video": clip.source, "fps": fps})
        prompt = apply_chat_template(processor, model.config, prompt, **prompt_options)
        generate_kwargs = dict(
            video=[clip.frames],
            fps=fps,
            max_tokens=max_tokens,
            temperature=0.0,
            verbose=False,
        )
        if prompt_cache_state is not None:
            generate_kwargs["prompt_cache_state"] = prompt_cache_state
        result = generate(
            model,
            processor,
            prompt,
            **generate_kwargs,
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
    local_only=False,
    fps=DEFAULT_FPS,
    chunk_seconds=DEFAULT_CHUNK_SECONDS,
    width=DEFAULT_WIDTH,
    max_tokens=DEFAULT_MAX_TOKENS,
    trigger_descriptors=None,
    report=None,
    json_progress=False,
):
    """Scan ``video`` for each requested trigger and return candidate events.

    The video is decoded directly into memory and never modified.
    Events cover the complete positive chunk because this first scanner does
    not claim sub-chunk temporal precision. ``report`` receives progress text.

    When ``trigger_descriptors`` is a dict mapping trigger labels (case-folded)
    to descriptive strings, the descriptor replaces the bare label in the model
    question. The event ``label`` stays as the original trigger name.
    """
    video = Path(video).resolve()
    if not video.is_file():
        raise VisionError("video does not exist: {}".format(video))
    if not triggers or any(not isinstance(trigger, str) or not trigger.strip() for trigger in triggers):
        raise VisionError("--model-trigger needs at least one non-empty trigger")
    triggers = [trigger.strip() for trigger in triggers]
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

    with ProgressBar(total_triggers=len(triggers),
                     enabled=report is not None or json_progress,
                     json_progress=json_progress) as progress:
        with VideoReader(video, fps, width) as reader:
            duration = reader.duration
            chunks = math.ceil(duration / chunk_seconds)
            progress.total_chunks = chunks
            progress.set_stage("Resolving local model" if local_only else "Resolving model (may download)")
            # Resolve first: inference only receives an existing local directory.
            with contextlib.redirect_stderr(io.StringIO()):
                model_path = resolve_model(model, revision=revision, local_only=local_only)
            progress.set_stage("Loading model")
            mx, generate, apply_chat_template, loaded_model, processor, PromptCacheState = _load_backend(model_path)
            descs = {k.casefold(): v for k, v in (trigger_descriptors or {}).items()}
            events = []
            for number in range(chunks):
                progress.start_chunk(number)
                start = number * chunk_seconds
                end = min(duration, start + chunk_seconds)
                clip = reader.read_clip(start, end)
                # A fresh cache per chunk: within the chunk every trigger
                # shares the same video-frame prefix so the KV prefill from
                # trigger 1 is reused for triggers 2–N.
                cache_state = PromptCacheState() if len(triggers) > 1 else None
                for trigger_idx, trigger in enumerate(triggers):
                    progress.start_trigger(trigger_idx, trigger)
                    desc = descs.get(trigger.strip().casefold())
                    if _answer(generate, apply_chat_template, loaded_model, processor, mx,
                               clip, trigger.strip(), max_tokens, fps,
                               prompt_cache_state=cache_state,
                               descriptor=desc):
                        events.append({
                            "start": round(start, 3),
                            "end": round(end, 3),
                            "label": trigger.strip(),
                            "severity": "model-candidate",
                        })
                    progress.finish_trigger()
                del clip, cache_state
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
        "localOnly": local_only,
        "videoInput": "in-memory-video",
        "fps": fps,
        "chunkSeconds": chunk_seconds,
        "frameWidth": width,
        "maxTokens": max_tokens,
        "durationSeconds": duration,
        "chunks": chunks,
        "requestedTriggers": [trigger.strip() for trigger in triggers],
        "triggerDescriptors": descs if descs else None,
        "candidateEvents": len(events),
        "notes": notes,
    }
    return VisionScan(events, notes, metadata)
