# Roadmap

Small on purpose. No dates are promised. Anything not listed here has not been
decided, and a request in an issue is the way to change that.

## Planned

- **Evaluate local model detections** against labelled, appropriately licensed
  clips. The current model mode produces candidate timestamps for review.
- **A worked example for a real film**, using timestamps a person wrote
  themselves, so the second run is as clear as the first.
- **Better reporting of a category with no timestamped entries**, so "the
  service has no times for this" cannot read as "there is nothing to warn
  about".

## Exploring

- **More timestamp sources.** Which one comes next is an open question, and
  the answer depends on what people ask for and on each service's terms.
- **A subtitle-track chooser that survives a re-encode**, because stream
  indices move when a file is remuxed.
- **Warning styling options**, such as position and duration, without letting
  the banner name the category.

## Not planned

- **A safety rating, score or all-clear.** Absence of a warning is not
  evidence of absence.
- **Editing, cutting or skipping video.** The output is a subtitle track and
  nothing else touches the file.
- **A hosted service or an account of our own.** Everything runs locally with
  credentials that belong to the person using it.
- **Analytics or telemetry**, in the tool or in this repository.

## Help wanted

Good first contributions, in rough order of size:

1. Player notes for a player that is not VLC or mpv, added to the cookbook.
2. A synthetic fixture covering a subtitle edge case the suite misses.
3. A translated warning banner, with the sync caveats intact.

Read [CONTRIBUTING.md](CONTRIBUTING.md) first. Fixtures must be synthetic or
appropriately licensed.
