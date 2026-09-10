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


def sanitize_video(video_path, target_height=480, report=None):
    """Strip metadata, container tags, and downscale video for lightweight cloud upload.

    Returns the path to a newly created temporary MP4 file that the caller must remove.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        if report:
            report("ffmpeg not found on PATH; skipping local sanitization/downscaling.")
        return None

    if report:
        report("Sanitizing video (stripping metadata & tags, downscaling to {}p)...".format(target_height))

    temp_file = tempfile.NamedTemporaryFile(suffix=".mp4", prefix="scan_", delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    cmd = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(video_path),
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-sn",
        "-vf", f"scale=-2:{target_height}",
        "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
        "-c:a", "aac", "-ac", "1", "-b:a", "64k",
        str(temp_path),
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
        if result.returncode != 0:
            if temp_path.exists():
                temp_path.unlink()
            if report:
                report(f"ffmpeg sanitization failed ({result.stderr.strip()}); using original file.")
            return None
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

    sanitized_tmp = None
    upload_path = video
    if sanitize:
        sanitized_tmp = sanitize_video(video, target_height=target_height, report=report)
        if sanitized_tmp is not None:
            upload_path = sanitized_tmp

    uploaded_file = None
    try:
        if report:
            size_mb = upload_path.stat().st_size / (1024 * 1024)
            report(f"Uploading {size_mb:.1f} MB to Google AI Studio File API...")

        anon_name = f"scan_{os.urandom(4).hex()}"
        upload_config = types.UploadFileConfig(display_name=anon_name) if types else None

        if upload_config:
            uploaded_file = client.files.upload(file=str(upload_path), config=upload_config)
        else:
            uploaded_file = client.files.upload(file=str(upload_path))

        if report:
            report(f"Uploaded as {uploaded_file.name}. Processing on Google Cloud...")

        poll_start = time.time()
        while getattr(uploaded_file.state, "name", str(uploaded_file.state)) == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
            if time.time() - poll_start > 300:
                raise GeminiError("Timed out waiting for Google File API to process video (300s).")

        state_str = getattr(uploaded_file.state, "name", str(uploaded_file.state))
        if state_str == "FAILED":
            err_msg = getattr(uploaded_file, "error", "unknown error")
            raise GeminiError(f"Google File API processing failed: {err_msg}")

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
            response = client.models.generate_content(
                model=model,
                contents=[uploaded_file, prompt_text],
                config=gen_config,
            )
        else:
            response = client.models.generate_content(
                model=model,
                contents=[uploaded_file, prompt_text],
            )

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
                "description": desc_raw,
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
