#!/usr/bin/env python3
"""Create a silent, synthetic video with embedded English demonstration subtitles."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new MP4 path")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; choose a new path")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("ffmpeg is required on PATH")
    dialogue = Path(__file__).resolve().parents[1] / "examples" / "dialogue.srt"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-f", "lavfi", "-i", "color=c=0x23354d:s=960x540:r=12:d=60",
        "-i", str(dialogue), "-map", "0:v:0", "-map", "1:s:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
        "-metadata:s:s:0", "title=Harmless demo dialogue", "-t", "60",
        str(args.output.resolve()),
    ]
    try:
        subprocess.run(command, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        print("Demo generation failed: {}".format(error), file=sys.stderr)
        return 1
    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
