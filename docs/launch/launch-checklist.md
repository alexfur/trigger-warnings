# Launch checklist

Working record for the public launch of Trigger Warnings. Every tick needs a
command output, a link or a short factual note. An unchecked owner action is a
launch blocker, not a deferred nicety.

Status key: `[x]` done and evidenced, `[ ]` outstanding, `[owner]` needs the
repository owner and cannot be done by an implementation agent.

## Decision record

| Decision | Value | Note |
| --- | --- | --- |
| Target release version | `0.3.0` (unchanged) | The source version stays as it is. A bump is a separate reviewable change once the owner names the target. |
| Intended release date | `TBD` | Owner decision. |
| Baseline commit | `898bcd6` | `docs: add launch readiness specification`. |
| Primary audience | People who play local video in VLC or mpv and want a discreet advance warning for particular content. | They may bring their own timestamps or draw on a supported service. |
| Promise | Create a spoiler-free subtitle track that gives advance warnings before scenes you choose in a local video. | Not detection, not an all-clear, not medical advice. |
| Tagline | `Spoiler-free trigger warnings for local video.` | Used in the README, package description and repository description. |
| First-run path | Source install plus the bundled `examples/` fixtures. | No account, no network, no FFmpeg. |
| PyPI install command | Documented but labelled `After the first release`. | No public release exists yet, so the README leads with the source install. |
| Visual assets | Generated from the synthetic demo video and the bundled fixtures only. | No film clip, no personal media, no mock UI. |

### Owner-only external actions

None of these may be performed by an implementation agent.

- [ ] `[owner]` Confirm the PyPI project name `trigger-warnings` is available or already held.
- [ ] `[owner]` Configure PyPI Trusted Publishing for this repository and the `pypi` environment.
- [ ] `[owner]` Make the GitHub repository public.
- [ ] `[owner]` Set the repository description to the approved tagline.
- [ ] `[owner]` Add repository topics.
- [ ] `[owner]` Create the version tag and GitHub release that triggers publication.
- [ ] `[owner]` Confirm the published PyPI page and install the released package.
- [ ] `[owner]` Create the Product Hunt listing and post the maker comment.
- [ ] `[owner]` Send the community launch posts.
- [ ] `[owner]` Recruit the external testers and collect their reports.

### Open risks

| Risk | Why it matters | Mitigation in this launch |
| --- | --- | --- |
| A visitor reads the tool as content detection. | It cannot detect scenes, and a false sense of safety is the worst possible failure. | The safety limit sits on the first screen, in the package description and in every launch draft. |
| A visitor cannot try it without an account. | An API key in the way of the first run kills the demo. | The no-account path is the shortest documented route and uses tracked fixtures. |
| A coding agent handles a credential. | Secrets must never reach an agent transcript. | The agent contract stops the agent before any secret step. The person types secrets into the local hidden prompt. |
| Community timestamp data is thin or mismatched. | A missing warning looks like a bug and reads like a safety failure. | The README and the launch replies both say that missing data and a different edition are expected. |
| Demo assets imply a working product feature that does not exist. | Misleading a launch audience is not recoverable. | Every asset comes from the real tool, run on synthetic input, and lists its regeneration command. |
| Python 3.9 is the floor and is end of life upstream. | A visitor on a very new Python may hit an untested path. | CI covers 3.9 through 3.13 on Ubuntu and macOS. No change proposed for this launch. |

## Release candidate validation

### Baseline, before any launch edits

Recorded 2026-09-07 at commit `898bcd6`.

| Check | Command | Result |
| --- | --- | --- |
| Platform | - | macOS 26.6.2, arm64, Python 3.9.6, FFmpeg 8.0.1 |
| Unit tests | `python3 -m unittest discover -s tests` | `Ran 447 tests` `OK (skipped=2)` |
| Unit tests with media | `RUN_MEDIA_TESTS=1 python3 -m unittest discover -s tests` | `Ran 447 tests` `OK` |
| Pytest | `python -m pytest -q` | `445 passed, 2 skipped` |
| Build | `python -m build` | `Successfully built trigger_warnings-0.3.0.tar.gz and trigger_warnings-0.3.0-py3-none-any.whl` |
| Metadata | `python -m twine check dist/*` | `PASSED` for both artefacts |
| CLI help | `trigger-warnings --help` | Prints usage, exits 0 |
| No-account run | `trigger-warnings --subtitles examples/dialogue.srt --events examples/events.json --output /tmp/example.warned.ass` | `Wrote /tmp/example.warned.ass.`, exit 0, 1.1K file |

No known failing test. Nothing in this table is a launch blocker.

### Final validation

Filled in at Milestone 6.

- [ ] `python -m pytest`
- [ ] `python -m build`
- [ ] `python -m twine check dist/*`
- [ ] Clean-checkout first run, no account, no network, no keychain
- [ ] Wheel install smoke test in a new virtual environment
- [ ] Wizard path in a real terminal, no live credentials entered
- [ ] Every README anchor, internal link and asset path
- [ ] README rendered as GitHub-flavoured Markdown
- [ ] Visual assets legible at GitHub and mobile widths
- [ ] Coding-agent prompt reasoned through line by line
- [ ] Diff reviewed for secrets, personal paths, copyrighted media and version changes

## Public repository and PyPI owner actions

See the owner list above, and `release.md` for the exact steps and the order.

## Visual assets approval

- [ ] `[owner]` Approve the assets listed in `visuals.md`.

## Tester results and required fixes

Anonymised results from `tester-script.md`.

| Tester | Reached a rendered warning | What they thought it does | First confusing step | Action |
| --- | --- | --- | --- | --- |
| - | - | - | - | - |

## Product Hunt setup

- [ ] `[owner]` Maker profile complete.
- [ ] `[owner]` Gallery uploaded in the order given in `product-hunt.md`.
- [ ] `[owner]` Maker comment ready to post at launch.
- [ ] `[owner]` First-day replies to hand.

## Launch-day run of show

Times are Europe/Amsterdam.

| Time | Action | Owner |
| --- | --- | --- |
| T-1 day | Final validation table complete; tag and release drafted but not published. | Owner |
| 00:01 | Product Hunt listing goes live. | Owner |
| 00:15 | Post the maker comment. | Owner |
| 08:00 | Publish the version tag; confirm the release workflow and the PyPI page. | Owner |
| 09:00 | Post to the first community. | Owner |
| Hourly to 20:00 | Answer questions. Fix any reproducible first-run failure the same day. | Owner |
| 20:00 | Note metrics and the top three questions asked. | Owner |

## Follow-up

- [ ] `[owner]` 24 hours: answer every open thread, record the top three questions, file an issue for each reproducible failure.
- [ ] `[owner]` 7 days: publish a short follow-up note, close or triage every issue, decide what moves on the roadmap.

## Metrics and learning notes

Read from GitHub, PyPI and Product Hunt dashboards. The project adds no
analytics, no telemetry and no tracking of its own.

| Measure | Where | Day 1 | Day 7 |
| --- | --- | --- | --- |
| Repository stars | GitHub | - | - |
| Unique clones | GitHub traffic | - | - |
| PyPI downloads | PyPI / pypistats | - | - |
| Issues opened | GitHub | - | - |
| Product Hunt comments | Product Hunt | - | - |

What we wanted to learn:

1. Does a visitor understand the promise from the first screen alone?
2. Does the no-account demo work first time on a machine we do not control?
3. Which data source do people ask for next?
