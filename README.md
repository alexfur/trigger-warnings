# Trigger warnings

Add advance warnings to a subtitle track while keeping its dialogue. Supply an
SRT file plus local event timestamps, or explicitly request timestamped ratings
from the official DoesTheDogDie API. The tool produces an ASS or SRT file for
local playback, with a generic **TRIGGER INCOMING** message above the dialogue.

This is an experimental developer tool, not a source of trigger information.
It does not detect scenes or verify that your timestamps describe the video.
Missing warnings can mean missing data, a different video edition, or a playback
problem. An ended warning does not mean the following scene is safe.

## Try the harmless example

Requirements: Python 3.9 or newer. Subtitle-only generation needs no third-party
Python packages, video file, or FFmpeg installation.

1. From this repository, generate a subtitle file:

   ```sh
   python3 -m trigger_warnings \
     --subtitles examples/dialogue.srt \
     --events examples/events.json \
     --category example \
     --output example.warned.ass
   ```

2. Check the terminal report. It lists selected and excluded categories and warns
   that timing has not been checked against a video.
3. If FFmpeg is installed, create a matching silent demonstration video:

   ```sh
   python3 scripts/make_demo.py --output example.mp4
   ```

4. Open `example.mp4` in VLC, choose **Subtitle > Add Subtitle File**, and load
   `example.warned.ass`. At 12 seconds and 30 seconds, check that the warning and
   dialogue are both visible. These timestamps describe this synthetic video,
   not any film. Do not use them as real warnings.

For an installed command, run `python3 -m pip install .` in a virtual environment.
Then replace `python3 -m trigger_warnings` with `trigger-warnings`.

## Add warnings to your subtitles

1. Obtain an SRT dialogue track and event timestamps you have permission to use.
   Match the title, episode and video edition. Keep that provenance with your
   local source files; the tool cannot check it.
2. Create an event file using the [event format](#event-format).
3. Generate a separate output file:

   ```sh
   trigger-warnings --subtitles dialogue.srt --events events.json \
     --category 'loud noises' --lead 20 --offset 0 --output movie.warned.ass
   ```

4. Load the output in your player. Check a known warning's timing, simultaneous
   dialogue, seeking and fullscreen placement before relying on the track.

Existing files are never replaced. Pick a new output name when regenerating.
The tool does not modify your video or original subtitle file.

### Use the official DoesTheDogDie API

This optional source requests ratings for one official API item and converts only
timestamped ratings into the same local event pipeline. It makes no persistent
cache. Use your own API key, never someone else's.

Set the key in the environment so it does not appear in shell history or process
arguments, then pass the item ID from the API's title lookup:

```sh
export DDD_API_KEY='your-key'
trigger-warnings --subtitles dialogue.srt --ddd-item 10752 \
  --category 'a dog dies' --output movie.warned.ass
```

`--ddd-api-key KEY` is available when an environment variable is impractical,
but can expose the key to shell history or process inspection. It is never
written to output, logs or cache files.

The tool prints `Powered by DoesTheDogDie.com` when it uses this source. The API
may return community timestamps, which are incomplete and unverified. A result
with no timestamped ratings is an error, not a claim that the title has no
triggers. Scene Alerts require the provider's separate written agreement and
the appropriate API entitlement. Read and comply with the [API terms](https://www.doesthedogdie.com/api/terms), including its attribution, caching and use restrictions.

### Extract subtitles and render a preview

These operations require `ffmpeg` and `ffprobe` on PATH. Embedded subtitles must
be text-based; bitmap subtitles need a separate transcription workflow.

1. List a video's subtitle streams:

   ```sh
   trigger-warnings --video movie.mkv --list-streams
   ```

2. Select a stream by its absolute index from that listing:

   ```sh
   trigger-warnings --video movie.mkv --stream 2 --events events.json \
     --output movie.warned.ass --verify movie.preview.png
   ```

3. Inspect the PNG. Then test actual playback in VLC. A successful render proves
   that FFmpeg produced a frame, not that timestamps are correct or another player
   will position the text identically.

`--subtitles dialogue.srt --video movie.mkv` uses your supplied track instead of
extracting one. Verification renders within a warning even when no dialogue is
present. A failed render exits non-zero instead of silently skipping the check.

## Event format

The input is a JSON array. The example data is self-authored and harmless.

```json
[
  {"start": 125.5, "end": 138, "label": "loud noises", "severity": "moderate"},
  {"start": "00:12:05.000", "label": "flashing lights"}
]
```

| Field | Meaning |
| --- | --- |
| `start` | Required non-negative seconds, or `HH:MM:SS.mmm` / `HH:MM:SS,mmm`. |
| `end` | Optional end in the same format, no earlier than `start`. Null means unknown. |
| `label` | Required non-empty category. Selection is exact and case-insensitive. |
| `severity` | Optional text metadata. It does not change selection or warning text. |

The default warning starts 20 seconds before the supplied start and ends at the
supplied end. An unknown end creates an advance-only warning ending at the event's
start. Neither behaviour establishes a safe time to resume watching.

Overlapping or touching warning windows merge. Dialogue timestamps are not
shifted. Invalid records are rejected even if their categories are excluded.
Unknown requested categories, empty event files and unusable warning windows
produce errors, not a claim that the video contains none of your triggers.

## Options

Run `trigger-warnings --help` for the complete command syntax.

| Option | Behaviour |
| --- | --- |
| `--subtitles PATH` | Input SRT dialogue. Without it, `--video` supplies embedded subtitles. |
| `--events PATH` | Local event JSON. Required unless `--ddd-item` is used. |
| `--ddd-item ID` | Official DoesTheDogDie API item ID. Mutually exclusive with `--events`. |
| `--ddd-api-key KEY` | Official API key. Prefer the `DDD_API_KEY` environment variable. |
| `--output PATH` | New `.ass` or `.srt` file. Required for generation. |
| `--category LABEL` | Select a category; repeat for several. Omitted selects all. |
| `--lead SECONDS` | Advance warning duration, default 20. Non-negative. |
| `--tail SECONDS` | Extra time after the event end, default 0. Non-negative. |
| `--offset SECONDS` | Constant signed shift applied only to event times, default 0. |
| `--video PATH` | Video for subtitle extraction, duration checks or preview rendering. |
| `--language CODE` | Language for automatic stream selection, default `eng`. Common two- and three-letter ISO tags are equivalent. |
| `--stream INDEX` | Explicit absolute subtitle-stream index. |
| `--list-streams` | List subtitle streams from `--video`, without generating output. |
| `--verify PATH` | Render a new PNG within a warning; requires `--video`. |

## Playback and limitations

ASS is the preferred local output because it has separate styles and positions
for warnings and dialogue. SRT is also available, but its `{\an8}` positioning
convention and overlapping cues are player-dependent. Test both together in your
actual player. Converting to ASS may not preserve every original SRT formatting
convention; dialogue content and timing are the priority.

A constant offset does not correct playback-speed differences, inserted scenes,
adverts, intro skipping or different edits. Recheck synchronisation at several
points. Two-point calibration is not implemented.

There is no browser extension, automatic scene detection or unofficial timeline
importer. Streaming compatibility is not claimed.

Automatic stream selection refuses a choice between matching tracks, including
regular and SDH subtitles. Run `--list-streams` and pass `--stream INDEX` when
that happens, when a language tag is missing, or when the video uses an uncommon
language code.

The asbplayer source inspected on 6 September 2026 reads SRT as text and reduces
ASS cues to text fragments, without carrying their positioning information into
its subtitle model. That suggests literal positioning tags in SRT and lost
top/bottom placement in ASS. It is not a drop-in renderer for this layout.
This is a [source-level finding](https://github.com/asbplayer/asbplayer/blob/main/common/subtitle-reader/subtitle-reader.ts),
not a browser playback test.

## Optional agent skill

The agent-neutral [SKILL.md](skills/trigger-warnings/SKILL.md) guides an assistant
through input selection, generation and playback checks. Install the Python tool
first, then copy that skill directory into your agent's skills directory. The
skill accepts local event data or the opt-in official API source; it does not
infer missing timestamps.

## Related work

[Jumpskip](https://github.com/alyssaxuu/jumpskip) uses timed jump-scare warnings.
[CleanStream](https://github.com/ameen-roayan/stremio-cleanstream) supplies
category-based warnings through Stremio.
[asbplayer](https://github.com/asbplayer/asbplayer) loads external subtitles into
browser video and is a possible delivery option, not a verified integration here.
This project focuses on producing a local file that contains warnings and dialogue.

## Development

Run the deterministic tests without installing dependencies:

```sh
python3 -m unittest discover -s tests -v
```

Run real extraction and preview checks when FFmpeg is installed:

```sh
RUN_MEDIA_TESTS=1 python3 -m unittest discover -s tests -v
```

The media integration tests use a generated silent video. They check for visible
text separately at the top and bottom, including a warning without dialogue.

Only contribute synthetic or appropriately licensed fixtures. Do not submit
commercial subtitle files, copied timelines, account credentials or personal
trigger profiles. Reports should identify the command, tool versions, player,
video edition and a minimal harmless reproduction.

## Licence

Code, documentation and synthetic examples are [MIT-licensed](LICENSE).
This licence does not grant rights to videos, subtitles or event data supplied
by users. No third-party dataset is bundled.
