#!/usr/bin/env python3
"""Launch an authorised scan in a visible Maestri terminal, with live progress."""

import datetime
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args == ["--help"]:
        print("Usage: python scripts/scan-in-terminal.py <trigger-warnings options>\n"
              "Opens a Maestri terminal running this Python environment.\n"
              "Use --json for the final result; omit --progress-json for the live bar.\n"
              "All other arguments pass through unchanged. Run only once per scan.")
        return 0 if args else 2
    if "--progress-json" in args:
        print("Omit --progress-json: the visible terminal displays human progress.", file=sys.stderr)
        return 2
    maestri = shutil.which("maestri") or os.environ.get("MAESTRI_CLI")
    if not maestri:
        print("Maestri is unavailable. Run trigger-warnings in a visible plain terminal.",
              file=sys.stderr)
        return 2
    # Maestri accepts a shell command. Quote every argument, including paths
    # and trigger descriptions, before handing it to the terminal's shell.
    command = shlex.join([sys.executable, "-m", "trigger_warnings", *args])
    title = "Trigger scan " + datetime.datetime.now().strftime("%H:%M:%S")
    return subprocess.run([
        maestri, "recruit", title, "--command", command,
        "--dir", str(Path(__file__).resolve().parents[1]),
    ], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
