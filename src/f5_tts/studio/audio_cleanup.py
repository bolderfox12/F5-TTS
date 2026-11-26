"""Voice extraction and background-music replacement for uploaded videos."""

import math
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

from f5_tts.studio.video_dubbing import VIDEO_EXTENSIONS, VIDEO_LIMIT, _convert, dubbing_stems
from f5_tts.studio.video_probe import video_media_info


AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
AUDIO_LIMIT = 500_000_000
_HANDOFFS = {}
_HANDOFFS_LOCK = threading.Lock()


def managed_media_path(service, path):
    if not path:
        raise ValueError("Escolha um arquivo do Studio.")
    path = Path(path).resolve()
    roots = (service.uploads.resolve(), service.outputs.resolve())
    if not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        raise ValueError("O arquivo precisa estar enviado ou gerado neste Studio.")
    return path


def audio_cleanup_handoff_url(service, *, video=None, music=None):
    video_path = managed_media_path(service, video) if video else None
    music_path = managed_media_path(service, music) if music else None
    if video_path and video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("O arquivo selecionado não é um vídeo compatível.")
    if music_path and music_path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("O arquivo selecionado não é uma música compatível.")
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _HANDOFFS_LOCK:
        for old_token, (_, expires) in list(_HANDOFFS.items()):
            if expires < now:
                _HANDOFFS.pop(old_token, None)
        _HANDOFFS[token] = ((video_path, music_path), now + 900)
    return f"/audio-cleanup?transfer_id={token}"


def read_audio_cleanup_handoff(service, token):
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token):
        return None
    with _HANDOFFS_LOCK:
        entry = _HANDOFFS.get(token)
        if not entry:
            return None
        (video_path, music_path), expires = entry
        if expires < time.monotonic():
            _HANDOFFS.pop(token, None)
            return None
    try:
        return {
            "video": managed_media_path(service, video_path) if video_path else None,
            "music": managed_media_path(service, music_path) if music_path else None,
        }
    except ValueError:
        return None


def process_vocal_track(service, ffmpeg, source, noise_reduction=12):
    source = managed_media_path(service, source)
    try:
        reduction = int(noise_reduction)
    except (TypeError, ValueError) as error:
        raise ValueError("A redução de ruído deve estar entre 0 e 30 dB.") from error
    if not 0 <= reduction <= 30:
        raise ValueError("A redução de ruído deve estar entre 0 e 30 dB.")
    output = source.with_name(f"{source.stem}-voz-profissional-nr{reduction}.wav")
    if output.is_file() and output.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return output
    filters = []
    if reduction:
        filters.append(f"afftdn=nr={reduction}:nf=-45:tn=1")
    filters.extend(
        (
            "highpass=f=75",
            "equalizer=f=3000:t=q:w=1:g=2",
            "acompressor=threshold=0.125:ratio=3:attack=12:release=160:makeup=2",
            "loudnorm=I=-16:TP=-1.5:LRA=9",
            "alimiter=limit=0.95",
        )
    )
    _convert(
        ffmpeg,
        "-i",
        source,
        "-af",
        ",".join(filters),
        "-ar",
        48000,
        "-ac",
        2,
        "-c:a",
        "pcm_s16le",
        output,
    )
    return output


def export_cleaned_video(
    service,
    source,
    music,
    voice_volume,
    music_volume,
    resolution,
    *,
    noise_reduction=12,
    auto_duck=True,
    report=None,
):
    source = managed_media_path(service, source)
    if source.suffix.lower() not in VIDEO_EXTENSIONS or source.stat().st_size > VIDEO_LIMIT:
        raise ValueError("Envie um vídeo compatível de até 500 MB.")
    music_path = managed_media_path(service, music) if music else None
    if music_path and (music_path.suffix.lower() not in AUDIO_EXTENSIONS or music_path.stat().st_size > AUDIO_LIMIT):
        raise ValueError("Envie uma música compatível de até 500 MB.")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Instale o FFmpeg para limpar o áudio do vídeo.")

    if resolution not in {"1080x1920", "1920x1080"}:
        raise ValueError("Escolha um dos formatos de vídeo disponíveis.")
    voice_volume, music_volume = float(voice_volume), float(music_volume)
    if not math.isfinite(voice_volume) or not math.isfinite(music_volume):
        raise ValueError("Os volumes informados são inválidos.")
    duration, _width, _height, has_audio = video_media_info(source)
    if not duration or duration <= 0:
        raise ValueError("Não foi possível identificar a duração do vídeo.")
    if not has_audio:
        raise ValueError("Este vídeo não contém uma faixa de áudio para separar.")
    if report:
        report(0.05, "Separando voz e fundo do vídeo com Demucs…")
    raw_voice, _original_background = dubbing_stems(service, ffmpeg, source, report=report)
    if report:
        report(0.25, f"Limpando ruído e nivelando a voz ({noise_reduction} dB)…")
    voice = process_vocal_track(service, ffmpeg, raw_voice, noise_reduction)

    output_dir = service.outputs / "audio-cleanup"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"video-limpo-{uuid4().hex[:10]}-{resolution}.mp4"
    width, height = (1080, 1920) if resolution == "1080x1920" else (1920, 1080)
    filters = [f"[1:a]volume={max(0.0, min(float(voice_volume), 2.0))},apad,atrim=duration={duration:.3f}[voice]"]
    arguments = ["-i", source, "-i", voice]
    if music_path:
        arguments.extend(["-stream_loop", "-1", "-i", music_path])
        filters.append(
            f"[2:a]volume={max(0.0, min(float(music_volume), 2.0))},atrim=duration={duration:.3f}[music]"
        )
        if auto_duck:
            filters.append("[music][voice]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500[ducked]")
            filters.append("[voice][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
        else:
            filters.append("[voice][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    else:
        filters.append("[voice]anull[aout]")
    # Fit the entire source into the selected frame, preserving aspect ratio and
    # padding with black bars instead of cropping faces or subtitles.
    filters.append(
        f"[0:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[vout]"
    )
    arguments.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            output,
        ]
    )
    if report:
        report(0.35, f"Renderizando vídeo em {width}×{height}…")
    try:
        result = subprocess.run(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, arguments)],
            capture_output=True,
            text=True,
            timeout=max(600, int(duration * 30)),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output.unlink(missing_ok=True)
        raise ValueError("A renderização excedeu o tempo limite. Tente um vídeo menor.") from error
    if result.returncode or not output.is_file():
        output.unlink(missing_ok=True)
        details = (result.stderr or "").strip().splitlines()
        raise ValueError(f"Falha ao renderizar o vídeo: {details[-1] if details else 'erro do FFmpeg'}")
    if report:
        report(1, "Vídeo limpo e renderizado com sucesso.")
    return output
