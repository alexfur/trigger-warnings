#!/usr/bin/env python3
"""Run Gemini cloud video trigger scans across Dark Season 1 episodes 8, 9, and 10."""

import os
from pathlib import Path
import sys
import time

from trigger_warnings import cli, keychain

keychain.hydrate()

EPISODES = [
    Path("Dark.S01E08.mkv"),
    Path("Dark.S01E09.mkv"),
    Path("Dark.S01E10.mkv"),
]

TRIGGERS = ["eyes", "nails", "teeth", "head smashing"]
DESCRIPTORS = [
    "eyes=an attack, gouging, blinding, or severe physical injury to someone's eye",
    "nails=fingernails or toenails being violently torn, ripped off, or mutilated",
    "teeth=teeth being violently smashed, knocked out, or extracted in an attack",
    "head smashing=a person's head being violently smashed against a hard surface or crushed",
]

def scan_episode(video_path):
    stem = video_path.stem
    parent = video_path.parent
    output_ass = parent / f"{stem}.gemini-warnings.ass"
    warnings_only_srt = parent / f"{stem}.gemini-warnings-only.srt"

    argv = [
        "--video", str(video_path),
        "--provider", "gemini",
        "--gemini-model", "gemini-3.6-flash",
        "--output", str(output_ass),
        "--warnings-output", str(warnings_only_srt),
    ]
    for t in TRIGGERS:
        argv.extend(["--model-trigger", t])
    for d in DESCRIPTORS:
        argv.extend(["--model-trigger-desc", d])

    print(f"\n{'='*70}\nStarting Gemini scan for: {video_path.name}\n{'='*70}", flush=True)
    t0 = time.time()
    
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    
    result = {}
    exit_code = cli.run(args, lambda msg, level="info": print(f"[{level.upper()}] {msg}", flush=True), result)
    elapsed = time.time() - t0
    print(f"\nCompleted {video_path.name} in {elapsed:.1f}s with exit code {exit_code}\n", flush=True)
    return exit_code, result

def main():
    for ep in EPISODES:
        if not ep.exists():
            print(f"Error: {ep} does not exist!", file=sys.stderr)
            return 1
        code, res = scan_episode(ep)
        if code != 0:
            print(f"Warning: Episode {ep.name} returned exit code {code}", file=sys.stderr)

if __name__ == "__main__":
    main()
