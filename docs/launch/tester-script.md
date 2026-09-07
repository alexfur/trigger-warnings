# Tester script

For five to eight people, before the public launch. Twenty minutes each. The
point is to find out whether a stranger understands the tool and can reach a
result, not to collect praise.

**Draft. The owner recruits testers and sends this.**

## What to tell a tester

Send them this, and nothing else. In particular, do not explain what the tool
does first. That is the thing being tested.

> Thanks for doing this. It should take about twenty minutes.
>
> I would like you to try a small command-line tool and tell me where it loses
> you. Assume nothing: if a step is confusing, that is the finding.
>
> 1. Open <link to the repository>. Read the first screen only, without
>    scrolling, then tell me in your own words what you think it does and who
>    it is for.
> 2. Now follow the `Try it in two minutes` steps. You need Python 3.9 or
>    newer and nothing else. No account, no key.
> 3. Tell me whether it worked, and how long it took from opening the page.
> 4. If you have VLC or mpv and a video you already own, load the track you
>    made next to it and tell me what you see. Skip this if you would rather
>    not. It is optional.
> 5. Tell me the first moment you were confused, and whether you got past it.
>
> Two things you do not need to do. Do not tell me anything about what you
> would use it for personally, and do not watch anything you would rather not
> watch. Please do not send me a video, a subtitle file you do not own, or any
> account details. If a step asks for a credential, stop and tell me instead.
>
> Blunt is useful. "I did not understand this" is the most valuable sentence
> you can send.

## What to ask for back

Five short answers. Free text, not a form.

1. In one sentence, what does it do and who is it for?
2. Did the two-minute path work? How long did it take?
3. Did you open the result in a player? What did you see?
4. What was the first confusing step?
5. Anything that felt untrustworthy, overstated or unclear about the limits?

## Safety rules for the person running this

- Never ask a tester which content they want warnings about. It is not
  relevant to whether the tool works and it is not your business.
- Never ask anyone to watch distressing material to test a warning. The
  synthetic demo covers it.
- Never accept a video file, a subtitle file or a credential from a tester.
- Anonymise findings before they reach the checklist. Use `Tester A` and so
  on, and drop anything identifying.
- If a tester says the tool made them feel safer than it should, treat that as
  a copy defect and fix the wording before launch. That is a blocker, not
  feedback.

## Recording results

Copy each result into the tester table in
[launch-checklist.md](launch-checklist.md), anonymised. Then decide, per
finding:

| Finding | Response |
| --- | --- |
| Two or more testers misread what the tool does from the first screen. | Blocker. Rewrite the first screen and retest with a fresh reader. |
| Any tester believed a missing warning meant a video was clear. | Blocker. Strengthen the limit and retest. |
| The two-minute path failed on a supported platform. | Blocker. Fix, add a test, then rerun the release-candidate checks. |
| One tester was confused by a step that others passed. | Fix the wording if it is cheap. Otherwise record it and move on. |
| A request for a feature. | Roadmap or an issue. Not a launch blocker. |
