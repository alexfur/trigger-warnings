# Launch checklist

Working record for the public launch of Trigger Warnings. Every tick needs a
command output, a link or a short factual note. An unchecked owner action is a
launch blocker, not a deferred nicety.

Status key: `[x]` done and evidenced, `[ ]` outstanding, `[owner]` needs the
repository owner and cannot be done by an implementation agent.

| Document | What it covers |
| --- | --- |
| [release.md](release.md) | What is already proven, the owner steps to publish, known limitations. |
| [visuals.md](visuals.md) | Every asset, its fixture, placement, alt text and regeneration command. |
| [product-hunt.md](product-hunt.md) | Taglines, descriptions, gallery, maker comment, replies, community posts. |
| [tester-script.md](tester-script.md) | What to send five to eight external testers, and how to act on what comes back. |

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

- [ ] `[owner]` Confirm the PyPI project name `trigger-warnings` is available or already held. A read-only check on 2026-09-07 returned `404` from `https://pypi.org/pypi/trigger-warnings/json`, so no project of that name is registered. Nothing was submitted.
- [ ] `[owner]` Configure PyPI Trusted Publishing for this repository and the `pypi` environment. Steps in [release.md](release.md).
- [ ] `[owner]` Create the `pypi` GitHub environment, with a required reviewer if a manual gate is wanted.
- [ ] `[owner]` Make the GitHub repository public.
- [ ] `[owner]` Set the repository description to `Spoiler-free trigger warnings for local video.`
- [ ] `[owner]` Add repository topics: `subtitles`, `accessibility`, `video`, `cli`, `python`.
- [ ] `[owner]` Decide on GitHub Discussions. It is currently off and nothing links to it.
- [ ] `[owner]` Create the version tag and GitHub release that triggers publication.
- [ ] `[owner]` Confirm the published PyPI page and install the released package.
- [ ] `[owner]` Decide whether to switch the README image paths to absolute raw URLs so they render on PyPI. See the limitations table in [release.md](release.md).
- [ ] `[owner]` Pin a launch issue or discussion, if one is created.
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

Run 2026-09-07 on macOS 26.6.2 arm64, Python 3.9.6, FFmpeg 8.0.1, against a
fresh `git clone` of the working branch unless noted.

| # | Check | Evidence |
| --- | --- | --- |
| 1 | `python -m pytest` | `454 passed, 2 skipped` |
| 2 | `python3 -m unittest discover -s tests` | `Ran 456 tests` `OK (skipped=2)` |
| 3 | `RUN_MEDIA_TESTS=1 python3 -m unittest discover -s tests` | `Ran 456 tests` `OK` |
| 4 | `python -m build` | Built `trigger_warnings-0.3.0.tar.gz` and `trigger_warnings-0.3.0-py3-none-any.whl` |
| 5 | `python -m twine check dist/*` | `PASSED` for both artefacts |
| 6 | Clean-checkout first run: no account, no network, no keychain, no FFmpeg | Ran the documented command with `PATH=/usr/bin:/bin`, an empty `HOME` and a `sitecustomize` that refuses every socket connect. Exit 0, 1168 byte output, `git status` shows only the new file. |
| 7 | Acceptance test from the specification, verbatim | Fresh clone, venv, `pip install .`, then the documented command writing to `/tmp`. `test -s` passes. |
| 8 | Wheel install smoke test | New virtual environment, `pip install dist/*.whl`, `trigger-warnings --version` reports `0.3.0`, the no-account command exits 0 with a 1168 byte output. |
| 9 | Wizard in a real terminal, no live credentials entered | Driven through a pseudo-terminal. `--setup` shows `Set this up now? [Y/n]`; `--setup --save` walks all three steps with hidden prompts and Enter skips each. Nothing entered, nothing stored, exit 0. |
| 10 | No prompt in JSON or non-interactive mode | `--setup --json` and `--setup --save </dev/null` both return without prompting. |
| 11 | Every README anchor, internal link and asset path | 32 links across 13 documents resolve. GitHub's own Markdown API generates all five heading anchors the README links to. |
| 12 | README rendered as GitHub-flavoured Markdown | Rendered through `POST /markdown` and screenshotted. The first screen carries name, tagline, explanation, safety limit and the GIF, in that order. |
| 13 | Visual assets legible at GitHub and mobile widths | Checked at 1012px and at a 358px column. Two defects found and fixed: the GIF opened on a blank frame, and the terminal card rendered near 5.8px on a phone. Both corrected in `dd9d878`. |
| 14 | Assets reproducible from a clean checkout | `python3 scripts/make_visuals.py --check` reports `same` for all three. |
| 15 | Coding-agent prompt reasoned through line by line | See the walkthrough below. |
| 16 | Diff reviewed | `git diff 898bcd6..HEAD`: no secret, no token, no personal path, no hostname, no copyrighted media and no version change. The only change to shipped code is the FFmpeg error message. |

#### Coding-agent prompt walkthrough

Each claim in the prompt was checked against the tool rather than assumed.

| Prompt line | Checked |
| --- | --- |
| Read `--help` first, on its own | `--json --help` still prints plain text and exits 0. |
| `--setup --json`, stop on a missing credential | Reports three missing credentials, exits 0 and prompts for nothing. `ready` is `false` while `timestampsFromFile` and `dialogueFromFile` stay `true`, so the tool is still usable. |
| The person runs `--setup --save` in their own terminal | Confirmed through a pseudo-terminal. The prompts are hidden and the tool writes no file. |
| Never ask for a secret in chat, never ask to export or copy one | Present, and the prompt says why: there is nothing an agent can do with a credential that the person cannot do by running setup. |
| `--ddd-search` shows candidates, the agent never chooses | The search mode selects nothing and refuses the generation flags. |
| `--dry-run` without `--category` shows categories and stops | Confirmed. It lists the categories and windows, then reports `Dry run complete. No files were written.` |
| A new `--output` and a `--provenance` sidecar, never overwrite | An existing output is refused with `Existing files are never replaced; choose a new name.` |
| Branch on `ok`, report `filesWritten` and `notes` | Confirmed against a real result object, including the failure shape `{"ok": false, "error": {...}}`. |
| The selected and excluded categories | Corrected during this check. They are prose in `messages`, not a field of their own. The README and the skill now say so. |

The prompt stops at step 1 whenever a credential is missing, so it never
reaches a step that could handle a secret.

## Public repository and PyPI owner actions

See the owner list above, and [release.md](release.md) for the exact steps and
the order.

## Visual assets approval

- [ ] `[owner]` Approve the assets listed in [visuals.md](visuals.md).

## Tester results and required fixes

Anonymised results from [tester-script.md](tester-script.md).

| Tester | Reached a rendered warning | What they thought it does | First confusing step | Action |
| --- | --- | --- | --- | --- |
| - | - | - | - | - |

## Product Hunt setup

- [ ] `[owner]` Maker profile complete.
- [ ] `[owner]` Gallery uploaded in the order given in [product-hunt.md](product-hunt.md).
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
