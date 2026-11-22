"""Normalize and process locally recorded ADR takes."""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import soundfile as sf

from f5_tts.studio.video_dubbing import _convert

TAKE_LIMIT = 20_000_000
TAKE_MAX_DURATION = 180
MEDIA_INPUTS = {
    "audio/webm": "matroska,webm",
    "video/webm": "matroska,webm",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/mp4": "mov",
    "video/mp4": "mov",
}

HUMAN_TREATMENT = (
    "afftdn=nf=-32:nr=8",
    "highpass=f=80",
    "acompressor=threshold=0.125:ratio=2:attack=12:release=120",
    "loudnorm=I=-17:TP=-1.5:LRA=7",
    "aresample=48000",
)


@dataclass(frozen=True)
class Take:
    id: UUID
    path: Path
    duration: float
    capture_offset_ms: int
    number: int


def save_take(directory, source, media_type, capture_offset_ms, number):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Instale o FFmpeg para salvar gravações.")
    if media_type not in MEDIA_INPUTS:
        raise ValueError("Formato de gravação não suportado.")
    directory.mkdir(parents=True, exist_ok=True)
    take_id = uuid4()
    output = directory / f"{take_id.hex}.wav"
    with tempfile.TemporaryDirectory(prefix="take-", dir=directory) as temporary:
        draft = Path(temporary) / output.name
        _convert(
            ffmpeg,
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            MEDIA_INPUTS[media_type],
            "-i",
            source,
            "-map",
            "0:a:0",
            "-vn",
            "-t",
            TAKE_MAX_DURATION + 1,
            "-ar",
            48000,
            "-ac",
            1,
            "-c:a",
            "pcm_s16le",
            draft,
        )
        duration = sf.info(draft).duration
        if not 0.05 <= duration <= TAKE_MAX_DURATION:
            raise ValueError("Grave um trecho entre 0,05 e 180 segundos.")
        draft.replace(output)
    return Take(take_id, output, duration, capture_offset_ms, number)
