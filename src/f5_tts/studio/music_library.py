"""Shared local library for background music used throughout the Studio."""

import re
import shutil
from pathlib import Path
from uuid import uuid4

from nicegui import ui


MUSIC_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
MUSIC_LIMIT = 500_000_000
MUSIC_FOLDER = "music-library"


def library_path(service):
    path = service.outputs / MUSIC_FOLDER
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_music(service):
    folder = library_path(service)
    rows = []
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in MUSIC_EXTENSIONS:
            rows.append(
                {
                    "id": path.name,
                    "name": path.name[33:],
                    "path": path,
                    "size": path.stat().st_size,
                    "modified": path.stat().st_mtime,
                }
            )
    return sorted(rows, key=lambda row: (row["modified"], row["name"].casefold()), reverse=True)


def resolve_music(service, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}-.+", identifier):
        raise ValueError("Selecione uma música da biblioteca local.")
    path = (library_path(service) / identifier).resolve()
    if path.parent != library_path(service).resolve() or not path.is_file():
        raise ValueError("A música selecionada não está mais na biblioteca.")
    if path.suffix.lower() not in MUSIC_EXTENSIONS:
        raise ValueError("O arquivo da biblioteca não é um formato de música compatível.")
    return path


def add_music(service, source, name=None):
    source = service._managed_asset(source)
    if source.suffix.lower() not in MUSIC_EXTENSIONS or source.stat().st_size > MUSIC_LIMIT:
        raise ValueError("A música precisa estar em WAV, MP3, FLAC, OGG, M4A ou AAC e ter até 500 MB.")
    original_name = Path(name or source.name).name
    original_name = re.sub(r"[^\w.() -]+", "_", original_name, flags=re.UNICODE).strip(" .")[:160]
    if not original_name or Path(original_name).suffix.lower() != source.suffix.lower():
        original_name = source.name
    destination = library_path(service) / f"{uuid4().hex}-{original_name}"
    if source.is_relative_to(service.outputs / "youtube-downloads"):
        shutil.move(str(source), destination)
        try:
            source.parent.rmdir()
        except OSError:
            pass
    else:
        shutil.copy2(source, destination)
    return next(row for row in list_music(service) if row["id"] == destination.name)


def delete_music(service, identifier):
    path = resolve_music(service, identifier)
    service.delete_asset(path)
    return path.name[33:]


def register_music_library_page(service, shell, action):
    t = service.translate_ui

    @ui.page("/music-library")
    def music_library_page():
        with shell("Músicas de fundo"):
            ui.label(t("Músicas de fundo")).classes("studio-title")
            ui.label(t("Uma biblioteca local compartilhada pelo criador de áudio, a dublagem e os editores.")).classes(
                "studio-muted"
            )
            with ui.card().classes("studio-card w-full gap-4"):
                ui.label(t("Adicionar música à biblioteca")).classes("text-lg font-semibold")
                status = ui.label(t("Envie WAV, MP3, FLAC, OGG, M4A ou AAC de até 500 MB.")).classes(
                    "studio-muted text-sm"
                )

                async def upload_music(event):
                    suffix = Path(event.file.name).suffix.lower()
                    if suffix not in MUSIC_EXTENSIONS or event.file.size() > MUSIC_LIMIT:
                        ui.notify(t("Arquivo recusado. Envie uma música compatível de até 500 MB."), type="warning")
                        uploader.reset()
                        return
                    source = service.upload_path(event.file.name)
                    try:
                        await event.file.save(source)
                        item = await action(add_music, service, source, event.file.name)
                        if item:
                            source.unlink(missing_ok=True)
                            status.set_text(t("Música adicionada: {value_0}", value_0=item["name"]))
                            refresh()
                    finally:
                        source.unlink(missing_ok=True)
                        uploader.reset()

                uploader = ui.upload(
                    label=t("Enviar música"),
                    auto_upload=True,
                    max_file_size=MUSIC_LIMIT,
                    on_upload=upload_music,
                ).props(f'accept="{",".join(sorted(MUSIC_EXTENSIONS))}"').classes("w-full")

                tracks = ui.column().classes("w-full gap-3")

                def refresh():
                    tracks.clear()
                    rows = list_music(service)
                    if not rows:
                        with tracks:
                            ui.label(t("Nenhuma música na biblioteca. Baixe um MP3 do YouTube ou envie um arquivo."))
                        return
                    for item in rows:
                        with tracks:
                            with ui.card().classes("w-full border border-slate-200 p-3"):
                                with ui.row().classes("w-full items-center justify-between gap-3"):
                                    ui.label(item["name"]).classes("font-medium break-all flex-1")
                                    ui.label(f"{item['size'] / 1_000_000:.1f} MB").classes("text-xs studio-muted")
                                ui.audio(str(item["path"])).classes("w-full")

                                async def remove(identifier=item["id"]):
                                    result = await action(delete_music, service, identifier)
                                    if result is not None:
                                        ui.notify(t("Música removida da biblioteca: {value_0}", value_0=result), type="positive")
                                        refresh()

                                ui.button(t("Apagar música"), icon="delete", on_click=remove).props(
                                    "flat no-caps color=negative"
                                )

                refresh()
