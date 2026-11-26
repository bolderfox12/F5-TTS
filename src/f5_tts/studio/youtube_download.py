"""Download publicly available YouTube videos for local editing."""

import shutil
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def download_youtube_video(service, url, max_height=1080, download_mode="video", *, progress=None):
    parsed = urlparse((url or "").strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname not in YOUTUBE_HOSTS
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Cole um link https válido de um vídeo do YouTube.")
    try:
        max_height = int(max_height)
    except (TypeError, ValueError) as error:
        raise ValueError("Selecione uma resolução disponível.") from error
    if max_height not in {720, 1080, 2160}:
        raise ValueError("Selecione uma resolução disponível.")
    if download_mode not in {"video", "mp3"}:
        raise ValueError("Selecione vídeo ou MP3.")
    try:
        import yt_dlp
    except ImportError as error:
        raise ValueError(
            "O yt-dlp não está instalado. Instale as dependências opcionais do Studio e reinicie o aplicativo."
        ) from error

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Instale o FFmpeg para combinar vídeo e áudio baixados.")
    folder = service.outputs / "youtube-downloads" / uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    status = {"percent": 0.0, "message": "Conectando ao YouTube…", "speed": ""}

    def hook(item):
        state = item.get("status")
        if state == "downloading":
            total = item.get("total_bytes") or item.get("total_bytes_estimate")
            downloaded = item.get("downloaded_bytes", 0)
            if total:
                status["percent"] = min(0.99, downloaded / total)
            status["message"] = "Baixando vídeo e áudio…"
            speed = item.get("speed")
            status["speed"] = f"{speed / 1_000_000:.1f} MB/s" if speed else ""
        elif state == "finished":
            status.update(percent=0.99, message="Baixado. Finalizando o arquivo…")
        if progress:
            progress(dict(status))

    options = {
        "format": (
            f"bestaudio/best"
            if download_mode == "mp3"
            else f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"
        ),
        "outtmpl": str(folder / "%(title).150B-%(id)s.%(ext)s"),
        "noplaylist": True,
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
        "progress_hooks": [hook],
        "ffmpeg_location": ffmpeg,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "overwrites": False,
        "socket_timeout": 30,
    }
    if download_mode == "mp3":
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
        ]
    else:
        options["merge_output_format"] = "mp4"
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url.strip(), download=True)
        if not info:
            raise ValueError("O YouTube não retornou um arquivo para este vídeo.")
        candidates = [
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in ({".mp3"} if download_mode == "mp3" else {".mp4", ".mkv", ".webm", ".mov"})
        ]
        if not candidates:
            raise ValueError("O download terminou, mas o arquivo de vídeo não foi encontrado.")
        output = max(candidates, key=lambda path: path.stat().st_mtime_ns)
        status.update(percent=1.0, message="Download concluído.", speed="")
        if progress:
            progress(dict(status))
        return {"path": output, "title": info.get("title", output.name), "status": dict(status)}
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
