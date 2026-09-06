# Trigger warnings

Build a subtitle track that shows a generic **TRIGGER INCOMING** banner ahead of
scenes you asked to be warned about, with the original dialogue still in place.
Timestamps come from a JSON file you supply, or from the official DoesTheDogDie
API using your own key. Output is a new `.ass` or `.srt` file for local playback.

Three things it does not do, and cannot be made to do:

- **It does not find scenes.** Every timestamp comes from you or from the API. A
  missing warning can mean missing data, a different edition, or a playback fault.
- **It does not check timing.** Nothing confirms your timestamps describe your
  copy of the video. Play the file and check.
- **It never gives an all-clear.** A warning ending does not mean the next scene
  is safe. No matching events does not mean the video is free of your triggers.

## Setup

Python 3.9 or newer, no third-party packages. FFmpeg is needed only for
`--video`, `--list-streams` and `--verify`.

```sh
git clone https://github.com/alexfur/trigger-warnings.git
cd trigger-warnings
python3 -m venv .venv && .venv/bin/python -m pip install .
.venv/bin/trigger-warnings --subtitles examples/dialogue.srt \
  --events examples/events.json --output example.warned.ass
```

That last command is the bundled harmless example, so a clean run confirms the
install. Activate the environment (`. .venv/bin/activate`) to drop the
`.venv/bin/` prefix, or run `python3 -m trigger_warnings` with no install at all.

## Give the job to a coding agent

Paste this alongside your files. It works with any coding agent.

```text
Use the `trigger-warnings` CLI to build a warned subtitle track from the
dialogue and timestamps I gave you.

- Read `trigger-warnings --help` first, on its own. `--help` and `--version`
  always print plain text and are the only flags `--json` does not wrap. Drive
  every other run with `--json`: one JSON object on stdout, `ok` true or false,
  exit 0 or 2. Never parse the prose report.
- Timestamps must come from me, or from `--ddd-search` then `--ddd-item` with my
  own API key. Never invent scene times, and never read them off a plot summary.
- Plan with `--dry-run` before writing anything. Then generate with a new
  `--output` name and a `--provenance` sidecar. Do not delete or overwrite my
  files to clear a "file exists" error.
- Report `filesWritten` from the result, which is what the run actually
  created, alongside the selected and excluded categories, every note, and what
  you actually checked. A warning ending is not an all-clear, and no matching
  events does not mean the video is free of my triggers.
```

The [SKILL.md](skills/trigger-warnings/SKILL.md) in this repository carries the
same rules in agent-neutral form. Copy that directory into your agent's skills
directory to load them automatically.

## Drive it from a program

On every run but `--help` and `--version`, `--json` writes exactly one JSON
object to standard output and leaves standard error empty. Exit status is 0 on
success, 2 on failure. Argument errors arrive in the same shape, so a caller
never falls back to text parsing.

`--help` and `--version` are the exception. They print their own plain text and
exit 0 whatever else is on the command line, `--json` included. Call `--help` on
its own before the JSON calls rather than trying to parse what it returns.

```sh
trigger-warnings --json --dry-run --subtitles dialogue.srt --events events.json \
  --output movie.warned.ass
```

```json
{"ok": true, "mode": "dry-run", "output": "movie.warned.ass", "preview": null,
 "provenance": null, "filesWritten": [], "selectedEvents": 2, "excludedEvents": 0,
 "dialogueCues": 3, "warningWindows": 2, "notes": ["..."],
 "source": {"kind": "local-events", "eventsPath": "events.json"},
 "messages": [{"level": "info", "message": "..."}]}
```

Drop `--dry-run` from that command and `mode` becomes `generate` and
`filesWritten` becomes `["movie.warned.ass"]`. The three path keys are
unchanged, because they report what was asked for rather than what was written.

A failure replaces the result fields with `{"error": {"code": ..., "message": ...}}`.
Branch on `ok` rather than on the set of codes, which can grow between releases,
and show `error.message` to the user.

| Key | Value |
| --- | --- |
| `ok` | `true` on success, `false` on failure. |
| `mode` | `generate`, `dry-run`, `list-streams` or `ddd-search`. |
| `filesWritten` | Every path this run created, in order. Report it to the user. |
| `output` | The `--output` path, or `null`. On `generate` and `dry-run`. |
| `preview` | The `--verify` PNG path, or `null`. On `generate` and `dry-run`. |
| `provenance` | The `--provenance` sidecar path, or `null`. On `generate` and `dry-run`. |
| `messages` | Every report line, in order, as `{level, message}`. |
| `notes` | Data-quality notes on `generate` and `dry-run`. Surface all of them. |
| `error.code` | Failure class, such as `invalid-input` or `operation-failed`. |

`output`, `preview` and `provenance` name what was asked for, and are present on
`generate` and `dry-run` whether or not the flag was passed, so one shape parses
every mode. `filesWritten` is the fact: it stays empty until publication has
actually happened, so it is empty on every `dry-run`, `list-streams` and
`ddd-search`. A failed run carries no result fields at all and rolls back
anything it had already created, so it leaves nothing to name. Report
`filesWritten`, not the requests.

`list-streams` adds `listStreams` (video duration plus one record per stream);
`ddd-search` adds `candidates`.

## Plan, then record

`--dry-run` validates the inputs, resolves categories and reports the warning
windows it would produce, then stops. It writes nothing, so `--output` is
optional. It refuses `--verify`, `--provenance` and `--list-streams`.

A supplied `--output` path is checked the same way the real run checks it: the
file must not exist, its parent directory must exist, and it must not alias an
input. The check does not reserve the name, so a path that is free during the
plan can still be taken by the time you generate.

`--provenance PATH` writes a JSON sidecar beside the subtitle output, created
under the same rules: a new file only, removed again if any part of the run
fails. It holds `schemaVersion`, `generatedAt`, `source`, `output`, `video`,
`categories`, `timing` and `result`, and contains no API key and no subtitle
text. With the API source, `source` also carries `itemId`, `timestampedRatings`,
`communityRatings`, `sceneAlerts`, `queriedAt`, `attribution` and `notes`.

```sh
trigger-warnings --dry-run --subtitles dialogue.srt --events events.json
trigger-warnings --subtitles dialogue.srt --events events.json \
  --output movie.warned.ass --provenance movie.provenance.json
```

## Use the official DoesTheDogDie API

This optional source converts only timestamped ratings into the same local event
pipeline, and keeps no cache. Use your own API key, never someone else's.

Put the key in the environment so it stays out of shell history and the process
list. `--ddd-api-key KEY` works where that is impractical, but exposes the key to
anyone who can read your history or process table. Either way the key is never
written to the subtitle output, the provenance sidecar or the result object, and
a misspelled flag does not echo it back: an unrecognised argument is named
without its value, and a flag that takes no value rejects `--flag=KEY` without
repeating it. A flag that does take a value quotes a rejected one in its own
error, so `--lead=KEY` would print the key. Keep it on `--ddd-api-key` or in
`DDD_API_KEY`.

```sh
export DDD_API_KEY='your-key'
trigger-warnings --ddd-search 'Old Yeller' --ddd-year 1957
```

```text
DoesTheDogDie candidates:
  id       year   type         title
  10752    1957   Movie        Old Yeller
```

`--ddd-search` only searches and never picks a title for you, so it refuses to
run alongside any generation argument. Under `--json` the rows become
`candidates`, each with `id`, `name`, `releaseYear`, `itemType`, `imdbId` and
`tmdbId`. An empty list means nothing matched the query, not that the title is
absent from the service. Pass the chosen ID in place of `--events`:

```sh
trigger-warnings --subtitles dialogue.srt --ddd-item 10752 \
  --category 'a dog dies' --output movie.warned.ass
```

The tool prints `Powered by DoesTheDogDie.com` whenever it uses this source; keep
that attribution on anything you share. The API returns community timestamps,
which are incomplete and unverified, and it reports how many ratings carried no
timestamp. A response with no timestamped ratings is an error, not a claim that
the title has no triggers. Scene Alerts need the provider's separate written
agreement and the matching entitlement. Read and comply with the
[API terms](https://www.doesthedogdie.com/api/terms), including its attribution,
caching and use restrictions.

## Extract subtitles and render a preview

These need `ffmpeg` and `ffprobe` on PATH. Embedded subtitles must be text-based;
bitmap subtitles need a separate transcription workflow.

```sh
trigger-warnings --video movie.mkv --list-streams
trigger-warnings --video movie.mkv --stream 2 --events events.json \
  --output movie.warned.ass --verify movie.preview.png
```

Inspect the PNG, then test real playback. A successful render proves FFmpeg drew
a frame, not that the timestamps match your video. A failed render exits non-zero
and leaves no output behind.

Automatic stream selection refuses to choose between matching tracks, including a
regular and SDH pair. Run `--list-streams` and pass `--stream INDEX` when that
happens, or when a language tag is missing or uncommon. Passing `--subtitles`
with `--video` uses your own track instead of extracting one.

## Event format

A JSON array. The bundled example data is self-authored and harmless.

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
| `severity` | Optional text metadata. It changes neither selection nor warning text. |

A warning starts `--lead` seconds before the event and ends at its end plus
`--tail`. An unknown end gives an advance-only warning stopping at the event
start, which is not a safe time to resume watching. Overlapping windows merge,
and dialogue timestamps are never shifted.

Invalid records are rejected even when their category is excluded. An unknown
requested category, an empty event file and an unusable warning window are all
errors, never a quiet claim that the video contains none of your triggers.

## Options

`trigger-warnings --help` prints the full syntax.

| Option | Behaviour |
| --- | --- |
| `--subtitles PATH` | Input SRT dialogue. Without it, `--video` supplies the track. |
| `--events PATH` | Local event JSON. Mutually exclusive with `--ddd-item`. |
| `--output PATH` | New `.ass` or `.srt` file. Required unless `--dry-run`. |
| `--category LABEL` | Select a category; repeat for several. Omitted selects all. |
| `--lead SECONDS` | Advance warning duration, default 20. Non-negative. |
| `--tail SECONDS` | Extra time after a known event end, default 0. Non-negative. |
| `--offset SECONDS` | Constant signed shift applied to event times only, default 0. |
| `--dry-run` | Validate inputs and any output path, report the plan, write nothing. |
| `--provenance PATH` | New JSON sidecar recording source and generation details. |
| `--json` | One machine-readable result or error object on standard output. Wraps every run but `--help` and `--version`. |
| `--ddd-search TITLE` | Search official API titles. Runs alone, and selects nothing. |
| `--ddd-year YEAR` | Release year narrowing `--ddd-search`. |
| `--ddd-item ID` | Official API item ID, used in place of `--events`. |
| `--ddd-api-key KEY` | Official API key. Prefer `DDD_API_KEY`. Needs `--ddd-item` or `--ddd-search`. |
| `--video PATH` | Video for extraction, duration checks or preview rendering. |
| `--language CODE` | Language for automatic stream selection, default `eng`. |
| `--stream INDEX` | Explicit absolute subtitle-stream index. |
| `--list-streams` | List `--video` subtitle streams and exit. Generates nothing. |
| `--verify PATH` | Render a check PNG inside a warning window. Requires `--video`. |

Existing files are never replaced, and an input path may not be reused as an
output path, so pick a new name when regenerating. Nothing is published until
every step succeeds, leaving no half-written file to mistake for a checked one.
Your video and your original subtitles are never modified.

## Playback and limits

ASS is the better default, because it gives warnings and dialogue separate
styles and positions. SRT works, but its `{\an8}` positioning and overlapping
cues are player-dependent, so test in the player you actually use. Conversion to
ASS prioritises dialogue content and timing over original SRT formatting
conventions.

A constant `--offset` cannot correct playback-speed differences, inserted scenes,
adverts or a different edit; recheck synchronisation at several points in the
running time. Two-point calibration is not implemented.

There is no browser extension, scene detection or timeline importer, and no
streaming compatibility is claimed. A source reading of
[asbplayer](https://github.com/asbplayer/asbplayer/blob/main/common/subtitle-reader/subtitle-reader.ts)
on 6 September 2026 found that it drops ASS positioning, so it will not render
this layout as written. That is a code reading, not a playback test.

## Development

```sh
python3 -m unittest discover -s tests -v                     # deterministic
RUN_MEDIA_TESTS=1 python3 -m unittest discover -s tests -v   # adds real FFmpeg checks
```

`python3 scripts/make_demo.py --output example.mp4` builds the silent
demonstration video for checking placement by hand.

Contribute only synthetic or appropriately licensed fixtures: no commercial
subtitle files, copied timelines, account credentials or personal trigger
profiles. A report should name the command, the tool and player versions, the
video edition and a minimal harmless reproduction.

## Licence

Code, documentation and synthetic examples are [MIT-licensed](LICENSE). That
licence covers none of the videos, subtitles or event data you supply, and no
third-party dataset is bundled.
