# Trigger Warnings

[![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=for-the-badge)](pyproject.toml)
[![Tests](https://img.shields.io/github/actions/workflow/status/alexfur/trigger-warnings/tests.yml?branch=main&style=for-the-badge)](https://github.com/alexfur/trigger-warnings/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)

Add advance warnings to the subtitles of a local video. The output combines
dialogue with a generic `TRIGGER INCOMING` warning, without naming the trigger
on screen. Load the new subtitle file in your video player.

## Choose your trigger source

**Local AI scanning and DoesTheDogDie are two alternative ways to get trigger
timestamps.** Neither requires the other.

| Source | How it gets the times | What you need |
| --- | --- | --- |
| **Local AI model** | Scans your video for triggers you describe with `--model-trigger`. | A Mac with an Apple M-series chip; no API account |
| **DoesTheDogDie (DDD)** | Imports community timestamps for `--ddd-item`. | Your own DDD API key; no local AI model |
| **Your own timestamps** | Reads times and labels from `--events events.json`. | An event JSON file; no API account |

All three also need dialogue subtitles: an existing SRT file or a suitable
subtitle track inside the video.

[Scan a video](#scan-a-video-with-local-ai) ·
[Use DDD timestamps](#use-doesthedogdie-timestamps) ·
[Supply your own times](#supply-your-own-timestamps)

## Scan a video with local AI

The AI runs on your Mac. Your video stays on your computer. It scans the video
before you watch and then creates a subtitle file; this is not a live playback
warning system.

### 1. Install

You need:

- A Mac with an Apple M-series chip, such as M1 or M2.
- Python 3.10 or newer and Git installed.
- [Homebrew](https://brew.sh/) for the FFmpeg install command below.

Run these commands in Terminal:

```bash
brew install ffmpeg
git clone https://github.com/alexfur/trigger-warnings.git
cd trigger-warnings
python3 -m venv .venv
.venv/bin/python -m pip install '.[vision]'
```

FFmpeg reads the video. The last command installs Trigger Warnings and the
additional software it needs to run AI models. Keep using this Terminal in
the `trigger-warnings` folder for the examples below.

### 2. Choose the video and triggers

Replace `movie.mkv` with your video's path and describe each trigger in a
separate `--model-trigger` option. Quote paths containing spaces.

```bash
.venv/bin/trigger-warnings \
  --video "movie.mkv" \
  --model-trigger 'blood' \
  --model-trigger 'a person holding a gun' \
  --output "movie.warnings.ass"
```

This example uses an English subtitle track embedded in the video. If you
have a separate SRT file, add `--subtitles "movie.srt"` to the command.

To check which embedded tracks are available before scanning:

```bash
.venv/bin/trigger-warnings --video "movie.mkv" --list-streams
```

Use `--stream INDEX` to select an index from that list, or `--language CODE`
to change the preferred language (default: `eng`). If the video has no usable
subtitle track, supply an SRT file.

**SmolVLM2 is the default model:**
`mlx-community/SmolVLM2-500M-Video-Instruct-mlx`. The first scan downloads
the model from Hugging Face. Later runs reuse the cached files. More triggers
mean more model checks and a longer scan.

### 3. Load the result

When the command reports `Wrote movie.warnings.ass`, open the video in VLC
and select **Subtitle → Add Subtitle File** to load it. Warnings normally
start 20 seconds before each detected event; `--lead` changes that interval.

> [!IMPORTANT]
> The video and original subtitle file remain unchanged. Existing output files
> are never overwritten. Choose a new output name when running again.

> [!WARNING]
> AI results are candidate timestamps, not verified detections. By default,
> the model checks one frame per second in 10-second chunks and marks the whole
> positive chunk. It can miss brief events or flag scenes incorrectly.
> This scanner checks images, not audio. Review the output before relying on it.
> No candidates means no subtitle is written; it does not mean the video is
> free of your triggers. A warning ending is never an all-clear.

### Optional: choose another model

Use `--model` to override SmolVLM2, for example with Qwen2.5-VL:

```bash
.venv/bin/trigger-warnings \
  --video "movie.mkv" \
  --model "mlx-community/Qwen2.5-VL-7B-Instruct-4bit" \
  --model-trigger 'blood' \
  --output "movie.qwen-warnings.ass"
```

Qwen is optional. The repository's [initial benchmark](docs/experiments/prescan-m4.md)
measures SmolVLM2 on synthetic footage, not Qwen or real trigger-detection accuracy.

| Option | Default | Purpose |
| --- | --- | --- |
| `--model-fps` | `1` | Frames sampled per second |
| `--model-chunk` | `10` | Video seconds checked per chunk |
| `--model-width` | `384` | Extracted frame width in pixels |
| `--model-max-tokens` | `16` | Maximum length of each model answer |
| `--model-revision` | Unspecified | Pin a model revision |

## Use DoesTheDogDie timestamps

This route imports timestamps without running an AI model. The base package
needs Python 3.9 or newer. From the cloned repository, install it with:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
```

These examples use an existing `movie.srt`, so they do not need FFmpeg.
For Windows, use `.venv\Scripts\python.exe` and
`.venv\Scripts\trigger-warnings.exe` in place of the `.venv/bin/` commands.

1. Get your own [DDD API key](https://www.doesthedogdie.com/api).
2. Run the setup command in your terminal and follow the hidden prompts.
   Skip OpenSubtitles setup if you already have dialogue subtitles.

   ```bash
   .venv/bin/trigger-warnings --setup --save
   ```

3. Search for the title and choose the matching item ID:

   ```bash
   .venv/bin/trigger-warnings --ddd-search 'Jaws' --ddd-year 1975
   ```

4. Replace `ITEM_ID` below with that numeric ID. Preview its available
   categories, then generate warnings for the exact labels you choose:

   ```bash
   .venv/bin/trigger-warnings --subtitles "movie.srt" \
     --ddd-item ITEM_ID --dry-run

   .venv/bin/trigger-warnings --subtitles "movie.srt" \
     --ddd-item ITEM_ID --category 'a dog dies' \
     --output "movie.warnings.ass"
   ```

Repeat `--category` for more labels. Omitting it selects every available
category. DDD timestamps can be incomplete or belong to a different edition.
Retain the `Powered by DoesTheDogDie.com` attribution.

> [!WARNING]
> Keep API keys out of chat, source control and logs. The setup command can
> save credentials in your operating system's keychain. `DDD_API_KEY` is the
> environment-variable alternative and overrides a stored key. Avoid
> `--ddd-api-key`: its value can appear in shell history and the process list.

### Optional: use DDD labels for a local scan

With local AI support installed, add `--model-from-ddd` to use an item's
labels as the checklist while the model finds times in your video:

```bash
.venv/bin/trigger-warnings --video "movie.mkv" \
  --ddd-item ITEM_ID --model-from-ddd --category 'a dog dies' \
  --output "movie.model-warnings.ass"
```

> [!NOTE]
> This mode currently takes labels from DDD's timestamped ratings, so it still
> requires those ratings even though it replaces their times. It does not
> fetch your personal saved trigger preferences. Use `--model-trigger` to
> supply labels directly when no timestamped ratings exist.

## Supply your own timestamps

Use the base installation above. Save this as `events.json`:

```json
[
  {"start": 125.5, "end": 138, "label": "loud noises", "severity": "moderate"},
  {"start": "00:12:05.000", "label": "flashing lights"}
]
```

`start` accepts seconds or `HH:MM:SS.mmm`. `end` and `severity` are
optional. Without an end time, the warning stops at the event start, which
is not a safe point to resume watching.

```bash
.venv/bin/trigger-warnings --subtitles "movie.srt" \
  --events events.json --output "movie.warnings.ass"
```

To try this route without preparing files, use the synthetic examples:

```bash
.venv/bin/trigger-warnings --subtitles examples/dialogue.srt \
  --events examples/events.json --output example.warnings.ass
```

## Other useful options

- `--output` accepts a new `.ass` or `.srt` filename.
- `--provenance FILE.json` saves source, timing settings and result counts.
- `--verify FILE.png` renders one preview frame; it requires `--video` and FFmpeg.
- `--dry-run` writes no files. With a local model, it still runs the scan.
- `--json` returns one result at the end; omit it to see progress messages.
- OpenSubtitles supplies dialogue only through `--os-search` and `--os-file`.
  It needs your own account and API key.

Run `.venv/bin/trigger-warnings --help` for all flags. Coding agents should
follow the [agent skill](skills/trigger-warnings/SKILL.md): check `ok`, read
`notes` and `messages`, and use `filesWritten` to confirm output was created.

## Contributing and licence

```bash
python3 -m unittest discover -s tests -v
```

Set `RUN_MEDIA_TESTS=1` to include FFmpeg integration tests. See
[CONTRIBUTING.md](CONTRIBUTING.md) for development rules and
[SECURITY.md](SECURITY.md) for vulnerability reporting. Never commit private
trigger profiles, credentials or commercial media and subtitle files.

[MIT licence](LICENSE). Videos, subtitles and event data you supply are not
covered by the project's licence.
