<div align="center">

# Trigger Warnings

Spoiler-free trigger warning subtitles for local video.

[![Tests](https://img.shields.io/github/actions/workflow/status/alexfur/trigger-warnings/tests.yml?style=for-the-badge)](https://github.com/alexfur/trigger-warnings/actions)
[![Licence](https://img.shields.io/github/license/alexfur/trigger-warnings?style=for-the-badge)](LICENSE)
[![Stars](https://img.shields.io/github/stars/alexfur/trigger-warnings?style=for-the-badge)](https://github.com/alexfur/trigger-warnings/stargazers)

</div>

## What is this?

Trigger Warnings creates a separate `.ass` or `.srt` track that displays a
generic `TRIGGER INCOMING` banner before selected scenes. It never changes the
video or dialogue subtitles. Use a local vision model, DoesTheDogDie (DDD)
timestamps, or your own event JSON as the timestamp source.

<p align="center">
  <img src="assets/demo.gif" width="720"
       alt="A generic trigger warning appears above ordinary dialogue in a video.">
</p>

## Quick start: local model scan

Use this route only on a Mac with an Apple chip: M1, M2, M3 or M4. The model
runs on your Mac; no video is uploaded to an AI service. You need FFmpeg to
read video and a separate Python extra that installs the local-model support.

On that Mac, run:

```sh
brew install ffmpeg
git clone https://github.com/alexfur/trigger-warnings.git
cd trigger-warnings
python3 -m venv .venv
.venv/bin/python -m pip install '.[vision]'
```

`brew` is [Homebrew](https://brew.sh/); install it first if the command is not
available. `MLX` is the Apple-chip library installed by the last command. You
do not need to install or configure it separately. If you use an Intel Mac,
Windows or Linux, skip this section and use DDD timestamps or your own
`events.json` instead.

Run one model check per trigger. This extracts an embedded subtitle track from
the video when available and writes a new warning track:

```sh
.venv/bin/trigger-warnings \
  --video movie.mkv \
  --model-trigger 'head smashing against a hard surface' \
  --model-trigger 'eye injury or mutilation' \
  --model-trigger 'fingernail or toenail injury' \
  --output movie.warnings.ass \
  --provenance movie.warnings.json
```

The default is the compact
`mlx-community/SmolVLM2-500M-Video-Instruct-mlx`. It downloads on first use and
was used for the initial throughput benchmark. Use `--model` for a larger
alternative, for example:

```sh
.venv/bin/trigger-warnings --video movie.mkv \
  --model 'mlx-community/Qwen2.5-VL-7B-Instruct-4bit' \
  --model-trigger 'blood' --output movie.warnings.ass
```

`--model-fps`, `--model-chunk`, `--model-width` and `--model-max-tokens` tune
speed and coverage. `--model-revision` pins a revision. `--dry-run` performs
the scan without writing files.

## Choose one trigger source

| Source | Command | Timestamp source |
| --- | --- | --- |
| Local model | `--model-trigger LABEL` | Positive sampled video chunks |
| DoesTheDogDie | `--ddd-item ID` | Community timestamps |
| Your own data | `--events events.json` | Your event JSON |

Local model scanning and DDD timestamps are alternatives. They are not combined
in a normal run.

### DoesTheDogDie timestamps

Find the correct item, then choose only the categories you want:

```sh
.venv/bin/trigger-warnings --ddd-search 'Title' --ddd-year 2024

.venv/bin/trigger-warnings --subtitles movie.srt \
  --ddd-item ITEM_ID --category 'eye mutilation' \
  --output movie.warnings.ass
```

DDD needs your own API key. Prefer `DDD_API_KEY` to `--ddd-api-key`, which can
be exposed through shell history and the process list. DDD data is community
supplied, incomplete, and may not match your edition.

`--model-from-ddd` is the deliberate hybrid: DDD supplies the trigger labels as
a checklist, while the local model supplies the timestamps. It ignores DDD
timestamps:

```sh
.venv/bin/trigger-warnings --video movie.mkv --ddd-item ITEM_ID \
  --model-from-ddd --category 'eye mutilation' \
  --output movie.model-warnings.ass
```

### Your own event JSON

```json
[
  {"start": 125.5, "end": 138, "label": "loud noises", "severity": "moderate"},
  {"start": "00:12:05.000", "label": "flashing lights"}
]
```

```sh
.venv/bin/trigger-warnings --subtitles movie.srt \
  --events events.json --output movie.warnings.ass
```

`start` accepts seconds or `HH:MM:SS.mmm`; `end` is optional.

## Subtitles, output and safety

- Pass `--subtitles movie.srt`, or use `--video movie.mkv` to extract an
  embedded stream. `--list-streams` shows stream indices and `--language` sets
  the preferred language.
- OpenSubtitles is an optional dialogue-only source. It never supplies trigger
  data.
- `--provenance FILE` records source and timing metadata without credentials or
  dialogue text. `--verify FILE.png` renders one FFmpeg preview frame.
- Existing files are never overwritten. A failed run rolls back files it made.
- The output never names the trigger on screen.

> Model candidates cover their complete sampled chunks, not exact frames. They
> can miss short or ambiguous events. No result, source, or warning ending is
> an all-clear. Review the generated track against the edition you will watch.

For automation, use `--json` except with `--help` and `--version`. Branch on
`ok`, read every `notes` entry, and treat `filesWritten` as proof that a file
was created.

## Project structure

```text
assets/                 Demo and preview assets
docs/                   Experiments and release material
examples/               Synthetic subtitles and events
scripts/                Demo and benchmark helpers
skills/                 Reusable coding-agent instructions
tests/                  Unit and integration tests
trigger_warnings/       CLI and subtitle-processing package
CHANGELOG.md            Release history
CONTRIBUTING.md         Contribution rules
ROADMAP.md              Planned work
SECURITY.md             Security policy
pyproject.toml          Package metadata and dependencies
```

## Documentation

| Resource | Description |
| --- | --- |
| [`--help`](trigger_warnings/cli.py) | Command-line options and modes. |
| [Agent skill](skills/trigger-warnings/SKILL.md) | JSON contract and credential rules for coding agents. |
| [Pre-scan benchmark](docs/experiments/prescan-m4.md) | SmolVLM2 throughput experiment on Apple M4. |
| [Contributing](CONTRIBUTING.md) | Development setup, fixtures and release process. |
| [Security](SECURITY.md) | Credential handling and vulnerability reporting. |

## Contributing

Run the test suite before opening a pull request:

```sh
python3 -m unittest discover -s tests -v
```

Set `RUN_MEDIA_TESTS=1` to run the FFmpeg checks. Do not commit commercial
video or subtitle files, copied timelines, credentials or personal trigger
profiles.

<a href="https://github.com/alexfur/trigger-warnings/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=alexfur/trigger-warnings" alt="Contributors" />
</a>

## Licence

MIT. See [LICENSE](LICENSE). The licence does not cover videos, subtitles or
event data you supply.

---

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=alexfur/trigger-warnings&type=Date)](https://star-history.com/#alexfur/trigger-warnings&Date)

</div>
