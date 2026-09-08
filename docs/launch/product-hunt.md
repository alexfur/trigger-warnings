# Launch pack

**Draft. Nothing here has been published, and none of it should be until the
owner has approved the wording.**

Every claim below is checkable against the tool. Keep it that way when
editing: an overstatement in launch copy for a tool about advance warnings
does more damage than a dull sentence.

## Taglines

Product Hunt allows 60 characters.

| | Candidate | Length |
| --- | --- | --- |
| **Chosen** | `Spoiler-free trigger warnings for local video.` | 46 |
| | `Warn yourself before a scene, without the spoiler.` | 50 |
| | `A subtitle track that warns you before scenes you pick.` | 55 |

The first is chosen because it names the two things a visitor needs: it is
spoiler-free, and it is for video they already have. The second reads well but
leaves out that the file stays local. The third describes the mechanism before
the benefit.

## Short description

> Trigger Warnings builds a second subtitle track for a video on your own
> machine. Before a scene you chose, it shows a generic `TRIGGER INCOMING`
> banner. It never says what the scene is, so it does not spoil it, and it
> never touches your video or your original subtitles.

## Longer description

> Some scenes are worth knowing about a few seconds early. The usual options
> are bad ones: read a spoiler-heavy list first, or take your chances.
>
>
> Trigger Warnings turns timestamps into a separate subtitle track. Load it in
> VLC or mpv alongside the video and a generic banner appears about twenty
> seconds before each one. The banner never names the category, so you get the
> warning without the plot.
>
> Two free accounts do the work. DoesTheDogDie supplies the timestamps and
> OpenSubtitles supplies the dialogue track, so a typical film is five
> commands: find it, fetch the dialogue, see which categories have times, write
> the track, check it in your player. One setup command stores both
> credentials in your operating system keychain. If you would rather not
> register, write the times into a JSON file and skip both.
>
> Everything runs locally. There is no upload and no telemetry, and the two
> services only ever see a film title or an ID.
>
> It cannot detect scenes and it does not tell you a video is safe. Community
> timestamps are incomplete and may not match your edition, so a warning that
> never comes is not evidence of anything. It is a Python command-line tool
> under an MIT licence.

## Feature list

Each of these is something the tool does today.

- Writes a new `.ass` or `.srt` warning track. Never edits the video or the
  original subtitles.
- Shows a generic banner. It never names the category on screen.
- Reads timestamps from the official DoesTheDogDie API with your own free
  key, or from a JSON file you write.
- Reads dialogue from OpenSubtitles with your own free account, from a
  subtitle stream inside the video, or from a file you already have.
- One `--setup` command registers both, verifying each credential before
  storing it.
- Runs entirely locally. The bundled example needs no account at all, and the
  tool makes no outbound call without one.
- Never overwrites a file, and rolls back anything a failed run created.
- `--dry-run` shows the categories and warning windows and writes nothing.
- `--lead`, `--tail` and `--offset` adjust timing. The offset moves events
  only, never the dialogue.
- `--verify` renders a frame so you can see the result before playing.
- `--provenance` writes a sidecar recording source, categories and timings. It
  holds no credential and no dialogue.
- Credentials come from the environment first, and from the operating system
  keychain only if you opt in. There is no credential file, and no
  command-line flag for the OpenSubtitles password.
- `--json` on every mode, so a coding agent can drive it without scraping
  prose.
- Python 3.9 to 3.13, Linux and macOS, no runtime dependencies.

## Gallery

Playback result first. The terminal comes after it, because the terminal is
how you make the thing, not what the thing is.

| Order | Asset | Caption |
| --- | --- | --- |
| 1 | `assets/demo.gif` | The banner runs ahead of the scene, over ordinary dialogue, then clears and returns for the next one. It never says what is coming. |
| 2 | `assets/preview.png` | Warning at the top, your dialogue at the bottom. The original subtitles are untouched. |
| 3 | `assets/terminal.svg` | One command, no account. The tool says what it selected and what it wrote. |
| 4 | `assets/workflow.svg` | Your dialogue and your timestamps in, a dry run, then new files. Nothing is overwritten. |

Asset details, dimensions and regeneration commands are in
[visuals.md](visuals.md).

## Maker's first comment

> Hi. I built this because I kept hitting the same problem from both ends.
> Content warning lists spoil the film, and finding out mid-scene is too late.
> What I actually wanted was a few seconds of notice and no detail at all.
>
> So this makes a second subtitle track. You give it timestamps, it gives you
> a generic `TRIGGER INCOMING` banner about twenty seconds before each one,
> and you load it next to the video in VLC or mpv. Your video and your
> original subtitles are never touched. Nothing is uploaded. The first run
> uses two example files in the repository and needs no account.
>
> The limit matters more than the feature list, so I will state it plainly: it
> cannot detect anything. Every timestamp comes from you or from a source you
> chose, and community data is incomplete and often does not match your
> edition. A warning that never appears proves nothing. Check playback
> yourself, at the start and again near the end.
>
> The question I would most like answered: if you tried it, did you understand
> what it does from the first screen of the README alone, before scrolling? If
> not, tell me which sentence lost you and I will fix it today.

## Replies to likely questions

Keep replies short. Answer the question and stop.

**Does it upload my video or send anything anywhere?**
> No. It reads local files and writes local files. The core path makes no
> network call at all. The two optional sources only reach out when you have
> supplied your own key for them, and even then it sends a title or an ID,
> never your video.

**Where do the timings come from?**
> From you, or from DoesTheDogDie with your own free API key. The tool never
> guesses a timestamp and never looks at the picture.

**What if a warning is missing?**
> Then a warning is missing, and that is a real limit rather than an edge
> case. Community data is incomplete, and a different cut or release will not
> line up. The tool says so, repeatedly, and it never reports that a video is
> clear. Please open an issue if the tool itself dropped an event you gave it.

**Which players work?**
> Anything that loads an external subtitle track. Tested with VLC and mpv. ASS
> is the default because it positions the warning and the dialogue separately.
> SRT is available where a player needs it, and its positioning is up to the
> player.

**Do I need an API key?**
> Not for the core tool. You need one only for the optional sources: a
> DoesTheDogDie key for community timestamps, and an OpenSubtitles account to
> fetch a dialogue track. Both are free, and both stay on your machine.

**Can I use it with a coding agent?**
> Yes, and the README has a prompt for it. The agent runs the commands. You
> type any credential yourself, in your own terminal, into a hidden prompt.
> The agent stops when setup reports something missing, and there is nothing
> it could do with a secret that you cannot do by running setup.

**Why is the banner generic? Why not say what is coming?**
> Because naming it is the spoiler. You already know which categories you
> asked for, so the banner only has to tell you that one of them is close. If
> you want the detail, the provenance sidecar records exactly which categories
> a run used.

**Is this medical or safety software?**
> No. It is a subtitle formatter. It is not advice, it is not a safety
> certification, and it is not a substitute for your own judgment.

## Community posts

Each of these has to stand on its own. Do not post the same text everywhere,
do not ask anyone to upvote, and do not message people who have not asked to
hear from you.

### Hacker News (Show HN)

> **Show HN: Trigger Warnings, a subtitle track that warns you before scenes
> you pick**
>
> This builds a second subtitle track for a local video. You give it
> timestamps, it emits a generic `TRIGGER INCOMING` banner twenty seconds
> before each one, and you load it in VLC or mpv next to the film. The banner
> never names the category, which is the whole point: warning lists spoil the
> film, and finding out during the scene is too late.
>
> It cannot detect scenes and it never says a video is clear. Timestamps come
> from a JSON file you write, or from DoesTheDogDie with your own key.
> Everything runs locally, there is no account for the core path, and the
> repository ships two example files so the first run needs no key.
>
> Python, MIT, no runtime dependencies. The bit I found hardest was the
> credential boundary: a coding agent can drive every command, but it must
> never see a secret, so setup only ever prompts in a real terminal and the
> tool has no flag for the OpenSubtitles password.

### r/Python

> **A CLI that merges advance trigger warnings into a subtitle track**
>
> I wanted a few seconds of notice before certain scenes without reading a
> spoiler-heavy list first. This takes timestamps and writes a separate `.ass`
> track with a generic banner ahead of each one, which you load alongside the
> video.
>
> Things that might be of interest here rather than the feature list: no
> runtime dependencies on 3.9 to 3.13; every output is created with `O_EXCL`
> so the no-clobber check cannot lose a race; a failed run rolls back only the
> files it created; and the whole CLI has a `--json` mode so it can be driven
> by a script or an agent without parsing prose.
>
> MIT. Happy to hear where the argument surface is wrong.

### r/mpv and r/VLC

> **Tool: generate a second subtitle track that warns you before scenes you
> choose**
>
> Writes an `.ass` file you load with `--sub-file` in mpv, or Subtitle then
> Add Subtitle File in VLC. It puts a generic banner at the top of the frame
> about twenty seconds before each timestamp you gave it, and leaves the
> dialogue where it is. It can pull the dialogue track straight out of the
> container, and `--list-streams` shows what is in there first.
>
> It does not touch the video and it does not detect anything: the timestamps
> come from you. Interested in whether the default positioning survives your
> setup, particularly with SDH tracks.

### Where not to post

Do not post to mental-health or survivor communities. Most forbid promotion,
several would find a launch post intrusive, and a tool that cannot guarantee a
warning has no business being advertised to people who would be hurt if it
missed one. If someone from such a community finds it and asks a question,
answer it plainly.

Do not create accounts to comment on your own launch, do not ask friends for
votes as a favour, and do not automate any part of this.
