# Trigger warnings

Builds a subtitle track that shows a generic **TRIGGER INCOMING** banner ahead of scenes you
asked to be warned about, with the original dialogue left in place. The output is a new `.ass`
or `.srt` for local playback. Timestamps come from the official DoesTheDogDie API using your
own key, or from a JSON file you write. The dialogue track can be one you already have,
one pulled out of the video file, or one fetched from OpenSubtitles with your own account.

![Three stages, left to right. You supply two things: a dialogue track, either a dialogue.srt you have or --os-file ID needing OPENSUBTITLES_API_KEY plus username and password; and timestamps, either an events.json you write or --ddd-item ID needing DDD_API_KEY. The agent plans a dry run that validates, resolves categories, reports the windows and writes nothing. The tool writes warned subtitles and a provenance sidecar as new files.](assets/workflow.svg)

**It does not find scenes, and it never gives an all-clear.** Every timestamp comes from you or
the API, and nothing confirms they describe your copy, so play the file and check. A missing
warning can mean missing data, a different edition or a playback fault. A warning ending is not
a safe-to-resume signal, and no matching events does not mean the video is free of triggers.

## Quickstart

Every run needs two things: a **dialogue track** and **timestamps**. The quickest route uses the
subtitle track already inside your video, so it needs no OpenSubtitles account and no files of
your own.

```sh
git clone https://github.com/alexfur/trigger-warnings.git
cd trigger-warnings
python3 -m venv .venv && .venv/bin/python -m pip install .
source .venv/bin/activate     # so the commands below are just `trigger-warnings`
```

Get your own [DoesTheDogDie API key](https://www.doesthedogdie.com/api), then run three commands
against the film you are about to watch:

```sh
export DDD_API_KEY='your-key'

# 1. Find the title. This only searches, and never picks for you.
trigger-warnings --ddd-search 'Jaws' --ddd-year 1975              # -> id 10154

# 2. See which categories that title actually has. Writes nothing.
trigger-warnings --dry-run --video movie.mkv --ddd-item 10154

# 3. Build the track, warning only about the categories you chose.
trigger-warnings --video movie.mkv --ddd-item 10154 \
  --category 'a dog dies' --output movie.warned.ass
```

Step 2 is where you find out what actually exists, before committing to anything:

```text
Fetched 2 timestamped DoesTheDogDie ratings for item 10154 (2 community, 0 Scene Alerts).
DoesTheDogDie returned 149 ratings without timestamps; they were not turned into warnings.
Selected categories: a dog dies (1 event), a pet dies (1 event)
```

Load `movie.warned.ass` as a subtitle track in VLC or mpv, then **play it and check a warning you
can predict**. Nothing has confirmed that these timestamps match your copy.

**Expect most titles to have nothing.** Timestamps are contributed by volunteers and the large
majority of ratings carry none: Jaws has 2 out of 151. A title with no timestamped rating at all
stops at step 2 with an error, which is not a claim that it is free of triggers. When that
happens, write the times yourself.

`--video` reads the subtitle track embedded in the file, which needs FFmpeg. Swap any step that
does not fit:

| If you have no | Use instead |
| --- | --- |
| subtitles inside the video | `--subtitles dialogue.srt`, or [fetch one from OpenSubtitles](#fetch-the-dialogue-track-from-opensubtitles) |
| DoesTheDogDie key, or no data for your film | [your own timestamps](#or-supply-your-own-timestamps) in a small JSON file |
| patience for a command line | [hand it to a coding agent](#hand-it-to-a-coding-agent) |

## Setup

Python 3.9 or newer, no third-party packages. FFmpeg is needed only for `--video`,
`--list-streams` and `--verify`.

This runs the bundled harmless example, so a clean run confirms the install:

```sh
trigger-warnings --subtitles examples/dialogue.srt \
  --events examples/events.json --output example.warned.ass
```

Prefix with `.venv/bin/` instead of activating the environment, or run
`python3 -m trigger_warnings` to use it uninstalled.

## Hand it to a coding agent

Export your own key, then paste this. It works with any coding agent.

```text
Use the `trigger-warnings` CLI to build a warned subtitle track for the video I named.
Read `trigger-warnings --help` first, on its own.

1. Find the title with `--ddd-search` and show me the candidates. Never pick for me.
2. With the ID I choose, run `--dry-run` with NO `--category` flag: that reports the
   categories this title actually has. Show me that list and stop.
3. Warn me only about the categories I then name. Never invent scene times, and never
   read them off a plot summary.
4. Generate with a new `--output` and a `--provenance` sidecar. Never delete or
   overwrite my files to clear a "file exists" error.

Drive every run with `--json` except `--help`/`--version`, branch on `ok`, and report
`filesWritten`, the selected and excluded categories, and every note. A warning ending is
not an all-clear, and no matching events does not mean the video is free of my triggers.
```

The [SKILL.md](skills/trigger-warnings/SKILL.md) here carries the same rules in agent-neutral
form; copy that directory into your agent's skills directory.

## Do it yourself

**1. Find the title.** Use your own API key, never someone else's, kept in the environment so
it stays out of shell history. `--ddd-search` only searches, and never picks a title for you.

```sh
export DDD_API_KEY='your-key'
trigger-warnings --ddd-search 'Jaws' --ddd-year 1975   # -> id 10154
```

**2. List the categories that exist.** Run a dry run with no `--category` at all: it validates
the inputs, reports the plan and writes nothing. Guessing instead fails the run, because a
`--category` matching no event is a mistake, not an absence of triggers.

```sh
trigger-warnings --dry-run --subtitles dialogue.srt --ddd-item 10154
```

```text
Fetched 2 timestamped DoesTheDogDie ratings for item 10154 (2 community, 0 Scene Alerts).
DoesTheDogDie returned 149 ratings without timestamps; they were not turned into warnings.
Selected categories: a dog dies (1 event), a pet dies (1 event)
```

**3. Generate the categories you chose**, with a sidecar recording where they came from. Prefer
`.ass`, which gives warnings and dialogue separate styles and positions.

```sh
trigger-warnings --subtitles dialogue.srt --ddd-item 10154 \
  --category 'a dog dies' --output movie.warned.ass --provenance movie.provenance.json
```

Both paths must be new: existing files are never replaced, nothing is published unless every
step succeeds, and your video and original subtitles are never touched. The sidecar records the
source, categories and timing, and never the key.

## Or supply your own timestamps

Write a JSON array and pass `--events` in place of `--ddd-item`. Everything else, the dry run
included, is the same.

```json
[{"start": 125.5, "end": 138, "label": "loud noises", "severity": "moderate"},
 {"start": "00:12:05.000", "label": "flashing lights"}]
```

`start` is required, in seconds or `HH:MM:SS.mmm`; `label` is the category, matched exactly and
case-insensitively; `severity` changes nothing. Omitting `end` warns only in advance, stopping at
the event start, which is not a safe time to resume. `--lead` sets the run-up, default 20.

## Fetch the dialogue track from OpenSubtitles

Every run needs a dialogue `.srt`. Extract one from your video with `--video`, or download one
with your own OpenSubtitles account. Searching and downloading are separate modes that add no
warnings and refuse the generation flags: they only fetch the track `--subtitles` then takes.

**1. Search.** This needs the API key alone, and `--os-search` never picks a file for you.

```sh
export OPENSUBTITLES_API_KEY='your-key'
trigger-warnings --os-search 'Jaws' --os-year 1975   # -> a table of file ids
```

Narrow a series with `--os-season` and `--os-episode`. `--os-language` takes an ISO 639-1 code
and defaults to `en`.

**2. Download the file id you chose.** This one signs in, so it also needs your username and
password. The tool reads both from the environment only: neither has a command line flag, and no
credential file is read.

```sh
export OPENSUBTITLES_USERNAME='your-username'
export OPENSUBTITLES_PASSWORD='your-password'
trigger-warnings --os-file 4610837 --output dialogue.srt
```

A download spends one of a small daily quota, so the tool asks once and never retries. It decodes
what arrives as UTF-8 and parses it as SRT before writing anything, so a rate-limit page cannot
reach your disk as dialogue. The output must be new and must end in `.srt`.

That file is dialogue only. Pass it to `--subtitles` to add the warnings.

## Attribution and API terms

The tool prints `Powered by DoesTheDogDie.com` whenever it uses that source; keep the attribution
on anything you share. The API returns community timestamps, which are incomplete and unverified,
and reports how many carried no timestamp at all. A response with no timestamped ratings is an
error, not a claim that the title is clear. Scene Alerts need the provider's separate written
agreement. Read and comply with the [API terms](https://www.doesthedogdie.com/api/terms).

The tool prints `Subtitles from OpenSubtitles.com` whenever it uses that source. Downloads come
out of your own account's daily quota, and the subtitles themselves are uploaded by that site's
contributors rather than by this project. Read and comply with the
[OpenSubtitles terms](https://www.opensubtitles.com/en/terms).

## More

`trigger-warnings --help` prints the full syntax, including subtitle extraction from a video,
preview rendering, `--offset` calibration and the `--json` result shape. Run the tests with
`python3 -m unittest discover -s tests -v`; `RUN_MEDIA_TESTS=1` adds the real FFmpeg checks.
Contribute only synthetic or appropriately licensed fixtures: no commercial subtitle files,
copied timelines, account credentials or personal trigger profiles. Code, docs and synthetic
examples are [MIT-licensed](LICENSE); that licence covers none of the videos, subtitles or
event data you supply, and no third-party dataset is bundled.
