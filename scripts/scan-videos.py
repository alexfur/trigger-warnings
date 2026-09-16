#!/usr/bin/env python3
"""Scan explicitly selected videos with Gemini, preserving the original files.

Run from a source checkout after installing ``.[gemini]`` in a virtual
environment. Credentials come from GEMINI_API_KEY or the operating system
keychain, as with the main CLI.
"""

import argparse
from pathlib import Path
import sys

from trigger_warnings import cli


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", nargs="+", type=Path, help="video files to scan")
    parser.add_argument("--trigger", action="append", required=True,
                        help="trigger to look for; repeat for multiple triggers")
    parser.add_argument("--trigger-desc", action="append", default=[], metavar="LABEL=DESC",
                        help="description of a trigger; repeat as needed")
    parser.add_argument("--model", help="override the CLI's default Gemini model")
    args = parser.parse_args(argv)

    # Validate the entire batch before uploading anything.
    missing = [str(path) for path in args.videos if not path.is_file()]
    if missing:
        parser.error("video files not found: " + ", ".join(missing))

    failed = False
    for video in args.videos:
        command = [
            "--video", str(video), "--provider", "gemini",
            "--output", str(video.with_suffix(".gemini-warnings.ass")),
            "--warnings-output", str(video.with_suffix(".gemini-warnings-only.srt")),
        ]
        for trigger in args.trigger:
            command.extend(["--model-trigger", trigger])
        for description in args.trigger_desc:
            command.extend(["--model-trigger-desc", description])
        if args.model:
            command.extend(["--gemini-model", args.model])
        print("Scanning {}".format(video), flush=True)
        result = cli.main(command)
        failed = failed or result != 0

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
