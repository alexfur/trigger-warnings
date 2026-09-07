# Visual assets

Every asset here was produced by the real tool from material tracked in this
repository. None contains a film clip, a personal video, a provider logo, a
testimonial or a credential. None is an illustration of a feature that does not
exist.

Regenerate all of them, or check them against the current code, with:

```sh
python3 scripts/make_visuals.py            # writes into assets/
python3 scripts/make_visuals.py --check    # report only, writes nothing
```

FFmpeg is required for that script. It is not required to use the tool from a
subtitle file, which is what the first-run path does.

## The fixtures behind the pictures

| Fixture | What it is |
| --- | --- |
| `examples/dialogue.srt` | Three synthetic dialogue cues, written for this project. The lines are deliberately about the demonstration itself. |
| `examples/events.json` | Two synthetic events, both labelled `example`. One has no end time, which is why the reports mention an unknown end. |
| `scripts/make_demo.py` | Builds a 60 second silent video: one flat colour, 960x540, 12fps, with `examples/dialogue.srt` muxed in as an English subtitle stream. Nothing is depicted. |

The demo scene shows no event and names none. That is deliberate: an asset for
a tool about advance warnings must not itself put a triggering image in front
of someone browsing a launch page.

## Assets

### `assets/demo.gif`

| | |
| --- | --- |
| What it shows | The synthetic demo video playing. The generic `TRIGGER INCOMING` banner is up from the first frame, a dialogue line appears underneath it, the banner ends, and a second one arrives. |
| Source | `scripts/make_demo.py` plus `examples/events.json`, burned in with libass from the track the tool generated. |
| Placement | README, directly under the one-sentence explanation. Product Hunt gallery, first item. |
| Dimensions | 720 x 405, 12 seconds, 10fps, 120 frames, 20 KB |
| Alt text | `A silent demo video plays. A generic TRIGGER INCOMING banner sits at the top of the frame, a line of ordinary dialogue appears at the bottom underneath it, the banner ends, then a second banner arrives ahead of the next event.` |
| Regenerate | `python3 scripts/make_visuals.py` |

The window is 28s to 40s of the demo. It was moved there from 6s to 18s
because the earlier window opened on four seconds of empty frame, which made a
blank rectangle wherever the GIF appears as a still rather than playing, such
as a gallery thumbnail. The new window carries the banner from frame one and
still shows the whole cycle: banner, banner with dialogue, banner gone, next
banner. Both events lead by the default 20 seconds, so neither event itself is
in the clip.

### `assets/preview.png`

| | |
| --- | --- |
| What it shows | One frame rendered by the tool's own `--verify` option: the warning banner at the top and the original dialogue at the bottom, at the same moment. |
| Source | `trigger-warnings --video <demo> --events examples/events.json --output <track> --verify <png>` |
| Placement | README, in the `Use it with your video` section. Product Hunt gallery, third item. |
| Dimensions | 960 x 540, 28 KB |
| Alt text | `A rendered video frame. The generic TRIGGER INCOMING banner sits at the top and the unchanged dialogue line sits at the bottom, so the warning never covers the dialogue.` |
| Regenerate | `python3 scripts/make_visuals.py` |

This is the tool's own output, not a screenshot of a player. It is labelled
that way wherever it appears, because a preview frame proves that FFmpeg drew
the subtitle file and nothing more. Another player may position the text
differently.

### `assets/terminal.svg`

| | |
| --- | --- |
| What it shows | A terminal card carrying the no-account command and the report the tool printed in response, verbatim. |
| Source | The documented first-run command, captured by `scripts/make_visuals.py` inside a copy of `examples/`, so every path in it is relative. |
| Placement | README, in `Try it in two minutes`. Product Hunt gallery, fourth item. |
| Dimensions | 640 x 366, 3 KB |
| Alt text | `A terminal card. The command runs trigger-warnings against the bundled example files and the tool reports three dialogue cues kept, two warning windows, one event with no end time, and the file it wrote.` |
| Regenerate | `python3 scripts/make_visuals.py` |

SVG rather than a screenshot on purpose. The text stays readable in the diff,
so a reviewer can check that the picture says what the tool said, and it stays
sharp when GitHub scales it. Card width is a legibility decision, not a layout
one: what a phone sees is the font size divided by the card width. The card was
860 wide with 14px text, which renders near 5.8px in a 358px column and cannot
be read. It is now 640 wide with 15px text, which renders near 8.4px.

It is a rendering of captured output, not a photograph of a terminal, and it
carries no personal path, hostname or credential because the capture runs with
relative paths only.
`tests/test_first_run.py` compares the embedded text with what the tool prints
today, so a stale card fails the suite.

### `assets/workflow.svg`

| | |
| --- | --- |
| What it shows | The existing diagram: two inputs, a dry run, then the files that get written. |
| Source | Hand-authored, pre-existing. |
| Placement | README, inside the collapsed reference material. It supports the story but does not stand in for evidence of playback. |
| Dimensions | 880 x 300, 5 KB |
| Alt text | Already carried in the file's `title` and `desc` elements. |
| Regenerate | Edit by hand. |

## Checks before approval

- [ ] Each asset opens and renders on GitHub, in light and dark appearance.
- [ ] The GIF is legible at roughly 400px wide, which is what a phone gives it.
- [ ] Alt text describes the visible outcome, not the decoration.
- [ ] `python3 scripts/make_visuals.py --check` reports no change against a
      clean checkout.
- [ ] No asset shows a real film, a personal path, an account or a key.
- [ ] `[owner]` Approve the set.

## Not made, and why

- **A player screenshot from VLC or mpv.** It would need a real video window
  captured on a real desktop. The `--verify` frame is the same evidence from a
  source a reviewer can reproduce, so that is what the README shows.
- **A mock interface.** The tool has no interface beyond a terminal and a
  subtitle track. Drawing one would misrepresent the product.
- **A comparison against another tool.** Out of scope for this launch.
