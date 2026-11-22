"""Read video stream metadata with FFprobe."""

import json
import math
import shutil
import subprocess


VIDEO_INPUT = ("-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm")


def video_media_info(path):
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise ValueError("Instale o FFprobe para analisar vídeos.")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                *VIDEO_INPUT,
                "-show_entries",
                "stream=codec_type,width,height,duration:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
        duration = float(video.get("duration") or data["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        has_audio = any(stream["codec_type"] == "audio" for stream in data["streams"])
        return duration, video["width"], video["height"], has_audio
    except (KeyError, StopIteration, ValueError, subprocess.SubprocessError) as error:
        raise ValueError("Não foi possível ler a duração ou a imagem de um dos vídeos.") from error


def video_duration(path):
    return video_media_info(path)[0]
