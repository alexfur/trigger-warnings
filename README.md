# Trigger warnings

Build a local subtitle track that shows a generic **TRIGGER INCOMING** banner
before scenes you choose. Your original dialogue stays in place.

[Get started](#make-your-first-warning) · [Wizard cookbook](#wizard-cookbook) · [Common recipes](#common-recipes) · [Safety limits](#safety-limits) · [For agents](#use-it-with-a-coding-agent)

<p align="center">
  <img src="assets/workflow.svg" alt="Two inputs, dialogue and event timestamps, are checked in a dry run before a warned subtitle file and optional provenance sidecar are created." width="760">
</p>

> **This tool does not find scenes or give an all-clear.** Check every warning
> against the copy you plan to watch. Missing data, a different edition or a
> playback fault can all leave a warning out.

## Make your first warning

You will create a new subtitle file and load it alongside your video in VLC or
mpv. Nothing changes your video or original subtitles.

### 1. Install the command

```sh
git clone https://github.com/alexfur/trigger-warnings.git
cd trigger-warnings
python3 -m venv .venv
.venv/bin/python -m pip install .
source .venv/bin/activate
```

### 2. Run the setup wizard

```sh
trigger-warnings --setup
```

The wizard checks Python, FFmpeg and your optional accounts. If it can help,
answer `Y` to enter credentials in hidden terminal prompts. It proves API keys
before saving them in your operating system keychain.

### 3. Make a warning track

This example uses the subtitle track already inside `movie.mkv` and the
DoesTheDogDie entry for *Jaws* (1975).

```sh
# Find the title. The command only lists candidates.
trigger-warnings --ddd-search 'Jaws' --ddd-year 1975   # item 10154

# Inspect available categories. This writes nothing.
trigger-warnings --dry-run --video movie.mkv --ddd-item 10154

# Write a new warning track for categories you chose.
trigger-warnings --video movie.mkv --ddd-item 10154 \
  --category 'a dog dies' --output movie.warned.ass
```

Load `movie.warned.ass` as an additional subtitle track, then play a known
warning and check that it appears at the expected time.

<details>
<summary>Install or verify without activating a virtual environment</summary>

Prefix commands with `.venv/bin/`, or run `python3 -m trigger_warnings` from the
checkout. This harmless smoke test confirms the install and writes a new file:

```sh
trigger-warnings --subtitles examples/dialogue.srt \
  --events examples/events.json --output example.warned.ass
```

Python 3.9 or newer is required. FFmpeg is required only for `--video`,
`--list-streams` and `--verify`.

</details>

## Wizard cookbook

The wizard keeps credentials out of chat, command lines and files. API keys are
hidden as you type, checked against their service, then stored only if valid.
An exported environment variable always overrides a stored value.

<details open>
<summary>Set up a personal machine</summary>

Run this in a real terminal:

```sh
trigger-warnings --setup
```

If a credential is missing, choose `Y` at `Set this up now? [Y/n]`. The wizard
shows what each skipped value prevents, links to the relevant signup page and
guides you through three steps. Press Enter to skip a value.

Use this command to go straight to the wizard:

```sh
trigger-warnings --setup --save
```

For OpenSubtitles, create or sign in to an account before opening the
[API consumer page](https://www.opensubtitles.com/en/consumers). Logged-out
visitors are redirected to sign-in.

</details>

<details>
<summary>Use a coding agent safely</summary>

Let the agent inspect the machine without prompting:

```sh
trigger-warnings --setup --json
```

If the report names a missing credential, run `trigger-warnings --setup --save`
yourself and type it into the local hidden prompt. Do not paste it into a chat,
issue, command or file. Then let the agent run `--setup --json` again.

</details>

<details>
<summary>Work without an account or API key</summary>

The tool still works with a dialogue `.srt` and an event file you write. Use
`--subtitles` and `--events`; see [Use your own timestamps](#use-your-own-timestamps).
No account, keychain or network call is required.

</details>

<details>
<summary>Check, retry or remove saved credentials</summary>

| Goal | Command |
| --- | --- |
| Check the current state without a network call | `trigger-warnings --setup --no-verify` |
| Get machine-readable checks for an agent | `trigger-warnings --setup --json` |
| Retry a refused key | `trigger-warnings --setup --save` |
| Remove this tool's saved credentials | `trigger-warnings --setup --forget` |

A network problem is reported as unverified, not as a refused key. Try again
when the network is available. `--json` and non-terminal runs never prompt.

</details>

<details>
<summary>No supported keychain?</summary>

macOS uses the login keychain and Linux uses libsecret. Where neither is
available, keep credentials in environment variables for that shell. The tool
never creates a credential file.

</details>

## Common recipes

<details open>
<summary>Use your own timestamps</summary>

Create `events.json`:

```json
[
  {"start": 125.5, "end": 138, "label": "loud noises", "severity": "moderate"},
  {"start": "00:12:05.000", "label": "flashing lights"}
]
```

Then create the warning track:

```sh
trigger-warnings --subtitles dialogue.srt --events events.json \
  --category 'loud noises' --output movie.warned.ass
```

`start` is required and accepts seconds or `HH:MM:SS.mmm`. `end` is optional.
When it is absent, the banner stops at the event start. That is not a safe time
to resume playback.

</details>

<details>
<summary>Fetch a dialogue track from OpenSubtitles</summary>

Search first and choose the matching file ID yourself:

```sh
trigger-warnings --os-search 'Jaws' --os-year 1975
```

Then download it to a new `.srt` file:

```sh
trigger-warnings --os-file 4610837 --output dialogue.srt
```

Searching needs the API key. Downloading also needs the OpenSubtitles username
and password, which have no command-line flags. A download spends account quota,
so the tool asks once and does not retry it automatically.

</details>

<details>
<summary>Preview and calibrate a video</summary>

Use `--list-streams` before extracting a subtitle track when a video has more
than one. Add `--verify preview.png` to render one warning frame. Use `--offset`
only to shift event timestamps by a known fixed amount. It cannot correct a
different edit or playback-speed drift.

</details>

<details>
<summary>Keep a record of a run</summary>

Add `--provenance movie.provenance.json` when generating a track. The sidecar
records source, categories and timings. It never contains a credential or a copy
of the dialogue.

</details>

## Use it with a coding agent

Copy this prompt into any coding agent:

<details>
<summary>Agent instructions</summary>

```text
Use the trigger-warnings CLI to build a warned subtitle track for the video I name.
Read `trigger-warnings --help` first, on its own.

1. Run `trigger-warnings --setup --json`. If it reports a missing credential, tell
   me to run `trigger-warnings --setup --save` in my own terminal. Never ask me to
   paste a credential into chat, put it in a command line argument or write it to a file.
2. Find the title with `--ddd-search` and show me the candidates. Never choose one.
3. With the ID I choose, run `--dry-run` without `--category`, show the categories and stop.
4. Generate only the categories I name, using a new `--output` and a `--provenance` sidecar.
   Never invent timestamps or overwrite a file.

Use `--json` for every run except `--help` and `--version`. Branch on `ok`, report
`filesWritten`, selected and excluded categories, and every note. A warning ending
is not an all-clear, and no matching events does not prove a video is trigger-free.
```

</details>

The [agent skill](skills/trigger-warnings/SKILL.md) carries the same operating
rules in a reusable form.

## Safety limits

- The tool does not detect scenes. It only formats timestamps from you or the
  official DoesTheDogDie API.
- Community timestamps are incomplete and may not match your edition. A title
  with no timestamped data is not evidence that it is free of triggers.
- Existing files are never replaced. A failed run rolls back files it created.
- The banner does not name the category, so it does not spoil the scene.
- A warning ending is not an all-clear. Check playback yourself.

When the DoesTheDogDie source is used, keep the `Powered by DoesTheDogDie.com`
attribution. Read its [API terms](https://www.doesthedogdie.com/api/terms).
Scene Alerts require a separate written agreement. When OpenSubtitles is used,
keep its attribution and read the [OpenSubtitles terms](https://www.opensubtitles.com/en/terms).

## Develop

Run the test suite with:

```sh
python3 -m unittest discover -s tests -v
```

Set `RUN_MEDIA_TESTS=1` to include the FFmpeg checks. Contribute only synthetic
or appropriately licensed fixtures. Do not commit commercial subtitle files,
copied timelines, credentials or personal trigger profiles. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

Code, documentation and synthetic examples are [MIT-licensed](LICENSE). The
licence does not cover videos, subtitles or event data you provide.
