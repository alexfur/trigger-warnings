"""Optional cloud video trigger scanning through Google Gemini API.

This backend provides single-shot, whole-movie trigger detection by uploading
the video to Google's File API and querying Gemini 2.0 Flash (or 1.5 Flash)
for structured timestamped detections.

To guard user privacy and avoid triggering automated piracy or copyright
refusals:
1. Videos are sanitized locally with FFmpeg (metadata and rip tags stripped,
   and downscaled to 480p to cut file size by ~80%).
2. Files are uploaded under a randomized anonymous hash name.
3. The uploaded file is immediately deleted from Google's servers in a
   `finally:` block after inference completes.
"""

from collections import namedtuple
import decimal
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time

from .progress import ProgressBar
from .vision import VisionScan

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"


class GeminiError(ValueError):
    """The Gemini cloud scanner could not safely produce events."""


def _parse_time_seconds(val):
    """Convert string timestamps (HH:MM:SS[.mmm], MM:SS[.mmm], or seconds) to float seconds."""
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    parts = s.split(":")
    try:
        if len(parts) == 2:
            m, sec = parts
            return int(m) * 60 + float(sec.replace(",", "."))
        elif len(parts) == 3:
            h, m, sec = parts
            return int(h) * 3600 + int(m) * 60 + float(sec.replace(",", "."))
        return float(s)
    except (ValueError, decimal.InvalidOperation) as err:
        raise GeminiError(f"unusable timestamp from Gemini: {val!r}") from err


def sanitize_video(video_path, target_height=480, report=None, progress=None):
    """Strip metadata, container tags, and downscale video for lightweight cloud upload.

    Returns the path to a newly created temporary MP4 file that the caller must remove.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        if report:
            report("ffmpeg not found on PATH; skipping local sanitization/downscaling.")
        return None

    duration = None
    try:
        from . import media
        info = media.probe(video_path)
        duration = float(media.duration_ms(info)) / 1000.0
    except Exception:
        pass

    if report:
        report("Sanitizing video (stripping metadata & tags, downscaling to {}p)...".format(target_height))

    temp_file = tempfile.NamedTemporaryFile(suffix=".mp4", prefix="scan_", delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    hwaccel_args = []
    vcodec_args = ["-c:v", "libx264", "-crf", "28", "-preset", "veryfast"]
    if platform.system() == "Darwin":
        hwaccel_args = ["-hwaccel", "videotoolbox"]
        vcodec_args = ["-c:v", "h264_videotoolbox", "-b:v", "450k"]

    cmd = [
        ffmpeg,
        "-hide_banner", "-nostdin", "-y",
        *hwaccel_args,
        "-i", str(video_path),
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-sn",
        "-vf", f"scale=-2:{target_height}",
        *vcodec_args,
        "-c:a", "aac", "-ac", "1", "-b:a", "64k",
        "-progress", "pipe:1",
        str(temp_path),
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        speed = "1x"
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("speed="):
                speed = line.split("=")[1].strip()
            elif line.startswith("out_time_us="):
                try:
                    us = int(line.split("=")[1])
                    cur_s = us / 1_000_000.0
                    if duration and duration > 0:
                        frac = min(1.0, cur_s / duration)
                        if progress:
                            progress.set_custom_progress("Sanitizing video", fraction=frac, detail=f"{speed} speed")
                except (ValueError, IndexError):
                    pass
        proc.wait()
        if proc.returncode != 0:
            if temp_path.exists():
                temp_path.unlink()
            if report:
                report("ffmpeg sanitization exited with error; using original file.")
            return None
        if progress:
            progress.set_custom_progress("Sanitizing video", fraction=1.0, detail="done")
        return temp_path
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink()
        if report:
            report(f"ffmpeg sanitization error ({exc}); using original file.")
        return None


def scan_video_gemini(
    video,
    triggers,
    *,
    api_key=None,
    model=DEFAULT_GEMINI_MODEL,
    trigger_descriptors=None,
    report=None,
    json_progress=False,
    sanitize=True,
    target_height=480,
    client_factory=None,
):
    """Scan ``video`` using Google Gemini multimodal cloud API.

    Uploads the video via the File API, prompts Gemini with structured JSON
    schema for exact start and end timestamps of the requested triggers, and
    cleans up the uploaded file immediately after.
    """
    video = Path(video).resolve()
    if not video.is_file():
        raise GeminiError(f"video does not exist: {video}")

    if not triggers or any(not isinstance(t, str) or not t.strip() for t in triggers):
        raise GeminiError("Gemini scan needs at least one non-empty trigger")

    triggers = [t.strip() for t in triggers]
    if len(set(t.casefold() for t in triggers)) != len(triggers):
        raise GeminiError("trigger values must be unique")

    effective_api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not effective_api_key:
        raise GeminiError(
            "Gemini API key is required. Set the GEMINI_API_KEY environment variable "
            "or pass --gemini-api-key."
        )

    if client_factory is None:
        try:
            from google import genai
            from google.genai import types
            from pydantic import BaseModel, Field
            from typing import List
        except ImportError as err:
            raise GeminiError(
                "Gemini scanning requires google-genai and pydantic. "
                "Install them with `pip install 'trigger-warnings[gemini]'`."
            ) from err

        client = genai.Client(api_key=effective_api_key)
    else:
        client = client_factory(api_key=effective_api_key)
        try:
            from google.genai import types
            from pydantic import BaseModel, Field
            from typing import List
        except ImportError:
            types = None
            BaseModel = object
            Field = None
            List = list

    descs = {k.casefold(): v for k, v in (trigger_descriptors or {}).items()}

    with ProgressBar(total_triggers=len(triggers),
                     enabled=report is not None or json_progress,
                     json_progress=json_progress) as progress:
        sanitized_tmp = None
        upload_path = video
        if sanitize:
            progress.set_custom_progress("Sanitizing video", fraction=0.0)
            sanitized_tmp = sanitize_video(video, target_height=target_height, report=report, progress=progress)
            if sanitized_tmp is not None:
                upload_path = sanitized_tmp

        uploaded_file = None
        try:
            size_mb = upload_path.stat().st_size / (1024 * 1024)
            progress.set_custom_progress(f"Uploading video ({size_mb:.1f} MB)", fraction=0.0)
            if report:
                report(f"Uploading {size_mb:.1f} MB to Google AI Studio File API...")

            anon_name = f"scan_{os.urandom(4).hex()}"
            upload_config = types.UploadFileConfig(display_name=anon_name) if types else None

            t_upload_start = time.time()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                if upload_config:
                    future = executor.submit(client.files.upload, file=str(upload_path), config=upload_config)
                else:
                    future = executor.submit(client.files.upload, file=str(upload_path))

                while not future.done():
                    time.sleep(0.5)
                    elapsed = time.time() - t_upload_start
                    # Smooth visual progress indicator while upload completes
                    est_frac = min(0.95, elapsed / max(5.0, size_mb / 4.0))
                    progress.set_custom_progress(
                        f"Uploading video ({size_mb:.1f} MB)",
                        fraction=est_frac,
                        detail=f"{elapsed:.0f}s elapsed",
                    )
                uploaded_file = future.result()

            progress.set_custom_progress("Uploaded video", fraction=1.0, detail="complete")

            progress.set_custom_progress("Processing on Google Cloud", fraction=0.25, detail="ingesting...")
            if report:
                report(f"Uploaded as {uploaded_file.name}. Processing on Google Cloud...")

            poll_start = time.time()
            while getattr(uploaded_file.state, "name", str(uploaded_file.state)) == "PROCESSING":
                time.sleep(2)
                elapsed_poll = time.time() - poll_start
                frac = min(0.65, 0.25 + (elapsed_poll / 120.0))
                progress.set_custom_progress(
                    "Processing on Google Cloud",
                    fraction=frac,
                    detail=f"{elapsed_poll:.0f}s elapsed",
                )
                uploaded_file = client.files.get(name=uploaded_file.name)
                if time.time() - poll_start > 300:
                    raise GeminiError("Timed out waiting for Google File API to process video (300s).")

            state_str = getattr(uploaded_file.state, "name", str(uploaded_file.state))
            if state_str == "FAILED":
                err_msg = getattr(uploaded_file, "error", "unknown error")
                raise GeminiError(f"Google File API processing failed: {err_msg}")

            progress.set_custom_progress(f"Gemini analyzing video", fraction=0.7, detail="querying model...")

            prompt_lines = [
                "Analyze this full video for the following specific physical trauma or violent triggers:",
            ]
            for t in triggers:
                desc = descs.get(t.casefold())
                if desc:
                    prompt_lines.append(f"- Trigger '{t}': {desc}")
                else:
                    prompt_lines.append(f"- Trigger '{t}': actual physical injury, attack, or violent threat involving {t}")

            prompt_lines.append(
                "\nIMPORTANT INSTRUCTIONS:\n"
                "1. Report ONLY actual violence, severe injury, trauma, or imminent physical attacks.\n"
                "2. Do NOT report ordinary everyday occurrences (e.g. normal eye contact, blinking, applying eye makeup, or manicures).\n"
                "3. For each detected event, provide:\n"
                "   - start_time: timestamp in 'HH:MM:SS' format when the trigger starts.\n"
                "   - end_time: timestamp in 'HH:MM:SS' format when the trigger ends.\n"
                "   - trigger: the exact trigger name matched from the list.\n"
                "   - description: a concise objective explanation of what happens.\n"
                "4. If none of the requested triggers occur, return an empty list."
            )
            prompt_text = "\n".join(prompt_lines)

            if report:
                report(f"Sending prompt to {model} for whole-movie scan...")

            gen_config = None
            if types and issubclass(BaseModel, object) and BaseModel is not object:
                class TriggerItem(BaseModel):
                    start_time: str = Field(description="Start timestamp as HH:MM:SS or seconds")
                    end_time: str = Field(description="End timestamp as HH:MM:SS or seconds")
                    trigger: str = Field(description="Exact trigger name matched")
                    description: str = Field(description="Concise description of the event")

                class GeminiScanResponse(BaseModel):
                    events: List[TriggerItem]

                safety_settings = [
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                ]

                gen_config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GeminiScanResponse,
                    safety_settings=safety_settings,
                    temperature=0.0,
                )

            def _call_gemini_with_retry(active_model):
                delay = 5.0
                for attempt in range(4):
                    try:
                        if gen_config:
                            return client.models.generate_content(
                                model=active_model,
                                contents=[uploaded_file, prompt_text],
                                config=gen_config,
                            )
                        else:
                            return client.models.generate_content(
                                model=active_model,
                                contents=[uploaded_file, prompt_text],
                            )
                    except Exception as gen_err:
                        err_msg = str(gen_err)
                        is_transient = any(code in err_msg for code in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "demand"))
                        if is_transient and attempt < 3:
                            if report:
                                report(f"Model {active_model} temporarily unavailable ({err_msg[:60]}...); retrying in {delay:.0f}s...")
                            progress.set_custom_progress(f"Gemini busy, retrying", fraction=0.75, detail=f"retry in {delay:.0f}s")
                            time.sleep(delay)
                            delay *= 2
                        elif is_transient and active_model != "gemini-3.7-flash":
                            if report:
                                report(f"Model {active_model} busy; falling back to gemini-3.7-flash...")
                            return _call_gemini_with_retry("gemini-3.7-flash")
                        else:
                            raise

            response = _call_gemini_with_retry(model)

            resp_text = getattr(response, "text", "") or ""
            try:
                data = json.loads(resp_text)
            except json.JSONDecodeError as err:
                raise GeminiError(f"Gemini returned invalid JSON: {resp_text[:200]}") from err

            raw_events = data.get("events", []) if isinstance(data, dict) else data

            events = []
            canonical_triggers = {t.casefold(): t for t in triggers}

            for item in raw_events:
                if not isinstance(item, dict):
                    continue
                start_raw = item.get("start_time") or item.get("start")
                end_raw = item.get("end_time") or item.get("end")
                trigger_raw = str(item.get("trigger", "")).strip()
                desc_raw = str(item.get("description", "")).strip()

                if start_raw is None or end_raw is None:
                    continue

                try:
                    start_s = _parse_time_seconds(start_raw)
                    end_s = _parse_time_seconds(end_raw)
                except GeminiError:
                    continue

                if end_s < start_s:
                    start_s, end_s = end_s, start_s
                if end_s == start_s:
                    end_s = start_s + 5.0

                matched_label = canonical_triggers.get(trigger_raw.casefold(), trigger_raw)

                events.append({
                    "start": round(start_s, 3),
                    "end": round(end_s, 3),
                    "label": matched_label,
                    "severity": "model-candidate",
                })

                if report:
                    report(f"Detected {matched_label} at {start_s:.1f}s - {end_s:.1f}s: {desc_raw}")

            if not events:
                raise GeminiError(
                    "Gemini found no candidate events for the requested triggers. "
                    "This does not establish that the video is free of triggers; no subtitle was written."
                )

        finally:
            if sanitized_tmp and sanitized_tmp.exists():
                try:
                    sanitized_tmp.unlink()
                except OSError:
                    pass

            if uploaded_file is not None and hasattr(client, "files"):
                try:
                    client.files.delete(name=uploaded_file.name)
                    if report:
                        report(f"Cleaned up remote file {uploaded_file.name} from Google Cloud.")
                except Exception as cleanup_err:
                    if report:
                        report(f"Note: Could not immediately delete remote file {uploaded_file.name}: {cleanup_err}")

    notes = [
        f"Generated by Google Gemini ({model}) cloud video analysis.",
        "Candidate timestamps are generated by whole-video multimodal comprehension.",
        "Model candidates are not a safety guarantee. A warning ending is not an all-clear.",
    ]

    metadata = {
        "kind": "gemini-cloud-model",
        "provider": "google-gemini",
        "model": model,
        "sanitized": sanitize and (sanitized_tmp is not None),
        "requestedTriggers": triggers,
        "triggerDescriptors": descs if descs else None,
        "candidateEvents": len(events),
        "notes": notes,
    }

    return VisionScan(events, notes, metadata)
