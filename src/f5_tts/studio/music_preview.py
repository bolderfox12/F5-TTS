"""Shared music library and temporary narration previews."""

import shutil
import subprocess
from asyncio import Lock
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from nicegui import run, ui

from f5_tts.audio import local_audio_input
from f5_tts.studio.audio_timeline import AudioTimeline
from f5_tts.studio.music_library import MUSIC_EXTENSIONS, MUSIC_LIMIT, add_music, delete_music, list_music


def mix_preview(narration, music, volume, target):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Instale o FFmpeg para ouvir a prévia com música.")
    command = [
        ffmpeg, "-nostdin", "-v", "error", *local_audio_input(narration), "-stream_loop", "-1",
        *local_audio_input(music), "-filter_complex",
        f"[1:a:0]volume={volume / 100:.2f}[music];"
        "[0:a:0][music]amix=inputs=2:duration=first:normalize=0,"
        "alimiter=limit=0.95:level=false:latency=true[preview]",
        "-map", "[preview]", "-c:a", "libmp3lame", "-b:a", "128k", str(target),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        target.unlink(missing_ok=True)
        raise ValueError("Não foi possível misturar a música com a fala.") from error
    return target


def music_preview(service, t, action):
    lock = Lock()
    storage = narration = preview = None
    download_button = None
    closed = False

    def clear_preview():
        nonlocal preview
        player.set_source("")
        player.set_visibility(False)
        if download_button is not None:
            download_button.set_enabled(False)
        if preview is not None:
            preview.unlink(missing_ok=True)
            preview = None

    def update_buttons():
        selected = music_list.value is not None
        mix_button.set_enabled(selected and narration is not None)
        delete_button.set_enabled(selected)
        download_button.set_enabled(preview is not None)

    def refresh_library(selected=None):
        rows = list_music(service)
        options = {row["id"]: row["name"] for row in rows}
        choice = selected if selected in options else (music_list.value if music_list.value in options else next(iter(options), None))
        music_list.set_options(options, value=choice)
        empty_library.set_visibility(not options)
        update_buttons()

    def register_refresh(callbacks):
        if callbacks is not None:
            callbacks.append(refresh_library)

    async def build_preview():
        nonlocal preview, storage
        async with lock:
            selected = music_list.value
            if closed or selected is None or narration is None:
                return
            row = next((item for item in list_music(service) if item["id"] == selected), None)
            if row is None:
                refresh_library()
                return
            mix_button.disable()
            mix_button.props("loading")
            try:
                if storage is None:
                    storage = TemporaryDirectory(prefix="f5-studio-music-")
                target = Path(storage.name) / f"{uuid4().hex}.mp3"
                result = await action(mix_preview, narration, row["path"], int(volume.value), target)
                if result and music_list.value == selected and row["path"].is_file():
                    clear_preview()
                    preview = result
                    player.set_visibility(True)
                    await player.load(preview, service._measure_waveform)
                    download_button.set_enabled(True)
                else:
                    target.unlink(missing_ok=True)
            finally:
                mix_button.props(remove="loading")
                refresh_library(selected)

    async def select_music(_event=None):
        clear_preview()
        update_buttons()
        if narration is not None and music_list.value:
            await build_preview()

    async def set_narration(path):
        nonlocal narration
        async with lock:
            if not closed:
                narration = Path(path)
                clear_preview()
                update_buttons()
        if not closed and music_list.value:
            await build_preview()

    async def upload_music(event):
        candidate = service.upload_path(event.file.name)
        try:
            if event.file.size() > MUSIC_LIMIT:
                raise ValueError("Arquivo recusado. O limite é 500 MB por arquivo.")
            await event.file.save(candidate)
            item = await action(add_music, service, candidate, event.file.name)
            if item:
                refresh_library(item["id"])
                ui.notify(t("Música adicionada à biblioteca compartilhada."), type="positive")
        except Exception as error:
            ui.notify(t(str(error)), type="negative")
        finally:
            candidate.unlink(missing_ok=True)
            uploader.reset()

    async def remove_music():
        async with lock:
            selected = music_list.value
            if closed or selected is None:
                return
            removed = await action(delete_music, service, selected)
            if removed:
                clear_preview()
                refresh_library()
                ui.notify(t("Música removida da biblioteca: {value_0}", value_0=removed), type="positive")

    async def close():
        nonlocal closed
        async with lock:
            closed = True
            if storage is not None:
                storage.cleanup()

    with ui.expansion(
        t("Testar música de fundo"), icon="music_note", on_value_change=lambda event: refresh_library() if event.value else None
    ).classes("w-full"):
        uploader = ui.upload(
            label=t("Enviar música à biblioteca compartilhada"), auto_upload=True, max_file_size=MUSIC_LIMIT,
            max_files=1, on_upload=upload_music,
            on_rejected=lambda: ui.notify(t("Arquivo recusado. O limite é 500 MB por arquivo."), type="warning"),
        ).props(f'accept="{",".join(sorted(MUSIC_EXTENSIONS))}"').classes("w-full")
        empty_library = ui.label(t("Nenhuma música na biblioteca. Baixe um MP3 do YouTube ou envie um arquivo.")).classes("text-sm studio-muted")
        music_list = ui.select({}, label=t("Música de fundo salva")).props("outlined clearable").classes("w-full")
        music_list.on_value_change(select_music)
        ui.label(t("Volume da música de fundo")).classes("text-sm")
        volume = ui.slider(min=0, max=100, step=5, value=20).props("label-always").classes("w-full")
        with ui.row().classes("gap-2 flex-wrap"):
            mix_button = ui.button(t("Preparar prévia"), icon="graphic_eq", on_click=build_preview).props("outline no-caps")
            download_button = ui.button(
                t("Baixar áudio com música"), icon="download", on_click=lambda: ui.download.file(preview) if preview else None
            ).props("outline no-caps")
            download_button.disable()
            delete_button = ui.button(t("Apagar música"), icon="delete", on_click=remove_music).props("flat no-caps color=negative")
            ui.button(t("Gerenciar biblioteca"), icon="library_music", on_click=lambda: ui.navigate.to("/music-library")).props("flat")
        player = AudioTimeline(t).classes("w-full")
        player.set_visibility(False)
    refresh_library()
    ui.context.client.on_delete(close)
    set_narration.register_refresh = register_refresh
    return set_narration
