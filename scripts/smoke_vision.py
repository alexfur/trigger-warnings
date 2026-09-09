"""Exercise the installed vision extra on a four-second synthetic video.

Run with the Python environment containing the installed wheel. FFmpeg and
Apple Silicon are required. The default SmolVLM2 model downloads on first use.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix="trigger-warnings-smoke-") as folder:
        root = Path(folder)
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=2:d=4",
            "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='gte(t,2)'",
            str(root / "synthetic.mp4"),
        ], check=True, timeout=60)
        (root / "dialogue.srt").write_text(
            "1\n00:00:00,000 --> 00:00:04,000\nSynthetic dialogue.\n", encoding="utf-8")
        result = subprocess.run([
            sys.executable, "-m", "trigger_warnings", "--json",
            "--video", "synthetic.mp4", "--subtitles", "dialogue.srt",
            "--model-trigger", "a large solid red rectangle",
            "--model-chunk", "2", "--model-fps", "1",
            "--lead", "0", "--output", "warnings.ass", "--provenance", "source.json",
        ], cwd=root, capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not result.stderr, result.stderr
        data = json.loads(result.stdout)
        assert data["ok"] and data["mode"] == "model-scan", data
        assert data["selectedEvents"] == 1, data
        output = (root / "warnings.ass").read_text()
        assert "TRIGGER INCOMING" in output and "Synthetic dialogue." in output
        assert "0:00:02.00" in output, output
        assert (root / "source.json").is_file()
        print("Installed vision smoke passed: blue negative, red positive, subtitle and provenance written.")


if __name__ == "__main__":
    main()
