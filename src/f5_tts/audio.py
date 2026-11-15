"""Audio I/O independent of TorchCodec and the removed TorchAudio backends."""

from dataclasses import dataclass

import soundfile as sf
import torch


@dataclass(frozen=True)
class AudioInfo:
    sample_rate: int
    num_frames: int


def load_audio(path):
    """Read a supported audio file as a float32 tensor shaped [channels, frames]."""
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    return torch.from_numpy(samples.T.copy()), sample_rate


def save_audio(path, waveform, sample_rate):
    """Write a [channels, frames] tensor, including tensors residing on a GPU."""
    samples = waveform.detach().to(device="cpu", dtype=torch.float32).numpy()
    if samples.ndim != 2:
        raise ValueError("Expected audio shaped [channels, frames]")
    sf.write(path, samples.T, sample_rate)


def audio_info(path):
    """Read duration metadata without decoding the entire file."""
    metadata = sf.info(path)
    return AudioInfo(sample_rate=metadata.samplerate, num_frames=metadata.frames)
