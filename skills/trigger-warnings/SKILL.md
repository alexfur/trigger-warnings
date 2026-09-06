---
name: trigger-warnings
description: Add advance trigger warnings to local dialogue subtitles using supplied event timestamps or the opt-in official DoesTheDogDie API source. Use when preparing warning subtitles for VLC or checking warning placement and synchronisation. Does not infer scene timestamps.
---

# Trigger warnings

The `trigger-warnings` CLI merges warning banners into a dialogue subtitle track.
It does not detect scenes, so every timestamp comes from the user or from the
official DoesTheDogDie API with the user's own key.

Run `trigger-warnings --help` before building a command, on its own and without
`--json`. From the repository without installing, use `python3 -m
trigger_warnings` instead.

## Drive it with `--json`

Pass `--json` on every run but `--help` and `--version`, which print their own
plain text and exit 0 whatever else is on the command line. Otherwise `--json`
writes one JSON object to stdout, leaves stderr empty, and exits 0 or 2. Parse
that object; never scrape the prose report.

- `ok` is `true` or `false`. On failure, read `error.message`, and treat
  `error.code` as an open set of failure classes rather than a fixed list.
  Branch on `ok`; never on a code you have hardcoded.
- `mode` is `generate`, `dry-run`, `list-streams` or `ddd-search`.
- `filesWritten` lists every path the run created, in order. It stays empty
  until publication has happened, so it is empty on `dry-run`, `list-streams`
  and `ddd-search`. A failure carries no result fields at all and rolls back
  anything already created, so there is nothing to name. Report it to the user.
- `output`, `preview` and `provenance` name the `--output`, `--verify` and
  `--provenance` paths that were asked for, or `null`. They appear on `generate`
  and `dry-run` whether or not the flag was passed, so never read them as
  evidence that a file exists. `filesWritten` is that evidence.
- `notes` and `messages` carry the data-quality caveats. Read every one and
  surface them to the user. Do not summarise them away.

Argument errors come back in the same shape, so one parser covers every failure.

## Establish the inputs

Confirm the dialogue SRT, the event timestamps, the selected categories and the
video edition. Use only timestamps the user supplies or authorises.

Events are a JSON array of `{start, end, label, severity}`; `end` and `severity`
are optional, and times are non-negative seconds or `HH:MM:SS.mmm`. An unknown
end is not a safe position.

Never invent scene times, read them off a plot summary, copy subscriber-only
records or fetch protected timelines. Ask for a missing input that blocks
generation rather than substituting a clean result.

### The official API source

Permitted only with the user's own key and a tier that grants access. Prefer the
`DDD_API_KEY` environment variable; `--ddd-api-key` exposes the key to shell
history and the process list. Never log, echo or write the key anywhere. The
tool keeps it out of the subtitle output, the provenance sidecar and the result
object, and redacts a value attached to an unrecognised or valueless flag, but a
flag that does take a value quotes a rejected one back in its own error. So put
the key on `--ddd-api-key` or in the environment and nowhere else, and if a run
fails, check `error.message` before repeating it to the user.

Find the item ID first, and let the user choose it:

```sh
trigger-warnings --json --ddd-search 'Title' --ddd-year 1957
```

`--ddd-search` runs alone and selects nothing. Present the `candidates` rows
(`id`, `name`, `releaseYear`, `itemType`) and ask which one matches the user's
copy. An empty list means nothing matched the query, not that the title is absent
from the service.

Then swap `--ddd-item ID` in for `--events`. Repeat the `Powered by
DoesTheDogDie.com` attribution in your report whenever this source is used. API
timestamps are community-supplied, incomplete and unverified, and the result
reports how many ratings had no timestamp. A response with no timestamped
ratings is an error, not evidence that a title is free of triggers.

## Plan, then generate

Dry-run first. It validates the inputs, reports the windows it would produce and
writes nothing, and it refuses `--verify` and `--provenance`. Pass the real
`--output` path so it is checked too: the file must not exist, its parent must
exist, and it must not alias an input. The check does not reserve the name, so
still handle a file-exists failure on the generating run.

```sh
trigger-warnings --json --dry-run --subtitles dialogue.srt --events events.json \
  --category 'loud noises' --output movie.warned.ass
```

Then generate to a new filename, with a provenance sidecar so the run is
auditable later. The sidecar records source, categories, timing and result
counts, and holds no API key or subtitle text.

```sh
trigger-warnings --json --subtitles dialogue.srt --events events.json \
  --category 'loud noises' --lead 20 --offset 0 \
  --output movie.warned.ass --provenance movie.provenance.json
```

Prefer ASS, which positions warnings and dialogue separately. Use SRT only when
the user's player requires it, and say that its positioning is player-dependent.
Keep the generic banner text; do not put the category on screen.

Never delete or overwrite the user's files to clear a "file exists" error: choose
a new name. Never shift dialogue to compensate for event timing. When extracting
from a video, run `--list-streams` and respect the user's language, forced and
accessibility preferences rather than discarding SDH.

## Check before hand-off

With a video available, add `--verify preview.png` and inspect the frame. A
failed render blocks any claim of a verified output. Arrange the inspection so it
cannot show the user distressing content unexpectedly: prefer a known harmless
advance-warning frame, or a trusted reviewer.

A PNG does not verify player behaviour. Check real playback too: top placement,
simultaneous dialogue, seeking and synchronisation at several known points. If
playback was not tested, say so plainly.

## Report

Give `filesWritten` from the result, which is the set of paths the run actually
created, then the selected and excluded categories, lead and offset, the checks
performed and the remaining limitations. Quote every note from the result.

Do not describe the output as clinically reliable or complete. State that a
warning ending is not an all-clear, and that no matching events does not
establish that a video is free of the user's triggers. A constant offset cannot
fix drift from a different playback speed or edit; stop and ask for
edition-matched timestamps when that happens.
