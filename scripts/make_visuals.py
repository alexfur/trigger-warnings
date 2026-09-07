#!/usr/bin/env python3
"""Regenerate every launch visual from the bundled synthetic fixtures.

Each asset is produced by the real tool on material tracked in this repository,
so a reviewer can rerun this script and get the same pictures. Nothing here
reads a video, a subtitle file or a credential belonging to the person running
it.

    python3 scripts/make_visuals.py            # writes into assets/
    python3 scripts/make_visuals.py --check    # report only, write nothing

FFmpeg is required for the video assets and is not needed to use the tool from
a subtitle file. See docs/launch/visuals.md for what each asset is for.
"""

import argparse
import html
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
EXAMPLES = ROOT / "examples"

# The demo runs 60 seconds. Warnings sit at 10-34s and 35-55s, dialogue at
# 12-17s, 30-33s and 51-54s. This window opens on a clear frame, shows the
# banner arrive, then shows banner and dialogue together.
GIF_START = 6
GIF_END = 18
GIF_FPS = 10
GIF_WIDTH = 720

TERMINAL_COMMAND = [
    "$ trigger-warnings --subtitles examples/dialogue.srt \\",
    "      --events examples/events.json --output example.warned.ass",
]


def run(argv, **kwargs):
    kwargs.setdefault("check", True)
    kwargs.setdefault("timeout", 300)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(argv, **kwargs)


def wrap(text, width=74):
    """Wrap report prose the way a narrow terminal would."""
    lines = []
    for paragraph in text.splitlines():
        indent = " " * (len(paragraph) - len(paragraph.lstrip(" ")))
        words, current = paragraph.split(), indent
        if not words:
            lines.append("")
            continue
        for word in words:
            candidate = word if current == indent else current + " " + word
            if current != indent and len(candidate) > width:
                lines.append(current)
                current = indent + "  " + word
            else:
                current = indent + word if current == indent else candidate
        lines.append(current)
    return lines


def capture_first_run(workspace):
    """Run the documented no-account command and return what a visitor sees.

    It runs inside a copy of `examples/`, so every path in the captured text is
    relative. No personal directory, machine name or credential can appear.
    """
    shutil.copytree(EXAMPLES, workspace / "examples")
    result = subprocess.run(
        [sys.executable, "-m", "trigger_warnings",
         "--subtitles", "examples/dialogue.srt",
         "--events", "examples/events.json",
         "--output", "example.warned.ass"],
        cwd=str(workspace), env={"PYTHONPATH": str(ROOT), "PATH": ""},
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise SystemExit("the documented command failed:\n" + result.stderr)
    return result.stderr.rstrip("\n")


def terminal_svg(report):
    """Render a captured session as an SVG card that GitHub can display.

    SVG rather than a screenshot on purpose: the text stays readable in the
    diff, so a reviewer can check that the picture says what the tool said.
    """
    lines = list(TERMINAL_COMMAND) + [""] + wrap(report)
    line_height, top, left = 21, 62, 26
    height = top + line_height * len(lines) + 20
    width = 860

    rows = []
    for index, line in enumerate(lines):
        y = top + index * line_height
        css = "out"
        if line.startswith("$"):
            css = "cmd"
        elif line.startswith("      "):
            css = "cmd"
        elif line.startswith("Wrote "):
            css = "good"
        rows.append(
            '    <text x="{}" y="{}" class="mono {}" xml:space="preserve">{}'
            "</text>".format(left, y, css, html.escape(line))
        )

    title = "A terminal running the no-account example"
    desc = (
        "A terminal window. The command trigger-warnings is run with "
        "--subtitles examples/dialogue.srt, --events examples/events.json and "
        "--output example.warned.ass. It reports: " + " ".join(report.split())
    )
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" \
width="{width}" height="{height}" role="img" aria-labelledby="tw-term-title tw-term-desc">
  <title id="tw-term-title">{title}</title>
  <desc id="tw-term-desc">{desc}</desc>

  <style>
    .chrome {{ fill: #f6f8fa; stroke: #d0d7de; }}
    .screen {{ fill: #ffffff; }}
    .mono   {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 14px; }}
    .cmd    {{ fill: #1f2328; font-weight: 600; }}
    .out    {{ fill: #57606a; }}
    .good   {{ fill: #115e59; font-weight: 600; }}
    .dot    {{ fill: #d0d7de; }}
    .label  {{ fill: #57606a; font-size: 12px; }}

    @media (prefers-color-scheme: dark) {{
      .chrome {{ fill: #161b22; stroke: #30363d; }}
      .screen {{ fill: #0d1117; }}
      .cmd    {{ fill: #e6edf3; }}
      .out    {{ fill: #8b949e; }}
      .good   {{ fill: #56d4c4; }}
      .dot    {{ fill: #30363d; }}
      .label  {{ fill: #8b949e; }}
    }}
  </style>

  <rect x="0.5" y="0.5" width="{inner}" height="{iheight}" rx="10" class="chrome"/>
  <rect x="1" y="34" width="{screen}" height="{sheight}" class="screen"/>
  <circle cx="24" cy="18" r="5" class="dot"/>
  <circle cx="42" cy="18" r="5" class="dot"/>
  <circle cx="60" cy="18" r="5" class="dot"/>
  <text x="82" y="22" class="mono label">trigger-warnings</text>
{rows}
</svg>
""".format(
        width=width, height=height, inner=width - 1, iheight=height - 1,
        screen=width - 2, sheight=height - 35, title=html.escape(title),
        desc=html.escape(desc), rows="\n".join(rows),
    )


def build_video_assets(workspace, warned):
    """Burn the generated track into the demo video and export a GIF."""
    video = workspace / "demo.mp4"
    run([sys.executable, str(ROOT / "scripts/make_demo.py"),
         "--output", str(video)])
    shutil.copyfile(warned, workspace / "warned.ass")
    chain = (
        "ass=warned.ass,"
        "trim=start={start}:end={end},setpts=PTS-STARTPTS,"
        "fps={fps},scale={w}:-1:flags=lanczos,"
        "split[s0][s1];[s0]palettegen=max_colors=64:stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=bayer:bayer_scale=5"
    ).format(start=GIF_START, end=GIF_END, fps=GIF_FPS, w=GIF_WIDTH)
    gif = workspace / "demo.gif"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
         "-i", "demo.mp4", "-an", "-vf", chain, "-loop", "0", "demo.gif"],
        cwd=str(workspace))
    return video, gif


def build_preview(workspace, video):
    """Ask the tool itself for a rendered frame. No other program draws this."""
    preview = workspace / "preview.png"
    warned = workspace / "preview.ass"
    run([sys.executable, "-m", "trigger_warnings",
         "--video", str(video), "--events", str(EXAMPLES / "events.json"),
         "--output", str(warned), "--verify", str(preview)],
        cwd=str(ROOT), env={"PYTHONPATH": str(ROOT), "PATH": _path()})
    return preview


def _path():
    import os
    return os.environ.get("PATH", "")


def publish(source, target, check):
    if check:
        state = "same" if target.exists() and target.read_bytes() == \
            source.read_bytes() else "would change"
        print("{:<22} {:>9} bytes  {}".format(
            target.name, source.stat().st_size, state))
        return
    ASSETS.mkdir(exist_ok=True)
    shutil.copyfile(source, target)
    print("{:<22} {:>9} bytes".format(target.name, target.stat().st_size))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report what would be written and write nothing")
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        parser.error("FFmpeg is required for the video assets; install it first")

    with tempfile.TemporaryDirectory(prefix="tw visuals ") as name:
        workspace = Path(name)
        report = capture_first_run(workspace)
        card = workspace / "terminal.svg"
        card.write_text(terminal_svg(report), encoding="utf-8")
        video, gif = build_video_assets(workspace, workspace / "example.warned.ass")
        preview = build_preview(workspace, video)

        publish(gif, ASSETS / "demo.gif", args.check)
        publish(preview, ASSETS / "preview.png", args.check)
        publish(card, ASSETS / "terminal.svg", args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
