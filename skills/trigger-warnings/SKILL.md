---
name: trigger-warnings
description: Add advance trigger warnings to local dialogue subtitles using supplied event timestamps or the opt-in official DoesTheDogDie API source. Use when preparing warning subtitles for VLC or checking warning placement and synchronisation. Does not infer scene timestamps.
---

# Trigger warnings

## Establish the inputs

Use the installed `trigger-warnings` command, or `python3 -m trigger_warnings`
from the repository. Read `--help` before constructing the command.

Confirm the dialogue SRT, event JSON, selected categories and video edition.
Use only timestamps the user supplies or authorises from a permitted source.
The official DoesTheDogDie API is permitted only when the user supplies their
own API key and the requested tier grants access. Do not fetch protected
timelines, copy subscriber-only records, invent scene times or treat a film
summary as timing data.

Events are a JSON array of `{start, end, label, severity}` records. `end` and
`severity` are optional. Times are non-negative seconds or `HH:MM:SS.mmm`.
An unknown end is not a safe position. Ask for missing inputs that prevent
generation; do not substitute a clean result.

## Generate a separate track

Prefer ASS for separate top warnings and bottom dialogue in local playback.
Use SRT only when required by the user's player, with a positioning caveat.
Keep the generic warning text; do not reveal descriptive categories on screen.

Run, substituting the user's paths and categories:

```sh
trigger-warnings --subtitles dialogue.srt --events events.json \
  --category 'loud noises' --lead 20 --offset 0 --output movie.warned.ass
```

For the official API source, prefer an environment variable over putting a key
in the command line. Use `--ddd-item ID` in place of `--events`:

```sh
DDD_API_KEY='user-supplied-key' trigger-warnings --subtitles dialogue.srt \
  --ddd-item 10752 --output movie.warned.ass
```

Read the attribution and data-quality notes in the terminal report. Community
timestamps are incomplete. An API response without timestamped ratings is not
evidence that a title is free of triggers.

Use a new output filename. Never delete or replace the user's original subtitles
to resolve a file-exists error. Do not shift dialogue to compensate for event
timing. If extracting from video, list streams and respect the user's language,
forced-subtitle and accessibility preferences rather than discarding SDH.

Read the complete terminal report. Surface excluded categories, truncated lead
times, unknown ends and any failure. No matching events does not establish that
a video is free of the user's triggers.

## Check before hand-off

If a video is available, use `--video` and `--verify preview.png`, then inspect the
rendered frame. Verification failure blocks claiming a verified output. Arrange
inspection so it does not unexpectedly show distressing content to the user;
prefer a known harmless advance-warning frame or a trusted reviewer.

Check actual player playback as well: top placement, simultaneous dialogue,
seeking and synchronisation at several known points. A rendered PNG alone does
not verify VLC or browser behaviour. If playback was not tested, say so.

Report the output path, selected categories, lead/offset, checks performed and
remaining limitations. Explain that warning disappearance is not an all-clear.
Do not describe the output as clinically reliable or complete. Constant offsets
cannot fix drift from different playback speeds or edits; stop and request
edition-matched timestamps when that occurs.
