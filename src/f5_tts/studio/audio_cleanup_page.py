"""Studio page for removing a video's original background audio."""

from pathlib import Path
from shutil import which
from uuid import uuid4

from nicegui import run, ui

from f5_tts.studio.audio_cleanup import (
    AUDIO_EXTENSIONS,
    AUDIO_LIMIT,
    export_cleaned_video,
    process_vocal_track,
    read_audio_cleanup_handoff,
)
from f5_tts.studio.video_dubbing import VIDEO_EXTENSIONS, VIDEO_LIMIT, dubbing_stems
from f5_tts.studio.video_probe import video_duration


def register_audio_cleanup_page(service, shell, studio_action):
    t = service.translate_ui

    @ui.page("/audio-cleanup")
    def audio_cleanup_page(transfer_id: str = ""):
        incoming = read_audio_cleanup_handoff(service, transfer_id)
        state = {
            "video": incoming["video"] if incoming else None,
            "music": incoming["music"] if incoming else None,
            "voice": None,
            "voice_raw": None,
            "background": None,
            "output": None,
            "duration": 0.0,
        }
        with shell("Limpar áudio do vídeo"):
            with ui.column().classes("studio-hero w-full gap-2"):
                ui.label(t("LIMPEZA DE ÁUDIO")).classes("text-xs tracking-widest opacity-75")
                ui.label(t("Separe a voz e escolha a música de fundo.")).classes("studio-title")
                ui.label(
                    t("O Demucs separa a voz dos demais sons. Depois, você pode ouvir a voz isolada e mixar uma música.")
                ).classes("opacity-85")

            with ui.row().classes("w-full gap-6 items-start flex-wrap md:flex-nowrap"):
                with ui.card().classes("studio-card w-full md:w-1/2 gap-4"):
                    ui.label(t("1. Arquivos de origem")).classes("text-lg font-semibold")
                    video_status = ui.label(t("Envie um vídeo para começar.")).classes("studio-muted text-sm")

                    async def receive_video(event):
                        suffix = Path(event.file.name).suffix.lower()
                        if suffix not in VIDEO_EXTENSIONS or event.file.size() > VIDEO_LIMIT:
                            ui.notify(t("Envie MP4, MOV, MKV ou WebM de até 500 MB."), type="warning")
                            video_upload.reset()
                            return
                        path = service.uploads / f"{uuid4().hex}{suffix}"
                        try:
                            await event.file.save(path)
                        except Exception:
                            path.unlink(missing_ok=True)
                            raise
                        state.update(video=path, voice=None, voice_raw=None, background=None, output=None, duration=0.0)
                        video_status.set_text(path.name)
                        voice_player.set_source("")
                        background_player.set_source("")
                        update_bed_preview()
                        source_video.set_source(str(path))
                        result_video.set_source("")

                    video_upload = ui.upload(
                        label=t("Enviar vídeo"), auto_upload=True, max_file_size=VIDEO_LIMIT, on_upload=receive_video
                    ).props(f'accept="{",".join(VIDEO_EXTENSIONS)}"').classes("w-full")

                    music_status = ui.label(t("Música opcional: sem música, o vídeo ficará apenas com a voz.")).classes(
                        "studio-muted text-sm"
                    )

                    async def receive_music(event):
                        suffix = Path(event.file.name).suffix.lower()
                        if suffix not in AUDIO_EXTENSIONS or event.file.size() > AUDIO_LIMIT:
                            ui.notify(t("Envie WAV, MP3, FLAC, OGG, M4A ou AAC de até 500 MB."), type="warning")
                            music_upload.reset()
                            return
                        path = service.upload_path(event.file.name)
                        await event.file.save(path)
                        state.update(music=path, output=None)
                        music_status.set_text(path.name)
                        update_bed_preview()

                    music_upload = ui.upload(
                        label=t("Enviar música de fundo (opcional)"),
                        auto_upload=True,
                        max_file_size=AUDIO_LIMIT,
                        on_upload=receive_music,
                    ).props(f'accept="{",".join(sorted(AUDIO_EXTENSIONS))}"').classes("w-full")

                    ui.label(t("2. Mixagem e formato")).classes("text-lg font-semibold pt-3")
                    noise_reduction = ui.slider(min=0, max=30, value=12, step=1).props("label-always")
                    ui.label(t("Redução automática de ruído da voz (dB)")).classes("text-sm")
                    ui.label(t("Aumente aos poucos e compare na prévia; reduções fortes podem alterar a voz.")).classes(
                        "studio-muted text-xs"
                    )
                    voice_volume = ui.slider(min=0, max=1, value=1, step=0.01).props("label-always")
                    ui.label(t("Volume da voz (0 a 100%)")).classes("text-sm")
                    music_volume = ui.slider(min=0, max=1, value=0.18, step=0.01).props("label-always")
                    ui.label(t("Volume da música (0 a 100%)")).classes("text-sm")
                    auto_duck = ui.checkbox(
                        t("Abaixar automaticamente a música quando a voz falar"), value=True
                    )
                    keep_original_background = ui.checkbox(
                        t("Usar o fundo original sem voz se nenhuma música for enviada"), value=False
                    )
                    resolution = ui.radio(
                        {"1920x1080": t("Paisagem · 1920 × 1080"), "1080x1920": t("Retrato · 1080 × 1920")},
                        value="1920x1080",
                    ).props("inline")
                    ui.label(
                        t("O vídeo inteiro será ajustado ao formato escolhido, sem cortar a imagem; sobras recebem barras pretas.")
                    ).classes("studio-muted text-xs")

                    status = ui.label("").classes("studio-muted text-sm")
                    progress = ui.linear_progress(value=0).props("indeterminate").classes("w-full")
                    progress.set_visibility(False)

                    async def separate_voice():
                        if not state["video"]:
                            ui.notify(t("Envie um vídeo primeiro."), type="warning")
                            return
                        status.set_text(t("Separando o fundo e tratando a voz. Isso pode levar alguns minutos…"))
                        progress.set_visibility(True)
                        try:
                            ffmpeg = which("ffmpeg")
                            if not ffmpeg:
                                raise ValueError(t("Instale o FFmpeg para limpar o áudio do vídeo."))
                            stems = await run.io_bound(dubbing_stems, service, ffmpeg, state["video"])
                            state["voice_raw"], state["background"] = stems
                            state["duration"] = await run.io_bound(video_duration, state["video"])
                            seek_preview.props(f'max={state["duration"]:.3f}')
                            seek_preview.set_value(0)
                            state["voice"] = await run.io_bound(
                                process_vocal_track, service, ffmpeg, state["voice_raw"], noise_reduction.value
                            )
                            voice_player.set_source(str(state["voice"]))
                            background_player.set_source(str(state["background"]))
                            update_bed_preview()
                            status.set_text(t("Voz separada, filtrada e nivelada. Ajuste e ouça a prévia."))
                            ui.notify(t("Separação concluída."), type="positive")
                        except Exception as error:
                            status.set_text(t(str(error)))
                            ui.notify(t(str(error)), type="negative", close_button=True, timeout=10000)
                        finally:
                            progress.set_visibility(False)

                    async def reprocess_voice():
                        if not state["voice_raw"]:
                            ui.notify(t("Separe a voz antes de ajustar o tratamento."), type="warning")
                            return
                        status.set_text(t("Atualizando redução de ruído e tratamento da voz…"))
                        progress.set_visibility(True)
                        try:
                            ffmpeg = which("ffmpeg")
                            state["voice"] = await run.io_bound(
                                process_vocal_track, service, ffmpeg, state["voice_raw"], noise_reduction.value
                            )
                            pause_preview()
                            voice_player.set_source(str(state["voice"]))
                            status.set_text(t("Prévia atualizada. Reproduza para conferir o tratamento."))
                        except Exception as error:
                            status.set_text(t(str(error)))
                            ui.notify(t(str(error)), type="negative", close_button=True, timeout=10000)
                        finally:
                            progress.set_visibility(False)

                    async def export_video():
                        if not state["video"]:
                            ui.notify(t("Envie um vídeo primeiro."), type="warning")
                            return
                        background = state["music"] or (
                            state["background"] if keep_original_background.value else None
                        )
                        status.set_text(t("Separando, tratando e renderizando o vídeo…"))
                        progress.set_visibility(True)
                        try:
                            output = await run.io_bound(
                                export_cleaned_video,
                                service,
                                state["video"],
                                background,
                                voice_volume.value,
                                music_volume.value,
                                resolution.value,
                                noise_reduction=noise_reduction.value,
                                auto_duck=auto_duck.value,
                            )
                            state["output"] = output
                            result_video.set_source(str(output))
                            status.set_text(t("Exportação concluída."))
                            ui.notify(t("Vídeo pronto para baixar."), type="positive")
                        except Exception as error:
                            status.set_text(t(str(error)))
                            ui.notify(t(str(error)), type="negative", close_button=True, timeout=10000)
                        finally:
                            progress.set_visibility(False)

                    with ui.row().classes("w-full gap-2"):
                        ui.button(t("Separar e ouvir voz"), icon="graphic_eq", on_click=separate_voice).props("outline")
                        ui.button(t("Atualizar tratamento da voz"), icon="tune", on_click=reprocess_voice).props("outline")
                        ui.button(t("Exportar vídeo"), icon="movie", on_click=export_video)

                with ui.card().classes("studio-card w-full md:w-1/2 gap-4"):
                    ui.label(t("Prévia sincronizada da mixagem")).classes("text-lg font-semibold")
                    ui.label(
                        t("Depois de separar a voz, toque o vídeo e ajuste os volumes enquanto assiste. A prévia é atualizada durante a reprodução.")
                    ).classes("studio-muted text-sm")
                    source_video = ui.video("", muted=True, controls=False).classes(
                        "w-full aspect-video bg-slate-950 rounded-lg"
                    )
                    voice_player = ui.audio("", controls=False)
                    music_player = ui.audio("", controls=False, loop=True)
                    background_player = ui.audio("", controls=True).classes("w-full")
                    ui.label(t("Fundo separado da voz (prévia e download)")).classes("text-sm font-semibold")
                    ui.button(
                        t("Baixar fundo sem voz"),
                        icon="download",
                        on_click=lambda: ui.download.file(state["background"]) if state["background"] else None,
                    ).props("outline dense")
                    ui.button(
                        t("Baixar voz tratada"),
                        icon="download",
                        on_click=lambda: ui.download.file(state["voice"]) if state["voice"] else None,
                    ).props("outline dense")
                    seek_preview = ui.slider(min=0, max=1, value=0, step=0.1).props("label-always")

                    def selected_bed_path():
                        return state["music"] or (
                            state["background"] if keep_original_background.value else None
                        )

                    def seek_tracks(event):
                        position = max(0.0, float(event.value or 0))
                        if state["voice"]:
                            source_video.seek(position)
                            voice_player.seek(position)
                            if selected_bed_path():
                                music_player.seek(position)

                    seek_preview.on_value_change(seek_tracks)

                    def set_element_volume(element, amount, tag):
                        volume = max(0.0, min(float(amount or 0), 1.0))
                        ui.run_javascript(
                            f"(() => {{ const media = getHtmlElement({element.id})?.querySelector('{tag}'); "
                            f"if (media) media.volume = {volume:.3f}; }})()"
                        )

                    voice_volume.on_value_change(
                        lambda event: set_element_volume(voice_player, event.value, "audio")
                    )
                    music_volume.on_value_change(
                        lambda event: set_element_volume(music_player, event.value, "audio")
                    )

                    def update_bed_preview():
                        selected = selected_bed_path()
                        music_player.set_source(str(selected) if selected else "")

                    keep_original_background.on_value_change(lambda _event: update_bed_preview())

                    def play_preview():
                        if not state["voice"]:
                            ui.notify(t("Separe a voz antes de ouvir a prévia."), type="warning")
                            return
                        position = float(seek_preview.value or 0)
                        set_element_volume(voice_player, voice_volume.value, "audio")
                        set_element_volume(music_player, music_volume.value, "audio")
                        source_video.seek(position)
                        voice_player.seek(position)
                        source_video.play()
                        voice_player.play()
                        if selected_bed_path():
                            music_player.seek(position)
                            music_player.play()
                        preview_state["playing"] = True

                    def pause_preview():
                        preview_state["playing"] = False
                        source_video.run_method("pause")
                        voice_player.run_method("pause")
                        music_player.run_method("pause")

                    with ui.row().classes("w-full gap-2"):
                        ui.button(t("Reproduzir prévia"), icon="play_arrow", on_click=play_preview)
                        ui.button(t("Pausar"), icon="pause", on_click=pause_preview).props("outline")
                    preview_position = ui.label(t("Posição: 0,0 s")).classes("studio-muted text-xs")
                    ui.label(t("Vídeo exportado")).classes("text-lg font-semibold pt-4")
                    result_video = ui.video("").classes("w-full aspect-video bg-slate-950 rounded-lg")
                    ui.button(
                        t("Baixar vídeo"),
                        icon="download",
                        on_click=lambda: ui.download.file(state["output"]) if state["output"] else None,
                    ).props("outline")

                    preview_state = {"playing": False}

                    async def follow_preview_position():
                        if not preview_state["playing"]:
                            return
                        current = await ui.run_javascript(
                            f"getHtmlElement({source_video.id})?.querySelector('video')?.currentTime || 0"
                        )
                        if isinstance(current, (int, float)):
                            seek_preview.set_value(min(float(current), state["duration"]))
                            preview_position.set_text(t("Posição: {value_0} s", value_0=f"{current:.1f}"))
                            if state["duration"] and current >= state["duration"]:
                                pause_preview()

                    ui.timer(0.4, follow_preview_position)

            if transfer_id and incoming is None:
                ui.notify(t("A transferência expirou. Envie o arquivo novamente."), type="warning")
            elif incoming:
                if state["video"]:
                    video_status.set_text(state["video"].name)
                    source_video.set_source(str(state["video"]))
                if state["music"]:
                    music_status.set_text(state["music"].name)
                    update_bed_preview()
