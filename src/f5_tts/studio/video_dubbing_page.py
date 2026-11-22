"""Review the cast and transcript before generating a dubbed video."""

from pathlib import Path
from uuid import uuid4

from nicegui import app, ui

from f5_tts.studio import chatterbox_backend as chatterbox
from f5_tts.studio.adr_panel import take_panel
from f5_tts.studio.adr_recorder import ADRRecorder
from f5_tts.studio.dubbing_review import (
    DubbingProject,
    ExportRequest,
    ReviewLine,
    VoiceRequest,
    demucs_command,
)
from f5_tts.studio.dubbing_review_api import register_dubbing_routes
from f5_tts.studio.dubbing_review_panel import speech_card
from f5_tts.studio.local_tools import srt_time
from f5_tts.studio.montage_page import send_to_video_editor
from f5_tts.studio.script_review import shorten_dubbing_line
from f5_tts.studio.video_dubbing import (
    GEMINI_TRANSCRIPTION,
    VIDEO_EXTENSIONS,
    VIDEO_LIMIT,
    analyze_video,
    speaker_references,
    translate_dialogue,
)

from f5_tts.studio.video_probe import video_duration
from f5_tts.studio.video_editor_panel import caption_track, original_player, video_editor
from f5_tts.studio.video_timeline import VideoTimeline


LANGUAGES = {
    "pt": "Português",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "ko": "Coreano · 한국어",
    "ja": "Japonês · 日本語",
    "zh": "Chinês (mandarim) · 中文",
}


def register_video_dubbing_page(service, shell, action):
    t = service.translate_ui
    projects = {}
    register_dubbing_routes(app, projects)

    @ui.page("/video-dubbing")
    def ai_page():
        video_dubbing_page(False)

    @ui.page("/video-dubbing-human")
    def human_page():
        video_dubbing_page(True)

    def video_dubbing_page(initial_human):
        human = initial_human
        take_offsets = {}
        source = None
        segments = []
        analyzed_language = None
        busy = False
        translation_key = None
        dialogue = []
        original_timeline = None
        project = None
        previews = {}
        shorter_lines = {}
        original = None

        def clear_result():
            result.clear()
            render_status.set_text("")
            source_preview.set_visibility(source is not None)

        progress_state = (0, "", {})

        def update_progress(value, message, **values):
            nonlocal progress_state
            progress_state = value, message, values

        def redraw_progress():
            value, message, values = progress_state
            progress_bar.set_value(value)
            progress_label.set_text(t(message, **values))

        def begin_progress(message):
            update_progress(0.02, message)
            redraw_progress()
            progress_corner.set_visibility(True)
            progress_timer.activate()

        def end_progress():
            progress_timer.deactivate()
            progress_corner.set_visibility(False)

        def switch_mode(value):
            nonlocal human
            if busy:
                return
            if value != human:
                path = "/video-dubbing-human" if value else "/video-dubbing"
                ui.run_javascript(f"history.replaceState(null, '', '{path}')")
                if not value:
                    recorder.run_method("closeMic")
            human = value
            heading.set_text(t("Dublagem convencional com humanos" if human else "Dublagem por IA"))
            ai_settings.set_visibility(not human)
            adr_controls.set_visibility(human)
            auto_speed.set_visibility(not human)
            treat_human.set_visibility(human)
            latency_group.set_visibility(human)
            mode_buttons[0].props(
                "unelevated" if not human else "outline", remove="outline" if not human else "unelevated"
            )
            mode_buttons[1].props("unelevated" if human else "outline", remove="outline" if human else "unelevated")
            review.refresh()

        with shell(
            "Dublagem convencional com humanos" if human else "Dublagem por IA",
            local_navigation={
                "/video-dubbing": lambda: switch_mode(False),
                "/video-dubbing-human": lambda: switch_mode(True),
            },
        ):
            heading = ui.label(t("Dublagem convencional com humanos" if human else "Dublagem por IA")).classes(
                "studio-title"
            )
            with ui.row().classes("w-full"):
                mode_buttons = [
                    ui.button(t("Dublagem por IA"), on_click=lambda: switch_mode(False)).props("no-caps"),
                    ui.button(t("Dublagem convencional com humanos"), on_click=lambda: switch_mode(True)).props(
                        "no-caps"
                    ),
                ]

            ui.label(t("Separe as vozes do vídeo, revise a transcrição e duble no idioma escolhido.")).classes(
                "studio-muted"
            )
            with ui.card().classes("studio-card w-full"):
                ui.label(t("Vídeo e modelo de transcrição")).classes("text-lg font-semibold")
                upload_status = ui.label(t("Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB."))

                async def receive(event):
                    nonlocal source, busy, original_timeline, project, original
                    if busy:
                        return
                    suffix = Path(event.file.name).suffix.lower()
                    if suffix not in VIDEO_EXTENSIONS or event.file.size() > VIDEO_LIMIT:
                        ui.notify(t("Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB."), type="warning")
                        uploader.reset()
                        return
                    path = service.uploads / (uuid4().hex + suffix)
                    busy = True
                    analyze.disable()
                    try:
                        await event.file.save(path)
                    except Exception as error:
                        path.unlink(missing_ok=True)
                        ui.notify(t(str(error)), type="negative")
                    else:
                        source = path
                        project = None
                        previews.clear()
                        shorter_lines.clear()
                        take_offsets.clear()
                        segments.clear()
                        dialogue.clear()
                        review.refresh()
                        clear_result()
                        source_preview.clear()
                        with source_preview:
                            ui.label(t("Vídeo original")).classes("text-lg font-semibold")
                            original = original_player(service, action, path)
                            original_timeline = VideoTimeline(t, original).classes("w-full")
                        recorder.set_video(original)
                        upload_status.set_text(event.file.name)
                        status.set_text("")
                        mix.set_visibility(False)
                    finally:
                        busy = False
                        add_line_button.set_enabled(source is not None)
                        guide_button.set_enabled(source is not None)
                        analyze.set_enabled(source is not None)
                        uploader.reset()

                uploader = (
                    ui.upload(
                        label=t("Escolher vídeo"),
                        auto_upload=True,
                        max_file_size=VIDEO_LIMIT,
                        on_upload=receive,
                        on_rejected=lambda: ui.notify(
                            t("Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB."), type="warning"
                        ),
                    )
                    .props(f'accept="{",".join(VIDEO_EXTENSIONS)}"')
                    .classes("w-full")
                )

                with ui.row().classes("w-full"):
                    origin = ui.select(LANGUAGES, value="pt", label=t("Idioma original")).props("outlined")
                    input_model = ui.select(
                        {
                            "openai/whisper-small": t("Whisper Small · mais leve"),
                            "openai/whisper-large-v3-turbo": t("Whisper Turbo · mais pesado"),
                            GEMINI_TRANSCRIPTION: t("Gemini · transcrição e personagens"),
                        },
                        value="openai/whisper-small",
                        label=t("Modelo de transcrição"),
                    ).props("outlined")

                ui.label(t("Ao transcrever com Gemini, o áudio extraído do vídeo é enviado ao Google.")).classes(
                    "studio-muted"
                ).bind_visibility_from(input_model, "value", lambda value: value == GEMINI_TRANSCRIPTION)
                with ui.column().classes("w-full") as gemini_options:
                    gemini_key = (
                        ui.input(
                            t("Chave Gemini (opcional se já configurada)"),
                            password=True,
                            password_toggle_button=True,
                        )
                        .props("outlined autocomplete=off")
                        .classes("w-full max-w-md")
                    )
                gemini_options.set_visibility(False)

                def set_busy(value):
                    nonlocal busy
                    busy = value
                    uploader.set_enabled(not value)
                    origin.set_enabled(not value)
                    input_model.set_enabled(not value)
                    recover_missing.set_enabled(not value)
                    gemini_key.set_enabled(not value)
                    analyze.set_enabled(not value and source is not None)
                    destination.set_enabled(not value)
                    translation_provider.set_enabled(not value)
                    output_model.set_enabled(not value)
                    add_line_button.set_enabled(not value and source is not None)
                    guide_button.set_enabled(not value and source is not None)
                    for button in mode_buttons:
                        button.set_enabled(not value)
                    treat_audio.set_enabled(not value)
                    mix.set_visibility(bool(project and segments))
                    for element in (
                        background,
                        voice_volume,
                        auto_speed,
                        auto_fit,
                        treat_human,
                        latency,
                        preview_button,
                        export_button,
                    ):
                        element.set_enabled(not value)
                    review.refresh()

                async def transcribe():
                    nonlocal analyzed_language, project
                    if busy or source is None:
                        return
                    if any(row["start"] is None or row["end"] is None for row in segments):
                        return ui.notify(
                            t("Preencha os tempos das falas antes de atualizar a transcrição."), type="warning"
                        )
                    source_language = origin.value
                    status.set_text(t("Transcrevendo e identificando as vozes…"))
                    begin_progress("Transcrevendo e identificando as vozes…")
                    set_busy(True)
                    try:
                        found = await action(
                            analyze_video,
                            service,
                            source,
                            source_language,
                            input_model.value,
                            gemini_key=gemini_key.value if input_model.value == GEMINI_TRANSCRIPTION else "",
                            on_progress=update_progress,
                            recover_missing=recover_missing.value,
                        )
                        if not found:
                            return
                        duration = project.duration if project else await action(video_duration, source)
                        if duration is None:
                            return
                        recognized = [
                            {**row, "end": min(row["end"], duration), "reference_duration": row["end"] - row["start"]}
                            for row in found
                            if row["start"] < min(row["end"], duration)
                        ]
                        if not recognized:
                            ui.notify(t("Não foi reconhecida fala. Confira o arquivo e o idioma."), type="warning")
                            return
                        if project is None:
                            project = await action(DubbingProject, service, source, recognized, duration)
                            if project is None:
                                return
                            projects[project.id] = project
                            segments.extend(recognized)
                            dialogue.extend([""] * len(recognized))
                        else:

                            def overlap(first, second):
                                return min(first["end"], second["end"]) - max(first["start"], second["start"])

                            existing = list(zip(project.rows, segments, strict=True))
                            for identifier, row in existing:
                                sample = max(recognized, key=lambda candidate: overlap(row, candidate))
                                if overlap(row, sample) > 0 and not row.get("reference"):
                                    row["reference"] = sample["reference"]
                                    row["reference_duration"] = sample["reference_duration"]
                                    row["reference_transcript"] = sample["text"]
                                    row["text"] = row["text"] or sample["text"]
                                    project.attach_reference(identifier, row)
                            for sample in recognized:
                                if all(overlap(row, sample) <= 0 for _, row in existing):
                                    project.add_line(sample)
                                    segments.append(sample)
                                    dialogue.append("")
                        analyzed_language = source_language
                        if original_timeline:
                            original_timeline.set_tracks(original=[caption_track(t("Transcrição original"), segments)])
                    finally:
                        end_progress()
                        status.set_text("")
                        set_busy(False)

                recover_missing = ui.checkbox(t("Revisar trechos sem transcrição"), value=True)
                analyze = ui.button(t("Transcrever e separar vozes"), icon="subtitles", on_click=transcribe)
                analyze.disable()
                status = ui.label("").classes("studio-muted")

            source_preview = ui.card().classes("studio-card w-full")
            source_preview.set_visibility(False)

            with ui.card().classes("studio-card w-full") as adr_controls:
                ui.label(t("Estúdio de gravação humana (ADR)")).classes("text-lg font-semibold")

                async def ensure_project():
                    nonlocal project
                    if source is None:
                        return None
                    if project is None:
                        duration = await action(video_duration, source)
                        if duration is None:
                            return None
                        project = await action(DubbingProject, service, source, [], duration)
                        if project is not None:
                            projects[project.id] = project
                    return project

                async def add_line():
                    if busy or source is None:
                        return
                    set_busy(True)
                    try:
                        current = await ensure_project()
                        if current is None:
                            return
                        start = min(current.duration - 0.001, max((row["end"] or 0 for row in segments), default=0))
                        end = min(current.duration, start + 3)
                        row = {"start": max(0, round(start, 3)), "end": end, "text": "", "speaker": 1}
                        current.add_line(row)
                        segments.append(row)
                        dialogue.append("")
                    finally:
                        set_busy(False)

                async def prepare_guide():
                    if busy or source is None:
                        return
                    set_busy(True)
                    begin_progress("Separando música e efeitos do áudio original…")
                    try:
                        current = await ensure_project()
                        if current:
                            track = await action(current.guide)
                            if track:
                                recorder.set_background(current.url(track))
                                ui.notify(t("Guia M&E pronto."), type="positive")
                    finally:
                        end_progress()
                        set_busy(False)

                recorder = ADRRecorder(t, lambda event: set_busy(bool(event.args)), lambda: review.refresh()).classes(
                    "w-full"
                )
                with ui.row():
                    add_line_button = ui.button(t("Adicionar fala"), icon="add", on_click=add_line)
                    guide_button = ui.button(t("Preparar guia M&E"), icon="headphones", on_click=prepare_guide).props(
                        "outline"
                    )
                    add_line_button.disable()
                    guide_button.disable()

            with ui.card().classes("studio-card w-full"):
                ui.label(t("Tradução e modelo de dublagem")).classes("text-lg font-semibold")
                with ui.row().classes("w-full") as ai_settings:
                    destination = ui.select(LANGUAGES, value="en", label=t("Idioma da dublagem")).props("outlined")
                    translation_provider = ui.select(
                        {"nllb": t("NLLB · tradução local"), "gemini": t("Gemini · tradução do roteiro")},
                        value="nllb",
                        label=t("Modelo de tradução"),
                    ).props("outlined")
                    output_model = ui.select(
                        service.model_choices(),
                        value=chatterbox.MODEL_ID,
                        label=t("Modelo de dublagem"),
                    ).props("outlined")
                ui.label(t("Ao traduzir com Gemini, o texto do roteiro é enviado ao Google.")).classes(
                    "studio-muted"
                ).bind_visibility_from(translation_provider, "value", lambda value: value == "gemini")

                def show_gemini_key():
                    gemini_options.set_visibility(
                        input_model.value == GEMINI_TRANSCRIPTION
                        or translation_provider.value == "gemini"
                        or auto_fit.value
                    )

                input_model.on_value_change(show_gemini_key)
                translation_provider.on_value_change(show_gemini_key)

                treat_audio = ui.switch(t("Tratar o áudio da dublagem"), value=True)
                ui.label(t("Reduz graves indesejados, equilibra o volume e controla picos da voz.")).classes(
                    "studio-muted"
                )

                @ui.refreshable
                def review():
                    if not segments or project is None:
                        return
                    reference_segments = [row for row in segments if row.get("reference")]
                    if not human and reference_segments:
                        ui.label(t("Referências por personagem")).classes("text-lg font-semibold")

                    @ui.refreshable
                    def references():
                        with ui.row().classes("w-full"):
                            for speaker, segment in speaker_references(reference_segments).items():
                                with ui.column():
                                    ui.label(t("Personagem {value_0}", value_0=speaker))
                                    candidates = {
                                        index: srt_time(row["start"] or 0)[:-4] + " · " + row["text"][:45]
                                        for index, row in enumerate(segments)
                                        if row["speaker"] == speaker
                                    }
                                    player = ui.audio(str(segment["reference"])).classes("max-w-full")

                                    def choose(event, actor=speaker, audio=player):
                                        selected = segments[event.value]
                                        for row in segments:
                                            if row["speaker"] == actor:
                                                row["preferred_reference"] = row is selected
                                        audio.set_source(str(selected["reference"]))
                                        review.refresh()

                                    ui.select(
                                        candidates,
                                        value=next(i for i in candidates if segments[i] is segment),
                                        label=t("Trecho de referência"),
                                        on_change=choose,
                                    ).props("outlined dense").classes("w-64").set_enabled(not busy)

                    if not human:
                        references()
                    ui.label(t("Editor e revisão de dublagem")).classes("text-lg font-semibold")
                    if not human:
                        ui.button(t("Traduzir roteiro"), icon="translate", on_click=translate).set_enabled(not busy)
                        ui.button(t("Gerar prévias das falas"), icon="record_voice_over", on_click=prepare).props(
                            "outline no-caps"
                        ).set_enabled(not busy)
                    speakers = sorted({row["speaker"] for row in segments})
                    options = {speaker: t("Personagem {value_0}", value_0=speaker) for speaker in speakers}
                    options[max(speakers) + 1] = t("Novo personagem")
                    for index, (identifier, row) in enumerate(zip(project.rows, segments, strict=True)):

                        def change(field, value, i=index, item=row):
                            if field == "translated":
                                dialogue[i] = value or ""
                            elif field == "speaker" and value is not None:
                                item["speaker"] = value
                                item.pop("preferred_reference", None)
                                review.refresh()
                            elif field == "text":
                                item["text"] = value or ""

                        async def seek(item=row):
                            if item["start"] is not None and original:
                                original.seek(item["start"])
                                if original_timeline:
                                    original_timeline.run_method("select", "original")
                                await ui.run_javascript(
                                    f"getHtmlElement({original.id})?.scrollIntoView({{block: 'nearest'}})"
                                )

                        def regenerate(i=index):
                            return regenerate_line(i)

                        voice = voice_request(index)
                        preview = previews.get(identifier)
                        if preview and preview[0] != voice:
                            preview = None

                        def human_takes(segment_id=identifier, item=row):
                            take_panel(
                                t, project, segment_id, item, recorder, take_offsets, action, review.refresh, busy
                            )

                        if not human and identifier in project.active_takes:
                            ui.badge(t("Esta fala usa um take humano na exportação."), color="teal-8")
                        basis = revision_basis(index)
                        proposal = shorter_lines.get(identifier)

                        def shorten(i=index):
                            return ask_shorter_line(i)

                        def apply_shorter(text, i=index, key=identifier, source_basis=basis):
                            if revision_basis(i) != source_basis:
                                shorter_lines.pop(key, None)
                                review.refresh()
                                ui.notify(t("A fala mudou. Peça uma nova sugestão."), type="warning")
                                return
                            dialogue[i] = text
                            shorter_lines.pop(key, None)
                            clear_result()
                            review.refresh()

                        speech_card(
                            t,
                            row,
                            options,
                            dialogue[index],
                            preview[1] if preview else None,
                            change=change,
                            seek=seek,
                            regenerate=regenerate,
                            busy=busy,
                            takes_panel=human_takes if human else None,
                            shorten=shorten if not human and identifier not in project.active_takes else None,
                            suggestions=proposal[1] if proposal and proposal[0] == basis else (),
                            apply=apply_shorter,
                        )

                def voice_request(index):
                    row = segments[index]
                    reference = speaker_references([item for item in segments if item.get("reference")]).get(
                        row["speaker"]
                    )
                    selected = next(
                        (
                            identifier
                            for identifier, item in zip(project.rows, segments, strict=True)
                            if item is reference
                        ),
                        None,
                    )
                    return {
                        "text": dialogue[index],
                        "speaker": row["speaker"],
                        "language": destination.value,
                        "model": output_model.value,
                        "reference_id": selected,
                    }

                def requested_lines():
                    payload = []
                    for index, (identifier, row) in enumerate(zip(project.rows, segments, strict=True)):
                        take_id = project.active_takes.get(identifier)
                        if (not take_id and not dialogue[index].strip()) or row["start"] is None or row["end"] is None:
                            raise ValueError("Preencha os tempos e a tradução de todas as falas de IA.")
                        fields = voice_request(index)
                        if take_id and not fields["text"].strip():
                            fields["text"] = row["text"]
                        payload.append(
                            ReviewLine(
                                id=identifier,
                                start=row["start"],
                                end=row["end"],
                                take_id=take_id,
                                offset_ms=take_offsets.get(identifier, 0),
                                **fields,
                            )
                        )
                    return payload

                def remember_previews(lines, clips):
                    indexes = {identifier: index for index, identifier in enumerate(project.rows)}
                    for line, clip in zip((line for line in lines if line.take_id is None), clips, strict=True):
                        previews[line.id] = (voice_request(indexes[line.id]), clip)

                def revision_basis(index):
                    row = segments[index]
                    return (row["text"], dialogue[index], destination.value, row["start"], row["end"])

                async def ask_shorter_line(index):
                    if busy:
                        return
                    original_text, translated, language, start, end = revision_basis(index)
                    if not translated.strip() or start is None or end is None or end <= start:
                        return ui.notify(
                            t("Preencha o texto traduzido e confira o início e o fim desta fala."), type="warning"
                        )
                    if not service.read().get("offline") and not (
                        gemini_key.value.strip() or service.review_key_active("gemini")
                    ):
                        gemini_options.set_visibility(True)
                        gemini_key.run_method("focus")
                        await ui.run_javascript(f"getHtmlElement({gemini_key.id})?.scrollIntoView({{block: 'center'}})")
                        return ui.notify(t("Informe a chave da API do provedor escolhido."), type="warning")
                    identifier = list(project.rows)[index]
                    clip = previews.get(identifier)
                    measured = clip[1][1] if clip and clip[0] == voice_request(index) else None
                    context = [dialogue[i] for i in (index - 1, index + 1) if 0 <= i < len(dialogue) and dialogue[i]]
                    basis = revision_basis(index)
                    shorter_lines.pop(identifier, None)
                    begin_progress("Buscando uma fala mais curta…")
                    set_busy(True)
                    try:
                        variants = await action(
                            shorten_dubbing_line,
                            service,
                            original_text,
                            translated,
                            language,
                            end - start,
                            measured,
                            gemini_key=gemini_key.value,
                            context=context,
                        )
                        if variants:
                            shorter_lines[identifier] = (basis, variants)
                        elif variants == []:
                            ui.notify(
                                t("O Gemini não encontrou uma versão mais curta com o mesmo sentido."), type="warning"
                            )
                    finally:
                        end_progress()
                        set_busy(False)

                async def regenerate_line(index):
                    if busy or not dialogue[index].strip():
                        return ui.notify(t("Preencha a tradução desta fala."), type="warning")
                    identifier = list(project.rows)[index]
                    payload = voice_request(index)
                    try:
                        request = VoiceRequest(**payload)
                    except ValueError as error:
                        return ui.notify(t(str(error)), type="warning")
                    begin_progress("Regerando a fala…")
                    set_busy(True)
                    try:
                        generated = await action(project.regenerate, identifier, request, on_progress=update_progress)
                        if generated:
                            previews[identifier] = (payload, generated)
                    finally:
                        end_progress()
                        set_busy(False)

                async def prepare():
                    if busy:
                        return
                    try:
                        lines = requested_lines()
                    except ValueError as error:
                        return ui.notify(t(str(error)), type="warning")
                    render_status.set_text(t("Gerando prévias das falas…"))
                    begin_progress("Gerando prévias das falas…")
                    set_busy(True)
                    try:
                        generated = await action(project.prepare, lines, on_progress=update_progress)
                        if generated:
                            remember_previews(lines, generated)
                    finally:
                        end_progress()
                        render_status.set_text("")
                        set_busy(False)

                def current_translation_key():
                    return (origin.value, destination.value, tuple(row["text"] for row in segments))

                async def translate():
                    nonlocal translation_key
                    if busy or not segments:
                        return
                    if origin.value != analyzed_language:
                        ui.notify(t("Transcreva novamente após alterar o idioma original."), type="warning")
                        return
                    key = current_translation_key()
                    render_status.set_text(t("Traduzindo o roteiro…"))
                    set_busy(True)
                    try:
                        translated = await action(
                            translate_dialogue,
                            service,
                            [dict(row) for row in segments],
                            key[0],
                            key[1],
                            provider=translation_provider.value,
                            gemini_key=gemini_key.value if translation_provider.value == "gemini" else "",
                        )
                        if translated:
                            dialogue[:] = translated
                            translation_key = key
                            clear_result()
                    finally:
                        render_status.set_text("")
                        set_busy(False)

                def mix_request():
                    return ExportRequest(
                        lines=requested_lines(),
                        background_volume=int(background.value),
                        voice_volume=int(voice_volume.value),
                        auto_speed=auto_speed.value,
                        treat_audio=treat_audio.value,
                        treat_human=treat_human.value,
                        latency_ms=int(latency.value),
                    )

                async def fit_request(request):
                    originals = {
                        identifier: row["text"] for identifier, row in zip(project.rows, segments, strict=True)
                    }
                    if not service.read().get("offline") and not (
                        gemini_key.value.strip() or service.review_key_active("gemini")
                    ):
                        gemini_options.set_visibility(True)
                    return await action(
                        project.fit_preview,
                        request,
                        originals,
                        gemini_key.value,
                        on_progress=lambda value, message, **values: update_progress(value * 0.7, message, **values),
                    )

                async def keep_adjustments(before, after):
                    previous = {line.id: line.text for line in before.lines}
                    positions = {identifier: index for index, identifier in enumerate(project.rows)}
                    changed = [line for line in after.lines if line.text != previous[line.id]]
                    for line in changed:
                        dialogue[positions[line.id]] = line.text
                        shorter_lines.pop(line.id, None)
                    if changed:
                        ui.notify(
                            t("Falas encurtadas automaticamente: {value_0}", value_0=len(changed)), type="positive"
                        )
                    cached = await action(project.prepare, after.lines)
                    if cached:
                        remember_previews(after.lines, cached)

                async def render(*, preview):
                    if busy or project is None:
                        return
                    try:
                        request = mix_request()
                    except ValueError as error:
                        return ui.notify(t(str(error)), type="warning")
                    message = "Gerando a prévia dublada…" if preview else "Mixando e exportando o vídeo…"
                    render_status.set_text(t(message))
                    begin_progress(message)
                    set_busy(True)
                    try:
                        fitting = preview and auto_fit.value
                        adjusted_request = await fit_request(request) if fitting else request
                        if adjusted_request is None:
                            render_status.set_text("")
                            return

                        def render_progress(value, message, **values):
                            update_progress(0.70 + value * 0.30 if fitting else value, message, **values)

                        finished = await action(
                            project.export,
                            adjusted_request,
                            preview=preview,
                            on_progress=render_progress,
                        )
                        if finished:
                            video, captions, captions_data = finished
                            await keep_adjustments(request, adjusted_request)
                            base_request = adjusted_request

                            async def adapt_montage():
                                nonlocal base_request
                                if not auto_fit.value:
                                    return ()
                                try:
                                    latest = mix_request()
                                except ValueError as error:
                                    ui.notify(t(str(error)), type="warning")
                                    return None
                                begin_progress("Ajustando a dublagem para a prévia editada…")
                                try:
                                    fitted = await fit_request(latest)
                                    if fitted is None:
                                        return None
                                    if fitted == base_request:
                                        return ()
                                    updated = await action(
                                        project.export,
                                        fitted,
                                        preview=True,
                                        on_progress=lambda value, message, **values: update_progress(
                                            0.70 + value * 0.30, message, **values
                                        ),
                                    )
                                    if updated:
                                        await keep_adjustments(latest, fitted)
                                        base_request = fitted
                                    return updated
                                finally:
                                    end_progress()

                            result.clear()
                            with result:
                                if any(row.get("preview_overflow") for row in captions_data):
                                    ui.label(
                                        t(
                                            "Algumas falas foram cortadas nesta prévia. Ajuste os trechos destacados antes de exportar."
                                        )
                                    ).classes("text-amber-900")
                                if video.suffix == ".mp4":
                                    video_editor(
                                        service,
                                        action,
                                        video,
                                        captions,
                                        captions_data,
                                        project.duration,
                                        set_busy,
                                        lambda: busy,
                                        source=source,
                                        transcript=segments,
                                        download_original=not preview,
                                        adapt_preview=adapt_montage,
                                        auto_fit=auto_fit,
                                    )
                                else:
                                    ui.button(t("Baixar vídeo dublado"), on_click=lambda: ui.download.file(video))
                                    ui.button(t("Baixar SRT"), on_click=lambda: ui.download.file(captions))
                                    ui.button(
                                        t("Enviar ao editor de vídeo"),
                                        icon="video_library",
                                        on_click=lambda: send_to_video_editor(
                                            service,
                                            action,
                                            [(video, t("Vídeo dublado"))]
                                            + ([(captions, t("Legendas"))] if captions.stat().st_size else []),
                                            t("Vídeo dublado"),
                                        ),
                                    ).props("outline no-caps")
                            render_status.set_text(
                                t("Prévia dublada pronta." if preview else "Vídeo dublado pronto para baixar.")
                            )
                        else:
                            render_status.set_text("")
                    finally:
                        end_progress()
                        set_busy(False)

                async def render_preview():
                    await render(preview=True)

                async def export_video():
                    await render(preview=False)

                review()
                destination.on_value_change(lambda: review.refresh())
                output_model.on_value_change(lambda: review.refresh())
                with ui.card().classes("studio-card w-full") as mix:
                    ui.label(t("Mixagem e exportação")).classes("text-lg font-semibold")
                    has_demucs = bool(demucs_command(service))
                    ui.label(t("Áudio de fundo (música e efeitos)"))
                    background = ui.slider(min=0, max=100, value=70 if has_demucs else 0).props("label-always")
                    if not has_demucs:
                        ui.label(t("Instale o Demucs para preservar música e efeitos ou deixe o fundo em 0%.")).classes(
                            "studio-muted"
                        )
                    ui.label(t("Voz dublada"))
                    voice_volume = ui.slider(min=0, max=200, value=100).props("label-always")
                    auto_speed = ui.checkbox(
                        t("Ajustar velocidade automaticamente para caber no tempo da fala"), value=True
                    )
                    auto_fit = ui.checkbox(t("Encurtar e sincronizar falas com Gemini na prévia"), value=False)
                    ui.label(
                        t("Ao ativar, o texto das falas longas é enviado ao Gemini. O áudio é regerado no Studio.")
                    ).classes("studio-muted")
                    auto_fit.on_value_change(
                        lambda: gemini_options.set_visibility(
                            auto_fit.value
                            or input_model.value == GEMINI_TRANSCRIPTION
                            or translation_provider.value == "gemini"
                        )
                    )
                    treat_human = ui.checkbox(
                        t("Aplicar tratamento de voz nas gravações humanas (redução de ruído e nivelamento)"),
                        value=True,
                    )
                    with ui.column().classes("w-full") as latency_group:
                        ui.label(t("Compensação de latência global (ms)"))
                        latency = ui.slider(min=-300, max=300, step=1, value=0).props("label-always")
                    with ui.row().classes("w-full"):
                        preview_button = ui.button(
                            t("Gerar prévia dublada"), icon="play_circle", on_click=render_preview
                        )
                        preview_button.props("outline no-caps")
                        export_button = ui.button(t("Exportar vídeo dublado"), icon="movie", on_click=export_video)
                mix.set_visibility(False)
                render_status = ui.label("").classes("studio-muted")
                result = ui.column().classes("w-full")

            with ui.page_sticky(position="bottom-right", x_offset=16, y_offset=16) as progress_corner:
                with ui.card().classes("studio-card w-80 max-w-[calc(100vw-2rem)] shadow-lg"):
                    progress_label = ui.label("").classes("font-medium")
                    progress_bar = ui.linear_progress(value=0).classes("w-full")
            progress_corner.set_visibility(False)
            progress_timer = ui.timer(0.2, redraw_progress, active=False)
            switch_mode(initial_human)
