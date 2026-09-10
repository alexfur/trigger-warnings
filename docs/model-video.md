# Local models and video input

Trigger Warnings resolves model files separately from inference. Existing model
directories load directly. Hugging Face model IDs use the shared cache and can
download missing files unless `--model-local-only` is set.

## Use an existing model

From the project checkout, install the updated vision dependencies:

```sh
.venv/bin/python -m pip install '.[vision]'
```

Pass an existing MLX-compatible model directory:

```sh
.venv/bin/trigger-warnings --model "$HOME/Models/smolvlm2" \
  --model-local-only --video "movie.mkv" --model-trigger 'blood' \
  --output "movie.warnings.ass"
```

The directory needs the weights, configuration, processor and tokenizer files.
An Ollama/GGUF weights file alone is not an MLX model directory. An existing
directory is loaded without contacting the Hugging Face Hub.

## Download a model separately

Run Hugging Face's downloader from the same environment:

```sh
.venv/bin/hf download mlx-community/SmolVLM2-500M-Video-Instruct-mlx \
  --local-dir "$HOME/Models/smolvlm2"
```

Then pass that directory to `--model` as above. Alternatively, omit
`--local-dir` to populate the shared Hugging Face cache and use the model ID
with `--model-local-only` when scanning.

The cache defaults to `~/.cache/huggingface/hub`. `HF_HOME` or `HF_HUB_CACHE` can
override its location. See the [Hugging Face download guide](https://huggingface.co/docs/huggingface_hub/guides/download).

## Model options

| Option | Default | Behaviour |
| --- | --- | --- |
| `--model NAME_OR_PATH` | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` | A Hub model ID or an existing local model directory |
| `--model-local-only` | Off | Refuses missing model files instead of downloading them |
| `--model-revision REVISION` | Unspecified | Pins a Hub model revision; rejected for local directories |

For a local directory, select the revision when downloading it. A mistyped
absolute path or a path starting with `./`, `../` or `~` fails as a local path;
it is not interpreted as a Hub model ID.

`--model-local-only` controls model retrieval. An explicitly requested DDD API
call still uses the network. Without this flag, Hub IDs retain automatic
downloading and cache reuse.

## Video decoding

The scanner opens the original video once through PyAV. It decodes sequentially,
samples by presentation timestamp and passes each clip to MLX-VLM's `video=`
input. It creates no temporary JPEGs, intermediate video files or FFmpeg
subprocesses. Memory holds the current sampled clip and decoder state.

SmolVLM's processor still represents video as frames internally. The installed
MLX video-path loader does not support clip start/end offsets, so passing it
the whole movie for every check would repeatedly process the full file. The
scanner's decoder supplies bounded clips instead. Ten-second windows remain
the default for assigning candidate timestamps; this change does not introduce
continuous model memory across clips.

Presentation timestamps preserve variable frame rates and clip boundaries.
Missing timestamps, corrupt frames and empty clips fail the scan. `--model-fps`
controls sampling and `--model-width` controls the decoded frame width.

FFmpeg remains necessary for subtitle extraction and preview rendering.
The model scan checks visual content only. Its candidate timestamps still need
review before use.
