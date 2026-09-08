# Local pre-scan measurement

On 8 September 2026, SmolVLM2 500M processed ten minutes of synthetic video
in 69.17 seconds at one sampled frame per second on an Apple M4 with 32 GiB RAM.
Sampling one frame every five seconds reduced this to 20.92 seconds.

These are throughput measurements for a simple visual question. They do not
establish trigger-detection accuracy or a production scan-time estimate.
The experiment adds no dependencies or scene detection to the shipped CLI.

## Results

| Sampling | Frames processed | Scan time | Video speed | Two-hour linear projection |
| --- | ---: | ---: | ---: | ---: |
| 1 frame/second | 600 | 69.17 seconds | 8.67x | 13.83 minutes |
| 1 frame/5 seconds | 120 | 20.92 seconds | 28.67x | 4.18 minutes |

The projections multiply the ten-minute measurement by twelve. No two-hour
film was tested. Both runs include model loading from an existing download,
FFmpeg extraction, image preparation and inference. They exclude package
installation, initial model download, Python imports, speech recognition,
subtitle analysis and a second verification pass. Reported MLX peak memory
was 1.73 GB and 1.65 GB respectively; this is not total process memory.

Both runs correctly marked all four positive ten-second chunks and all 56
negative chunks. The target was a red rectangle on a plain blue background,
not blood or a real weapon. The two appearances each lasted about 20 seconds,
so this says nothing about detecting brief appearances, scene context or audio.

Frames were supplied as ordered image batches, not through native temporal
video encoding. Each request covered ten seconds and generated three tokens
(yes/no plus punctuation), with a 16-token cap. Frames were extracted at
384 pixels wide. The image processor used a single 512-pixel input per frame;
default 2048-pixel image tiling was disabled. No prompt/vision cache was reused.

An earlier request for three JSON booleans on an animated test pattern failed
both ten-second chunks: it generated timestamp-like text until the 64-token
limit. A simple single-image colour question worked. Treat this as evidence
that prompt and input choice matter, not proof that the model is a usable
trigger classifier. Earlier overlapping trial runs were excluded from this
comparison; the two reported scans ran sequentially.

The complete per-chunk outputs and timings are in
[prescan-m4-results.json](prescan-m4-results.json). One run also contains an
earlier availability simulation; it is not a tested playback integration.

## Reproduce the control experiment

Run from the repository root on Apple Silicon with FFmpeg and uv installed.
The environment and generated files stay in a new temporary directory.

```sh
bench_dir=$(mktemp -d /tmp/triggersubs-prescan.XXXXXX)
uv venv --python 3.12 "$bench_dir/venv"
uv pip install --python "$bench_dir/venv/bin/python" \
  'mlx-vlm==0.7.0' 'mlx==0.32.2' 'mlx-metal==0.32.2' \
  'transformers==5.16.1' 'torch==2.14.0' 'torchvision==0.29.0'

ffmpeg -hide_banner -loglevel error -n \
  -f lavfi -i 'color=c=blue:s=640x360:r=24:d=600' \
  -vf 'drawbox=x=200:y=90:w=200:h=180:color=red:t=fill:enable=between(t\,120\,139.9)+between(t\,420\,439.9)' \
  -c:v libx264 -preset ultrafast -crf 30 -pix_fmt yuv420p \
  "$bench_dir/control.mp4"

"$bench_dir/venv/bin/python" scripts/benchmark_prescan.py \
  --video "$bench_dir/control.mp4" --task marker --max-tokens 16 \
  --duration 600 --fps 1 --output "$bench_dir/one-fps"

"$bench_dir/venv/bin/python" scripts/benchmark_prescan.py \
  --video "$bench_dir/control.mp4" --task marker --max-tokens 16 \
  --duration 600 --fps 0.2 --output "$bench_dir/sparse"
```

The runner defaults to the model revision measured here. Its first run
downloads roughly 1 GB of model files into the Hugging Face cache; that first
run's load time includes the download. Repeat with a new output directory for
comparable timings. Outputs are new directories and never replace source files.

Model: `mlx-community/SmolVLM2-500M-Video-Instruct-mlx`, revision
`fa57db46815177fbdfd65cc85a2b3416a8332268`.

## Next experiment

Use a local video with manually labelled trigger intervals, including short
events and difficult negative examples. Measure event recall, false warnings,
timestamp errors and runtime on the same inputs. Compare SmolVLM2 with a
larger candidate before choosing a model. A sparse pass can miss an event
entirely; rechecking only flagged chunks cannot recover those misses.
