#!/usr/bin/env python3
"""Experimental local MLX pre-scan probe; no playback or coverage guarantees."""

import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import statistics
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new result directory")
    parser.add_argument("--model", default="mlx-community/SmolVLM2-500M-Video-Instruct-mlx")
    parser.add_argument("--revision", help="model revision; defaults to the measured SmolVLM2 revision")
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--chunk", type=int, default=10)
    parser.add_argument("--fps", type=float, default=1)
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--task", choices=("structured", "marker"), default="structured")
    args = parser.parse_args()
    if min(args.duration, args.chunk, args.fps, args.width, args.max_tokens) <= 0:
        parser.error("numeric options must be positive")
    if not math.isfinite(args.fps):
        parser.error("fps must be finite")
    if not args.video.is_file():
        parser.error("video must be an existing local file")
    args.output.mkdir(parents=True, exist_ok=False)

    import mlx.core as mx
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from PIL import Image

    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "json", str(args.video.resolve())],
                           capture_output=True, text=True, check=True, timeout=30)
    duration = min(args.duration, float(json.loads(probe.stdout)["format"]["duration"]))
    if not math.isfinite(duration) or duration <= 0:
        parser.error("video duration must be finite and positive")
    clock = time.perf_counter()
    revision = args.revision
    if revision is None and args.model == "mlx-community/SmolVLM2-500M-Video-Instruct-mlx":
        revision = "fa57db46815177fbdfd65cc85a2b3416a8332268"
    model, processor = load(args.model, revision=revision)
    # Video-sized frames must not be expanded into the model's default 2048px
    # tiled-image path. Keep a single 512px image-encoder input per frame.
    processor.image_processor.size = {"longest_edge": 512}
    processor.image_processor.do_image_splitting = False
    mx.synchronize()
    load_s = time.perf_counter() - clock
    print(json.dumps({"model_loaded_s": load_s}), flush=True)
    # Ordered image batches, not native temporal video encoding. No prompt or
    # vision cache is reused between chunks. Each result is stored verbatim.
    instruction = (
        "These images are consecutive video frames in time order. "
        "Report whether any frame shows visible blood, a weapon, or a large "
        "solid red rectangle covering the centre. Return only JSON with "
        'boolean keys "blood", "weapon", "red_rectangle". '
        "Do not infer events outside the supplied frames."
    )
    if args.task == "marker":
        instruction = ("Does any image show a large solid red rectangle covering "
                       "the centre of the image? Answer only yes or no.")
    rows = []
    with (args.output / "chunks.jsonl").open("x") as log:
        for start in range(0, math.ceil(duration), args.chunk):
            end = min(start + args.chunk, duration)
            chunk_clock = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix="triggersubs-frames-") as frames_dir:
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                    "-ss", str(start), "-i", str(args.video.resolve()), "-t", str(end - start),
                    "-vf", f"fps={args.fps},scale={args.width}:-2", "-q:v", "3",
                    str(Path(frames_dir) / "%05d.jpg"),
                ], check=True, timeout=120)
                paths = sorted(Path(frames_dir).glob("*.jpg"))
                if not paths:
                    raise RuntimeError(f"No frames extracted at {start}")
                frames = []
                for path in paths:
                    with Image.open(path) as image:
                        frames.append(image.convert("RGB"))
                extraction_s = time.perf_counter() - chunk_clock
                prompt = apply_chat_template(processor, model.config, instruction,
                                             num_images=len(frames))
                result = generate(model, processor, prompt, image=frames,
                                  max_tokens=args.max_tokens, temperature=0.0, verbose=False)
                mx.synchronize()
            completed = time.perf_counter()
            try:
                answer = json.loads(result.text.strip())
                valid = (isinstance(answer, dict) and
                         set(answer) == {"blood", "weapon", "red_rectangle"} and
                         all(type(value) is bool for value in answer.values()))
            except (ValueError, TypeError):
                valid = False
            if args.task == "marker":
                valid = result.text.strip().lower().rstrip(".") in {"yes", "no"}
            row = {"start_s": start, "end_s": end, "frames": len(paths),
                   "extraction_s": extraction_s, "elapsed_s": completed - chunk_clock,
                   "completed_wall_s": completed - clock, "text": result.text,
                   "valid_response": valid, "prompt_tokens": result.prompt_tokens,
                   "generation_tokens": result.generation_tokens,
                   "finish_reason": result.finish_reason, "peak_memory_gb": result.peak_memory}
            rows.append(row)
            log.write(json.dumps(row) + "\n")
            log.flush()
            print(json.dumps(row), flush=True)
    total = time.perf_counter() - clock
    latencies = sorted(row["elapsed_s"] for row in rows)
    summary = {"model": args.model, "revision": revision, "versions": {
        name: importlib.metadata.version(name) for name in ("mlx", "mlx-vlm", "transformers")},
        "video_duration_s": duration, "fps": args.fps, "width": args.width,
        "image_processor_longest_edge": 512, "image_splitting": False,
        "chunk_s": args.chunk, "max_tokens": args.max_tokens,
        "task": args.task,
        "model_load_s": load_s, "scan_wall_s": total,
        "video_seconds_per_wall_second": duration / total,
        "median_chunk_s": statistics.median(latencies),
        "p95_chunk_s": latencies[math.ceil(len(latencies) * .95) - 1],
        "valid_response_chunks": sum(row["valid_response"] for row in rows),
        "total_chunks": len(rows),
        "caveat": "Synthetic footage measures throughput, not trigger recall. "
                   "Inspect invalid outputs separately. "
                   "No player integration, audio, subtitle processing, or temporal overlap."}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
