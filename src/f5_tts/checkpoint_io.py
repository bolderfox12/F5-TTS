"""Load PyTorch checkpoints with the restricted deserializer on patched releases."""

import torch
from huggingface_hub import hf_hub_download
from packaging.version import Version


def load_checkpoint_weights(path, map_location="cpu"):
    version = Version(torch.__version__)
    if version < Version("2.10.0") or version.release[:2] == (2, 14):
        raise RuntimeError("Checkpoint loading requires PyTorch 2.10 or later, excluding the 2.14 series.")
    return torch.load(path, map_location=map_location, weights_only=True)


def download_hf_model_file(uri, cache_dir=None):
    """Resolve documented hf://owner/repo[@revision]/path checkpoint URLs."""
    if not uri.startswith("hf://"):
        raise ValueError("Use an hf://owner/repo/file model URL.")
    parts = uri[5:].split("/")
    if len(parts) < 3 or any(segment in ("", ".", "..") for segment in parts):
        raise ValueError("Use an hf://owner/repo/file model URL.")
    owner, repo_with_revision, *filename = parts
    repo, _, revision = repo_with_revision.partition("@")
    return hf_hub_download(
        repo_id=f"{owner}/{repo}", filename="/".join(filename), revision=revision or None, cache_dir=cache_dir
    )
