# Trigger Warnings

**Spoiler-free trigger warnings for local video.**

Create a separate subtitle track that shows a generic warning before scenes you
choose. It never changes the video or original subtitles, and it cannot
guarantee every warning is present. You can supply timestamps yourself, use
DoesTheDogDie, or opt in to a local video model that proposes candidate times.

**Two free accounts do the work for you.** DoesTheDogDie supplies the
timestamps, OpenSubtitles supplies the dialogue track, and one setup command
stores both. After that it is one command per film. You can also write your own
timestamps and skip both accounts.

[Install](#install) ·
[Set up the two sources](#set-up-the-two-sources) ·
[Make a track for a film](#make-a-warning-track-for-a-film) ·
[Safety limits](#safety-limits) ·
[For coding agents](#use-it-with-a-coding-agent)

<p align="center">
  <img src="assets/demo.gif" width="720"
       alt="A silent demo video plays. A generic TRIGGER INCOMING banner sits at the top of the frame, a line of ordinary dialogue appears at the bottom underneath it, the banner ends, then a second banner arrives ahead of the next event.">
</p>

> **A model scan is a first pass, not a detector or an all-clear.** Model
> candidates cover the sampled chunk rather than an exact frame and can miss
> brief or visually ambiguous events. Check every warning against the copy you
> plan to watch: missing data, a different edition or a playback fault can all
> leave a warning out.

## Install

```sh
git clone https://github.com/alexfur/trigger-warnings.git
cd trigger-warnings
python3 -m venv .venv
.venv/bin/python -m pip install .
```

Python 3.9 or newer is required. There are no runtime dependencies. FFmpeg is
needed only to read a subtitle track out of a video file or to render a preview.

To use the optional local model scanner on Apple Silicon, install its extra:

```sh
.venv/bin/python -m pip install 'trigger-warnings[vision]'
```

<details>
<summary>Install without a virtual environment, or after the first release</summary>

From the checkout, `python3 -m trigger_warnings` runs the tool with no install
at all.

**After the first release** the command-line tool will install from PyPI:

```sh
pipx install trigger-warnings
```

That command does not work yet. No public release exists, so use the source
install above.

</details>

### Check the install, without an account

Thirty seconds, no key and no network. The repository ships two small synthetic
files:

```sh
.venv/bin/trigger-warnings --subtitles examples/dialogue.srt \
  --events examples/events.json --output example.warned.ass
```

The tool reports what it selected and names the file it created.
`example.warned.ass` is new, and neither example file is touched. Open it in a
text editor: the warning cues carry the generic text `TRIGGER INCOMING` and
never name the category, so the file itself does not spoil the scene.

<p align="center">
  <img src="assets/terminal.svg" width="640"
       alt="A terminal card. The command runs trigger-warnings against the bundled example files and the tool reports three dialogue cues kept, two warning windows, one event with no end time, and the file it wrote.">
</p>

## Set up the two sources

Do this once. It takes about five minutes, most of which is registering.

| Source | Gives you | Needs |
| --- | --- | --- |
| [DoesTheDogDie](https://www.doesthedogdie.com/api) | Community timestamps for a film, by category | A free API key |
| [OpenSubtitles](https://www.opensubtitles.com/en/consumers) | The film's dialogue track as an `.srt` | A free account, plus an API key from it |

Sign in to your OpenSubtitles account **before** opening its API consumer page.
Logged-out visitors are redirected to sign-in.

Then run this in your own terminal:

```sh
trigger-warnings --setup
```

It checks Python, FFmpeg and each account, then says what is missing and what
that stops you doing. Answer `Y` at `Set this up now? [Y/n]` to enter a value,
or press Enter to skip it. Secrets are typed into a hidden prompt, proved
against the service, and only then stored in the operating system keychain.
Nothing is ever written to a file.

Go straight to entering values with `trigger-warnings --setup --save`. An
exported environment variable always wins over a stored value.

You do not need OpenSubtitles if your video file already carries a subtitle
track, and you do not need either account if you write your own timestamps.

## Make a warning track for a film

Five commands. This example uses *Jaws* (1975).

### 1. Find the film on DoesTheDogDie

```sh
trigger-warnings --ddd-search 'Jaws' --ddd-year 1975
```

It prints candidate rows with an ID and selects nothing. Pick the one that
matches the copy you are going to watch. An empty list means nothing matched
the query, not that the film is absent from the service.

### 2. Get the dialogue track

Skip this step if your video already carries a subtitle track: pass
`--video jaws.mkv` instead of `--subtitles` in the steps below, and FFmpeg will
read it out.

```sh
trigger-warnings --os-search 'Jaws' --os-year 1975
```

It prints candidates with a file ID and selects nothing. Narrow a series with
`--os-season` and `--os-episode`.

```sh
trigger-warnings --os-file 4610837 --output jaws.srt
```

This writes `jaws.srt`, and dialogue is all it contains: no warnings yet. The
download signs in, so it needs your username and password as well as the key.
It spends part of a small daily allowance, so the tool asks once and never
retries a failure.

### 3. See which categories the film has

```sh
trigger-warnings --dry-run --subtitles jaws.srt --ddd-item 10154 \
  --output jaws.warned.ass
```

This lists the categories with timestamps and the warning windows it would
produce, then stops. It writes nothing.

### Or scan the video locally

The model path is deliberately explicit. Repeat `--model-trigger` for each
trigger you want checked; it samples the video in short chunks, asks a local
vision model a yes/no question for each trigger, and feeds positive chunks into
the same subtitle writer:

```sh
trigger-warnings --video jaws.mkv --model-trigger 'blood' \
  --model-trigger 'a dog is injured' --subtitles jaws.srt \
  --output jaws.model-warnings.ass --provenance jaws.model.provenance.json
```

The default model is `mlx-community/Qwen2.5-VL-7B-Instruct-4bit`. Use
`--model` (and optionally `--model-revision`) to choose another MLX-VLM model.
`--model-fps`, `--model-chunk` and `--model-width` trade speed for coverage.
The command writes no subtitle if the model returns no candidates. Review the
result before watching, and treat every timestamp as a model candidate rather
than a verified fact. `--dry-run` still performs the scan but publishes no
files.

To use the trigger labels already returned for a DoesTheDogDie item, let DDD
provide the checklist while the model provides all timestamps:

```sh
trigger-warnings --video jaws.mkv --ddd-item 10154 \
  --model-from-ddd --subtitles jaws.srt --output jaws.model-warnings.ass
```

This deliberately fetches DDD labels, not its timestamps. Add repeated
`--category` flags to restrict which DDD labels are checked.

### 4. Write the track for the categories you want

```sh
trigger-warnings --subtitles jaws.srt --ddd-item 10154 \
  --category 'a dog dies' --output jaws.warned.ass \
  --provenance jaws.provenance.json
```

Repeat `--category` for several. Omitting it selects every category, which is
rarely what you want. The tool names each file it created. It never overwrites
anything, and a failed run rolls back what it made.

### 5. Load it, then check a known warning

- **VLC**: Subtitle, then Add Subtitle File.
- **mpv**: `mpv jaws.mkv --sub-file=jaws.warned.ass`

Seek to a time you already know about and confirm the banner appears about
twenty seconds ahead of it. Do that once near the start and once near the end,
because a track that drifts is worse than no track. Use `--offset` to correct a
known constant shift.

<p align="center">
  <img src="assets/preview.png" width="640"
       alt="A rendered video frame. The generic TRIGGER INCOMING banner sits at the top and the unchanged dialogue line sits at the bottom, so the warning never covers the dialogue.">
</p>

<p align="center"><em>A frame rendered by the tool's own <code>--verify</code> option, from the synthetic demo video. It shows that the banner and the dialogue can share a frame. It does not prove that any player positions them this way.</em></p>

**About the data.** DoesTheDogDie timestamps are community-supplied and
incomplete, and they may not match your edition. A film with no timestamped
entries is not evidence that it contains nothing. Keep the
`Powered by DoesTheDogDie.com` attribution and read the
[API terms](https://www.doesthedogdie.com/api/terms); Scene Alerts need a
separate written agreement. Keep the `Subtitles from OpenSubtitles.com`
attribution and read the [OpenSubtitles terms](https://www.opensubtitles.com/en/terms).

## Bring your own timestamps

No account needed. Create `events.json`:

```json
[
  {"start": 125.5, "end": 138, "label": "loud noises", "severity": "moderate"},
  {"start": "00:12:05.000", "label": "flashing lights"}
]
```

`start` is required and takes seconds or `HH:MM:SS.mmm`. `end` is optional.
Where it is absent the banner stops at the event start, which is not a safe
time to resume playback.

Then pass `--events` where the steps above pass `--ddd-item`:

```sh
trigger-warnings --subtitles jaws.srt --events events.json \
  --category 'loud noises' --output jaws.warned.ass
```

## Use it with a coding agent

An agent can drive every command. It must never touch a secret.

| Activity | Person | Agent |
| --- | --- | --- |
| Chooses video, categories and output location | Yes | Can advise |
| Runs `--setup --save` | In their own local terminal only | No |
| Types, reads, exports or stores a credential | Yes, through the hidden local prompt | Never |
| Runs `--setup --json`, searches titles and generates tracks | May | Yes |
| Reviews whether an output is correct for playback | Yes | Can help inspect files, not certify safety |

Copy this prompt into any coding agent:

```text
Use the trigger-warnings CLI to build a warned subtitle track for the video I name.
Read `trigger-warnings --help` first, on its own.

1. Run `trigger-warnings --setup --json`. If any credential is reported missing,
   stop there. Tell me to run `trigger-warnings --setup --save` in my own terminal,
   and wait. Do not continue until I confirm that setup is complete, then run
   `trigger-warnings --setup --json` again to check. Never ask me for a secret in
   this chat, never ask me to read one out, and never ask me to export or copy one
   into your session. There is nothing you can do with a credential that I cannot
   do by running setup myself.
2. Ask where the dialogue track comes from: a file I already have, a subtitle
   stream inside my video (`--video`), or OpenSubtitles. For OpenSubtitles, run
   `--os-search`, show me the candidates and never choose one. Download only the
   file id I name, with `--os-file`, and only once: a download spends part of a
   small daily allowance, so do not retry a failure without asking me.
3. Find the title with `--ddd-search` and show me the candidates. Never choose one.
4. With the ID I choose, run `--dry-run` without `--category`, show me the
   categories and stop.
5. Generate only the categories I name, using a new `--output` and a
   `--provenance` sidecar. Never invent a timestamp and never overwrite a file.

Use `--json` for every run except `--help` and `--version`. Branch on `ok`, never on
an error code. Report `filesWritten`, every entry in `notes`, and the selected and
excluded categories, which the result reports as prose in `messages` rather than as
a field of their own. A warning ending is not an all-clear, and no matching events
does not prove a video is free of anything.
```

The [agent skill](skills/trigger-warnings/SKILL.md) carries the same rules in a
reusable form.

## Cookbook

<details>
<summary>Check, retry or remove saved credentials</summary>

| Goal | Command |
| --- | --- |
| Check the current state without a network call | `trigger-warnings --setup --no-verify` |
| Get a machine-readable report for an agent | `trigger-warnings --setup --json` |
| Retry a refused key | `trigger-warnings --setup --save` |
| Remove every credential this tool stored | `trigger-warnings --setup --forget` |

A network problem is reported as unverified, not as a refused key. Try again
when the network is back. `--json` and non-terminal runs never prompt for
anything.

</details>

<details>
<summary>No supported keychain?</summary>

macOS uses the login keychain and Linux uses libsecret. Where neither is
available, keep the values in environment variables for that shell. The tool
never creates a credential file.

</details>

<details>
<summary>Choose the subtitle stream inside a video</summary>

```sh
trigger-warnings --list-streams --video movie.mkv
```

It prints one row per subtitle stream with an absolute index. Pass the one you
want as `--stream INDEX`. Use `--language` to change which stream is picked
automatically. Selecting a stream does not confirm it matches your edition.

</details>

<details>
<summary>Shift every warning by a fixed amount</summary>

Use `--offset` when your copy runs a known constant distance from the
timestamps, for example because of a different intro. It moves the event times
only and never the dialogue. It cannot correct a different edit or
playback-speed drift; get edition-matched timestamps for that.

`--lead` sets how far ahead of the event the banner appears, twenty seconds by
default. `--tail` extends a warning past a known event end.

</details>

<details>
<summary>Render a preview frame</summary>

```sh
trigger-warnings --subtitles dialogue.srt --events events.json \
  --video movie.mkv --output movie.warned.ass --verify preview.png
```

This writes a PNG from inside a warning window. It proves that FFmpeg drew a
frame with this subtitle file, and nothing more. Another player may position
the text differently.

</details>

<details>
<summary>Keep a record of a run</summary>

Add `--provenance movie.provenance.json`. The sidecar records the source, the
categories, the timing settings and the result counts. It never holds a
credential or a copy of the dialogue.

</details>

<details>
<summary>How a run is put together</summary>

<p align="center">
  <img src="assets/workflow.svg" width="760"
       alt="Two inputs, dialogue and event timestamps, are checked in a dry run before a warned subtitle file and optional provenance sidecar are created.">
</p>

</details>

## Safety limits

- The tool does not detect scenes. It formats timestamps that came from you or
  from a source you chose.
- Community timestamps are incomplete and may not match your edition. A title
  with no timestamped data is not evidence that it is free of anything.
- Existing files are never replaced. A failed run rolls back the files it
  created.
- The banner does not name the category, so it does not spoil the scene.
- A warning ending is not an all-clear. Check playback yourself.
- This is not medical advice and it is not a substitute for your own judgment.

## Contribute and get help

- Something broken or confusing: open a
  [bug report](https://github.com/alexfur/trigger-warnings/issues/new/choose).
- A suspected vulnerability: follow [SECURITY.md](SECURITY.md) and do not open
  a public issue.
- Where the project is going: [ROADMAP.md](ROADMAP.md).
- Sending a change: [CONTRIBUTING.md](CONTRIBUTING.md).

Run the tests with:

```sh
python3 -m unittest discover -s tests -v
```

Set `RUN_MEDIA_TESTS=1` to include the FFmpeg checks. Contribute only synthetic
or appropriately licensed fixtures. Never commit a commercial subtitle file, a
copied timeline, a credential or a personal trigger profile.

Code, documentation and the synthetic examples are [MIT-licensed](LICENSE). The
licence does not cover videos, subtitles or event data you supply.
