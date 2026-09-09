# Trigger Warnings

Create a separate subtitle track that shows a generic `TRIGGER INCOMING`
warning before scenes in a local video. The video and original subtitles are
never changed. The result is a review aid, not a guarantee that a film is safe.

Choose one trigger source for each run:

| Source | What supplies the trigger timestamps |
| --- | --- |
| **Local model** | The model watches your video for the labels you provide. |
| **DoesTheDogDie** | DDD supplies community timestamps for a selected item. |
| **Your events** | You supply an `events.json` file. |

The local model and DoesTheDogDie are alternative sources. They are not used
together by default. The optional `--model-from-ddd` mode is the one deliberate
exception: it uses DDD labels as a checklist, then replaces DDD timestamps with
timestamps found by the local model.

## Quick start: scan a video

The model scanner currently runs on Apple Silicon through MLX. Install FFmpeg,
then install the optional vision dependencies:

```sh
git clone https://github.com/alexfur/trigger-warnings.git
cd trigger-warnings
python3 -m venv .venv
.venv/bin/python -m pip install '.[vision]'
```

Run one check per trigger. Repeat `--model-trigger` for more checks:

```sh
.venv/bin/trigger-warnings \
  --video movie.mkv \
  --model-trigger 'head smashing against a hard surface' \
  --model-trigger 'eye injury or mutilation' \
  --model-trigger 'fingernail or toenail injury' \
  --output movie.warnings.ass \
  --provenance movie.warnings.json
```

The default model is
`mlx-community/Qwen2.5-VL-7B-Instruct-4bit`. It is downloaded from Hugging
Face on first use. Choose another MLX-VLM model with `--model`:

```sh
.venv/bin/trigger-warnings --video movie.mkv \
  --model 'mlx-community/Qwen2.5-VL-7B-Instruct-4bit' \
  --model-trigger 'blood' --output movie.warnings.ass
```

For a smaller and faster model, use SmolVLM2 explicitly:

```sh
.venv/bin/trigger-warnings --video movie.mkv \
  --model 'mlx-community/SmolVLM2-500M-Video-Instruct-mlx' \
  --model-trigger 'blood' --output movie.warnings.ass
```

SmolVLM2 uses less memory and was the model used for the repository's initial
throughput benchmark. Qwen is the stronger default, but both are candidate
generators and need the same review.

Useful tuning flags are `--model-fps` (default `1`), `--model-chunk` (default
`10` seconds), `--model-width` (default `384` pixels), and
`--model-max-tokens` (default `16`). `--model-revision` pins a model revision.
`--dry-run` performs the scan but writes no files.

Each positive result covers its whole sampled chunk, not an exact frame. A
short or visually ambiguous event can be missed. The command refuses to write
a subtitle when it finds no candidates, so that is never presented as an
all-clear. Review the generated track against the video.

## Alternative source: DoesTheDogDie

DoesTheDogDie can provide community timestamps directly:

```sh
export DDD_API_KEY='your-key-in-your-shell'

.venv/bin/trigger-warnings \
  --video movie.mkv \
  --ddd-item ITEM_ID \
  --category 'eye mutilation' \
  --subtitles movie.srt \
  --output movie.warnings.ass
```

Repeat `--category` to select categories. DDD labels and
timestamps are community data, can be incomplete, and may not match your
edition. Keep the `Powered by DoesTheDogDie.com` attribution.

If you want DDD to provide only the checklist while the local model finds the
timestamps, add `--model-from-ddd`. That hybrid mode deliberately ignores DDD
timestamps:

```sh
.venv/bin/trigger-warnings --video movie.mkv --ddd-item ITEM_ID \
  --model-from-ddd --category 'eye mutilation' \
  --subtitles movie.srt --output movie.model-warnings.ass
```

Find an item ID first with `--ddd-search TITLE --ddd-year YEAR`. The search
only lists candidates; it does not choose one for you. DDD access requires your
own API key. Prefer `DDD_API_KEY` over `--ddd-api-key`, which is visible in
shell history and the process list.

## Dialogue subtitles

Pass an existing SRT with `--subtitles`, or let FFmpeg extract an embedded
subtitle stream from the video with `--video`:

```sh
.venv/bin/trigger-warnings --list-streams --video movie.mkv
.venv/bin/trigger-warnings --video movie.mkv --language eng \
  --events events.json --output movie.warnings.ass
```

If no suitable embedded track exists, obtain an SRT separately, for example
with the optional OpenSubtitles search and download modes. OpenSubtitles needs
your own account and API key; it supplies dialogue only, never trigger data.

## Alternative source: your own timestamps

Create an event JSON file when you already know the times:

```json
[
  {"start": 125.5, "end": 138, "label": "loud noises", "severity": "moderate"},
  {"start": "00:12:05.000", "label": "flashing lights"}
]
```

Then run:

```sh
.venv/bin/trigger-warnings --subtitles movie.srt \
  --events events.json --output movie.warnings.ass
```

`start` is required and accepts seconds or `HH:MM:SS.mmm`. `end` is optional;
without it, the warning ends at the event start, which is not a safe point to
resume playback.

## Output and safety

- `.ass` and `.srt` output contain the unchanged dialogue plus generic warning
  cues. The category is never shown on screen.
- `--provenance FILE` records the source, model settings, timing settings and
  counts. It contains no credential or dialogue copy.
- `--verify PNG` renders one preview frame from a video. It proves only that
  FFmpeg rendered the file, not that every player will position it identically.
- Existing files are never overwritten. Failed runs roll back files created by
  that run.
- A warning ending is never an all-clear. Check playback near the beginning and
  end of the track, and check the edition you intend to watch.

Use `--json` for automation (except `--help` and `--version`). Branch on `ok`,
read every `notes` entry, and use `filesWritten` as the evidence that a file
was published.

## Development

The base package has no runtime dependencies. Run the tests with:

```sh
python3 -m unittest discover -s tests -v
```

Set `RUN_MEDIA_TESTS=1` to include the FFmpeg integration checks. The
repository's [contributing guide](CONTRIBUTING.md) covers fixtures, security
and release work. Never commit a commercial video or subtitle file, a
credential, or a personal trigger profile.

The [reusable agent skill](skills/trigger-warnings/SKILL.md) contains the full
machine-readable CLI contract and credential rules.
