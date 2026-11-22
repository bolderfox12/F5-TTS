"""Workspace music library and temporary previews for generated narration."""

import shutil
import subprocess
from asyncio import Lock
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from nicegui import run, ui

from f5_tts.audio import local_audio_input
from f5_tts.studio.audio_timeline import AudioTimeline


def mix_preview(narration, music, volume, target):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Instale o FFmpeg para ouvir a prévia com música.")
    command = [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        *local_audio_input(narration),
        "-stream_loop",
        "-1",
        *local_audio_input(music),
        "-filter_complex",
        f"[1:a:0]volume={volume / 100:.2f}[music];"
        "[0:a:0][music]amix=inputs=2:duration=first:normalize=0,"
        "alimiter=limit=0.95:level=false:latency=true[preview]",
        "-map",
        "[preview]",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(target),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        target.unlink(missing_ok=True)
        raise ValueError("Não foi possível misturar a música com a fala.") from error
    return target


def music_preview(service, t, action):
    library = service.home / "preview-music"
    lock = Lock()
    storage = narration = preview = None
    closed = False

    def clear_preview():
        nonlocal preview
        player.set_source("")
        player.set_visibility(False)
        if preview is not None:
            preview.unlink(missing_ok=True)
            preview = None

    def update_buttons():
        selected = music_list.value is not None
        mix_button.set_enabled(selected and narration is not None)
        delete_button.set_enabled(selected)

    def select_music():
        clear_preview()
        update_buttons()

    def refresh_library(selected=None):
        paths = [path for path in library.glob("*") if not path.name.startswith(".") and path.is_file()]
        options = {
            path.name: path.name[33:] for path in sorted(paths, key=lambda path: (path.name[33:].casefold(), path.name))
        }
        selected = selected or music_list.value
        music_list.set_options(options, value=selected if selected in options else next(iter(options), None))
        empty_library.set_visibility(not options)
        update_buttons()

    def register_refresh(callbacks):
        if callbacks is not None:
            callbacks.append(refresh_library)

    async def set_narration(path):
        nonlocal narration
        async with lock:
            if not closed:
                narration = Path(path)
                clear_preview()
                update_buttons()

    async def upload_music(event):
        async with lock:
            if closed:
                return
            candidate = library / f".upload-{uuid4().hex}"
            try:
                if event.file.size() > 100_000_000:
                    raise ValueError("Arquivo recusado. O limite é 100 MB por arquivo.")
                filename = service.upload_path(event.file.name).name
                library.mkdir(mode=0o700, exist_ok=True)
                await event.file.save(candidate)
                await run.io_bound(local_audio_input, candidate)
                candidate.replace(library / filename)
            except Exception as error:
                ui.notify(t(str(error)), type="negative")
            else:
                refresh_library(filename)
            finally:
                candidate.unlink(missing_ok=True)
                uploader.reset()

    async def build_preview():
        nonlocal preview, storage
        async with lock:
            selected = music_list.value
            if closed or selected is None or narration is None:
                return
            music = library / selected
            if not music.is_file():
                refresh_library()
                return
            mix_button.disable()
            mix_button.props("loading")
            try:
                if storage is None:
                    storage = TemporaryDirectory(prefix="f5-studio-music-")
                target = Path(storage.name) / f"{uuid4().hex}.mp3"
                result = await action(mix_preview, narration, music, int(volume.value), target)
                if result and music_list.value == selected and music.is_file():
                    clear_preview()
                    preview = result
                    player.set_visibility(True)
                    await player.load(preview, service._measure_waveform)
                else:
                    target.unlink(missing_ok=True)
            finally:
                mix_button.props(remove="loading")
                refresh_library()

    async def delete_music():
        async with lock:
            selected = music_list.value
            if closed or selected is None:
                return
            try:
                (library / selected).unlink(missing_ok=True)
            except OSError:
                ui.notify(t("Não foi possível apagar a música."), type="negative")
                return
            clear_preview()
            refresh_library()
            ui.notify(t("Música e prévia apagadas."), type="positive")

    async def close():
        nonlocal closed
        async with lock:
            closed = True
            if storage is not None:
                storage.cleanup()

    with ui.expansion(
        t("Testar música de fundo"),
        icon="music_note",
        on_value_change=lambda event: refresh_library() if event.value else None,
    ).classes("w-full"):
        uploader = (
            ui.upload(
                label=t("Enviar música"),
                auto_upload=True,
                max_file_size=100_000_000,
                max_files=1,
                on_upload=upload_music,
                on_rejected=lambda: ui.notify(t("Arquivo recusado. O limite é 100 MB por arquivo."), type="warning"),
            )
            .props('accept=".wav,.mp3,.flac,.ogg,.m4a,.aac"')
            .classes("w-full")
        )
        ui.label(t("Músicas enviadas")).classes("font-medium")
        empty_library = ui.label(t("Nenhuma música enviada.")).classes("text-sm studio-muted")
        music_list = ui.radio({}, on_change=select_music).classes("w-full break-all")
        ui.label(t("Volume da música de fundo")).classes("text-sm")
        volume = ui.slider(min=0, max=100, step=5, value=20).props("label-always").classes("w-full")
        with ui.row().classes("gap-2"):
            mix_button = ui.button(t("Preparar prévia"), icon="graphic_eq", on_click=build_preview).props(
                "outline no-caps"
            )
            delete_button = ui.button(t("Apagar música"), icon="delete", on_click=delete_music).props(
                "flat no-caps color=negative"
            )
        player = AudioTimeline(t).classes("w-full")
        player.set_visibility(False)
    refresh_library()
    ui.context.client.on_delete(close)
    set_narration.register_refresh = register_refresh
    return set_narration
