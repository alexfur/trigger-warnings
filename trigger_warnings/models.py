"""Resolve model files separately from inference; imports stay optional."""

from pathlib import Path


class ModelError(ValueError):
    """The requested model files are unavailable."""


def resolve_model(name, revision=None, local_only=False):
    """Return a local model directory, optionally obtaining it from the Hub.

    An existing directory never invokes the Hub client. Repository IDs use its
    shared cache; local-only requests cannot initiate a download.
    """
    text = str(name)
    path = Path(text).expanduser()
    if path.exists():
        if not path.is_dir():
            raise ModelError("--model must name a model directory, not a single weights file")
        if revision is not None:
            raise ModelError("--model-revision applies to Hub model IDs, not local directories")
        return path.resolve()
    if path.is_absolute() or text.startswith((".", "~")):
        raise ModelError("local model directory does not exist: {}".format(path))
    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:
        raise ModelError("model IDs need the vision extra; install 'trigger-warnings[vision]'") from error
    try:
        result = snapshot_download(
            repo_id=text, revision=revision, local_files_only=local_only,
            allow_patterns=["*.json", "*.jsonl", "*.safetensors", "*.py",
                            "*.model", "*.tiktoken", "*.txt", "*.jinja"],
        )
    except Exception as error:
        if local_only:
            raise ModelError(
                "model {!r} is not available in the local Hugging Face cache. "
                "Download it separately with `hf download`, pass --model /path/to/model, "
                "or omit --model-local-only to allow a download.".format(text)
            ) from error
        raise ModelError("could not obtain model {!r}: {}".format(text, error)) from error
    return Path(result).resolve()
