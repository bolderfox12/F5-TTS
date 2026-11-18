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


def local_audio_input(path):
    """Restrict FFmpeg to recognized local audio containers without external tracks."""
    with open(path, "rb") as stream:
        header = stream.read(32)
    if header[:4] in (b"RIFF", b"RF64", b"RIFX") and header[8:12] == b"WAVE":
        format_name = "wav"
    elif header.startswith(b"fLaC"):
        format_name = "flac"
    elif header.startswith(b"OggS"):
        format_name = "ogg"
    elif header.startswith(b"\x1a\x45\xdf\xa3"):
        format_name = "matroska"
    elif header[4:8] == b"ftyp":
        format_name = "mov"
    elif len(header) >= 2 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0:
        format_name = "aac"
    elif header.startswith(b"ID3") or len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0:
        format_name = "mp3"
    else:
        raise ValueError("O arquivo não contém um formato de áudio reconhecido.")
    options = ["-protocol_whitelist", "file,pipe", "-f", format_name]
    if format_name == "mov":
        options += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    return [*options, "-i", str(path)]
