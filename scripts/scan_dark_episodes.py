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
    
    try:
        exit_code = cli.main(argv)
    except Exception as exc:
        print(f"\nError scanning {video_path.name}: {exc}\n", file=sys.stderr)
        exit_code = 1

    elapsed = time.time() - t0
    print(f"\nFinished {video_path.name} in {elapsed:.1f}s (exit code {exit_code})\n", flush=True)
    return exit_code

def main():
    print("Dark Season 1 Gemini Cloud Trigger Scan", flush=True)
    print("Triggers: eyes, nails, teeth, head smashing", flush=True)
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    
    episodes_to_scan = EPISODES
    if len(sys.argv) > 1:
        requested = sys.argv[1:]
        filtered = []
        for r in requested:
            for ep in EPISODES:
                if r in ep.name or f"E0{r}" in ep.name or f"E{r}" in ep.name:
                    filtered.append(ep)
        if filtered:
            episodes_to_scan = filtered

    results = {}
    for ep in episodes_to_scan:
        if not ep.exists():
            print(f"Error: {ep} does not exist!", file=sys.stderr)
            continue
        code = scan_episode(ep)
        results[ep.name] = "SUCCESS" if code == 0 else f"EXIT_{code}"

    print(f"\n{'='*70}\nALL EPISODES PROCESSED\n{'='*70}", flush=True)
    for name, status in results.items():
        print(f"  - {name}: {status}")

if __name__ == "__main__":
    main()
