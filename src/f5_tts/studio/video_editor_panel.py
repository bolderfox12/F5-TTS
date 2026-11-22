"""Editor controls for the generated dub and its additional media."""

from pathlib import Path
from uuid import uuid4

from nicegui import ui

from f5_tts.studio.local_tools import srt_time
from f5_tts.studio.montage_page import send_to_video_editor
from f5_tts.studio.video_dubbing import VIDEO_EXTENSIONS, VIDEO_LIMIT
from f5_tts.studio.video_editor import (
    MUSIC_EXTENSIONS,
    _caption_window,
    create_playable_preview,
    edit_dubbed_video,
)
from f5_tts.studio.video_probe import video_duration
from f5_tts.studio.video_timeline import VideoTimeline


def original_player(service, action, source):
    preview = source.with_name(f"{source.stem}-previa.mp4")
    tried = preview.is_file()
    player = ui.video(preview if tried else source).classes("w-full aspect-video bg-slate-950 rounded-lg")
    status = ui.label("").classes("studio-muted text-sm")

    async def fallback():
        nonlocal tried
        if tried:
            status.set_text(service.translate_ui("Não foi possível reproduzir o vídeo original."))
            return
        tried = True
        status.set_text(service.translate_ui("Preparando a prévia do vídeo original…"))
        compatible = await action(create_playable_preview, service, source)
        if not player.is_deleted:
            if compatible:
                player.set_source(compatible)
                status.set_text("")
            else:
                status.set_text(service.translate_ui("Não foi possível reproduzir o vídeo original."))

    player.on("error", fallback)
    return player


def caption_track(name, cues):
    return {
        "name": name,
        "kind": "captions",
        "blocks": [{**{key: row[key] for key in ("start", "end")}, "label": row["text"]} for row in cues],
    }


def video_editor(
    service,
    action,
    video,
    captions,
    original_cues,
    duration,
    set_busy,
    is_busy,
    *,
    source,
    transcript,
    download_original=True,
    adapt_preview=None,
    auto_fit=None,
):
    t = service.translate_ui
    current = video, captions
    clips = []
    music = None
    cues = [dict(row) for row in original_cues]

    with ui.card().classes("studio-tool-card w-full"):
        ui.label(t("Editor de vídeo dublado")).classes("text-lg font-semibold")
        with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 gap-4"):
            with ui.column().classes("min-w-0 gap-1"):
                ui.label(t("Vídeo original")).classes("font-semibold")
                original = original_player(service, action, source)
            with ui.column().classes("min-w-0 gap-1"):
                ui.label(t("Vídeo dublado")).classes("font-semibold")
                player = ui.video(video).classes("w-full aspect-video bg-slate-950 rounded-lg")
        timeline = VideoTimeline(t, original, player).classes("w-full")
        timeline.set_tracks(original=[caption_track(t("Transcrição original"), transcript)] if transcript else [])
        with ui.row() as downloads:
            ui.button(t("Baixar MP4"), icon="download", on_click=lambda: ui.download.file(current[0]))
            ui.button(t("Baixar SRT"), icon="subtitles", on_click=lambda: ui.download.file(current[1]))

        def send_montage():
            selected_video, selected_captions = current
            assets = [(selected_video, t("Vídeo dublado"))]
            if selected_captions.is_file() and selected_captions.stat().st_size:
                assets.append((selected_captions, t("Legendas")))
            send_to_video_editor(service, action, assets, t("Vídeo dublado"))

        ui.button(t("Enviar ao editor de vídeo"), icon="video_library", on_click=send_montage).props("outline no-caps")
        downloads.set_visibility(download_original)
        if duration is None:
            return

        maximum = round(duration, 3)
        for cue in cues:
            cue["end"] = min(round(cue["end"], 3), maximum)
            cue["start"] = round(cue["start"], 3)
        cues[:] = [row for row in cues if row["start"] < row["end"]]
        initial_cues = [dict(row) for row in cues]

        def show_tracks(dub_length, displayed_cues, extra=(), music_name=None):
            video_blocks = [{"start": 0, "end": dub_length, "label": t("Vídeo dublado")}]
            offset = dub_length
            for name, _path, length in extra:
                video_blocks.append({"start": offset, "end": offset + length, "label": name})
                offset += length
            tracks = [
                {"name": t("Vídeo"), "kind": "video", "blocks": video_blocks},
                {"name": t("Voz dublada"), "kind": "voice", "blocks": [video_blocks[0]]},
                caption_track(t("Legendas"), displayed_cues),
            ]
            if music_name:
                tracks.append(
                    {
                        "name": t("Música de fundo"),
                        "kind": "music",
                        "blocks": [{"start": 0, "end": offset, "label": music_name}],
                    }
                )
            timeline.set_tracks(dubbed=tracks)

        show_tracks(duration, initial_cues)
        with ui.column().classes("w-full") as controls:
            with ui.row().classes("w-full items-start"):
                start = ui.number(t("Início (s)"), value=0, min=0, max=maximum, step=0.001).props("outlined")
                end = ui.number(t("Fim (s)"), value=maximum, min=0, max=maximum, step=0.001).props("outlined")
                volume = ui.number(t("Volume da dublagem (%)"), value=100, min=0, max=200, step=1).props("outlined")

            async def store(file, extensions, limit, rejection):
                suffix = Path(file.name).suffix.lower()
                if suffix not in extensions or file.size() > limit:
                    ui.notify(t(rejection), type="warning")
                    return None
                path = service.uploads / f"{uuid4().hex}{suffix}"
                try:
                    await file.save(path)
                except Exception as error:
                    path.unlink(missing_ok=True)
                    ui.notify(t(str(error)), type="negative")
                    return None
                return path

            def lock(value):
                set_busy(value)
                controls.set_visibility(not value)

            with ui.expansion(t("Clipes e transições"), icon="movie_filter", value=True).classes("w-full"):
                ui.label(t("Os clipes entram depois do trecho dublado, na ordem da lista.")).classes("studio-muted")

                async def append(event):
                    if is_busy():
                        return
                    lock(True)
                    try:
                        for file in event.files:
                            path = await store(
                                file,
                                VIDEO_EXTENSIONS,
                                VIDEO_LIMIT,
                                "Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB.",
                            )
                            if path is not None:
                                length = await action(video_duration, path)
                                if length is None:
                                    path.unlink(missing_ok=True)
                                else:
                                    clips.append((file.name, path, length))
                        clip_list.refresh()
                    finally:
                        clip_upload.reset()
                        lock(False)

                clip_upload = (
                    ui.upload(
                        label=t("Adicionar outros vídeos"),
                        auto_upload=True,
                        multiple=True,
                        max_files=10,
                        max_file_size=VIDEO_LIMIT,
                        on_multi_upload=append,
                        on_rejected=lambda: ui.notify(
                            t("Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB."), type="warning"
                        ),
                    )
                    .props(f'accept="{",".join(VIDEO_EXTENSIONS)}"')
                    .classes("w-full")
                )
                transition = ui.select(
                    {"cut": t("Corte direto"), "fade": t("Escurecer entre clipes")},
                    value="cut",
                    label=t("Transição entre clipes"),
                ).props("outlined")

                @ui.refreshable
                def clip_list():
                    def move(index, step):
                        target = index + step
                        if 0 <= target < len(clips):
                            clips[index], clips[target] = clips[target], clips[index]
                            clip_list.refresh()

                    def remove(index):
                        clips.pop(index)
                        clip_list.refresh()

                    offset = duration
                    for index, (name, _path, length) in enumerate(clips):
                        span = f"{srt_time(offset)[:-4]} – {srt_time(offset + length)[:-4]}"
                        offset += length
                        with ui.row().classes("w-full items-center"):
                            ui.label(f"{name} · {span}").classes("grow")
                            ui.button(icon="arrow_upward", on_click=lambda i=index: move(i, -1)).props("flat").tooltip(
                                t("Mover para cima")
                            ).set_enabled(index > 0)
                            ui.button(icon="arrow_downward", on_click=lambda i=index: move(i, 1)).props("flat").tooltip(
                                t("Mover para baixo")
                            ).set_enabled(index < len(clips) - 1)
                            ui.button(icon="close", on_click=lambda i=index: remove(i)).props("flat").tooltip(
                                t("Retirar clipe")
                            )

                clip_list()

            with ui.expansion(t("Música de fundo"), icon="music_note").classes("w-full"):

                async def add_music(event):
                    nonlocal music
                    if is_busy():
                        return
                    lock(True)
                    try:
                        path = await store(
                            event.file,
                            MUSIC_EXTENSIONS,
                            100_000_000,
                            "Arquivo recusado. O limite é 100 MB por arquivo.",
                        )
                        if path is not None:
                            music = path
                            music_name.set_text(event.file.name)
                    finally:
                        music_upload.reset()
                        lock(False)

                def remove_music():
                    nonlocal music
                    music = None
                    music_name.set_text("")

                music_upload = (
                    ui.upload(
                        label=t("Adicionar música"),
                        max_file_size=100_000_000,
                        auto_upload=True,
                        on_upload=add_music,
                    )
                    .props(f'accept="{",".join(MUSIC_EXTENSIONS)}"')
                    .classes("w-full")
                )
                with ui.row().classes("items-center"):
                    music_name = ui.label("")
                    music_volume = ui.number(t("Volume da música (%)"), value=20, min=0, max=100, step=1).props(
                        "outlined"
                    )
                    ui.button(t("Retirar música"), icon="close", on_click=remove_music).props("flat")

            with ui.expansion(t("Editar legendas"), icon="subtitles").classes("w-full"):
                ui.label(
                    t("Os tempos das legendas seguem o vídeo dublado antes do corte e depois os clipes adicionados.")
                ).classes("studio-muted")
                with ui.row().classes("items-center"):
                    burn = ui.switch(t("Incorporar legendas no MP4"), value=True)
                    style = ui.select(
                        {
                            "white": t("Branco com contorno"),
                            "yellow": t("Amarelo com contorno"),
                            "box": t("Branco com fundo"),
                        },
                        value="white",
                        label=t("Estilo da legenda"),
                    ).props("outlined")
                    style.bind_enabled_from(burn, "value")

                @ui.refreshable
                def caption_list():
                    def remove(index):
                        cues.pop(index)
                        caption_list.refresh()

                    for index, cue in enumerate(cues):
                        with ui.row().classes("w-full items-start"):
                            for key, label in (("start", t("Início (s)")), ("end", t("Fim (s)"))):
                                ui.number(
                                    label,
                                    value=cue[key],
                                    min=0,
                                    step=0.001,
                                    on_change=lambda event, row=cue, k=key: row.update({k: event.value}),
                                ).props("outlined dense").classes("w-28")
                            ui.textarea(
                                t("Texto da legenda"),
                                value=cue["text"],
                                on_change=lambda event, row=cue: row.update(text=event.value or ""),
                            ).props("outlined dense autogrow").classes("flex-1 min-w-40")
                            ui.button(icon="delete", on_click=lambda i=index: remove(i)).props("flat").tooltip(
                                t("Remover legenda")
                            )

                def add_caption():
                    limit = round(duration + sum(clip[2] for clip in clips), 3)
                    tail = max((row["end"] or 0 for row in cues), default=0)
                    first = round(min(tail, max(0, limit - 2)), 3)
                    cues.append({"start": first, "end": min(limit, first + 2), "text": ""})
                    caption_list.refresh()

                caption_list()
                ui.button(t("Adicionar legenda"), icon="add", on_click=add_caption)

            def restore():
                nonlocal current
                current = video, captions
                downloads.set_visibility(download_original)
                player.set_source(video)
                show_tracks(duration, initial_cues)
                timeline.run_method("select", "dubbed")
                start.set_value(0)
                end.set_value(maximum)
                volume.set_value(100)
                transition.set_value("cut")
                music_volume.set_value(20)
                burn.set_value(True)
                style.set_value("white")
                clips.clear()
                clip_list.refresh()
                remove_music()
                cues[:] = [dict(row) for row in initial_cues]
                caption_list.refresh()
                status.set_text("")

            async def apply():
                nonlocal current, video, captions, download_original
                if is_busy():
                    return
                player.pause()
                lock(True)
                status.set_text(t("Aplicando edição ao vídeo…"))
                try:
                    if adapt_preview:
                        updated = await adapt_preview()
                        if updated is None:
                            status.set_text("")
                            return
                        if updated:
                            video, captions, refreshed_cues = updated
                            fresh = {row["segment_id"]: row for row in refreshed_cues}
                            previous = {row.get("segment_id"): row for row in initial_cues}
                            for cue in cues:
                                identifier = cue.get("segment_id")
                                if identifier in fresh and identifier in previous:
                                    for field in ("start", "end", "text"):
                                        if cue[field] == previous[identifier][field]:
                                            cue[field] = fresh[identifier][field]
                            cues.extend(dict(row) for row in refreshed_cues if row["segment_id"] not in previous)
                            initial_cues[:] = [dict(row) for row in refreshed_cues]
                            download_original = False
                            caption_list.refresh()
                    edited = await action(
                        edit_dubbed_video,
                        service,
                        video,
                        [dict(row) for row in cues],
                        start.value,
                        end.value,
                        volume.value,
                        style=style.value,
                        burn=burn.value,
                        clips=[clip[1] for clip in clips],
                        transition=transition.value,
                        music=music,
                        music_volume=music_volume.value,
                    )
                    if edited:
                        current = edited
                        downloads.set_visibility(True)
                        player.set_source(current[0])
                        cut_end = duration if end.value == maximum else end.value
                        adjusted = _caption_window(
                            cues,
                            start.value,
                            cut_end,
                            duration,
                            sum(clip[2] for clip in clips),
                        )
                        show_tracks(cut_end - start.value, adjusted, clips, music_name.text if music else None)
                        timeline.run_method("select", "dubbed")
                        status.set_text(t("Prévia editada. Os downloads usam esta versão."))
                    else:
                        status.set_text("")
                finally:
                    lock(False)

            if auto_fit is not None:
                ui.checkbox(t("Encurtar e sincronizar falas com Gemini na prévia")).bind_value(auto_fit, "value")
            with ui.row():
                ui.button(t("Gerar prévia editada"), icon="movie", on_click=apply)
                ui.button(t("Restaurar vídeo dublado"), icon="undo", on_click=restore).props("flat")

        status = ui.label("").classes("studio-muted")
