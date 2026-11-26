"""Edit a Studio dub and assemble optional clips, captions and background music."""

import math
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from f5_tts.studio.local_tools import subtitles
from f5_tts.studio.video_dubbing import VIDEO_EXTENSIONS, VIDEO_LIMIT, _convert, _publish_video
from f5_tts.studio.video_probe import VIDEO_INPUT, video_media_info

MUSIC_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")
MUSIC_INPUT = ("-protocol_whitelist", "file", "-format_whitelist", "wav,mp3,flac,ogg,mov,aac")
CAPTION_STYLES = {
    "white": "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Bold=1",
    "yellow": "PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,Outline=2,Bold=1",
    "box": "PrimaryColour=&H00FFFFFF,BackColour=&H80000000,OutlineColour=&H80000000,BorderStyle=3,Outline=3",
}


def create_playable_preview(service, source):
    source = service.managed_upload(source)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Instale o FFmpeg para preparar a prévia do vídeo.")
    output = source.with_name(f"{source.stem}-previa.mp4")
    with tempfile.TemporaryDirectory(prefix="previa-video-", dir=service.uploads) as temporary:
        draft = Path(temporary) / output.name
        _convert(
            ffmpeg,
            *VIDEO_INPUT,
            "-i",
            source,
            "-map",
            "0:V:0",
            "-map",
            "0:a:0?",
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            draft,
        )
        draft.replace(output)
    return output


def _caption_window(captions, start, end, duration, extra_duration):
    maximum = max(duration + extra_duration, round(duration + extra_duration, 3))
    adjusted = []
    for caption in captions:
        first, last = caption["start"], caption["end"]
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (first, last)):
            raise ValueError("Informe o início e o fim de cada legenda.")
        if not 0 <= first < last <= maximum:
            raise ValueError("Use um intervalo válido dentro do vídeo para cada legenda.")
        text = "\n".join(line.strip() for line in caption["text"].splitlines() if line.strip())
        if not text:
            raise ValueError("Preencha o texto das legendas ou remova as linhas vazias.")
        if min(last, end) > max(first, start):
            adjusted.append({"start": max(first, start) - start, "end": min(last, end) - start, "text": text})
        if last > duration and extra_duration:
            offset = duration - (end - start)
            adjusted.append({"start": max(first, duration) - offset, "end": last - offset, "text": text})
    return sorted(adjusted, key=lambda caption: caption["start"])


def _render_clip(ffmpeg, source, target, start, duration, volume, size, fades, has_audio, normalize):
    width, height = size
    video_filter = (
        [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
            "fps=30",
        ]
        if normalize
        else []
    )
    audio_filter = [f"volume={volume / 100}", "apad"]
    fade_duration = min(0.4, duration / 3)
    for direction, enabled, offset in (("in", fades[0], 0), ("out", fades[1], duration - fade_duration)):
        if enabled:
            video_filter.append(f"fade=t={direction}:st={offset:.6f}:d={fade_duration:.6f}")
            audio_filter.append(f"afade=t={direction}:st={offset:.6f}:d={fade_duration:.6f}")
    audio_source = () if has_audio else ("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo")
    filters = ("-vf", ",".join(video_filter)) if video_filter else ()
    _convert(
        ffmpeg,
        "-ss",
        start,
        *VIDEO_INPUT,
        "-i",
        source,
        *audio_source,
        "-t",
        duration,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0" if has_audio else "1:a:0",
        *filters,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-af",
        ",".join(audio_filter),
        "-ar",
        48000,
        "-ac",
        2,
        "-c:a",
        "aac",
        target,
    )


def edit_dubbed_video(
    service,
    video,
    captions,
    start,
    end,
    volume,
    *,
    style="white",
    burn=False,
    clips=(),
    transition="cut",
    music=None,
    music_volume=20,
):
    video = Path(video).resolve()
    if not video.is_relative_to(service.outputs) or not video.is_file() or not video.name.endswith("-dublado.mp4"):
        raise ValueError("Gere um vídeo dublado antes de editar.")
    duration, width, height, has_audio = video_media_info(video)
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end, volume, music_volume)
    ):
        raise ValueError("Informe início, fim e volume válidos.")
    if end == round(duration, 3):
        end = duration
    if not 0 <= start < end <= duration or end - start < 0.01:
        raise ValueError("Escolha um intervalo de pelo menos 0,01 s dentro do vídeo.")
    if not 0 <= volume <= 200 or not 0 <= music_volume <= 100:
        raise ValueError("Escolha um volume entre 0% e 200% para a voz e até 100% para a música.")
    if style not in CAPTION_STYLES or transition not in {"cut", "fade"}:
        raise ValueError("Escolha um estilo de legenda e uma transição disponíveis.")

    sources = [service.managed_upload(clip) for clip in clips]
    if any(path.suffix.lower() not in VIDEO_EXTENSIONS or path.stat().st_size > VIDEO_LIMIT for path in sources):
        raise ValueError("Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB.")
    if music:
        music = service._managed_asset(music)
        if music.suffix.lower() not in MUSIC_EXTENSIONS:
            raise ValueError("Escolha um arquivo de música compatível.")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Instale o FFmpeg para editar vídeos.")

    append_parts = [(clip, video_media_info(clip)) for clip in sources]
    adjusted = _caption_window(captions, start, end, duration, sum(info[0] for _, info in append_parts))
    output = service.outputs / f"{uuid4().hex}-editado.mp4"
    with tempfile.TemporaryDirectory(prefix="edicao-video-", dir=service.outputs) as temporary:
        directory = Path(temporary)
        sources = [(video, start, end - start, volume, has_audio)] + [
            (clip, 0, info[0], 100, info[3]) for clip, info in append_parts
        ]
        parts = []
        for index, (source, offset, length, gain, audio) in enumerate(sources):
            part = directory / f"clip-{index}.mp4"
            fades = (transition == "fade" and index > 0, transition == "fade" and index < len(sources) - 1)
            _render_clip(ffmpeg, source, part, offset, length, gain, (width, height), fades, audio, len(sources) > 1)
            parts.append(part)
        manifest = directory / "sequencia.txt"
        manifest.write_text("".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8")
        draft = directory / output.name
        inputs = ("-f", "concat", "-safe", 1, "-i", manifest) if len(parts) > 1 else ("-i", parts[0])
        filters = []
        maps = ["-map", "0:v:0"]
        if burn and adjusted:
            (directory / "legendas.srt").write_text(subtitles(adjusted), encoding="utf-8")
            filters += [
                "-vf",
                f"subtitles=legendas.srt:force_style='{CAPTION_STYLES[style]},FontSize=22,Alignment=2,MarginV=24'",
                "-c:v",
                "libx264",
            ]
        else:
            filters += ["-c:v", "copy"]
        if music:
            inputs += ("-stream_loop", -1, *MUSIC_INPUT, "-i", music)
            filters += [
                "-filter_complex",
                f"[1:a:0]volume={music_volume / 100}[music];[0:a:0][music]amix=inputs=2:duration=first:normalize=0[mix]",
            ]
            maps += ["-map", "[mix]"]
        else:
            filters += ["-c:a", "copy"]
            maps += ["-map", "0:a:0"]
        _convert(ffmpeg, *inputs, *maps, *filters, "-movflags", "+faststart", draft, cwd=directory)
        return _publish_video(draft, output, adjusted)
