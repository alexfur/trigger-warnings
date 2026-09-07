# Launch-readiness specification

## Purpose

Make Trigger Warnings ready for a public open-source launch. The launch should
make one promise that a new visitor can understand, try and verify quickly:

> Create a spoiler-free subtitle track that gives advance warnings before
> scenes you choose in a local video.

The product must not claim to detect every scene, judge safety, replace a
person's judgment or provide medical advice. It creates a second subtitle
track from event times the viewer selected or supplied.

This specification is for implementation agents. Work through the milestones
in order, keep changes reviewable and preserve the existing credential-safety
guarantees. Do not publish, tag a release, change repository visibility, edit
GitHub settings, create a Product Hunt listing or send launch messages. Those
are owner actions listed in the final checklist.

## Launch bar

The project is ready for a public launch only when all of these are true:

1. A visitor can see the repository, install a released package and complete a
   no-account example in under five minutes.
2. The README's first screen explains the outcome, the audience and the safety
   limit before it explains data sources, API keys or the wizard.
3. The repository contains genuine visual proof of the output and a clear
   path to test it without a personal video or an account.
4. A coding agent has unambiguous secret boundaries: it can inspect status and
   run normal commands, but the person enters secrets locally.
5. Release, support and launch-day materials exist, are internally consistent
   and identify which steps need the owner.
6. The complete test and build checks pass from a clean checkout.

## Audience and positioning

### Primary audience

People who play local video in VLC or mpv and want a discreet, advance warning
for particular content without being told the detail on screen. They may bring
their own timestamps or choose information from supported services.

Do not lead with the word "wizard", provider names or coding-agent support.
They help with setup, but they are not the benefit a visitor is choosing.

### Working message hierarchy

Use this hierarchy consistently in the README, package metadata, repository
description and launch copy. Exact wording may be tightened during editing,
but retain the claims and limits.

| Placement | Copy |
| --- | --- |
| Repository/package tagline | `Spoiler-free trigger warnings for local video.` |
| One-sentence explanation | `Create a separate subtitle track that shows a generic warning before scenes you choose.` |
| Proof/safety line | `It never changes the video or original subtitles, and it cannot guarantee every warning is present.` |
| Primary action | `Try the no-account example` |
| Secondary action | `Use a movie and optional data sources` |

Avoid unsupported superlatives, comparisons to a specific competitor and
claims that the tool makes a video safe. Use "warning track", not "safe
track". Use "viewer" or "person", not "user", where that reads naturally.

## Scope and deliverables

Implement the following tracked deliverables.

| Area | Deliverable | Completion evidence |
| --- | --- | --- |
| Product proof | A reproducible no-account demo, rendered output and documented command | A clean checkout creates and renders the advertised output without credentials or a network call. |
| README | Outcome-led README with concise installation, demo, visual proof, collapsible reference material and clear agent hand-off | Links, commands and assets are checked locally. |
| Visuals | Authentic GIF or short video plus 2–3 screenshots based on synthetic/bundled material | Every asset can be regenerated and contains no copyrighted film, personal data or secrets. |
| Distribution | Package metadata/release instructions ready for PyPI trusted publishing | `python -m build` and `twine check dist/*` pass; owner steps are documented. |
| Community | Contributing path, support/bug templates and small public roadmap | A visitor can report a problem or choose a bounded first contribution. |
| Launch pack | Product Hunt and community-launch copy, asset checklist, FAQ, maker comment and run-of-show | Copy uses the final positioning and is marked draft until owner approval. |
| Validation | Repeatable release-candidate checklist and recorded test results | All automated and manual checks are recorded before owner launch approval. |

Create a `docs/launch/` directory for launch material. Suggested files are:

```text
docs/launch/launch-checklist.md
docs/launch/product-hunt.md
docs/launch/visuals.md
docs/launch/release.md
docs/launch/tester-script.md
```

Add a short `ROADMAP.md` at repository root if it does not already exist. Keep
it deliberately small: planned, exploring and not planned. Do not promise
dates or a broad data-source roadmap without an owner decision.

## Milestone 0: baseline and decision record

Before changing copy or assets:

1. Read `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, the
   package metadata, existing tests and the `skills/trigger-warnings` skill.
2. Run the current automated test suite and package build. Record commands,
   platform, Python version and result in the launch checklist. Do not turn a
   known failing test into a launch checklist tick.
3. Inspect the actual CLI help and run the bundled no-account example. Treat
   output from the code as authoritative over the README.
4. Add a concise decision record in `docs/launch/launch-checklist.md`:
   target release version, intended release date left as `TBD`, target
   audience, owner-only external actions and open risks.

Use the existing source version until the owner decides the release version.
Do not invent a version tag. If the release requires a version bump, make it a
separate reviewable change after the owner confirms the target version.

## Milestone 1: first-run product proof

### Requirements

1. Make the no-account path the shortest documented route. It must use the
   repository's bundled `examples/dialogue.srt` and `examples/events.json`, or
   equally small tracked fixtures, and must not call a remote service.
2. Provide one exact command that produces `example.warned.ass` from a clean
   checkout. State where to load it in VLC and mpv, but avoid a long
   player-specific tutorial in the README.
3. Add a deterministic render/preview command or script for the visual asset.
   It must work using only synthetic/bundled input. If FFmpeg is required,
   declare it separately from the core no-account generation path.
4. Confirm the generated track is additive: the source subtitle file remains
   untouched and the new output uses a distinct filename.
5. Check failure messages for the no-account path. A missing input, invalid
   event file and absent optional FFmpeg should explain the next action without
   exposing internals or suggesting credentials.

### Acceptance tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/trigger-warnings --subtitles examples/dialogue.srt \
  --events examples/events.json --output /tmp/example.warned.ass
test -s /tmp/example.warned.ass
```

Run the equivalent command through the installed wheel as part of release
validation. Add focused automated coverage only where the present tests do not
already prove this contract.

## Milestone 2: README and documentation

### Information architecture

Restructure the README in this order:

1. Project name, tagline, one-sentence explanation and safety limit.
2. A real visual showing the generic banner during a synthetic example.
3. `Try it in two minutes`: install and one no-account command.
4. `Use it with your video`: concise three-step path, including loading the
   resulting track in a player and checking it at a known timestamp.
5. `Get data from supported sources`: optional, collapsed or lower on the
   page. This is where the setup wizard belongs.
6. `Use it with a coding agent`: visible enough to find, but after first-run
   value. Include a small responsibility table and a copy/paste prompt.
7. Collapsible cookbook/reference sections: own timestamps, OpenSubtitles,
   video stream selection, provenance, credential status/removal and no
   keychain fallback.
8. Safety, contribution and support links.

The existing workflow diagram may remain if it supports the story. It must not
replace visible evidence of the actual playback result.

### Copy rules

- Start with the outcome rather than internal mechanics.
- Give each code block one job and state its expected output immediately after
  it. Do not make a newcomer infer whether a command has written anything.
- Keep setup optional. An account/API key cannot block the first successful
  demo.
- Retain the current direct warning that data can be missing or mismatched.
- Use UK English, short active sentences and no em dashes.
- Every README command must be run or mechanically checked during validation.
- Do not put live API keys, `KEY=value` examples containing realistic tokens,
  private video filenames or a person's credentials in documentation.

### Coding-agent contract

Replace vague language with the following operational model:

| Activity | Person | Agent |
| --- | --- | --- |
| Chooses video, categories and output location | Yes | Can advise |
| Runs `--setup --save` | In their own local terminal only | No |
| Types, reads, exports or stores a credential | Yes, through the hidden local prompt | Never |
| Runs `--setup --json`, searches titles and generates tracks | May | Yes |
| Reviews whether an output is correct for playback | Yes | Can help inspect files, not certify safety |

The copy/paste prompt must say: stop when setup reports a credential missing;
tell the person to use their own terminal; resume only after they confirm setup
is complete; never request a secret in chat. It must not suggest copying an
environment variable from the person to the agent session.

## Milestone 3: visual proof

### Asset plan

Create only assets that show the current product truth. Preferred set:

1. A 6–12 second muted GIF or MP4 of a synthetic video playing with the
   generic `TRIGGER INCOMING` subtitle banner. The scene itself must not depict
   or name a triggering event.
2. A terminal screenshot showing the no-account command and a successful
   output path. Use a fake local path and no credentials.
3. A player screenshot showing the original dialogue plus the separate warning
   banner, or a clearly labelled preview image produced by the tool.

Store source inputs and the regeneration command. `docs/launch/visuals.md`
must identify each asset, its source fixture, intended placement, dimensions,
alt text and regeneration procedure. If browser/player automation is
unavailable, use a truthful tool-produced preview rather than a mock UI.

Never generate an illustrative image that could be mistaken for working UI.
Never use a film clip, a provider logo or a testimonial without permission.
Optimise assets for GitHub rendering and Product Hunt upload, but keep the
source file reasonably small.

### Acceptance criteria

- The README renders its visual assets from relative, tracked paths.
- Alt text describes the visible outcome, not decorative intent.
- A fresh clone can regenerate every image/video using documented commands.
- A reviewer can identify the exact command and fixture behind each asset.

## Milestone 4: public distribution and repository hygiene

### Package and release preparation

1. Confirm PyPI name availability and trusted-publisher configuration as an
   **owner action**. Do not submit a test or production package.
2. Ensure the package description, classifiers, license, URLs and Python
   support agree with the README. Correct factual inconsistencies only.
3. Verify that the release workflow uses least-privilege publishing and can be
   triggered only by the documented tag convention. Document the exact owner
   steps to create a GitHub release/tag and confirm the PyPI result.
4. Build both source and wheel distributions, inspect their contents and run
   `twine check`.
5. Test an installation from the wheel in a new virtual environment, then run
   the no-account command. This is the release-candidate smoke test.

Document the supported post-release installation command, preferably
`pipx install trigger-warnings` for a command-line user, plus a source-install
fallback for contributors. Do not advertise the PyPI command in the README as
available until a real public release exists. Before then, label it clearly as
`After the first release` or retain the source path as the primary command.

### Repository presentation

Prepare, but do not perform, these owner actions:

- make the GitHub repository public;
- set the description to the approved tagline;
- add a restrained set of relevant topics, for example `subtitles`,
  `accessibility`, `video`, `cli` and `python`;
- pin the launch issue/discussion if one is created;
- ensure the issue tracker and security contact are visible and accurate.

Add GitHub issue templates for a bug report and a feature request if absent.
The bug template must ask for version, OS, Python, reproduction command with
secrets removed, expected/actual result and a warning not to attach credentials
or personal video. Keep forms short enough to be used.

## Milestone 5: launch collateral

Create `docs/launch/product-hunt.md` as a draft launch pack. It should contain:

1. Three tagline candidates, each no more than 60 characters, with the chosen
   one clearly identified.
2. A short description, a longer description and a factual feature list.
3. The gallery order and captions, starting with the playback result rather
   than a terminal.
4. A maker's first comment: why the project exists, what it does, the safety
   limit and one concrete feedback question.
5. Short replies for likely questions: privacy/offline use, where timings come
   from, missing warnings, player compatibility, API keys, coding agents and
   why the banner is generic.
6. Draft posts for the project's most relevant communities. Make each useful
   on its own. Do not write engagement bait, fake endorsements or a mass-DM
   plan.

Create `docs/launch/tester-script.md` for five to eight external testers.
Ask them to try the no-account demo, identify what they think the tool does,
load output in their player if available, and report the first confusing step.
Do not ask people to disclose a trigger, watch distressing content or share
private video. Capture findings in a small anonymised table in the checklist.

Create `docs/launch/launch-checklist.md` with the following sections:

- release candidate validation;
- public repository and PyPI owner actions;
- visual-assets approval;
- tester results and required fixes;
- Product Hunt setup (maker profile, gallery, maker comment, first-day replies);
- launch-day run-of-show in local time;
- 24-hour and 7-day follow-up;
- metrics and learning notes.

Product Hunt guidance is practical rather than performative: have the maker
present to answer questions, publish a real demo, post a useful first comment,
and reply promptly. Do not ask for votes as a condition of friendship or use
automation to manufacture engagement.

## Milestone 6: validation and sign-off

Run and record these checks after all edits:

```sh
python -m pytest
python -m build
python -m twine check dist/*
```

Also complete these manual checks:

1. Follow the first-run instructions from a clean checkout without an account,
   a keychain entry or network access.
2. Follow the optional wizard path in a real terminal **without entering live
   credentials**. Confirm that no prompt appears in JSON/non-interactive mode.
3. Check every README anchor, internal documentation link and asset path.
4. Inspect the README on GitHub-style Markdown rendering if available.
5. Check visual assets at normal GitHub and mobile-like widths for legibility.
6. Run the coding-agent prompt against a test agent or reason through it line
   by line. It must stop before any secret-handling step.
7. Review the diff for accidental fixtures containing secrets, personal paths,
   copyrighted video, misleading claims or release version changes.

Mark an item complete only with a command output, link or concise factual
note. An unchecked owner action is a launch blocker, not a deferred nicety.

## Non-goals and constraints

- Do not add analytics, tracking pixels, telemetry or outbound calls merely to
  measure the launch. Use GitHub/PyPI/Product Hunt dashboards and voluntary
  feedback instead.
- Do not broaden the product into content detection, recommendation or safety
  certification for this launch.
- Do not require an account, network access, FFmpeg or a supported keychain for
  the core example.
- Do not change credential storage policy: no secrets in command lines, JSON,
  tracked configuration, screenshots, agent chat or generated provenance.
- Do not alter the underlying warning semantics merely to create a prettier
  demo. If a product bug is found, add a focused issue/test and fix it in a
  separate reviewed change.
- Do not publish externally without explicit owner direction.

## Implementation process

Make small, coherent commits by milestone. Before each commit, inspect the
diff and run the tests appropriate to the files changed. Use the repository's
approved commit and push workflow. In this checkout, invoke the wrappers from
the repository root as `../../infra/git/safe-commit.sh` and
`../../infra/git/safe-push.sh`, with exact file paths.

At each milestone, report:

- files changed and user-visible result;
- commands run and outcomes;
- decisions made within this specification;
- owner-only actions still outstanding;
- any blocker that requires a product decision.

If an item conflicts with current behaviour, preserve user safety and explain
the conflict before changing behaviour. The final hand-off should link the
checklist, identify the suggested launch commit and separate completed work
from owner-only launch actions.
